import argparse
import contextlib
import io
import json
import unittest
from unittest import mock

from cli_commands import work_items as work_item_commands
from errors import CliError
from providers.azure_devops import work_items as provider_work_items
from providers.interfaces import (
    EvidenceDownloadEntry,
    EvidenceDownloadResult,
    EvidenceReference,
    TeamRef,
    WorkItemCommentsSnapshot,
    WorkItemContextSnapshot,
    WorkItemEvidenceSnapshot,
)
import workflow_models


def sprint_fixture() -> workflow_models.Sprint:
    return workflow_models.Sprint(
        id="sprint-1",
        name="Sprint 1",
        path="Example\\Sprint 1",
        start_date="2026-07-01",
        finish_date="2026-07-15",
    )


def start_work_plan_fixture() -> workflow_models.StartWorkPlan:
    return workflow_models.StartWorkPlan(
        work_item=workflow_models.TrackedWorkItem(
            id=135821,
            title="Fix global filters",
            kind="Bug",
            state="Ready for development",
            assignee="Alice",
            iteration="Sprint 1",
            area="Example",
            estimate=3,
            labels=["rpp"],
        ),
        branch_name="fix/135821-fix-global-filters",
        note_path=".agent-notes/135821-fix-global-filters.md",
        commit_prefix="fix: 135821 ",
        change_request_title="[135821] Fix global filters",
        concise_title="Fix global filters",
        change_request_body="Closes #135821",
        commands=[
            "git checkout main && git pull",
            "git checkout -b fix/135821-fix-global-filters",
            "git push -u origin fix/135821-fix-global-filters",
        ],
    )


