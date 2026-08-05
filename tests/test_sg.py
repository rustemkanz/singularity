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
