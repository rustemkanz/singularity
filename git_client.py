import re
import subprocess
import urllib.parse

from errors import CliError


_FULL_COMMIT_SHA_PATTERN = re.compile(r"[0-9a-fA-F]{40}\Z")


def git_output(args: list[str], *, reject_stderr: bool = False) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=True)
    if result.returncode != 0 or (reject_stderr and result.stderr.strip()):
        raise CliError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def infer_git_repository_ref() -> str | None:
    result = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None

    remote_url = result.stdout.strip()
    patterns = (
        r"/_git/([^/?#]+)",
        r"(?:^|:)v3/[^/]+/[^/]+/([^/?#]+)$",
        r"/v3/[^/]+/[^/]+/([^/?#]+)$",
    )
    for pattern in patterns:
        match = re.search(pattern, remote_url)
        if match:
            return urllib.parse.unquote(match.group(1))
    return None


def current_git_branch() -> str:
    return git_output(["rev-parse", "--abbrev-ref", "HEAD"])


def resolve_git_commit(ref: str) -> str:
    normalized_ref = ref.strip() if isinstance(ref, str) else ""
    if not normalized_ref:
        raise CliError("ERROR: Git commit reference must not be empty.")
    try:
        commit = git_output(
            [
                "rev-parse",
                "--verify",
                "--end-of-options",
                f"{normalized_ref}^{{commit}}",
            ],
            reject_stderr=True,
        )
    except CliError as exc:
        raise CliError(
            f"ERROR: Could not resolve Git commit reference '{normalized_ref}': {exc}"
        ) from exc
    if _FULL_COMMIT_SHA_PATTERN.fullmatch(commit) is None:
        raise CliError(
            f"ERROR: Git commit reference '{normalized_ref}' did not resolve to one full "
            "40-character commit SHA."
        )
    return commit.lower()


def current_git_commit() -> str:
    return resolve_git_commit("HEAD")