class WorkItemTests(unittest.TestCase):
    def test_suggest_start_work_plan_for_bug_uses_fix_prefix(self):
        item = {
            "fields": {
                "System.Id": 316042,
                "System.Title": "Non-Critical | APC | MM | Customer detail submit guard",
                "System.WorkItemType": "Bug",
                "System.State": "In Progress",
                "System.AreaPath": "Example\\Market Model",
                "System.Tags": "MM;Customer Detail",
            }
        }

        plan = provider_work_items.suggest_start_work_plan(item)

        self.assertEqual(plan["branchName"], "fix/316042-customer-detail-submit-guard")
        self.assertEqual(plan["notePath"], ".agent-notes/316042-customer-detail-submit-guard.md")
        self.assertEqual(plan["commitPrefix"], "fix: 316042 ")
        self.assertEqual(plan["prTitle"], "[316042] Non-Critical | APC | MM | Customer detail submit guard")
        self.assertEqual(plan["conciseTitle"], "Customer detail submit guard")

    def test_parse_title_facets_extracts_prefixed_title_parts(self):
        facets = provider_work_items.parse_title_facets(
            "Non-Critical | AMI | MM | Summary - Active global filters on summary step leads to DB Exception"
        )

        self.assertEqual(facets["severity"], "Non-Critical")
        self.assertEqual(facets["scope"], "AMI")
        self.assertEqual(facets["product"], "MM")
        self.assertEqual(
            facets["tailTitle"],
            "Summary - Active global filters on summary step leads to DB Exception",
        )

    def test_open_candidate_items_falls_back_to_project_backlog_without_active_sprint(self):
        fetched_items = [
            {
                "System.Id": 200,
                "System.WorkItemType": "User Story",
                "System.State": "New",
                "System.Title": "Second item",
            },
            {
                "System.Id": 150,
                "System.WorkItemType": "Bug",
                "System.State": "Ready for development",
                "System.Title": "First item",
            },
        ]

        with mock.patch.object(provider_work_items, "current_sprint", return_value=None):
            with mock.patch.object(provider_work_items, "wiql", return_value=[200, 150]) as wiql_mock:
                with mock.patch.object(provider_work_items, "fetch_items", return_value=fetched_items):
                    sprint, items = provider_work_items.open_candidate_items("token")

        self.assertEqual(sprint["id"], "no-active-sprint")
        self.assertEqual(sprint["name"], "Project backlog")
        self.assertIn("[System.AssignedTo] =", wiql_mock.call_args.args[1])
        self.assertNotIn("[System.IterationPath] UNDER", wiql_mock.call_args.args[1])
        self.assertEqual(items[0]["System.Id"], 150)
        self.assertEqual(items[1]["System.Id"], 200)

    def test_resolve_transition_state_name_uses_supported_exact_name(self):
        with mock.patch.object(
            provider_work_items,
            "fetch_work_item",
            return_value={"fields": {"System.WorkItemType": "Bug", "System.State": "New"}},
        ):
            with mock.patch.object(
                provider_work_items,
                "fetch_work_item_type_states",
                return_value=[
                    {"name": "New", "category": "Proposed"},
                    {"name": "In Progress", "category": "InProgress"},
                ],
            ):
                resolved = provider_work_items.resolve_transition_state_name(
                    "token",
                    item_id=324955,
                    desired_state="In Progress",
                )

        self.assertEqual(resolved, "In Progress")

    def test_resolve_transition_state_name_maps_in_progress_by_category(self):
        with mock.patch.object(
            provider_work_items,
            "fetch_work_item",
            return_value={"fields": {"System.WorkItemType": "Bug", "System.State": "New"}},
        ):
            with mock.patch.object(
                provider_work_items,
                "fetch_work_item_type_states",
                return_value=[
                    {"name": "New", "category": "Proposed"},
                    {"name": "Active", "category": "InProgress"},
                    {"name": "Resolved", "category": "Resolved"},
                ],
            ):
                resolved = provider_work_items.resolve_transition_state_name(
                    "token",
                    item_id=324955,
                    desired_state="In Progress",
                )

        self.assertEqual(resolved, "Active")

    def test_resolve_transition_state_name_maps_review_to_resolved_name(self):
        with mock.patch.object(
            provider_work_items,
            "fetch_work_item",
            return_value={"fields": {"System.WorkItemType": "Bug", "System.State": "Active"}},
        ):
            with mock.patch.object(
                provider_work_items,
                "fetch_work_item_type_states",
                return_value=[
                    {"name": "New", "category": "Proposed"},
                    {"name": "Active", "category": "InProgress"},
                    {"name": "Resolved", "category": "Resolved"},
                ],
            ):
                resolved = provider_work_items.resolve_transition_state_name(
                    "token",
                    item_id=324955,
                    desired_state="In Review",
                )

        self.assertEqual(resolved, "Resolved")

    def test_cmd_start_prefers_actual_provider_state_name_in_output(self):
        args = argparse.Namespace(id=135821, branch=None, apply=True)
        provider = mock.Mock()
        provider.get_start_work_plan.return_value = start_work_plan_fixture()
        provider.transition_work_item.return_value = "Active"
        cmd_show = mock.Mock()

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            work_item_commands.cmd_start(
                args,
                token="token",
                build_work_tracking_provider_func=lambda _token: provider,
                cmd_show_func=cmd_show,
            )

        provider.transition_work_item.assert_called_once_with(item_id=135821, state="In Progress")
        cmd_show.assert_called_once_with(args, "token")
        self.assertIn("moved to 'Active'", stdout.getvalue())

    def test_cmd_list_json_can_render_project_backlog_context(self):
        args = argparse.Namespace(json=True)
        provider = mock.Mock()
        provider.get_open_candidate_items.return_value = (
            workflow_models.Sprint(
                id="no-active-sprint",
                name="Project backlog",
                path="",
                start_date=None,
                finish_date=None,
            ),
            [
                workflow_models.CandidateWorkItem(
                    id=135821,
                    kind="Bug",
                    state="Ready for development",
                    title="Fix global filters",
                )
            ],
        )

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            work_item_commands.cmd_list(
                args,
                token="token",
                build_work_tracking_provider_func=lambda _token: provider,
                me="alice@example.com",
            )

        rendered = json.loads(stdout.getvalue())
        self.assertEqual(rendered["sprint"]["id"], "no-active-sprint")
        self.assertEqual(rendered["sprint"]["name"], "Project backlog")

    def test_cmd_pick_next_can_render_project_backlog_context(self):
        args = argparse.Namespace(start=False, branch=None)
        provider = mock.Mock()
        provider.get_open_candidate_items.return_value = (
            workflow_models.Sprint(
                id="no-active-sprint",
                name="Project backlog",
                path="",
                start_date=None,
                finish_date=None,
            ),
            [
                workflow_models.CandidateWorkItem(
                    id=135821,
                    kind="Bug",
                    state="Ready for development",
                    title="Fix global filters",
                )
            ],
        )
        cmd_show = mock.Mock()

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            work_item_commands.cmd_pick_next(
                args,
                token="token",
                build_work_tracking_provider_func=lambda _token: provider,
                cmd_show_func=cmd_show,
            )

        cmd_show.assert_called_once()
        shown_args = cmd_show.call_args.args[0]
        self.assertEqual(shown_args.command, "show")
        self.assertEqual(shown_args.provider, "azure-devops")
        self.assertIn("Next candidate in Project backlog", stdout.getvalue())

    def test_slugify_text_truncates_on_token_boundary(self):
        slug = provider_work_items.slugify_text(
            "Customer Detail - Safeguard modal activates after successful submit",
            max_length=36,
        )

        self.assertEqual(slug, "customer-detail-safeguard-modal")

    def test_build_triage_report_groups_items_with_shared_area_and_keywords(self):
        report = provider_work_items.build_triage_report([
            {
                "System.Id": 316043,
                "System.Title": "CRITICAL | AMI | MM | Customer market share - DB Exception on data submit",
                "System.WorkItemType": "Bug",
                "System.State": "New",
                "System.AreaPath": "Example\\Market Model\\STM",
                "System.Tags": "AMI;STM",
            },
            {
                "System.Id": 316044,
                "System.Title": "Non-Critical | AMI | MM | Summary - Active global filters on summary step leads to DB Exception",
                "System.WorkItemType": "Bug",
                "System.State": "New",
                "System.AreaPath": "Example\\Market Model\\STM",
                "System.Tags": "AMI;STM",
            },
            {
                "System.Id": 316041,
                "System.Title": "Non-Critical | APP | MM | Market Growth no longer displays global filters",
                "System.WorkItemType": "Bug",
                "System.State": "New",
                "System.AreaPath": "Example\\Market Model\\STM",
                "System.Tags": "AutomatedRegression",
            },
        ])

        self.assertEqual(report["groups"][0]["ids"], [316043, 316044])
        self.assertEqual(report["groups"][1]["ids"], [316041])

    def test_cmd_show_json_uses_work_tracking_provider(self):
        args = argparse.Namespace(id=135821, json=True)
        snapshot = WorkItemContextSnapshot(
            work_item=workflow_models.WorkItemSummary(
                id=135821,
                title="Fix global filters",
                kind="Bug",
                state="Ready for development",
                assignee="Alice",
                iteration="Sprint 1",
                area="Example",
                estimate=3,
                tags=["rpp"],
                sections={
                    "description": "desc",
                    "reproSteps": "steps",
                    "acceptanceCriteria": "criteria",
                },
            ),
            reference_summary={"count": 1},
            references=[{"source": "field", "label": "Repro Steps", "url": "https://example.test/a.png", "isImage": True}],
            comment_count=2,
            recent_comments=[workflow_models.WorkItemComment(id=1, author="Alex", published_date="2026-07-23T10:00:00Z", text="Looks good")],
            related_items={"parents": [], "children": [], "related": []},
            development_artifacts={"pullRequests": [], "commits": [], "other": []},
        )

        provider = mock.Mock()
        provider.get_work_item_context.return_value = snapshot

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            work_item_commands.cmd_show(
                args,
                token="token",
                build_work_tracking_provider_func=lambda _token: provider,
            )

        rendered = json.loads(stdout.getvalue())
        self.assertEqual(rendered["workItem"]["id"], 135821)
        self.assertEqual(rendered["recentComments"][0]["author"], "Alex")

    def test_cmd_show_text_works_without_json_flag(self):
        args = argparse.Namespace(id=135821)
        snapshot = WorkItemContextSnapshot(
            work_item=workflow_models.WorkItemSummary(
                id=135821,
                title="Fix global filters",
                kind="Bug",
                state="Ready for development",
                assignee="Alice",
                iteration="Sprint 1",
                area="Example",
                estimate=3,
                tags=["rpp"],
                sections={
                    "description": "desc",
                    "reproSteps": "steps",
                    "acceptanceCriteria": "criteria",
                },
            ),
            reference_summary=None,
            references=[],
            comment_count=0,
            recent_comments=[],
            related_items={"parents": [], "children": [], "related": []},
            development_artifacts={"pullRequests": [], "commits": [], "other": []},
        )

        provider = mock.Mock()
        provider.get_work_item_context.return_value = snapshot

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            work_item_commands.cmd_show(
                args,
                token="token",
                build_work_tracking_provider_func=lambda _token: provider,
            )

        self.assertIn("Fix global filters", stdout.getvalue())

    def test_cmd_context_json_uses_work_tracking_provider(self):
        args = argparse.Namespace(id=135821, json=True)
        snapshot = WorkItemContextSnapshot(
            work_item=workflow_models.WorkItemSummary(
                id=135821,
                title="Fix global filters",
                kind="Bug",
                state="Ready for development",
                assignee="Alice",
                iteration="Sprint 1",
                area="Example",
                estimate=3,
                tags=["rpp"],
                sections={
                    "description": "desc",
                    "reproSteps": "steps",
                    "acceptanceCriteria": "criteria",
                },
            ),
            reference_summary={"count": 1},
            references=[{"source": "field", "label": "Repro Steps", "url": "https://example.test/a.png", "isImage": True}],
            comment_count=2,
            recent_comments=[workflow_models.WorkItemComment(id=1, author="Alex", published_date="2026-07-23T10:00:00Z", text="Looks good")],
            related_items={"parents": [], "children": [], "related": []},
            development_artifacts={"pullRequests": [], "commits": [], "other": []},
        )

        provider = mock.Mock()
        provider.get_work_item_context.return_value = snapshot

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            work_item_commands.cmd_context(
                args,
                token="token",
                build_work_tracking_provider_func=lambda _token: provider,
            )

        rendered = json.loads(stdout.getvalue())
        self.assertEqual(rendered["workItem"]["title"], "Fix global filters")
        self.assertEqual(rendered["commentCount"], 2)

    def test_cmd_comments_json_uses_work_tracking_provider(self):
        args = argparse.Namespace(id=135821, json=True, latest=1)
        snapshot = WorkItemCommentsSnapshot(
            work_item=workflow_models.WorkItemSummary(
                id=135821,
                title="Fix global filters",
                kind="Bug",
                state="Ready for development",
                assignee="Alice",
                iteration="Sprint 1",
                area="Example",
                estimate=3,
                tags=["rpp"],
                sections={
                    "description": "desc",
                    "reproSteps": "steps",
                    "acceptanceCriteria": "criteria",
                },
            ),
            comment_count=2,
            comments=[
                workflow_models.WorkItemComment(id=2, author="Alex", published_date="2026-07-23T10:00:00Z", text="Latest"),
                workflow_models.WorkItemComment(id=1, author="Sam", published_date="2026-07-22T10:00:00Z", text="Older"),
            ],
        )

        provider = mock.Mock()
        provider.get_work_item_comments.return_value = snapshot

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            work_item_commands.cmd_comments(
                args,
                token="token",
                build_work_tracking_provider_func=lambda _token: provider,
            )

        rendered = json.loads(stdout.getvalue())
        self.assertEqual(rendered["commentCount"], 2)
        self.assertEqual(len(rendered["comments"]), 1)
        self.assertEqual(rendered["comments"][0]["text"], "Latest")

    def test_cmd_start_work_json_uses_work_tracking_provider(self):
        args = argparse.Namespace(id=135821, json=True)
        plan = workflow_models.StartWorkPlan(
            work_item=workflow_models.TrackedWorkItem(
                id=135821,
                title="Fix global filters",
                kind="Bug",
                state="Ready for development",
                assignee="Alice",
                iteration="Sprint 1",
                area="Example",
                estimate=3,
                labels=["rpp"],
            ),
            branch_name="fix/135821-fix-global-filters",
            note_path=".agent-notes/135821-fix-global-filters.md",
            commit_prefix="fix: 135821 ",
            change_request_title="[135821] Fix global filters",
            concise_title="Fix global filters",
            change_request_body="Closes #135821",
            commands=["git checkout -b fix/135821-fix-global-filters"],
        )

        provider = mock.Mock()
        provider.get_start_work_plan.return_value = plan

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            work_item_commands.cmd_start_work(
                args,
                token="token",
                build_work_tracking_provider_func=lambda _token: provider,
            )

        rendered = json.loads(stdout.getvalue())
        self.assertEqual(rendered["branchName"], "fix/135821-fix-global-filters")
        self.assertEqual(rendered["workItem"]["id"], 135821)

    def test_cmd_cleanup_artifacts_dry_run_renders_plan(self):
        args = argparse.Namespace(
            provider="gitlab",
            repo="group/project",
            issues=[1],
            merge_requests=[2],
            branches=["issue/1-smoke"],
            dry_run=True,
        )

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            work_item_commands.cmd_cleanup_artifacts(args, token="token", cleanup_provider_factory=lambda _token: mock.Mock())

        rendered = json.loads("\n".join(stdout.getvalue().splitlines()[1:]))
        self.assertEqual(rendered["provider"], "gitlab")
        self.assertEqual(rendered["mergeRequests"], [2])

    def test_cmd_cleanup_artifacts_applies_cleanup(self):
        args = argparse.Namespace(
            provider="gitlab",
            repo="group/project",
            issues=[1],
            merge_requests=[2],
            branches=["issue/1-smoke"],
            dry_run=False,
        )
        provider = mock.Mock()
        provider.cleanup_artifacts.return_value = {
            "issues": [(1, "closed")],
            "mergeRequests": [(2, "closed")],
            "branches": ["issue/1-smoke"],
        }

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            work_item_commands.cmd_cleanup_artifacts(args, token="token", cleanup_provider_factory=lambda _token: provider)

        provider.cleanup_artifacts.assert_called_once_with(issue_ids=[1], merge_request_ids=[2], branches=["issue/1-smoke"])
        self.assertIn("Cleanup applied in group/project", stdout.getvalue())

    def test_cmd_attachments_json_uses_evidence_provider(self):
        args = argparse.Namespace(
            id=135821,
            json=True,
            open=False,
            no_download=True,
            images_only=False,
            download_all=False,
            download_dir=None,
        )
        evidence = WorkItemEvidenceSnapshot(
            work_item_id=135821,
            title="Fix global filters",
            references=[
                EvidenceReference(
                    source="field",
                    label="Repro Steps",
                    url="https://example.test/a.png",
                    name="a.png",
                    is_image=True,
                )
            ],
        )

        provider = mock.Mock()
        provider.get_work_item_evidence.return_value = evidence
        provider.default_download_dir.return_value = "/tmp/evidence"

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            work_item_commands.cmd_attachments(
                args,
                token="token",
                build_evidence_provider_func=lambda _token: provider,
            )

        rendered = json.loads(stdout.getvalue())
        self.assertEqual(rendered["workItemId"], 135821)
        self.assertEqual(rendered["references"][0]["isImage"], True)

    def test_cmd_attachments_rejects_open_without_download(self):
        args = argparse.Namespace(
            id=135821,
            json=False,
            open=True,
            no_download=True,
            images_only=False,
            download_all=False,
            download_dir=None,
        )

        with self.assertRaises(CliError) as exc:
            work_item_commands.cmd_attachments(args, token="token", build_evidence_provider_func=lambda _token: mock.Mock())

        self.assertIn("--open requires downloads", str(exc.exception))

    def test_cmd_list_json_uses_typed_items(self):
        args = argparse.Namespace(json=True)
        provider = mock.Mock()
        provider.get_open_candidate_items.return_value = (
            sprint_fixture(),
            [
                workflow_models.CandidateWorkItem(
                    id=135821,
                    kind="Bug",
                    state="Ready for development",
                    title="Fix global filters",
                )
            ],
        )

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            work_item_commands.cmd_list(
                args,
                token="token",
                build_work_tracking_provider_func=lambda _token: provider,
            )

        rendered = json.loads(stdout.getvalue())
        self.assertEqual(rendered["sprint"]["name"], "Sprint 1")
        self.assertEqual(rendered["items"][0]["workItemType"], "Bug")

    def test_cmd_triage_json_uses_typed_report(self):
        args = argparse.Namespace(ids=[1, 2], json=True)
        provider = mock.Mock()
        provider.get_triage_report.return_value = workflow_models.TriageReport(
            items=[
                workflow_models.TriageItem(
                    id=1,
                    title="Fix global filters",
                    concise_title="Fix global filters",
                    kind="Bug",
                    state="New",
                    area="Example",
                    owner="Example",
                    scope="AMI",
                    severity="Non-Critical",
                    product="MM",
                    tags=["rpp"],
                    keywords=["filters"],
                )
            ],
            pairings=[],
            groups=[workflow_models.TriageGroup(ids=[1], reason_summary=["No strong overlap signals."])],
        )

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            work_item_commands.cmd_triage(
                args,
                token="token",
                build_work_tracking_provider_func=lambda _token: provider,
            )

        rendered = json.loads(stdout.getvalue())
        self.assertEqual(rendered["items"][0]["workItemType"], "Bug")
        self.assertEqual(rendered["groups"][0]["ids"], [1])

    def test_cmd_start_defaults_to_plan_without_transition(self):
        args = argparse.Namespace(id=135821, branch=None, apply=False)
        provider = mock.Mock()
        provider.get_start_work_plan.return_value = start_work_plan_fixture()
        cmd_show = mock.Mock()

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            work_item_commands.cmd_start(
                args,
                token="token",
                build_work_tracking_provider_func=lambda _token: provider,
                cmd_show_func=cmd_show,
            )

        provider.transition_work_item.assert_not_called()
        cmd_show.assert_called_once_with(args, "token")
        self.assertIn("fix/135821-fix-global-filters", stdout.getvalue())
        self.assertIn("Preview only", stdout.getvalue())

    def test_cmd_testing_requires_qa_email(self):
        args = argparse.Namespace(id=135821, qa=None)

        with mock.patch.object(work_item_commands, "QA_EMAIL", ""):
            with self.assertRaises(CliError) as exc:
                work_item_commands.cmd_testing(
                    args,
                    token="token",
                    build_work_tracking_provider_func=lambda _token: mock.Mock(),
                    qa_email=None,
                )

        self.assertIn("No QA email set", str(exc.exception))

    def test_cmd_review_uses_work_tracking_provider_transition(self):
        args = argparse.Namespace(id=135821, apply=True)
        provider = mock.Mock()

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            work_item_commands.cmd_review(
                args,
                token="token",
                build_work_tracking_provider_func=lambda _token: provider,
            )

        provider.transition_work_item.assert_called_once_with(item_id=135821, state="In Review")
        self.assertIn("moved to 'In Review'", stdout.getvalue())

    def test_cmd_review_defaults_to_plan_without_transition(self):
        args = argparse.Namespace(id=135821, apply=False)
        provider = mock.Mock()

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            work_item_commands.cmd_review(
                args,
                token="token",
                build_work_tracking_provider_func=lambda _token: provider,
            )

        provider.transition_work_item.assert_not_called()
        self.assertIn("Plan: move work item 135821", stdout.getvalue())
        self.assertIn("Preview only", stdout.getvalue())

    def test_cmd_testing_uses_work_tracking_provider_transition(self):
        args = argparse.Namespace(id=135821, qa="qa@example.com")
        provider = mock.Mock()

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            work_item_commands.cmd_testing(
                args,
                token="token",
                build_work_tracking_provider_func=lambda _token: provider,
                qa_email="fallback@example.com",
            )

        provider.transition_work_item.assert_called_once_with(
            item_id=135821,
            state="In Testing",
            assignee="qa@example.com",
        )
        self.assertIn("assigned to qa@example.com", stdout.getvalue())

    def test_cmd_attachments_json_includes_download_results_from_provider(self):
        args = argparse.Namespace(
            id=135821,
            json=True,
            open=False,
            no_download=False,
            images_only=False,
            download_all=True,
            download_dir="/tmp/evidence",
        )
        evidence = WorkItemEvidenceSnapshot(
            work_item_id=135821,
            title="Fix global filters",
            references=[
                EvidenceReference(
                    source="field",
                    label="Repro Steps",
                    url="https://example.test/a.png",
                    name="a.png",
                    is_image=True,
                )
            ],
        )
        result = EvidenceDownloadResult(
            downloaded=[
                EvidenceDownloadEntry(
                    label="Repro Steps",
                    url="https://example.test/a.png",
                    path="/tmp/evidence/a.png",
                    reused=False,
                )
            ],
            download_dir="/tmp/evidence",
            failures=[],
            opened=False,
            open_error=None,
        )

        provider = mock.Mock()
        provider.get_work_item_evidence.return_value = evidence
        provider.default_download_dir.return_value = "/tmp/evidence"
        provider.download_references.return_value = result

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            work_item_commands.cmd_attachments(
                args,
                token="token",
                build_evidence_provider_func=lambda _token: provider,
            )

        rendered = json.loads(stdout.getvalue())
        self.assertEqual(rendered["downloaded"][0]["path"], "/tmp/evidence/a.png")
        self.assertEqual(rendered["downloadDir"], "/tmp/evidence")

    def test_cmd_introduced_by_json_uses_work_tracking_provider(self):
        args = argparse.Namespace(id=135821, json=True)
        snapshot = WorkItemContextSnapshot(
            work_item=workflow_models.WorkItemSummary(
                id=135821,
                title="Fix global filters",
                kind="Bug",
                state="Done",
                assignee="Alice",
                iteration="Sprint 1",
                area="Example",
                estimate=3,
                tags=["rpp"],
                sections={
                    "description": "desc",
                    "reproSteps": "steps",
                    "acceptanceCriteria": "criteria",
                },
            ),
            reference_summary=None,
            references=[],
            comment_count=0,
            recent_comments=[],
            related_items={"parents": [], "children": [], "related": []},
            development_artifacts={
                "pullRequests": [{
                    "pullRequestId": 29817,
                    "repoName": "sample-repo",
                    "status": "completed",
                    "title": "Fix filters",
                    "closedDate": "2024-06-24T08:00:00Z",
                    "lastMergeCommitId": "abc123",
                }],
                "commits": [],
                "other": [],
            },
        )

        provider = mock.Mock()
        provider.get_work_item_context.return_value = snapshot

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            work_item_commands.cmd_introduced_by(
                args,
                token="token",
                build_work_tracking_provider_func=lambda _token: provider,
            )

        rendered = json.loads(stdout.getvalue())
        self.assertEqual(rendered["workItem"]["id"], 135821)
        self.assertEqual(rendered["candidate"]["pullRequestId"], 29817)

    def test_cmd_triage_json_uses_work_tracking_provider(self):
        args = argparse.Namespace(ids=[316043, 316044], json=True)
        report = workflow_models.TriageReport(
            items=[
                workflow_models.TriageItem(
                    id=316043,
                    title="First",
                    concise_title="First",
                    kind="Bug",
                    state="New",
                    area="Example",
                    owner="Example",
                    scope="AMI",
                    severity="Non-Critical",
                    product="MM",
                    tags=[],
                    keywords=[],
                )
            ],
            pairings=[],
            groups=[workflow_models.TriageGroup(ids=[316043, 316044], reason_summary=["same area path"])],
        )

        provider = mock.Mock()
        provider.get_triage_report.return_value = report

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            work_item_commands.cmd_triage(
                args,
                token="token",
                build_work_tracking_provider_func=lambda _token: provider,
            )

        rendered = json.loads(stdout.getvalue())
        self.assertEqual(rendered["groups"][0]["ids"], [316043, 316044])
        provider.get_triage_report.assert_called_once_with(item_ids=[316043, 316044])

    def test_cmd_comment_uses_work_tracking_provider(self):
        args = argparse.Namespace(id=135821, text="Please add repro details.", apply=True)
        provider = mock.Mock()
        provider.add_work_item_comment.return_value = 5889999

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            work_item_commands.cmd_comment(
                args,
                token="token",
                build_work_tracking_provider_func=lambda _token: provider,
            )

        provider.add_work_item_comment.assert_called_once_with(
            item_id=135821,
            text="Please add repro details.",
        )
        self.assertIn("comment id: 5889999", stdout.getvalue())

    def test_cmd_comment_defaults_to_plan_without_posting(self):
        args = argparse.Namespace(id=135821, text="Please add repro details.", apply=False)
        provider = mock.Mock()

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            work_item_commands.cmd_comment(
                args,
                token="token",
                build_work_tracking_provider_func=lambda _token: provider,
            )

        provider.add_work_item_comment.assert_not_called()
        self.assertIn("Please add repro details.", stdout.getvalue())
        self.assertIn("Preview only", stdout.getvalue())

    def test_cmd_sprint_json_uses_work_tracking_provider(self):
        args = argparse.Namespace(json=True)
        provider = mock.Mock()
        provider.get_current_sprint.return_value = workflow_models.Sprint(
            id="sprint-1",
            name="Sprint 26",
            path="Example\\Sprint 26",
            start_date="2026-07-21",
            finish_date="2026-08-03",
        )

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            work_item_commands.cmd_sprint(
                args,
                token="token",
                build_work_tracking_provider_func=lambda _token: provider,
            )

        rendered = json.loads(stdout.getvalue())
        self.assertEqual(rendered["id"], "sprint-1")
        self.assertEqual(rendered["startDate"], "2026-07-21")

    def test_cmd_list_json_uses_work_tracking_provider(self):
        args = argparse.Namespace(json=True)
        provider = mock.Mock()
        provider.get_open_candidate_items.return_value = (
            workflow_models.Sprint(
                id="sprint-1",
                name="Sprint 26",
                path="Example\\Sprint 26",
                start_date="2026-07-21",
                finish_date="2026-08-03",
            ),
            [
                workflow_models.CandidateWorkItem(
                    id=135821,
                    kind="Bug",
                    state="Ready for development",
                    title="Fix global filters",
                )
            ],
        )

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            work_item_commands.cmd_list(
                args,
                token="token",
                build_work_tracking_provider_func=lambda _token: provider,
                me="alice@example.com",
            )

        rendered = json.loads(stdout.getvalue())
        self.assertEqual(rendered["sprint"]["name"], "Sprint 26")
        self.assertEqual(rendered["items"][0]["id"], 135821)

    def test_cmd_teams_json_uses_work_tracking_provider(self):
        args = argparse.Namespace(json=True)
        provider = mock.Mock()
        provider.list_teams.return_value = [
            TeamRef(
                id="00000000-0000-0000-0000-000000000000",
                name="Team 2",
                description="",
            )
        ]

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            work_item_commands.cmd_teams(
                args,
                token="token",
                build_work_tracking_provider_func=lambda _token: provider,
            )

        rendered = json.loads(stdout.getvalue())
        self.assertEqual(rendered["teams"][0]["name"], "Team 2")
        self.assertEqual(rendered["teams"][0]["id"], "00000000-0000-0000-0000-000000000000")

    def test_cmd_pick_next_uses_work_tracking_provider(self):
        args = argparse.Namespace(start=False, branch=None)
        provider = mock.Mock()
        provider.get_open_candidate_items.return_value = (
            workflow_models.Sprint(
                id="sprint-1",
                name="Sprint 26",
                path="Example\\Sprint 26",
                start_date="2026-07-21",
                finish_date="2026-08-03",
            ),
            [
                workflow_models.CandidateWorkItem(
                    id=135821,
                    kind="Bug",
                    state="Ready for development",
                    title="Fix global filters",
                )
            ],
        )
        cmd_show = mock.Mock()

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            work_item_commands.cmd_pick_next(
                args,
                token="token",
                build_work_tracking_provider_func=lambda _token: provider,
                cmd_show_func=cmd_show,
            )

        cmd_show.assert_called_once()
        shown_args = cmd_show.call_args.args[0]
        self.assertEqual(shown_args.command, "show")
        self.assertEqual(shown_args.provider, "azure-devops")
        self.assertIn("Next candidate in Sprint 26", stdout.getvalue())

    def test_cmd_comments_shows_latest_requested_comments(self):
        args = argparse.Namespace(id=310818, latest=2, json=False)
        snapshot = WorkItemCommentsSnapshot(
            work_item=workflow_models.WorkItemSummary(
                id=310818,
                title="Customer market share reset",
                kind="Bug",
                state="In Progress",
                assignee="Alice",
                iteration="Sprint 1",
                area="Example",
                estimate=None,
                tags=[],
                sections={
                    "description": "",
                    "reproSteps": "",
                    "acceptanceCriteria": "",
                },
            ),
            comment_count=3,
            comments=[
                workflow_models.WorkItemComment(
                    id=31,
                    author="Alex",
                    published_date="2026-07-09T08:00:00Z",
                    text="Newest comment",
                ),
                workflow_models.WorkItemComment(
                    id=30,
                    author="Alice",
                    published_date="2026-07-08T07:00:00Z",
                    text="Second comment",
                ),
                workflow_models.WorkItemComment(
                    id=29,
                    author="QA",
                    published_date="2026-07-07T06:00:00Z",
                    text="Oldest comment",
                ),
            ],
        )

        provider = mock.Mock()
        provider.get_work_item_comments.return_value = snapshot

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            work_item_commands.cmd_comments(
                args,
                token="token",
                build_work_tracking_provider_func=lambda _token: provider,
            )

        rendered = stdout.getvalue()
        self.assertIn("Comments   : latest 2 of 3", rendered)
        self.assertIn("#31", rendered)
        self.assertIn("#30", rendered)
        self.assertNotIn("#29", rendered)


if __name__ == "__main__":
    unittest.main()
