import argparse
import contextlib
import io
import json
import os
import re
import tempfile
import unittest
from unittest import mock

from cli_commands import work_items as work_item_commands
from errors import CliError
from mutation_plans import PLAN_STORE_ENVIRONMENT_VARIABLE
from providers.azure_devops import work_items as provider_work_items
from providers.interfaces import (
    EvidenceDownloadEntry,
    EvidenceDownloadResult,
    EvidenceReference,
    TeamRef,
    WorkItemCommentsSnapshot,
    WorkItemContextSnapshot,
    WorkItemEvidenceSnapshot,
    WorkItemTransitionPreview,
)
import workflow_models


PLAN_ID_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")


def plan_id_from_preview(rendered: str) -> str:
    match = PLAN_ID_PATTERN.search(rendered)
    if match is None:
        raise AssertionError(f"Preview did not contain a plan ID: {rendered!r}")
    return match.group(0)


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


def transition_preview_fixture(
    *,
    state: str,
    concrete_state: str | None = None,
    assignee: str | None = None,
) -> WorkItemTransitionPreview:
    concrete_state = concrete_state or state
    operations = [
        {"op": "test", "path": "/rev", "value": 7},
        {"op": "replace", "path": "/fields/System.State", "value": concrete_state},
    ]
    if assignee is not None:
        operations.append(
            {"op": "replace", "path": "/fields/System.AssignedTo", "value": assignee}
        )
    return WorkItemTransitionPreview(
        provider="azure-devops",
        item_id=135821,
        requested_state=state,
        concrete_state=concrete_state,
        current_snapshot={"state": "New", "revision": 7},
        request={"method": "PATCH", "operations": operations},
    )


