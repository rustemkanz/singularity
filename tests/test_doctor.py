import argparse
import contextlib
import io
import json
import unittest
from unittest import mock

from cli_commands import doctor as doctor_commands
from cli_commands import review as review_commands
from errors import CliError


def _run_doctor_json(**overrides):
    args = argparse.Namespace(json=True)
    kwargs = dict(
        probe_azure_token_func=lambda: {"ok": True, "token": "token", "hints": []},
        infer_git_repository_ref_func=lambda: "sample-repo",
        current_git_branch_func=lambda: "main",
        missing_required_config_func=lambda: [],
        broken_pip_cert_paths_func=lambda: [],
        org="example-org",
        project="Example Project",
        team_id="team-1",
        me="me@example.com",
        default_repo=None,
        cli_error_cls=CliError,
    )
    kwargs.update(overrides)
    with contextlib.redirect_stdout(io.StringIO()) as stdout:
        doctor_commands.cmd_doctor(args, **kwargs)
    return json.loads(stdout.getvalue())


class DoctorCommandTests(unittest.TestCase):
    def test_cmd_doctor_json_suggests_teams_and_azure_devops_user_when_missing(self):
        args = argparse.Namespace(json=True)

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            doctor_commands.cmd_doctor(
                args,
                probe_azure_token_func=lambda: {"ok": False, "error": "no token", "hints": []},
                infer_git_repository_ref_func=lambda: "sample-repo",
                current_git_branch_func=lambda: "main",
                missing_required_config_func=lambda: ["AZURE_DEVOPS_TEAM_ID", "AZURE_DEVOPS_USER"],
                org="example-org",
                project="Example Project",
                team_id="team-1",
                me="me@example.com",
                default_repo=None,
                broken_pip_cert_paths_func=lambda: [],
                cli_error_cls=CliError,
            )

        rendered = json.loads(stdout.getvalue())
        config_check = next(check for check in rendered["checks"] if check["name"] == "configuration")
        self.assertEqual(config_check["missingNames"], ["AZURE_DEVOPS_TEAM_ID", "AZURE_DEVOPS_USER"])
        self.assertIn("Run './sg teams' and copy the id for your team into AZURE_DEVOPS_TEAM_ID.", config_check["hints"])
        self.assertIn(
            "Set AZURE_DEVOPS_USER to your Azure DevOps identity so ./sg list, ./sg ready-items, and ./sg pick-next can filter to your assigned work.",
            config_check["hints"],
        )

    def test_cmd_doctor_json_treats_missing_git_repo_as_advisory_when_default_repo_set(self):
        args = argparse.Namespace(json=True)
        repos = [
            {"id": "repo-1", "name": "sample-repo"},
            {"id": "repo-2", "name": "other"},
        ]

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            doctor_commands.cmd_doctor(
                args,
                probe_azure_token_func=lambda: {"ok": True, "token": "token", "hints": []},
                infer_git_repository_ref_func=lambda: None,
                current_git_branch_func=lambda: "main",
                missing_required_config_func=lambda: [],
                list_repositories_func=lambda _token: repos,
                match_repository_func=review_commands.match_repository,
                org="example-org",
                project="Example Project",
                team_id="team-1",
                me="me@example.com",
                default_repo="sample-repo",
                broken_pip_cert_paths_func=lambda: [],
                cli_error_cls=CliError,
            )

        rendered = json.loads(stdout.getvalue())
        git_check = next(check for check in rendered["checks"] if check["name"] == "gitRemoteRepo")
        repo_check = next(check for check in rendered["checks"] if check["name"] == "azureDevopsRepoResolution")
        self.assertTrue(git_check["ok"])
        self.assertIn("No Azure DevOps repository is linked", git_check["detail"])
        self.assertIn("AZURE_DEVOPS_DEFAULT_REPO='sample-repo'", git_check["hints"][0])
        self.assertTrue(repo_check["ok"])
        self.assertIn("from AZURE_DEVOPS_DEFAULT_REPO", repo_check["detail"])

    def test_cmd_doctor_skips_repo_resolution_when_org_or_project_missing(self):
        args = argparse.Namespace(json=True)

        def fail_if_called(_token):
            raise AssertionError("list_repositories_func should not be called without org/project configured")

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            doctor_commands.cmd_doctor(
                args,
                probe_azure_token_func=lambda: {"ok": True, "token": "token", "hints": []},
                infer_git_repository_ref_func=lambda: None,
                current_git_branch_func=lambda: "main",
                missing_required_config_func=lambda: ["AZURE_DEVOPS_ORG", "AZURE_DEVOPS_PROJECT"],
                list_repositories_func=fail_if_called,
                match_repository_func=review_commands.match_repository,
                org=None,
                project=None,
                team_id=None,
                me=None,
                default_repo=None,
                broken_pip_cert_paths_func=lambda: [],
                cli_error_cls=CliError,
            )

        rendered = json.loads(stdout.getvalue())
        self.assertIsNone(next((check for check in rendered["checks"] if check["name"] == "azureDevopsRepoResolution"), None))


    def test_cmd_doctor_reports_the_active_profile(self):
        with (
            mock.patch.object(doctor_commands, "ACTIVE_PROFILE", "web-eu"),
            mock.patch.object(doctor_commands, "ACTIVE_PROFILE_SOURCE", "file"),
        ):
            rendered = _run_doctor_json()
        self.assertEqual(rendered["activeProfile"], "web-eu")
        profile_check = next(check for check in rendered["checks"] if check["name"] == "profile")
        self.assertIn("web-eu", profile_check["detail"])

    def test_cmd_doctor_flags_a_broken_pip_cert(self):
        rendered = _run_doctor_json(broken_pip_cert_paths_func=lambda: ["/no/such/bundle.pem"])
        pip_check = next(check for check in rendered["checks"] if check["name"] == "pipConfig")
        self.assertFalse(pip_check["ok"])
        self.assertIn("/no/such/bundle.pem", pip_check["detail"])

    def test_cmd_doctor_omits_pip_check_when_cert_config_is_fine(self):
        rendered = _run_doctor_json(broken_pip_cert_paths_func=lambda: [])
        self.assertIsNone(next((c for c in rendered["checks"] if c["name"] == "pipConfig"), None))

    def test_broken_pip_cert_paths_parses_pip_config_output(self):
        class Result:
            returncode = 0
            stdout = "global.cert='/missing/one.pem'\ninstall.cert=/etc/ssl/cert.pem\n"

        missing = doctor_commands.broken_pip_cert_paths(runner=lambda: Result())
        self.assertEqual(missing, ["/missing/one.pem"])

    def test_cmd_doctor_git_repo_line_is_not_a_contradiction_when_unlinked(self):
        rendered = _run_doctor_json(infer_git_repository_ref_func=lambda: None)
        git_check = next(check for check in rendered["checks"] if check["name"] == "gitRemoteRepo")
        self.assertTrue(git_check["ok"])
        self.assertNotIn("Could not infer", git_check["detail"])


if __name__ == "__main__":
    unittest.main()
