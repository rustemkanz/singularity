import re
import subprocess
import urllib.parse

from errors import CliError


def git_output(args: list[str]) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=True)
    if result.returncode != 0:
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


def current_git_commit() -> str:
    return git_output(["rev-parse", "HEAD"])