class WorkItemTests(unittest.TestCase):
    def setUp(self):
        self._plan_store = tempfile.TemporaryDirectory()
        self.addCleanup(self._plan_store.cleanup)
        self._plan_store_environment = mock.patch.dict(
            os.environ,
            {PLAN_STORE_ENVIRONMENT_VARIABLE: self._plan_store.name},
        )
        self._plan_store_environment.start()
        self.addCleanup(self._plan_store_environment.stop)

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
        args = argparse.Namespace(
            id=135821,
            branch=None,
            apply=None,
            json=False,
            provider="azure-devops",
            repo=None,
        )
        provider = mock.Mock()
        provider.get_start_work_plan.return_value = start_work_plan_fixture()
        transition_preview = transition_preview_fixture(
            state="In Progress",
            concrete_state="Active",
        )
        provider.prepare_work_item_transition.return_value = transition_preview
        provider.apply_prepared_work_item_transition.return_value = "Active"
        cmd_show = mock.Mock()

        with contextlib.redirect_stdout(io.StringIO()) as preview_stdout:
            work_item_commands.cmd_start(
                args,
                token="token",
                build_work_tracking_provider_func=lambda _token: provider,
                cmd_show_func=cmd_show,
            )
        approved_plan_id = plan_id_from_preview(preview_stdout.getvalue())
        provider.apply_prepared_work_item_transition.assert_not_called()

        args.apply = "sha256:" + "0" * 64
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(CliError, "does not match"):
                work_item_commands.cmd_start(
                    args,
                    token="token",
                    build_work_tracking_provider_func=lambda _token: provider,
                    cmd_show_func=cmd_show,
                )
        provider.apply_prepared_work_item_transition.assert_not_called()

        args.apply = approved_plan_id
        cmd_show.reset_mock()
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            work_item_commands.cmd_start(
                args,
                token="token",
                build_work_tracking_provider_func=lambda _token: provider,
                cmd_show_func=cmd_show,
            )

        provider.apply_prepared_work_item_transition.assert_called_once_with(transition_preview)
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

    def test_cmd_start_json_previews_canonical_plan_without_transition(self):
        args = argparse.Namespace(
            id=135821,
            branch=None,
            apply=None,
            json=True,
            provider="azure-devops",
            repo=None,
        )
        provider = mock.Mock()
        provider.get_start_work_plan.return_value = start_work_plan_fixture()
        transition_preview = transition_preview_fixture(state="In Progress", concrete_state="Active")
        provider.prepare_work_item_transition.return_value = transition_preview
        cmd_show = mock.Mock()

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            work_item_commands.cmd_start(
                args,
                token="token",
                build_work_tracking_provider_func=lambda _token: provider,
                cmd_show_func=cmd_show,
            )

        rendered = json.loads(stdout.getvalue())
        self.assertEqual(rendered["plan"]["action"], "work-item.start")
        self.assertEqual(rendered["plan"]["target"]["workItemId"], 135821)
        self.assertEqual(
            rendered["plan"]["payload"]["startWorkPlan"]["branchName"],
            "fix/135821-fix-global-filters",
        )
        self.assertEqual(rendered["plan"]["payload"]["transition"]["concreteState"], "Active")
        self.assertEqual(
            rendered["plan"]["payload"]["transition"]["request"]["operations"][0],
            {"op": "test", "path": "/rev", "value": 7},
        )
        self.assertEqual(rendered["applyArgument"], f"--apply {rendered['planId']}")
        self.assertRegex(rendered["planId"], r"^sha256:[0-9a-f]{64}$")
        provider.apply_prepared_work_item_transition.assert_not_called()
        cmd_show.assert_not_called()

    def test_cmd_start_json_reports_the_approved_plan_id_after_apply(self):
        args = argparse.Namespace(
            id=135821,
            branch=None,
            apply=None,
            json=True,
            provider="azure-devops",
            repo=None,
        )
        provider = mock.Mock()
        provider.get_start_work_plan.return_value = start_work_plan_fixture()
        transition_preview = transition_preview_fixture(
            state="In Progress",
            concrete_state="Active",
        )
        provider.prepare_work_item_transition.return_value = transition_preview
        provider.apply_prepared_work_item_transition.return_value = "Active"

        with contextlib.redirect_stdout(io.StringIO()) as preview_stdout:
            work_item_commands.cmd_start(
                args,
                token="token",
                build_work_tracking_provider_func=lambda _token: provider,
            )
        approved_plan_id = json.loads(preview_stdout.getvalue())["planId"]

        args.apply = approved_plan_id
        with contextlib.redirect_stdout(io.StringIO()) as apply_stdout:
            work_item_commands.cmd_start(
                args,
                token="token",
                build_work_tracking_provider_func=lambda _token: provider,
            )

        rendered = json.loads(apply_stdout.getvalue())
        self.assertEqual(rendered["appliedPlanId"], approved_plan_id)
        self.assertEqual(rendered["state"], "Active")
        provider.apply_prepared_work_item_transition.assert_called_once_with(transition_preview)

    def test_cmd_cleanup_artifacts_dry_run_renders_plan(self):
        args = argparse.Namespace(
            provider="gitlab",
            repo="group/project",
            issues=[1],
            merge_requests=[2],
            branches=["issue/1-smoke"],
            dry_run=True,
            apply=None,
        )
        provider = mock.Mock()
        provider.prepare_cleanup.return_value = {
            "projectId": "17",
            "branchSnapshots": [
                {"name": "issue/1-smoke", "commitSha": "a" * 40},
            ],
        }
        provider_factory = mock.Mock(return_value=provider)

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            work_item_commands.cmd_cleanup_artifacts(
                args,
                token="token",
                cleanup_provider_factory=provider_factory,
            )

        rendered = stdout.getvalue()
        plan_id = plan_id_from_preview(rendered)
        self.assertIn(f"--apply {plan_id}", rendered)
        self.assertIn("Action  : artifacts.cleanup", rendered)
        self.assertIn('"provider": "gitlab"', rendered)
        self.assertIn('"mergeRequests": [', rendered)
        self.assertIn('"projectId": "17"', rendered)
        self.assertIn('"commitSha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"', rendered)
        self.assertIn("Preview only", rendered)
        provider_factory.assert_called_once_with("token")
        provider.prepare_cleanup.assert_called_once_with(branches=["issue/1-smoke"])
        provider.cleanup_artifacts.assert_not_called()

    def test_cmd_cleanup_artifacts_requires_at_least_one_target(self):
        args = argparse.Namespace(
            provider="gitlab",
            repo="group/project",
            issues=[],
            merge_requests=[],
            branches=[],
            dry_run=False,
            apply=None,
        )

        with self.assertRaisesRegex(CliError, "at least one"):
            work_item_commands.cmd_cleanup_artifacts(
                args,
                token="token",
                cleanup_provider_factory=mock.Mock(),
            )

    def test_cmd_cleanup_artifacts_applies_cleanup(self):
        args = argparse.Namespace(
            provider="gitlab",
            repo="group/project",
            issues=[1],
            merge_requests=[2],
            branches=["issue/1-smoke"],
            dry_run=False,
            apply=None,
        )
        provider = mock.Mock()
        provider.prepare_cleanup.return_value = {
            "projectId": "17",
            "branchSnapshots": [
                {"name": "issue/1-smoke", "commitSha": "a" * 40},
            ],
        }
        provider.cleanup_artifacts.return_value = {
            "issues": [(1, "closed")],
            "mergeRequests": [(2, "closed")],
            "branches": ["issue/1-smoke"],
        }
        provider_factory = mock.Mock(return_value=provider)

        with contextlib.redirect_stdout(io.StringIO()) as preview_stdout:
            work_item_commands.cmd_cleanup_artifacts(
                args,
                token="token",
                cleanup_provider_factory=provider_factory,
            )
        approved_plan_id = plan_id_from_preview(preview_stdout.getvalue())
        provider_factory.assert_called_once_with("token")

        args.apply = "sha256:" + "0" * 64
        with self.assertRaisesRegex(CliError, "does not match"):
            work_item_commands.cmd_cleanup_artifacts(
                args,
                token="token",
                cleanup_provider_factory=provider_factory,
            )
        self.assertEqual(provider_factory.call_count, 2)
        provider.cleanup_artifacts.assert_not_called()

        args.apply = approved_plan_id
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            work_item_commands.cmd_cleanup_artifacts(
                args,
                token="token",
                cleanup_provider_factory=provider_factory,
            )

        self.assertEqual(provider_factory.call_count, 3)
        self.assertEqual(provider.prepare_cleanup.call_count, 3)
        provider.cleanup_artifacts.assert_called_once_with(
            project_id="17",
            issue_ids=[1],
            merge_request_ids=[2],
            branch_snapshots=[
                {"name": "issue/1-smoke", "commitSha": "a" * 40},
            ],
            on_result=mock.ANY,
        )
        self.assertIn("Cleanup applied in group/project", stdout.getvalue())

    def test_cmd_cleanup_artifacts_rejects_branch_tip_change_since_preview(self):
        args = argparse.Namespace(
            provider="gitlab",
            repo="group/project",
            issues=[],
            merge_requests=[],
            branches=["issue/1-smoke"],
            dry_run=False,
            apply=None,
        )
        provider = mock.Mock()
        provider.prepare_cleanup.side_effect = [
            {
                "projectId": "17",
                "branchSnapshots": [
                    {"name": "issue/1-smoke", "commitSha": "a" * 40},
                ],
            },
            {
                "projectId": "17",
                "branchSnapshots": [
                    {"name": "issue/1-smoke", "commitSha": "b" * 40},
                ],
            },
        ]
        provider_factory = mock.Mock(return_value=provider)

        with contextlib.redirect_stdout(io.StringIO()) as preview_stdout:
            work_item_commands.cmd_cleanup_artifacts(
                args,
                token="token",
                cleanup_provider_factory=provider_factory,
            )
        args.apply = plan_id_from_preview(preview_stdout.getvalue())

        with self.assertRaisesRegex(CliError, "does not match"):
            work_item_commands.cmd_cleanup_artifacts(
                args,
                token="token",
                cleanup_provider_factory=provider_factory,
            )

        provider.cleanup_artifacts.assert_not_called()

    def test_cmd_cleanup_artifacts_reports_partial_success_before_failure(self):
        args = argparse.Namespace(
            provider="gitlab",
            repo="group/project",
            issues=[1, 2],
            merge_requests=[],
            branches=[],
            dry_run=False,
            apply=None,
        )
        provider = mock.Mock()
        provider.prepare_cleanup.return_value = {
            "projectId": "17",
            "branchSnapshots": [],
        }

        def fail_after_first_issue(
            *, project_id, issue_ids, merge_request_ids, branch_snapshots, on_result
        ):
            self.assertEqual(project_id, "17")
            self.assertEqual(branch_snapshots, [])
            on_result("issue", issue_ids[0], "closed")
            raise CliError("ERROR: closing issue 2 failed")

        provider.cleanup_artifacts.side_effect = fail_after_first_issue
        provider_factory = mock.Mock(return_value=provider)

        with contextlib.redirect_stdout(io.StringIO()) as preview_stdout:
            work_item_commands.cmd_cleanup_artifacts(
                args,
                token="token",
                cleanup_provider_factory=provider_factory,
            )
        args.apply = plan_id_from_preview(preview_stdout.getvalue())

        with contextlib.redirect_stdout(io.StringIO()) as progress_stdout:
            with self.assertRaisesRegex(CliError, "issue 2 failed"):
                work_item_commands.cmd_cleanup_artifacts(
                    args,
                    token="token",
                    cleanup_provider_factory=provider_factory,
                )

        self.assertIn("Cleanup progress in group/project", progress_stdout.getvalue())
        self.assertIn("Issue  1 -> closed", progress_stdout.getvalue())
        self.assertNotIn("Cleanup applied", progress_stdout.getvalue())

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
        args = argparse.Namespace(
            id=135821,
            branch=None,
            apply=None,
            json=False,
            provider="azure-devops",
            repo=None,
        )
        provider = mock.Mock()
        provider.get_start_work_plan.return_value = start_work_plan_fixture()
        provider.prepare_work_item_transition.return_value = transition_preview_fixture(
            state="In Progress"
        )
        cmd_show = mock.Mock()

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            work_item_commands.cmd_start(
                args,
                token="token",
                build_work_tracking_provider_func=lambda _token: provider,
                cmd_show_func=cmd_show,
            )

        provider.apply_prepared_work_item_transition.assert_not_called()
        cmd_show.assert_called_once_with(args, "token")
        self.assertIn("fix/135821-fix-global-filters", stdout.getvalue())
        self.assertIn("Preview only", stdout.getvalue())
        self.assertRegex(stdout.getvalue(), r"Plan ID : sha256:[0-9a-f]{64}")

    def test_cmd_testing_requires_qa_email(self):
        args = argparse.Namespace(id=135821, qa=None, apply=None)

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
        args = argparse.Namespace(
            id=135821,
            apply=None,
            provider="azure-devops",
            repo=None,
            json=False,
        )
        provider = mock.Mock()
        transition_preview = transition_preview_fixture(state="In Review")
        provider.prepare_work_item_transition.return_value = transition_preview
        provider.apply_prepared_work_item_transition.return_value = "In Review"

        with contextlib.redirect_stdout(io.StringIO()) as preview_stdout:
            work_item_commands.cmd_review(
                args,
                token="token",
                build_work_tracking_provider_func=lambda _token: provider,
            )
        approved_plan_id = plan_id_from_preview(preview_stdout.getvalue())
        provider.apply_prepared_work_item_transition.assert_not_called()

        args.apply = "sha256:" + "0" * 64
        with self.assertRaisesRegex(CliError, "does not match"):
            work_item_commands.cmd_review(
                args,
                token="token",
                build_work_tracking_provider_func=lambda _token: provider,
            )
        provider.apply_prepared_work_item_transition.assert_not_called()

        args.apply = approved_plan_id
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            work_item_commands.cmd_review(
                args,
                token="token",
                build_work_tracking_provider_func=lambda _token: provider,
            )

        provider.apply_prepared_work_item_transition.assert_called_once_with(transition_preview)
        self.assertIn("moved to 'In Review'", stdout.getvalue())

    def test_cmd_review_defaults_to_plan_without_transition(self):
        args = argparse.Namespace(
            id=135821,
            apply=None,
            provider="azure-devops",
            repo=None,
            json=False,
        )
        provider = mock.Mock()
        provider.prepare_work_item_transition.return_value = transition_preview_fixture(
            state="In Review"
        )

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            work_item_commands.cmd_review(
                args,
                token="token",
                build_work_tracking_provider_func=lambda _token: provider,
            )

        provider.apply_prepared_work_item_transition.assert_not_called()
        self.assertIn("Action  : work-item.review", stdout.getvalue())
        self.assertIn('"workItemId": 135821', stdout.getvalue())
        self.assertIn('"state": "In Review"', stdout.getvalue())
        self.assertIn('"method": "PATCH"', stdout.getvalue())
        self.assertIn("Preview only", stdout.getvalue())

    def test_cmd_testing_uses_work_tracking_provider_transition(self):
        args = argparse.Namespace(
            id=135821,
            qa="qa@example.com",
            apply=None,
            provider="azure-devops",
            repo=None,
            json=False,
        )
        provider = mock.Mock()
        transition_preview = transition_preview_fixture(
            state="In Testing",
            assignee="qa@example.com",
        )
        provider.prepare_work_item_transition.return_value = transition_preview
        provider.apply_prepared_work_item_transition.return_value = "In Testing"

        with contextlib.redirect_stdout(io.StringIO()) as preview_stdout:
            work_item_commands.cmd_testing(
                args,
                token="token",
                build_work_tracking_provider_func=lambda _token: provider,
                qa_email="fallback@example.com",
            )
        approved_plan_id = plan_id_from_preview(preview_stdout.getvalue())
        provider.apply_prepared_work_item_transition.assert_not_called()

        args.apply = "sha256:" + "0" * 64
        with self.assertRaisesRegex(CliError, "does not match"):
            work_item_commands.cmd_testing(
                args,
                token="token",
                build_work_tracking_provider_func=lambda _token: provider,
                qa_email="fallback@example.com",
            )
        provider.apply_prepared_work_item_transition.assert_not_called()

        args.apply = approved_plan_id
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            work_item_commands.cmd_testing(
                args,
                token="token",
                build_work_tracking_provider_func=lambda _token: provider,
                qa_email="fallback@example.com",
            )

        provider.apply_prepared_work_item_transition.assert_called_once_with(transition_preview)
        self.assertIn("assigned to qa@example.com", stdout.getvalue())

    def test_cmd_handoff_to_qa_preserves_preview_and_exact_plan_approval(self):
        args = argparse.Namespace(
            id=135821,
            qa="qa@example.com",
            apply=None,
            provider="azure-devops",
            repo=None,
            json=False,
        )
        provider = mock.Mock()
        transition_preview = transition_preview_fixture(
            state="In Testing",
            assignee="qa@example.com",
        )
        provider.prepare_work_item_transition.return_value = transition_preview
        provider.apply_prepared_work_item_transition.return_value = "In Testing"

        def testing_command(delegated_args, delegated_token):
            return work_item_commands.cmd_testing(
                delegated_args,
                delegated_token,
                build_work_tracking_provider_func=lambda _token: provider,
                qa_email="fallback@example.com",
            )

        with contextlib.redirect_stdout(io.StringIO()) as preview_stdout:
            work_item_commands.cmd_handoff_to_qa(
                args,
                token="token",
                cmd_testing_func=testing_command,
            )
        approved_plan_id = plan_id_from_preview(preview_stdout.getvalue())
        provider.apply_prepared_work_item_transition.assert_not_called()

        args.apply = "sha256:" + "0" * 64
        with self.assertRaisesRegex(CliError, "does not match"):
            work_item_commands.cmd_handoff_to_qa(
                args,
                token="token",
                cmd_testing_func=testing_command,
            )
        provider.apply_prepared_work_item_transition.assert_not_called()

        args.apply = approved_plan_id
        with contextlib.redirect_stdout(io.StringIO()):
            work_item_commands.cmd_handoff_to_qa(
                args,
                token="token",
                cmd_testing_func=testing_command,
            )

        provider.apply_prepared_work_item_transition.assert_called_once_with(transition_preview)

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
        args = argparse.Namespace(
            id=135821,
            text="Please add repro details.",
            apply=None,
            provider="azure-devops",
            repo=None,
            json=False,
        )
        provider = mock.Mock()
        provider.add_work_item_comment.return_value = 5889999

        with contextlib.redirect_stdout(io.StringIO()) as preview_stdout:
            work_item_commands.cmd_comment(
                args,
                token="token",
                build_work_tracking_provider_func=lambda _token: provider,
            )
        approved_plan_id = plan_id_from_preview(preview_stdout.getvalue())
        provider.add_work_item_comment.assert_not_called()

        args.apply = "sha256:" + "0" * 64
        with self.assertRaisesRegex(CliError, "does not match"):
            work_item_commands.cmd_comment(
                args,
                token="token",
                build_work_tracking_provider_func=lambda _token: provider,
            )
        provider.add_work_item_comment.assert_not_called()

        args.apply = approved_plan_id
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
        args = argparse.Namespace(
            id=135821,
            text="Please add repro details.",
            apply=None,
            provider="azure-devops",
            repo=None,
            json=False,
        )
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

    def _draft_items_plan_file(self) -> str:
        handle = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8")
        handle.write(
            "# Backfill\n\n"
            "## P1 — Wire the toggle [Bug]\n\nRepro steps here.\n\n"
            "## Add settings page\n\nBuild it.\n"
        )
        handle.close()
        self.addCleanup(lambda: os.unlink(handle.name))
        return handle.name

    def _draft_items_provider(self) -> mock.Mock:
        from providers.interfaces import WorkItemTreePreview

        provider = mock.Mock()
        provider.prepare_work_item_tree.return_value = WorkItemTreePreview(
            provider="azure-devops",
            parent_id=812345,
            parent_snapshot={"id": 812345, "title": "Feature", "type": "Feature", "areaPath": "P\\A", "iterationPath": "P\\S1"},
            requests=[
                {"type": "Bug", "title": "Wire the toggle", "operations": [{"op": "add", "path": "/fields/System.Title", "value": "Wire the toggle"}]},
                {"type": "User Story", "title": "Add settings page", "operations": [{"op": "add", "path": "/fields/System.Title", "value": "Add settings page"}]},
            ],
        )
        return provider

    def test_cmd_draft_items_previews_tree_then_creates_on_apply(self):
        plan_file = self._draft_items_plan_file()
        provider = self._draft_items_provider()
        args = argparse.Namespace(
            plan=plan_file, parent=812345, tag=["v0.0.1"], assign=None,
            default_type="User Story", json=False, apply=None,
        )

        with contextlib.redirect_stdout(io.StringIO()) as preview_stdout:
            work_item_commands.cmd_draft_items(
                args, token="token", build_work_tracking_provider_func=lambda _t: provider,
            )
        preview = preview_stdout.getvalue()
        self.assertIn("Will create 2 child work item(s)", preview)
        self.assertIn("Bug: Wire the toggle", preview)
        provider.apply_prepared_work_item_tree.assert_not_called()
        approved = plan_id_from_preview(preview)

        created_ids = iter([4001, 4002])

        def fake_apply(preview_obj, *, on_result=None):
            for request in preview_obj.requests:
                on_result({"title": request["title"], "ok": True, "id": next(created_ids)})
            return []

        provider.apply_prepared_work_item_tree.side_effect = fake_apply
        args.apply = approved
        with contextlib.redirect_stdout(io.StringIO()) as apply_stdout:
            work_item_commands.cmd_draft_items(
                args, token="token", build_work_tracking_provider_func=lambda _t: provider,
            )

        self.assertIn("✓ [4001] Wire the toggle", apply_stdout.getvalue())
        self.assertIn("Created 2 work item(s) under 812345", apply_stdout.getvalue())

    def test_cmd_draft_items_raises_when_a_row_fails(self):
        plan_file = self._draft_items_plan_file()
        provider = self._draft_items_provider()
        args = argparse.Namespace(
            plan=plan_file, parent=812345, tag=[], assign=None,
            default_type="User Story", json=False, apply=None,
        )
        with contextlib.redirect_stdout(io.StringIO()) as preview_stdout:
            work_item_commands.cmd_draft_items(
                args, token="token", build_work_tracking_provider_func=lambda _t: provider,
            )
        args.apply = plan_id_from_preview(preview_stdout.getvalue())

        def fake_apply(preview_obj, *, on_result=None):
            on_result({"title": "Wire the toggle", "ok": True, "id": 1})
            on_result({"title": "Add settings page", "ok": False, "error": "HTTP 400"})
            return []

        provider.apply_prepared_work_item_tree.side_effect = fake_apply
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(CliError, "One or more work items were not created"):
                work_item_commands.cmd_draft_items(
                    args, token="token", build_work_tracking_provider_func=lambda _t: provider,
                )

    def test_cmd_tree_renders_ancestors_and_children_with_metadata(self):
        from providers.interfaces import WorkItemTreeSnapshot

        provider = mock.Mock()
        provider.get_work_item_tree.return_value = WorkItemTreeSnapshot(
            root={
                "id": 20, "workItemType": "Feature", "state": "Active", "title": "Feature",
                "assignedTo": "", "tags": [], "iterationPath": "P\\S1",
                "children": [
                    {"id": 30, "workItemType": "User Story", "state": "New", "title": "Story",
                     "assignedTo": "Alice", "tags": ["v0.0.1"], "iterationPath": "P\\S1", "children": []},
                ],
            },
            ancestors=[{"id": 10, "workItemType": "Epic", "state": "Active", "title": "Epic",
                        "assignedTo": "", "tags": [], "iterationPath": "", "children": []}],
            depth=1,
        )
        args = argparse.Namespace(id=20, depth=2, json=False)

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            work_item_commands.cmd_tree(args, token="token", build_work_tracking_provider_func=lambda _t: provider)

        out = stdout.getvalue()
        provider.get_work_item_tree.assert_called_once_with(item_id=20, depth=2)
        self.assertIn("[10] Epic - Active - Epic  (ancestor)", out)
        self.assertIn("[30] User Story - New - Story  (@Alice; tags: v0.0.1; P\\S1)", out)

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

    def test_cmd_pick_next_delegates_preview_and_exact_start_plan_approval(self):
        args = argparse.Namespace(start=True, branch=None, apply=None)
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
        provider.get_start_work_plan.return_value = start_work_plan_fixture()
        transition_preview = transition_preview_fixture(state="In Progress")
        provider.prepare_work_item_transition.return_value = transition_preview
        provider.apply_prepared_work_item_transition.return_value = "In Progress"
        cmd_show = mock.Mock()

        def start_command(delegated_args, delegated_token):
            self.assertEqual(delegated_args.id, 135821)
            self.assertFalse(delegated_args.json)
            return work_item_commands.cmd_start(
                delegated_args,
                delegated_token,
                build_work_tracking_provider_func=lambda _token: provider,
            )

        with contextlib.redirect_stdout(io.StringIO()) as preview_stdout:
            work_item_commands.cmd_pick_next(
                args,
                token="token",
                build_work_tracking_provider_func=lambda _token: provider,
                cmd_show_func=cmd_show,
                cmd_start_func=start_command,
            )
        approved_plan_id = plan_id_from_preview(preview_stdout.getvalue())
        provider.apply_prepared_work_item_transition.assert_not_called()

        args.apply = "sha256:" + "0" * 64
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(CliError, "does not match"):
                work_item_commands.cmd_pick_next(
                    args,
                    token="token",
                    build_work_tracking_provider_func=lambda _token: provider,
                    cmd_show_func=cmd_show,
                    cmd_start_func=start_command,
                )
        provider.apply_prepared_work_item_transition.assert_not_called()

        args.apply = approved_plan_id
        with contextlib.redirect_stdout(io.StringIO()):
            work_item_commands.cmd_pick_next(
                args,
                token="token",
                build_work_tracking_provider_func=lambda _token: provider,
                cmd_show_func=cmd_show,
                cmd_start_func=start_command,
            )

        provider.apply_prepared_work_item_transition.assert_called_once_with(transition_preview)

    def test_cmd_pick_next_rejects_apply_without_start(self):
        args = argparse.Namespace(
            start=False,
            branch=None,
            apply="sha256:" + "0" * 64,
        )
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

        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(CliError, "--apply requires --start"):
                work_item_commands.cmd_pick_next(
                    args,
                    token="token",
                    build_work_tracking_provider_func=lambda _token: provider,
                    cmd_show_func=mock.Mock(),
                )

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
