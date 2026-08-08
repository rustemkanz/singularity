import argparse
import contextlib
import io
import json
import os
import re
import tempfile
import unittest
from unittest import mock

from cli_commands import review as review_commands
from errors import CliError
from mutation_plans import PLAN_STORE_ENVIRONMENT_VARIABLE
from providers.azure_devops import pull_requests as provider_pull_requests
from providers.azure_devops import review_provider as provider_review_provider
from providers.interfaces import RepositoryRef, ReviewAnalysis, ReviewContext, ReviewMutationPreview, ReviewMutationResult
import workflow_models


PLAN_ID_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")


def plan_id_from_preview(rendered: str) -> str:
    match = PLAN_ID_PATTERN.search(rendered)
    if match is None:
        raise AssertionError(f"Preview did not contain a plan ID: {rendered!r}")
    return match.group(0)


def change_request_fixture(*, provider: str = "azure-devops") -> workflow_models.ChangeRequest:
    return workflow_models.ChangeRequest(
        id=42,
        title="[135821] Fix global filters",
        status="active",
        source_branch="fix/135821",
        target_branch="main",
        author="Alice",
        repo_name="sample-repo",
        repo_id="repo-1",
        api_url="https://example.test/pr/42",
        browser_url="https://example.test/browser/42",
        provider=provider,
    )


def review_context_fixture(*, provider: str = "azure-devops") -> ReviewContext:
    return ReviewContext(
        organization="example-org" if provider == "azure-devops" else "https://gitlab.example.com",
        project="Example Project" if provider == "azure-devops" else "group/project",
        repository=RepositoryRef(id="repo-1", name="example-repo"),
        change_request=change_request_fixture(provider=provider),
    )


