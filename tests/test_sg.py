import argparse
import contextlib
import io
import sys
import unittest
from unittest import mock

import sg


class SgEntrypointTests(unittest.TestCase):
    def test_module_doc_prefers_wrapper_entrypoint(self):
        self.assertIn("./sg <command> [options]", sg.__doc__)
        self.assertIn("python3 sg.py <command> [options]", sg.__doc__)

    def test_ready_items_help_exposes_json_flag(self):
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            with self.assertRaises(SystemExit):
                with mock.patch.object(sys, "argv", ["sg", "ready-items", "--help"]):
                    sg.main()

        help_text = stdout.getvalue()
        self.assertIn("--json", help_text)

    def test_comments_help_exposes_latest_flag(self):
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            with self.assertRaises(SystemExit):
                with mock.patch.object(sys, "argv", ["sg", "comments", "--help"]):
                    sg.main()

        help_text = stdout.getvalue()
        self.assertIn("--latest", help_text)
        self.assertIn("--json", help_text)

    def test_teams_help_exposes_json_flag(self):
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            with self.assertRaises(SystemExit):
                with mock.patch.object(sys, "argv", ["sg", "teams", "--help"]):
                    sg.main()

        help_text = stdout.getvalue()
        self.assertIn("--json", help_text)

    def test_pr_reply_help_shows_positional_text_example(self):
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            with self.assertRaises(SystemExit):
                with mock.patch.object(sys, "argv", ["sg", "pr-reply", "--help"]):
                    sg.main()

        help_text = stdout.getvalue()
        self.assertIn("Reply text (final positional argument)", help_text)
        self.assertIn("Examples:", help_text)
        self.assertIn(
            './sg pr-reply --url <ado-pr-url> --thread <thread-id> "Thanks, I will adjust this."',
            help_text,
        )

    def test_pr_statuses_help_exposes_json_flag(self):
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            with self.assertRaises(SystemExit):
                with mock.patch.object(sys, "argv", ["sg", "pr-statuses", "--help"]):
                    sg.main()

        help_text = stdout.getvalue()
        self.assertIn("--json", help_text)

    def test_external_mutator_help_requires_an_exact_apply_plan_id(self):
        commands = (
            "pick-next",
            "start",
            "review",
            "testing",
            "handoff-to-qa",
            "comment",
            "cleanup-artifacts",
            "create-pr",
            "pr-comment",
            "pr-inline-comment",
            "pr-reply",
            "pr-edit-comment",
            "pr-resolve",
            "pr-review-apply",
            "approve-gate",
            "queue-build",
        )
        for command in commands:
            with self.subTest(command=command):
                with contextlib.redirect_stdout(io.StringIO()) as stdout:
                    with self.assertRaises(SystemExit):
                        with mock.patch.object(sys, "argv", ["sg", command, "--help"]):
                            sg.main()

                self.assertIn("--apply PLAN_ID", stdout.getvalue())

    def test_apply_flag_without_plan_id_is_rejected_by_parser(self):
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            with self.assertRaises(SystemExit) as exit_context:
                with mock.patch.object(sys, "argv", ["sg", "start", "17", "--apply"]):
                    sg.main()

        self.assertEqual(exit_context.exception.code, 2)
        self.assertIn("argument --apply: expected one argument", stderr.getvalue())

    def test_pr_creation_help_retains_dry_run_flag(self):
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            with self.assertRaises(SystemExit):
                with mock.patch.object(sys, "argv", ["sg", "create-pr", "--help"]):
                    sg.main()

        self.assertIn("--dry-run", stdout.getvalue())

    def test_pr_creation_preview_requires_explicit_repo_and_source(self):
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            with self.assertRaises(SystemExit) as exit_context:
                with mock.patch.object(sys, "argv", ["sg", "create-pr", "17", "--source", "fix/17"]):
                    sg.main()

        self.assertEqual(exit_context.exception.code, 2)
        self.assertIn("--repo", stderr.getvalue())

    def test_profiles_and_use_need_no_azure_config_and_no_token(self):
        for command, extra in (("profiles", []), ("use", ["--clear"])):
            with self.subTest(command=command):
                self.assertEqual(sg.COMMAND_REQUIRED_CONFIG[command], ())
                captured = {}

                def fake(args, token=None):
                    captured["token"] = token

                with (
                    mock.patch.object(sg, f"cmd_{command.replace('-', '_')}", side_effect=fake),
                    mock.patch.object(sg, "get_token", side_effect=AssertionError("no token expected")),
                    mock.patch.object(sys, "argv", ["sg", command, *extra]),
                ):
                    sg.main()
                self.assertIsNone(captured["token"])

    def test_work_item_commands_accept_a_work_item_url_in_place_of_an_id(self):
        captured = {}

        def fake_show(args, token):
            captured["id"] = args.id

        with (
            mock.patch.object(sg, "cmd_show", side_effect=fake_show),
            mock.patch.object(sg, "missing_required_config", return_value=[]),
            mock.patch.object(sg, "get_token", return_value="azure-token"),
            mock.patch.object(
                sys,
                "argv",
                ["sg", "show", "https://dev.azure.com/contoso/Widgets/_workitems/edit/321"],
            ),
        ):
            sg.main()

        self.assertEqual(captured["id"], 321)

    def test_removed_start_work_and_prepare_review_commands_are_rejected(self):
        for command in ("start-work", "prepare-review"):
            with self.subTest(command=command):
                with contextlib.redirect_stderr(io.StringIO()) as stderr:
                    with self.assertRaises(SystemExit) as exit_context:
                        with mock.patch.object(sys, "argv", ["sg", command, "--help"]):
                            sg.main()

                self.assertEqual(exit_context.exception.code, 2)
                self.assertIn(f"invalid choice: '{command}'", stderr.getvalue())

    def test_consumed_apply_failure_warns_to_reconcile_provider_state(self):
        plan_id = "sha256:" + "a" * 64
        with (
            mock.patch.object(sg, "cmd_comment", side_effect=sg.CliError("ERROR: request timed out")),
            mock.patch.object(sg, "missing_required_config", return_value=[]),
            mock.patch.object(sg, "get_token", return_value="azure-token"),
            mock.patch.object(sg, "plan_id_was_consumed", return_value=True),
            mock.patch.object(
                sys,
                "argv",
                ["sg", "comment", "17", "hello", "--apply", plan_id],
            ),
            contextlib.redirect_stdout(io.StringIO()) as stdout,
        ):
            with self.assertRaises(SystemExit) as exit_context:
                sg.main()

        self.assertEqual(exit_context.exception.code, 1)
        self.assertIn("request timed out", stdout.getvalue())
        self.assertIn("Plan ID was consumed", stdout.getvalue())
        self.assertIn("inspect Azure DevOps or GitLab", stdout.getvalue())

    def test_build_status_help_has_no_inline_approval_flag(self):
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            with self.assertRaises(SystemExit):
                with mock.patch.object(sys, "argv", ["sg", "build-status", "--help"]):
                    sg.main()

        self.assertNotIn("--approve", stdout.getvalue())

    def test_pipeline_approval_commands_require_only_azure_org_config(self):
        for command in ("build-approvals", "approve-gate"):
            with self.subTest(command=command):
                args = argparse.Namespace(command=command, provider=None, url=None)
                self.assertEqual(
                    sg.required_config_for_command(command, args),
                    ("AZURE_DEVOPS_ORG",),
                )

    def test_pipeline_approval_commands_route_to_their_handlers(self):
        cases = (
            (
                "build-approvals",
                ["sg", "build-approvals", "42", "--project", "Example Project"],
                "cmd_build_approvals",
            ),
            (
                "approve-gate",
                [
                    "sg",
                    "approve-gate",
                    "42",
                    "--project",
                    "Example Project",
                    "--approval",
                    "approval-7",
                ],
                "cmd_approve_gate",
            ),
        )
        for command, argv, handler_name in cases:
            with self.subTest(command=command):
                with mock.patch.object(sg, handler_name) as handler:
                    with mock.patch.object(sg, "missing_required_config", return_value=[]):
                        with mock.patch.object(sg, "get_token", return_value="azure-token"):
                            with mock.patch.object(sys, "argv", argv):
                                sg.main()

                parsed_args, token = handler.call_args.args
                self.assertEqual(parsed_args.command, command)
                self.assertEqual(parsed_args.build_id, 42)
                self.assertEqual(parsed_args.project, "Example Project")
                self.assertEqual(token, "azure-token")

    def test_gitlab_cleanup_deletes_branch_with_scoped_authenticated_transport(self):
        provider = object.__new__(sg.GitLabCleanupProvider)
        provider.repo = "group/project"
        provider.work_tracking = mock.Mock()
        provider.work_tracking._require_project.return_value = "group/project"
        provider.work_tracking._gitlab_base_url.return_value = "https://gitlab.example.com"
        provider.review = mock.Mock()
        provider.review._request_json.side_effect = [
            {"id": 17},
            {"name": "fix/123-safe-cleanup", "commit": {"id": "a" * 40}},
            {"name": "fix/123-safe-cleanup", "commit": {"id": "a" * 40}},
            None,
        ]
        on_result = mock.Mock()

        prepared = provider.prepare_cleanup(branches=["fix/123-safe-cleanup"])

        result = provider.cleanup_artifacts(
            project_id=prepared["projectId"],
            issue_ids=[],
            merge_request_ids=[],
            branch_snapshots=prepared["branchSnapshots"],
            on_result=on_result,
        )

        self.assertEqual(result["branches"], ["fix/123-safe-cleanup"])
        on_result.assert_called_once_with("branch", "fix/123-safe-cleanup", "deleted")
        provider.review._request_json.assert_has_calls([
            mock.call(
                "https://gitlab.example.com",
                "/projects/group%2Fproject",
                allow_not_found=True,
            ),
            mock.call(
                "https://gitlab.example.com",
                "/projects/17/repository/branches/fix%2F123-safe-cleanup",
                allow_not_found=True,
            ),
            mock.call(
                "https://gitlab.example.com",
                "/projects/17/repository/branches/fix%2F123-safe-cleanup",
                allow_not_found=True,
            ),
            mock.call(
                "https://gitlab.example.com",
                "/projects/17/repository/branches/fix%2F123-safe-cleanup",
                method="DELETE",
            ),
        ])
        provider.review._request_with_curl.assert_not_called()

    def test_gitlab_cleanup_mutates_issues_and_merge_requests_by_bound_project_id(self):
        provider = object.__new__(sg.GitLabCleanupProvider)
        provider.repo = "group/project"
        provider.work_tracking = mock.Mock()
        provider.work_tracking._gitlab_base_url.return_value = "https://gitlab.example.com"
        provider.review = mock.Mock()
        provider.review._request_json.side_effect = [
            {"state": "closed"},
            {"state": "closed"},
        ]
        on_result = mock.Mock()

        result = provider.cleanup_artifacts(
            project_id="17",
            issue_ids=[3],
            merge_request_ids=[5],
            branch_snapshots=[],
            on_result=on_result,
        )

        self.assertEqual(result["issues"], [(3, "closed")])
        self.assertEqual(result["mergeRequests"], [(5, "closed")])
        provider.review._request_json.assert_has_calls([
            mock.call(
                "https://gitlab.example.com",
                "/projects/17/issues/3",
                method="PUT",
                form_data={"state_event": "close"},
            ),
            mock.call(
                "https://gitlab.example.com",
                "/projects/17/merge_requests/5",
                method="PUT",
                form_data={"state_event": "close"},
            ),
        ])
        self.assertEqual(on_result.call_count, 2)

    def test_gitlab_cleanup_rejects_branch_that_moved_after_preparation(self):
        provider = object.__new__(sg.GitLabCleanupProvider)
        provider.repo = "group/project"
        provider.work_tracking = mock.Mock()
        provider.work_tracking._gitlab_base_url.return_value = "https://gitlab.example.com"
        provider.review = mock.Mock()
        provider.review._request_json.return_value = {
            "name": "fix/123-safe-cleanup",
            "commit": {"id": "b" * 40},
        }

        with self.assertRaisesRegex(sg.CliError, "moved or was recreated"):
            provider.cleanup_artifacts(
                project_id="17",
                issue_ids=[],
                merge_request_ids=[],
                branch_snapshots=[
                    {"name": "fix/123-safe-cleanup", "commitSha": "a" * 40},
                ],
            )

        provider.review._request_json.assert_called_once_with(
            "https://gitlab.example.com",
            "/projects/17/repository/branches/fix%2F123-safe-cleanup",
            allow_not_found=True,
        )

    def test_gitlab_cleanup_rejects_missing_branch_after_preparation(self):
        provider = object.__new__(sg.GitLabCleanupProvider)
        provider.repo = "group/project"
        provider.work_tracking = mock.Mock()
        provider.work_tracking._gitlab_base_url.return_value = "https://gitlab.example.com"
        provider.review = mock.Mock()
        provider.review._request_json.return_value = None

        with self.assertRaisesRegex(sg.CliError, "no longer exists"):
            provider.cleanup_artifacts(
                project_id="17",
                issue_ids=[],
                merge_request_ids=[],
                branch_snapshots=[
                    {"name": "fix/123-safe-cleanup", "commitSha": "a" * 40},
                ],
            )

    def test_gitlab_cleanup_reports_deleted_branch_before_later_tip_mismatch(self):
        provider = object.__new__(sg.GitLabCleanupProvider)
        provider.repo = "group/project"
        provider.work_tracking = mock.Mock()
        provider.work_tracking._gitlab_base_url.return_value = "https://gitlab.example.com"
        provider.review = mock.Mock()
        provider.review._request_json.side_effect = [
            {"name": "first", "commit": {"id": "a" * 40}},
            None,
            {"name": "second", "commit": {"id": "c" * 40}},
        ]
        on_result = mock.Mock()

        with self.assertRaisesRegex(sg.CliError, "moved or was recreated"):
            provider.cleanup_artifacts(
                project_id="17",
                issue_ids=[],
                merge_request_ids=[],
                branch_snapshots=[
                    {"name": "first", "commitSha": "a" * 40},
                    {"name": "second", "commitSha": "b" * 40},
                ],
                on_result=on_result,
            )

        on_result.assert_called_once_with("branch", "first", "deleted")

    def test_show_fails_fast_when_required_config_is_missing(self):
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            with self.assertRaises(SystemExit) as exit_context:
                with mock.patch.object(sys, "argv", ["sg", "show", "135821"]):
                    with mock.patch.object(sg, "missing_required_config", return_value=["AZURE_DEVOPS_ORG", "AZURE_DEVOPS_PROJECT"]):
                        with mock.patch.object(sg, "get_token") as get_token:
                            sg.main()

        self.assertEqual(exit_context.exception.code, 1)
        get_token.assert_not_called()
        self.assertIn("Missing required configuration for 'show': AZURE_DEVOPS_ORG, AZURE_DEVOPS_PROJECT.", stdout.getvalue())

    def test_gitlab_review_url_uses_gitlab_token_instead_of_azure_token(self):
        with mock.patch.object(sg, "cmd_pr_analyze") as handler:
            with mock.patch.object(sg, "GITLAB_TOKEN", "gitlab-token"):
                with mock.patch.object(sg, "get_token") as get_token:
                    with mock.patch.object(
                        sys,
                        "argv",
                        ["sg", "pr-analyze", "--url", "https://gitlab.com/example/project/-/merge_requests/7"],
                    ):
                        sg.main()

        get_token.assert_not_called()
        self.assertEqual(handler.call_args.args[1], "gitlab-token")

    def test_gitlab_review_url_requires_gitlab_token(self):
        with mock.patch.object(sg, "cmd_pr_analyze") as handler:
            with mock.patch.object(sg, "GITLAB_TOKEN", None):
                with mock.patch.object(sg, "get_token") as get_token:
                    with mock.patch.object(
                        sys,
                        "argv",
                        ["sg", "pr-analyze", "--url", "https://gitlab.com/example/project/-/merge_requests/7"],
                    ):
                        sg.main()

        get_token.assert_not_called()
        self.assertEqual(handler.call_args.args[1], "")

    def test_pr_review_apply_hydrates_gitlab_provider_and_routes_gitlab_token(self):
        with mock.patch.object(sg, "cmd_pr_review_apply") as handler:
            with mock.patch.object(
                sg.review_commands,
                "load_review_draft",
                return_value={"provider": "gitlab"},
            ) as load_review_draft:
                with mock.patch.object(sg, "GITLAB_TOKEN", "gitlab-token"):
                    with mock.patch.object(sg, "missing_required_config", return_value=[]) as missing_config:
                        with mock.patch.object(sg, "get_token") as get_token:
                            with mock.patch.object(
                                sys,
                                "argv",
                                ["sg", "pr-review-apply", "review-draft.json"],
                            ):
                                sg.main()

        load_review_draft.assert_called_once_with("review-draft.json")
        missing_config.assert_called_once_with(())
        get_token.assert_not_called()
        parsed_args, token = handler.call_args.args
        self.assertEqual(parsed_args.provider, "gitlab")
        self.assertEqual(token, "gitlab-token")

    def test_pr_review_apply_hydrates_azure_provider_and_routes_azure_token(self):
        with mock.patch.object(sg, "cmd_pr_review_apply") as handler:
            with mock.patch.object(
                sg.review_commands,
                "load_review_draft",
                return_value={"provider": "azure-devops"},
            ):
                with mock.patch.object(sg, "missing_required_config", return_value=[]):
                    with mock.patch.object(sg, "get_token", return_value="azure-token") as get_token:
                        with mock.patch.object(
                            sys,
                            "argv",
                            ["sg", "pr-review-apply", "review-draft.json"],
                        ):
                            sg.main()

        get_token.assert_called_once_with()
        parsed_args, token = handler.call_args.args
        self.assertEqual(parsed_args.provider, "azure-devops")
        self.assertEqual(token, "azure-token")

    def test_pr_review_apply_rejects_provider_override_that_disagrees_with_draft(self):
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            with self.assertRaises(SystemExit) as exit_context:
                with mock.patch.object(sg, "cmd_pr_review_apply") as handler:
                    with mock.patch.object(
                        sg.review_commands,
                        "load_review_draft",
                        return_value={"provider": "gitlab"},
                    ):
                        with mock.patch.object(sg, "get_token") as get_token:
                            with mock.patch.object(
                                sys,
                                "argv",
                                [
                                    "sg",
                                    "pr-review-apply",
                                    "review-draft.json",
                                    "--provider",
                                    "azure-devops",
                                ],
                            ):
                                sg.main()

        self.assertEqual(exit_context.exception.code, 1)
        self.assertIn("does not match --provider 'azure-devops'", stdout.getvalue())
        get_token.assert_not_called()
        handler.assert_not_called()

    def test_gitlab_create_pr_uses_gitlab_token_instead_of_azure_token(self):
        with mock.patch.object(sg, "cmd_create_pr") as handler:
            with mock.patch.object(sg, "GITLAB_TOKEN", "gitlab-token"):
                with mock.patch.object(sg, "get_token") as get_token:
                    with mock.patch.object(
                        sys,
                        "argv",
                        ["sg", "create-pr", "17", "--provider", "gitlab", "--repo", "group/project", "--source", "feature/test"],
                    ):
                        sg.main()

        get_token.assert_not_called()
        self.assertEqual(handler.call_args.args[1], "gitlab-token")

    def test_gitlab_create_pr_skips_azure_devops_required_config(self):
        args = argparse.Namespace(command="create-pr", provider="gitlab", repo="group/project", url=None)

        self.assertEqual(sg.required_config_for_command("create-pr", args), ())

    def test_gitlab_show_uses_gitlab_token_instead_of_azure_token(self):
        with mock.patch.object(sg, "cmd_show") as handler:
            with mock.patch.object(sg, "GITLAB_TOKEN", "gitlab-token"):
                with mock.patch.object(sg, "get_token") as get_token:
                    with mock.patch.object(
                        sys,
                        "argv",
                        ["sg", "show", "17", "--provider", "gitlab", "--repo", "group/project"],
                    ):
                        sg.main()

        get_token.assert_not_called()
        self.assertEqual(handler.call_args.args[1], "gitlab-token")

    def test_gitlab_show_skips_azure_devops_required_config(self):
        args = argparse.Namespace(command="show", provider="gitlab", repo="group/project", url=None)

        self.assertEqual(sg.required_config_for_command("show", args), ())

    def test_gitlab_work_tracking_commands_require_explicit_repo(self):
        args = argparse.Namespace(command="review", provider="gitlab", repo=None, url=None, qa=None)

        with self.assertRaisesRegex(sg.CliError, "explicit --repo"):
            sg.ensure_command_configuration("review", args)

    def test_gitlab_start_uses_gitlab_token_instead_of_azure_token(self):
        with mock.patch.object(sg, "cmd_start") as handler:
            with mock.patch.object(sg, "GITLAB_TOKEN", "gitlab-token"):
                with mock.patch.object(sg, "get_token") as get_token:
                    with mock.patch.object(
                        sys,
                        "argv",
                        ["sg", "start", "17", "--provider", "gitlab", "--repo", "group/project"],
                    ):
                        sg.main()

        get_token.assert_not_called()
        self.assertEqual(handler.call_args.args[1], "gitlab-token")

    def test_gitlab_start_skips_azure_devops_required_config(self):
        args = argparse.Namespace(command="start", provider="gitlab", repo="group/project", url=None, qa=None)

        self.assertEqual(sg.required_config_for_command("start", args), ())

    def test_gitlab_review_skips_azure_devops_required_config(self):
        args = argparse.Namespace(command="review", provider="gitlab", repo="group/project", url=None, qa=None)

        self.assertEqual(sg.required_config_for_command("review", args), ())

    def test_gitlab_testing_requires_only_qa_config_when_not_provided(self):
        args = argparse.Namespace(command="testing", provider="gitlab", repo="group/project", url=None, qa=None)

        self.assertEqual(sg.required_config_for_command("testing", args), ("AZURE_DEVOPS_QA_USER",))

    def test_gitlab_testing_skips_azure_devops_required_config_when_qa_provided(self):
        args = argparse.Namespace(command="testing", provider="gitlab", repo="group/project", url=None, qa="qa-user")

        self.assertEqual(sg.required_config_for_command("testing", args), ())

    def test_gitlab_cleanup_skips_azure_devops_required_config(self):
        args = argparse.Namespace(command="cleanup-artifacts", provider="gitlab", repo="group/project", url=None)

        self.assertEqual(sg.required_config_for_command("cleanup-artifacts", args), ())


if __name__ == "__main__":
    unittest.main()
