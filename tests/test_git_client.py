import unittest
from unittest import mock

from errors import CliError
import git_client


class ResolveGitCommitTests(unittest.TestCase):
    def test_resolves_a_ref_to_a_canonical_full_commit_sha(self):
        commit = "ABCDEF1234567890ABCDEF1234567890ABCDEF12"

        with mock.patch.object(git_client, "git_output", return_value=commit) as git_output:
            resolved = git_client.resolve_git_commit(" main ")

        git_output.assert_called_once_with([
            "rev-parse",
            "--verify",
            "--end-of-options",
            "main^{commit}",
        ], reject_stderr=True)
        self.assertEqual(resolved, commit.lower())

    def test_rejects_an_ambiguous_or_unresolvable_ref(self):
        with mock.patch.object(
            git_client,
            "git_output",
            side_effect=CliError("fatal: short object ID deadbee is ambiguous"),
        ):
            with self.assertRaisesRegex(CliError, "Could not resolve Git commit reference 'deadbee'"):
                git_client.resolve_git_commit("deadbee")

    def test_rejects_a_ref_that_git_resolves_with_an_ambiguity_warning(self):
        result = mock.Mock(
            returncode=0,
            stdout="a" * 40 + "\n",
            stderr="warning: refname 'release' is ambiguous.\n",
        )

        with mock.patch.object(git_client.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(CliError, "refname 'release' is ambiguous"):
                git_client.resolve_git_commit("release")

    def test_rejects_noncanonical_resolver_output(self):
        with mock.patch.object(git_client, "git_output", return_value="abcdef123456"):
            with self.assertRaisesRegex(CliError, "one full 40-character commit SHA"):
                git_client.resolve_git_commit("main")

    def test_hints_at_wrong_working_directory_when_not_in_a_git_repo(self):
        with mock.patch.object(
            git_client,
            "git_output",
            side_effect=CliError(
                "fatal: not a git repository (or any of the parent directories): .git"
            ),
        ):
            with self.assertRaisesRegex(CliError, "outside a Git working directory"):
                git_client.resolve_git_commit("main")

    def test_rejects_an_empty_ref_without_running_git(self):
        with mock.patch.object(git_client, "git_output") as git_output:
            with self.assertRaisesRegex(CliError, "must not be empty"):
                git_client.resolve_git_commit("   ")

        git_output.assert_not_called()


if __name__ == "__main__":
    unittest.main()