def prepared_change_request_fixture() -> dict:
    return {
        "request": {
            "method": "POST",
            "body": {
                "title": "[135821] Fix global filters",
                "sourceRefName": "refs/heads/fix/135821",
            },
        },
        "sourceRef": {
            "name": "refs/heads/fix/135821",
            "commitId": "a" * 40,
        },
    }


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self._plan_store = tempfile.TemporaryDirectory()
        self.addCleanup(self._plan_store.cleanup)
        self._plan_store_environment = mock.patch.dict(
            os.environ,
            {PLAN_STORE_ENVIRONMENT_VARIABLE: self._plan_store.name},
        )
        self._plan_store_environment.start()
        self.addCleanup(self._plan_store_environment.stop)

    def exercise_plan_contract(self, args, invoke, mutation_mock):
        with contextlib.redirect_stdout(io.StringIO()) as preview_stdout:
            invoke()
        preview = preview_stdout.getvalue()
        approved_plan_id = plan_id_from_preview(preview)
        mutation_mock.assert_not_called()

        if hasattr(args, "dry_run"):
            args.dry_run = False
        args.apply = "sha256:" + "0" * 64
        with self.assertRaisesRegex(CliError, "does not match"):
            invoke()
        mutation_mock.assert_not_called()

        args.apply = approved_plan_id
        with contextlib.redirect_stdout(io.StringIO()) as applied_stdout:
            invoke()
        mutation_mock.assert_called_once()
        return preview, applied_stdout.getvalue(), approved_plan_id

    def test_resolve_repository_uses_default_repo_when_git_repo_missing(self):
        repos = [
            {"id": "repo-1", "name": "sample-repo"},
            {"id": "repo-2", "name": "other"},
        ]

        resolved = review_commands.resolve_repository(
            "token",
            None,
            list_repositories_func=lambda *_args, **_kwargs: repos,
            infer_git_repository_ref_func=lambda: None,
            default_repo="sample-repo",
        )

        self.assertEqual(resolved["name"], "sample-repo")

    def test_review_provider_resolve_repository_falls_back_to_default_repo(self):
        repos = [
            {"id": "repo-1", "name": "sample-repo"},
            {"id": "repo-2", "name": "other"},
        ]
        provider = provider_review_provider.AzureDevOpsReviewProvider("token")

        with mock.patch.object(provider_review_provider, "list_repositories", return_value=repos):
            with mock.patch.object(provider_review_provider, "infer_git_repository_ref", return_value=None):
                with mock.patch.object(provider_review_provider, "DEFAULT_REPO", "sample-repo"):
                    resolved = provider._resolve_repository(None)

        self.assertEqual(resolved["name"], "sample-repo")

    def test_azure_prepare_change_request_binds_and_rechecks_remote_source_tip(self):
        provider = provider_review_provider.AzureDevOpsReviewProvider("token")
        repository_data = {"id": "repo-1", "name": "sample-repo"}
        source_ref = {
            "value": [
                {
                    "name": "refs/heads/fix/135821",
                    "objectId": "a" * 40,
                }
            ]
        }

        with mock.patch.multiple(
            provider_review_provider,
            ORG="example-org",
            PROJECT="Example Project",
        ):
            with mock.patch.object(provider, "_resolve_repository", return_value=repository_data):
                with mock.patch.object(provider_review_provider, "api", return_value=source_ref):
                    repository, prepared = provider.prepare_change_request(
                        work_item_id=135821,
                        repo_ref="sample-repo",
                        source_branch="fix/135821",
                        target_branch="main",
                        title="[135821] Fix global filters",
                        description="Closes #135821",
                        work_item_title=None,
                    )

        self.assertEqual(
            prepared["sourceRef"],
            {"name": "refs/heads/fix/135821", "commitId": "a" * 40},
        )
        self.assertEqual(prepared["request"]["method"], "POST")
        self.assertEqual(
            prepared["request"]["body"]["sourceRefName"],
            "refs/heads/fix/135821",
        )

        with mock.patch.multiple(
            provider_review_provider,
            ORG="example-org",
            PROJECT="Example Project",
        ):
            with mock.patch.object(
                provider_review_provider,
                "api",
                side_effect=[
                    source_ref,
                    {
                        "pullRequestId": 42,
                        "lastMergeSourceCommit": {"commitId": "a" * 40},
                    },
                ],
            ) as api_mock:
                with mock.patch.object(
                    provider_review_provider,
                    "build_change_request",
                    return_value=change_request_fixture(),
                ):
                    change_request = provider.create_prepared_change_request(repository, prepared)

        self.assertEqual(change_request.id, 42)
        self.assertEqual(api_mock.call_count, 2)
        self.assertEqual(api_mock.call_args_list[1].args[1], "POST")
        self.assertEqual(api_mock.call_args_list[1].args[3], prepared["request"]["body"])

    def test_azure_create_change_request_rejects_moved_remote_source_tip(self):
        provider = provider_review_provider.AzureDevOpsReviewProvider("token")
        repository = RepositoryRef(id="repo-1", name="sample-repo", project="Example Project")
        prepared = prepared_change_request_fixture()
        moved_source_ref = {
            "value": [
                {
                    "name": "refs/heads/fix/135821",
                    "objectId": "b" * 40,
                }
            ]
        }

        with mock.patch.multiple(
            provider_review_provider,
            ORG="example-org",
            PROJECT="Example Project",
        ):
            with mock.patch.object(provider_review_provider, "api", return_value=moved_source_ref) as api_mock:
                with self.assertRaisesRegex(CliError, "moved after preview"):
                    provider.create_prepared_change_request(repository, prepared)

        api_mock.assert_called_once()

    def test_azure_create_change_request_reports_created_pr_with_unapproved_source_tip(self):
        provider = provider_review_provider.AzureDevOpsReviewProvider("token")
        repository = RepositoryRef(id="repo-1", name="sample-repo", project="Example Project")
        prepared = prepared_change_request_fixture()
        source_ref = {
            "value": [{
                "name": "refs/heads/fix/135821",
                "objectId": "a" * 40,
            }]
        }
        created = {
            "pullRequestId": 42,
            "lastMergeSourceCommit": {"commitId": "b" * 40},
        }

        with mock.patch.multiple(
            provider_review_provider,
            ORG="example-org",
            PROJECT="Example Project",
        ):
            with mock.patch.object(
                provider_review_provider,
                "api",
                side_effect=[source_ref, created],
            ):
                with self.assertRaisesRegex(
                    CliError,
                    "created pull request 42.*Do not retry",
                ):
                    provider.create_prepared_change_request(repository, prepared)

    def test_review_provider_get_change_request_file_content_uses_pull_request_helper(self):
        provider = provider_review_provider.AzureDevOpsReviewProvider("token")
        context = ReviewContext(
            organization="example-org",
            project="Example Project",
            repository=RepositoryRef(id="repo-1", name="example-repo"),
            change_request=workflow_models.ChangeRequest(
                id=42,
                title="Improve mapping",
                status="active",
                source_branch="fix/42",
                target_branch="main",
                author="Alice",
                repo_name="example-repo",
                repo_id="repo-1",
                api_url="https://example.test/pr/42",
                browser_url="https://example.test/browser/42",
            ),
        )

        with mock.patch.object(provider, "_fetch_pull_request", return_value={"pullRequestId": 42}):
            with mock.patch.object(
                provider_review_provider,
                "fetch_pr_file_content",
                return_value="example content\n",
            ) as fetch_mock:
                content = provider.get_change_request_file_content(
                    context,
                    file_path="/src/example.ts",
                    version="source",
                )

        self.assertEqual(content, "example content\n")
        fetch_mock.assert_called_once_with(
            "token",
            {"id": "repo-1", "name": "example-repo"},
            {"pullRequestId": 42},
            "/src/example.ts",
            version="source",
            project_name="Example Project",
            org_name="example-org",
        )

    def test_build_inline_thread_payload_includes_iteration_context(self):
        payload = provider_pull_requests.build_inline_thread_payload(
            "Please adjust this branch logic.",
            "databricks/5-curated/transform_DistributorValidation.py",
            12,
            end_line=14,
            start_offset=3,
            end_offset=9,
            iteration_id=7,
            change_tracking_id=42,
        )

        self.assertEqual(payload["status"], "active")
        self.assertEqual(
            payload["threadContext"],
            {
                "filePath": "/databricks/5-curated/transform_DistributorValidation.py",
                "rightFileStart": {"line": 12, "offset": 3},
                "rightFileEnd": {"line": 14, "offset": 9},
            },
        )
        self.assertEqual(
            payload["pullRequestThreadContext"],
            {
                "changeTrackingId": 42,
                "iterationContext": {
                    "firstComparingIteration": 7,
                    "secondComparingIteration": 7,
                },
            },
        )

    def test_build_unified_diff_renders_headers_and_changed_lines(self):
        diff_text = provider_pull_requests.build_unified_diff(
            "src/example.py",
            "alpha\nbeta\n",
            "alpha\ngamma\n",
        )

        self.assertIn("--- a/src/example.py", diff_text)
        self.assertIn("+++ b/src/example.py", diff_text)
        self.assertIn("-beta", diff_text)
        self.assertIn("+gamma", diff_text)

    def test_build_review_draft_starts_with_empty_draft_comments(self):
        repo = {"id": "repo-1", "name": "example-ado-repo"}
        pr = {
            "pullRequestId": 63293,
            "title": "feat: 288949: distributor approach validation",
            "status": "active",
            "sourceRefName": "refs/heads/feat/288949-em-distributor-approach-validation",
            "targetRefName": "refs/heads/main",
            "url": "https://example.test/pullrequests/63293",
        }
        analysis = {
            "browserUrl": "https://example.test/browser/63293",
            "changeSummary": {"iteration": 12, "count": 13, "byType": {"add": 13}},
            "files": [{"path": "/databricks/5-curated/transform_DistributorValidation.py", "changeType": "add"}],
            "existingComments": [{"threadId": 1, "commentId": 2, "content": "Looks good"}],
        }

        draft = provider_pull_requests.build_review_draft("example-org", "Example Project", repo, pr, analysis)

        self.assertEqual(draft["formatVersion"], provider_pull_requests.REVIEW_DRAFT_FORMAT_VERSION)
        self.assertEqual(draft["repo"], {"name": "example-ado-repo", "id": "repo-1"})
        self.assertEqual(draft["pullRequest"]["pullRequestId"], 63293)
        self.assertEqual(draft["analysis"]["changeSummary"]["count"], 13)
        self.assertEqual(draft["draftComments"], [])

    def test_collect_pr_reviewers_serializes_vote_labels(self):
        reviewers = provider_pull_requests.collect_pr_reviewers(
            {
                "reviewers": [
                    {"displayName": "Alex", "vote": 10, "isRequired": True},
                    {"displayName": "Sam", "vote": 0},
                ]
            }
        )

        self.assertEqual(
            [reviewer.to_legacy_dict() for reviewer in reviewers],
            [
                {"name": "Alex", "vote": 10, "voteLabel": "approved", "isRequired": True},
                {"name": "Sam", "vote": 0, "voteLabel": "noVote", "isRequired": False},
            ],
        )

    def test_cmd_repos_json_uses_review_provider(self):
        args = argparse.Namespace(json=True)
        provider = mock.Mock()
        provider.list_repositories.return_value = [
            RepositoryRef(id="repo-1", name="sample-repo", project="Example Project")
        ]

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            review_commands.cmd_repos(
                args,
                token="token",
                build_review_provider_func=lambda _token: provider,
            )

        rendered = json.loads(stdout.getvalue())
        self.assertEqual(rendered["repositories"][0]["name"], "sample-repo")

    def test_cmd_create_pr_dry_run_uses_review_provider(self):
        args = argparse.Namespace(
            id=135821,
            repo="sample-repo",
            source="fix/135821",
            target="main",
            title=None,
            description=None,
            work_item_title=None,
            dry_run=True,
            apply=None,
            json=True,
            provider="azure-devops",
        )
        provider = mock.Mock()
        provider.prepare_change_request.return_value = (
            RepositoryRef(id="repo-1", name="sample-repo", project="Example Project"),
            prepared_change_request_fixture(),
        )

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            review_commands.cmd_create_pr(
                args,
                token="token",
                build_review_provider_func=lambda _token: provider,
            )

        rendered = json.loads(stdout.getvalue())
        self.assertEqual(rendered["plan"]["action"], "change-request.create")
        self.assertEqual(rendered["plan"]["target"]["repository"]["name"], "sample-repo")
        self.assertEqual(rendered["plan"]["payload"]["sourceRef"]["commitId"], "a" * 40)
        self.assertEqual(rendered["plan"]["payload"]["request"]["method"], "POST")
        self.assertEqual(rendered["applyArgument"], f"--apply {rendered['planId']}")
        self.assertRegex(rendered["planId"], r"^sha256:[0-9a-f]{64}$")
        provider.prepare_change_request.assert_called_once()
        provider.create_prepared_change_request.assert_not_called()

    def test_cmd_create_pr_defaults_to_plan_without_creation(self):
        args = argparse.Namespace(
            id=135821,
            repo="sample-repo",
            source="fix/135821",
            target="main",
            title=None,
            description=None,
            work_item_title=None,
            dry_run=False,
            apply=None,
            json=False,
            provider="azure-devops",
        )
        provider = mock.Mock()
        provider.prepare_change_request.return_value = (
            RepositoryRef(id="repo-1", name="sample-repo", project="Example Project"),
            prepared_change_request_fixture(),
        )

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            review_commands.cmd_create_pr(
                args,
                token="token",
                build_review_provider_func=lambda _token: provider,
        )

        provider.create_prepared_change_request.assert_not_called()
        self.assertIn("Action  : change-request.create", stdout.getvalue())
        self.assertRegex(stdout.getvalue(), r"Plan ID : sha256:[0-9a-f]{64}")
        self.assertIn("Preview only", stdout.getvalue())

    def test_cmd_create_pr_apply_creates_change_request(self):
        args = argparse.Namespace(
            id=135821,
            repo="sample-repo",
            source="fix/135821",
            target="main",
            title=None,
            description=None,
            work_item_title=None,
            dry_run=False,
            apply=None,
            json=False,
            provider="azure-devops",
        )
        provider = mock.Mock()
        provider.prepare_change_request.return_value = (
            RepositoryRef(id="repo-1", name="sample-repo", project="Example Project"),
            prepared_change_request_fixture(),
        )
        provider.create_prepared_change_request.return_value = change_request_fixture()

        with contextlib.redirect_stdout(io.StringIO()) as preview_stdout:
            review_commands.cmd_create_pr(
                args,
                token="token",
                build_review_provider_func=lambda _token: provider,
            )
        approved_plan_id = plan_id_from_preview(preview_stdout.getvalue())
        provider.create_prepared_change_request.assert_not_called()

        args.apply = "sha256:" + "0" * 64
        with self.assertRaisesRegex(CliError, "does not match"):
            review_commands.cmd_create_pr(
                args,
                token="token",
                build_review_provider_func=lambda _token: provider,
            )
        provider.create_prepared_change_request.assert_not_called()

        args.apply = approved_plan_id
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            review_commands.cmd_create_pr(
                args,
                token="token",
                build_review_provider_func=lambda _token: provider,
            )

        provider.create_prepared_change_request.assert_called_once()
        submitted_repository, submitted_payload = provider.create_prepared_change_request.call_args.args
        self.assertEqual(submitted_repository, provider.prepare_change_request.return_value[0])
        self.assertEqual(submitted_payload, provider.prepare_change_request.return_value[1])
        self.assertIsNot(submitted_payload, provider.prepare_change_request.return_value[1])
        self.assertIn("Pull request created", stdout.getvalue())

    def test_cmd_create_pr_preview_and_apply_require_explicit_repo_and_source(self):
        for repo, source, expected_flag in (
            (None, "fix/135821", "--repo"),
            ("sample-repo", None, "--source"),
        ):
            with self.subTest(expected_flag=expected_flag):
                args = argparse.Namespace(
                    id=135821,
                    repo=repo,
                    source=source,
                    target="main",
                    title=None,
                    description=None,
                    work_item_title=None,
                    dry_run=False,
                    apply=None,
                    json=False,
                    provider="azure-devops",
                )
                provider_factory = mock.Mock()

                with self.assertRaisesRegex(CliError, expected_flag):
                    review_commands.cmd_create_pr(
                        args,
                        token="token",
                        build_review_provider_func=provider_factory,
                    )

                provider_factory.assert_not_called()

    def test_cmd_pr_comments_json_serializes_review_threads(self):
        args = argparse.Namespace(json=True, unresolved_only=False, repo=None, pr=None, source=None, url=None)
        change_request = workflow_models.ChangeRequest(
            id=42,
            title="Improve mapping",
            status="active",
            source_branch="fix/42",
            target_branch="main",
            author="Alice",
            repo_name="example-repo",
            repo_id="repo-1",
            api_url="https://example.test/pr/42",
            browser_url="https://example.test/browser/42",
        )
        context = ReviewContext(
            organization="example-org",
            project="Example Project",
            repository=RepositoryRef(id="repo-1", name="example-repo"),
            change_request=change_request,
        )
        review_threads = [
            workflow_models.ReviewThread(
                thread_id=7,
                status="active",
                is_deleted=False,
                file_path="/src/example.ts",
                line=14,
                location="/src/example.ts:14",
                comments=[
                    workflow_models.ReviewComment(
                        comment_id=3,
                        parent_comment_id=0,
                        author="Alex",
                        content="Please clarify this.",
                        published_date="2026-07-23T10:00:00Z",
                        last_updated_date=None,
                    )
                ],
            )
        ]

        provider = mock.Mock()
        provider.resolve_review_context.return_value = context
        provider.list_review_threads.return_value = review_threads

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            review_commands.cmd_pr_comments(
                args,
                token="token",
                build_review_provider_func=lambda _token: provider,
            )

        rendered = json.loads(stdout.getvalue())
        self.assertEqual(rendered["threads"][0]["threadId"], 7)
        self.assertEqual(rendered["threads"][0]["comments"][0]["author"], "Alex")

    def test_cmd_pr_comment_requires_exact_previewed_plan(self):
        args = argparse.Namespace(
            json=True,
            dry_run=True,
            apply=None,
            repo=None,
            pr=None,
            source=None,
            url=None,
            text="Please check this.",
        )
        context = review_context_fixture()
        provider = mock.Mock()
        provider.resolve_review_context.return_value = context
        provider.prepare_review_comment.return_value = ReviewMutationPreview(
            payload={"comments": [{"content": "Please check this."}], "status": "active"}
        )
        provider.create_prepared_review_comment.return_value = ReviewMutationResult(thread_id=7)

        def invoke():
            review_commands.cmd_pr_comment(
                args,
                token="token",
                build_review_provider_func=lambda _token: provider,
            )

        preview, applied, approved_plan_id = self.exercise_plan_contract(
            args,
            invoke,
            provider.create_prepared_review_comment,
        )
        rendered = json.loads(preview)
        self.assertEqual(rendered["plan"]["action"], "review.comment.create")
        self.assertEqual(rendered["plan"]["target"]["provider"], "azure-devops")
        self.assertEqual(rendered["plan"]["payload"]["status"], "active")
        self.assertEqual(rendered["applyArgument"], f"--apply {approved_plan_id}")
        self.assertIn("Comment thread 7 created", applied)

    def test_cmd_pr_inline_comment_requires_exact_previewed_plan(self):
        args = argparse.Namespace(
            json=True,
            dry_run=True,
            apply=None,
            repo=None,
            pr=None,
            source=None,
            url=None,
            text="Inline note",
            path="src/example.ts",
            line=14,
            end_line=None,
            start_offset=1,
            end_offset=None,
        )
        context = review_context_fixture()
        provider = mock.Mock()
        provider.resolve_review_context.return_value = context
        provider.prepare_inline_review_comment.return_value = ReviewMutationPreview(
            payload={"threadContext": {"filePath": "/src/example.ts"}, "status": "active"}
        )
        provider.create_prepared_inline_review_comment.return_value = ReviewMutationResult(thread_id=8)

        def invoke():
            review_commands.cmd_pr_inline_comment(
                args,
                token="token",
                build_review_provider_func=lambda _token: provider,
            )

        preview, applied, _approved_plan_id = self.exercise_plan_contract(
            args,
            invoke,
            provider.create_prepared_inline_review_comment,
        )
        rendered = json.loads(preview)
        self.assertEqual(rendered["plan"]["action"], "review.inline-comment.create")
        self.assertEqual(rendered["plan"]["target"]["filePath"], "/src/example.ts")
        self.assertEqual(
            rendered["plan"]["payload"]["threadContext"]["filePath"],
            "/src/example.ts",
        )
        self.assertIn("Inline thread 8 created", applied)

    def test_cmd_pr_reply_requires_exact_previewed_plan(self):
        args = argparse.Namespace(
            json=False,
            dry_run=False,
            apply=None,
            repo=None,
            pr=None,
            source=None,
            url=None,
            thread=7,
            parent_comment=None,
            text="Thanks, updating.",
        )
        context = review_context_fixture()
        provider = mock.Mock()
        provider.resolve_review_context.return_value = context
        provider.prepare_review_reply.return_value = ReviewMutationPreview(
            payload={"parentCommentId": 3, "content": "Thanks, updating.", "commentType": "text"},
            thread_id=7,
        )
        provider.create_prepared_review_reply.return_value = ReviewMutationResult(thread_id=7, comment_id=11)

        def invoke():
            review_commands.cmd_pr_reply(
                args,
                token="token",
                build_review_provider_func=lambda _token: provider,
            )

        preview, applied, approved_plan_id = self.exercise_plan_contract(
            args,
            invoke,
            provider.create_prepared_review_reply,
        )
        self.assertIn("Action  : review.reply.create", preview)
        self.assertIn(f"--apply {approved_plan_id}", preview)
        self.assertIn("Reply added to thread 7 on PR 42 (comment id: 11)", applied)

    def test_cmd_pr_edit_comment_requires_exact_previewed_plan(self):
        args = argparse.Namespace(
            json=True,
            dry_run=True,
            apply=None,
            repo=None,
            pr=None,
            source=None,
            url=None,
            thread=7,
            comment=11,
            text="Updated wording.",
        )
        context = review_context_fixture()
        provider = mock.Mock()
        provider.resolve_review_context.return_value = context
        provider.prepare_review_comment_edit.return_value = ReviewMutationPreview(
            payload={"content": "Updated wording.", "commentType": "text"},
            thread_id=7,
            comment_id=11,
            current_content="Old wording.",
        )
        provider.edit_prepared_review_comment.return_value = ReviewMutationResult(thread_id=7, comment_id=11)

        def invoke():
            review_commands.cmd_pr_edit_comment(
                args,
                token="token",
                build_review_provider_func=lambda _token: provider,
            )

        preview, applied, _approved_plan_id = self.exercise_plan_contract(
            args,
            invoke,
            provider.edit_prepared_review_comment,
        )
        rendered = json.loads(preview)
        self.assertEqual(rendered["plan"]["action"], "review.comment.edit")
        self.assertEqual(rendered["plan"]["target"]["threadId"], 7)
        self.assertEqual(rendered["plan"]["target"]["commentId"], 11)
        self.assertEqual(rendered["plan"]["payload"]["currentContent"], "Old wording.")
        self.assertIn("Comment 11 updated", applied)

    def test_cmd_pr_resolve_requires_exact_previewed_plan(self):
        args = argparse.Namespace(
            json=False,
            dry_run=False,
            apply=None,
            repo=None,
            pr=None,
            source=None,
            url=None,
            thread=7,
        )
        context = review_context_fixture()
        provider = mock.Mock()
        provider.resolve_review_context.return_value = context
        provider.prepare_review_thread_resolution.return_value = ReviewMutationPreview(
            payload={"status": "fixed"},
            thread_id=7,
            current_status="active",
        )
        provider.resolve_prepared_review_thread.return_value = ReviewMutationResult(thread_id=7, status="fixed")

        def invoke():
            review_commands.cmd_pr_resolve(
                args,
                token="token",
                build_review_provider_func=lambda _token: provider,
            )

        preview, applied, approved_plan_id = self.exercise_plan_contract(
            args,
            invoke,
            provider.resolve_prepared_review_thread,
        )
        self.assertIn("Action  : review.thread.resolve", preview)
        self.assertIn(f"--apply {approved_plan_id}", preview)
        self.assertIn("Thread 7 marked resolved on PR 42 (status: fixed)", applied)

    def test_cmd_pr_analyze_json_includes_reviewers(self):
        args = argparse.Namespace(json=True, repo=None, pr=None, source=None, url=None)
        change_request = workflow_models.ChangeRequest(
            id=42,
            title="Improve mapping",
            status="completed",
            source_branch="fix/42",
            target_branch="main",
            author="Alice",
            repo_name="example-repo",
            repo_id="repo-1",
            api_url="https://example.test/pr/42",
            browser_url="https://example.test/browser/42",
        )
        analysis = ReviewAnalysis(
            context=ReviewContext(
                organization="example-org",
                project="Example Project",
                repository=RepositoryRef(id="repo-1", name="example-repo"),
                change_request=change_request,
            ),
            reviewers=[workflow_models.ChangeRequestReviewer("Alex", 10, "approved", False)],
            change_summary=workflow_models.ReviewChangeSummary(iteration=1, count=2, by_type={"edit": 2}),
            files=[workflow_models.ReviewFileChange(path="/src/example.ts", change_type="edit")],
            existing_comments=[],
        )

        provider = mock.Mock()
        provider.resolve_review_context.return_value = analysis.context
        provider.analyze_change_request.return_value = analysis

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            review_commands.cmd_pr_analyze(
                args,
                token="token",
                build_review_provider_func=lambda _token: provider,
            )

        rendered = json.loads(stdout.getvalue())
        self.assertEqual(rendered["pullRequest"]["reviewers"], [
            {"name": "Alex", "vote": 10, "voteLabel": "approved", "isRequired": False}
        ])

    def test_cmd_pr_file_raises_cli_error_for_missing_file(self):
        args = argparse.Namespace(
            repo=None,
            pr=None,
            source=None,
            url=None,
            path="src/missing.ts",
            version="source",
            start_line=None,
            end_line=None,
            number_lines=False,
        )
        change_request = workflow_models.ChangeRequest(
            id=42,
            title="Improve mapping",
            status="active",
            source_branch="fix/42",
            target_branch="main",
            author="Alice",
            repo_name="example-repo",
            repo_id="repo-1",
            api_url="https://example.test/pr/42",
            browser_url="https://example.test/browser/42",
        )
        context = ReviewContext(
            organization="example-org",
            project="Example Project",
            repository=RepositoryRef(id="repo-1", name="example-repo"),
            change_request=change_request,
        )
        provider = mock.Mock()
        provider.resolve_review_context.return_value = context
        provider.get_change_request_file_content.return_value = None

        with self.assertRaises(CliError):
            review_commands.cmd_pr_file(
                args,
                token="token",
                build_review_provider_func=lambda _token: provider,
            )

    def test_cmd_pr_review_draft_records_provider_metadata(self):
        context = review_context_fixture(provider="gitlab")
        analysis = ReviewAnalysis(
            context=context,
            reviewers=[],
            change_summary=workflow_models.ReviewChangeSummary(iteration=1, count=0, by_type={}),
            files=[],
            existing_comments=[],
        )
        provider = mock.Mock()
        provider.resolve_review_context.return_value = context
        provider.analyze_change_request.return_value = analysis
        args = argparse.Namespace(
            repo=None,
            pr=None,
            source=None,
            url="https://gitlab.example.com/group/project/-/merge_requests/42",
            output=None,
        )

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            review_commands.cmd_pr_review_draft(
                args,
                token="token",
                build_review_provider_func=lambda _token: provider,
            )

        rendered = json.loads(stdout.getvalue())
        self.assertEqual(rendered["provider"], "gitlab")
        self.assertEqual(rendered["organization"], "https://gitlab.example.com")
        self.assertEqual(rendered["project"], "group/project")

    def test_cmd_pr_review_draft_records_provider_from_real_azure_deserializer(self):
        repository = {"id": "repo-1", "name": "example-repo"}
        pull_request = {
            "pullRequestId": 42,
            "title": "Improve mapping",
            "status": "active",
            "sourceRefName": "refs/heads/fix/42",
            "targetRefName": "refs/heads/main",
            "createdBy": {"displayName": "Alice"},
            "url": "https://dev.azure.com/example-org/Example%20Project/_apis/git/pullrequests/42",
        }
        change_request = provider_pull_requests.build_change_request(
            repository,
            pull_request,
            project_name="Example Project",
            org_name="example-org",
        )
        context = ReviewContext(
            organization="example-org",
            project="Example Project",
            repository=RepositoryRef(id="repo-1", name="example-repo"),
            change_request=change_request,
        )
        analysis = ReviewAnalysis(
            context=context,
            reviewers=[],
            change_summary=workflow_models.ReviewChangeSummary(iteration=1, count=0, by_type={}),
            files=[],
            existing_comments=[],
        )
        provider = provider_review_provider.AzureDevOpsReviewProvider("token")
        args = argparse.Namespace(repo="example-repo", pr=42, source=None, url=None, output=None)

        with mock.patch.object(provider, "resolve_review_context", return_value=context):
            with mock.patch.object(provider, "analyze_change_request", return_value=analysis):
                with contextlib.redirect_stdout(io.StringIO()) as stdout:
                    review_commands.cmd_pr_review_draft(
                        args,
                        token="token",
                        build_review_provider_func=lambda _token: provider,
                    )

        rendered = json.loads(stdout.getvalue())
        self.assertEqual(change_request.provider, "azure-devops")
        self.assertEqual(rendered["provider"], "azure-devops")

    def test_cmd_pr_review_apply_requires_exact_previewed_plan(self):
        draft = {
            "formatVersion": review_commands.REVIEW_DRAFT_FORMAT_VERSION,
            "provider": "azure-devops",
            "organization": "example-org",
            "project": "Example Project",
            "repo": {"id": "repo-1", "name": "example-repo"},
            "pullRequest": {"pullRequestId": 42},
            "draftComments": [{"type": "comment", "text": "Please clarify this."}],
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump(draft, handle)
            draft_path = handle.name

        context = review_context_fixture()
        provider = mock.Mock()
        provider.resolve_review_context.return_value = context
        provider.prepare_review_comment.return_value = ReviewMutationPreview(
            payload={"comments": [{"content": "Please clarify this."}], "status": "active"}
        )
        provider.create_prepared_review_comment.return_value = ReviewMutationResult(thread_id=7)
        args = argparse.Namespace(
            draft_file=draft_path,
            dry_run=True,
            apply=None,
            json=True,
            provider=None,
        )

        try:
            def invoke():
                review_commands.cmd_pr_review_apply(
                    args,
                    token="token",
                    build_review_provider_func=lambda _token: provider,
                )

            preview, applied, approved_plan_id = self.exercise_plan_contract(
                args,
                invoke,
                provider.create_prepared_review_comment,
            )
            rendered = json.loads(preview)
            self.assertEqual(rendered["plan"]["action"], "review.draft-entry.apply")
            self.assertEqual(rendered["plan"]["payload"]["action"]["type"], "comment")
            self.assertEqual(rendered["applyArgument"], f"--apply {approved_plan_id}")
            self.assertEqual(json.loads(applied)["threadId"], 7)
        finally:
            os.unlink(draft_path)

    def test_cmd_pr_review_apply_requires_one_entry_for_multi_action_draft(self):
        draft = {
            "formatVersion": review_commands.REVIEW_DRAFT_FORMAT_VERSION,
            "provider": "azure-devops",
            "organization": "example-org",
            "project": "Example Project",
            "repo": {"id": "repo-1", "name": "example-repo"},
            "pullRequest": {"pullRequestId": 42},
            "draftComments": [
                {"type": "comment", "text": "First"},
                {"type": "comment", "text": "Second"},
            ],
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump(draft, handle)
            draft_path = handle.name

        provider = mock.Mock()
        provider.resolve_review_context.return_value = review_context_fixture()
        provider.prepare_review_comment.side_effect = lambda _context, *, text: ReviewMutationPreview(
            payload={"comments": [{"content": text}], "status": "active"}
        )
        provider.create_prepared_review_comment.return_value = ReviewMutationResult(thread_id=8)
        args = argparse.Namespace(
            draft_file=draft_path,
            entry=None,
            dry_run=False,
            apply=None,
            json=True,
            provider=None,
        )

        try:
            with self.assertRaisesRegex(CliError, "Pass --entry"):
                review_commands.cmd_pr_review_apply(
                    args,
                    token="token",
                    build_review_provider_func=lambda _token: provider,
                )
            provider.prepare_review_comment.assert_not_called()

            args.entry = 2
            with contextlib.redirect_stdout(io.StringIO()) as stdout:
                review_commands.cmd_pr_review_apply(
                    args,
                    token="token",
                    build_review_provider_func=lambda _token: provider,
                )
            rendered = json.loads(stdout.getvalue())
            self.assertEqual(rendered["plan"]["payload"]["action"]["index"], 2)
            self.assertEqual(
                rendered["plan"]["payload"]["action"]["payload"]["comments"][0]["content"],
                "Second",
            )
        finally:
            os.unlink(draft_path)

    def test_cmd_pr_review_apply_routes_gitlab_draft_context(self):
        draft = {
            "formatVersion": review_commands.REVIEW_DRAFT_FORMAT_VERSION,
            "provider": "gitlab",
            "organization": "https://gitlab.example.com",
            "project": "group/project",
            "repo": {"id": "77", "name": "group/project"},
            "pullRequest": {"pullRequestId": 42},
            "draftComments": [{"type": "comment", "text": "Please clarify this."}],
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump(draft, handle)
            draft_path = handle.name

        context = review_context_fixture(provider="gitlab")
        provider = mock.Mock()
        provider.resolve_review_context.return_value = context
        provider.prepare_review_comment.return_value = ReviewMutationPreview(
            payload={"body": "Please clarify this."}
        )
        provider.create_prepared_review_comment.return_value = ReviewMutationResult(thread_id="note-7")
        args = argparse.Namespace(
            draft_file=draft_path,
            dry_run=False,
            apply=None,
            json=True,
            provider="gitlab",
        )

        try:
            def invoke():
                review_commands.cmd_pr_review_apply(
                    args,
                    token="token",
                    build_review_provider_func=lambda _token: provider,
                )

            preview, _applied, _approved_plan_id = self.exercise_plan_contract(
                args,
                invoke,
                provider.create_prepared_review_comment,
            )
            rendered = json.loads(preview)
            self.assertEqual(rendered["plan"]["target"]["provider"], "gitlab")
            provider.resolve_review_context.assert_any_call(
                repo_ref="77",
                change_request_id=42,
                source_branch=None,
                url="https://gitlab.example.com/group/project/-/merge_requests/42",
                project_name="group/project",
                org_name="https://gitlab.example.com",
            )
        finally:
            os.unlink(draft_path)

    def test_cmd_pr_statuses_json_serializes_statuses(self):
        args = argparse.Namespace(json=True, repo=None, pr=None, source=None, url=None)
        change_request = workflow_models.ChangeRequest(
            id=42,
            title="Improve mapping",
            status="active",
            source_branch="fix/42",
            target_branch="main",
            author="Alice",
            repo_name="example-repo",
            repo_id="repo-1",
            api_url="https://example.test/pr/42",
            browser_url="https://example.test/browser/42",
        )
        context = ReviewContext(
            organization="example-org",
            project="Example Project",
            repository=RepositoryRef(id="repo-1", name="example-repo"),
            change_request=change_request,
        )
        statuses = [
            workflow_models.ChangeRequestStatus(
                id=7,
                state="succeeded",
                description="Build passed",
                context_name="CI",
                context_kind="build",
                target_url="https://example.test/build/7",
                created_by="Azure Pipelines",
                creation_date="2026-07-23T10:00:00Z",
                updated_date=None,
            )
        ]

        provider = mock.Mock()
        provider.resolve_review_context.return_value = context
        provider.list_statuses.return_value = statuses

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            review_commands.cmd_pr_statuses(
                args,
                token="token",
                build_review_provider_func=lambda _token: provider,
            )

        rendered = json.loads(stdout.getvalue())
        self.assertEqual(rendered["statuses"][0]["state"], "succeeded")
        self.assertEqual(rendered["statuses"][0]["contextGenre"], "build")
        self.assertEqual(rendered["statuses"][0]["contextName"], "CI")

    def test_load_review_draft_rejects_wrong_format_version(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump({"formatVersion": 999, "draftComments": []}, handle)
            draft_path = handle.name

        try:
            with self.assertRaises(CliError) as exc:
                provider_pull_requests.load_review_draft(draft_path)
            self.assertIn("Unsupported review draft format version", str(exc.exception))
        finally:
            os.unlink(draft_path)

    def test_resolve_pr_analysis_context_rejects_mixed_url_and_repo_flags(self):
        args = argparse.Namespace(
            url="https://dev.azure.com/example/project/_git/repo/pullrequest/1",
            repo="repo",
            pr=None,
            source=None,
        )

        with self.assertRaises(CliError) as exc:
            review_commands.resolve_pr_analysis_context("token", args)

        self.assertIn("Use either --url or --repo/--pr/--source, not both.", str(exc.exception))


if __name__ == "__main__":
    unittest.main()
