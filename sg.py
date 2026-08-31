#!/usr/bin/env python3
"""
Singularity CLI for agent-assisted delivery workflows

Usage:
    ./sg <command> [options]
    python3 sg.py <command> [options]

Commands:
    sprint          Show the current sprint
    list            List my open items (New / Ready for development)
    ready-items     Alias for list
    pick-next       Show the next candidate and optionally preview its start plan
    team-members    List the members of a team (default: AZURE_DEVOPS_TEAM_ID)
    show <id>       Show full details of a work item
    comments <id>   Show work-item comments
    context <id>    Show full work-item context including comments and linked dev artifacts
    tree <id>       Show a work item's parent chain and child items with assignee/tags
    attachments <id> Show or download attachment and screenshot context
    introduced-by <id> Show linked PR/commit candidates that likely introduced a bug
    triage <ids...> Summarize several work items and suggest grouping for PRs
    start <id>      Preview the canonical start plan; apply by exact Plan ID
    review <id>     Preview moving an item to 'In Review'; apply by exact Plan ID
    testing <id>    Preview moving an item to 'In Testing' and assigning QA
    handoff-to-qa   Alias for testing
    create-pr       Preview a pull request; apply by exact Plan ID
    comment <id>    Preview a work-item comment; apply by exact Plan ID
    draft-items <plan> Preview child work items from a plan file; create by exact Plan ID
    repos           List git repositories in the project
    pr-analyze      Summarize a PR from a URL or repo/PR reference
    pr-files        List changed files for a PR
    pr-file         Show PR file contents from source or target branch
    pr-diff         Show a unified diff for one PR file
    pr-comments     Show review thread comments on a PR
    pr-statuses     Show read-only PR statuses such as build or policy checks
    pr-comment      Preview a new discussion thread on a PR
    pr-inline-comment Preview a new inline review thread on a PR file/line
    pr-reply        Preview a reply to an existing PR review thread
    pr-edit-comment Preview editing an existing PR thread comment
    pr-resolve      Preview resolving a PR review thread
    pr-review-draft Create a JSON review draft bundle for later approval/posting
    pr-review-apply Preview one mutation entry from a review draft
    builds          List recent builds for a pipeline definition
    build-status    Show build status grouped by stage
    build-approvals List pending pipeline gate approvals
    approve-gate    Preview approval of one pipeline gate
    build-logs      Show build logs for matching stages, jobs, or steps
    queue-build     Preview queueing a new pipeline run
    service-endpoints List Azure DevOps service connections/endpoints
    service-endpoint-show Show one Azure DevOps service connection/endpoint
    profiles        List project profiles and show the active one
    use             Set or clear the active project profile
    doctor          Check Azure CLI auth, project access, and repo resolution
"""

import argparse
import os
from pathlib import Path
import sys
import urllib.parse
from cli_commands import builds as build_commands
from cli_commands import doctor as doctor_commands
from cli_commands import profiles as profile_commands
from cli_commands import review as review_commands
from cli_commands import service_endpoints as service_endpoint_commands
from cli_commands import work_items as work_item_commands
from app_config import (
    DEFAULT_REPO,
    GITLAB_TOKEN,
    ME,
    missing_required_config,
    ORG,
    PROJECT,
    QA_EMAIL,
    TEAM_ID,
)
from errors import CliError
from git_client import current_git_branch, infer_git_repository_ref, resolve_git_commit
from mutation_plans import plan_id_was_consumed
from providers.azure_devops import (
    AzureDevOpsBuildProvider,
    AzureDevOpsEvidenceProvider,
    AzureDevOpsReviewProvider,
    AzureDevOpsServiceEndpointProvider,
    AzureDevOpsWorkTrackingProvider,
)
from providers.gitlab import GitLabReviewProvider, is_gitlab_merge_request_url
from providers.gitlab import GitLabWorkTrackingProvider
from providers.azure_devops.auth import get_token, probe_azure_token
def build_review_provider(token: str) -> AzureDevOpsReviewProvider:
    return AzureDevOpsReviewProvider(token)


def build_review_provider_for_args(token: str, args):
    if uses_gitlab_review_provider(args.command, args):
        return GitLabReviewProvider(token)
    return build_review_provider(token)


def build_build_provider(token: str) -> AzureDevOpsBuildProvider:
    return AzureDevOpsBuildProvider(token)


def build_work_tracking_provider(token: str) -> AzureDevOpsWorkTrackingProvider:
    return AzureDevOpsWorkTrackingProvider(token)


def build_work_tracking_provider_for_args(token: str, args):
    if uses_gitlab_work_tracking_provider(args.command, args):
        return GitLabWorkTrackingProvider(token, getattr(args, "repo", None))
    return build_work_tracking_provider(token)


def build_evidence_provider(token: str) -> AzureDevOpsEvidenceProvider:
    return AzureDevOpsEvidenceProvider(token)


def build_service_endpoint_provider(token: str) -> AzureDevOpsServiceEndpointProvider:
    return AzureDevOpsServiceEndpointProvider(token)


def truncate_text(text: str, limit: int = 1000) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def format_section(label: str, text: str) -> str:
    body = truncate_text(text or "(none)")
    return f"\n  {label}:\n    {body.replace(chr(10), chr(10) + '    ')}"


# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_sprint(args, token):
    return work_item_commands.cmd_sprint(
        args,
        token,
        build_work_tracking_provider_func=build_work_tracking_provider,
    )


def cmd_list(args, token):
    return work_item_commands.cmd_list(
        args,
        token,
        build_work_tracking_provider_func=build_work_tracking_provider,
        me=ME,
    )


def cmd_teams(args, token):
    return work_item_commands.cmd_teams(
        args,
        token,
        build_work_tracking_provider_func=build_work_tracking_provider,
    )


def cmd_team_members(args, token):
    return work_item_commands.cmd_team_members(
        args,
        token,
        build_work_tracking_provider_func=build_work_tracking_provider,
        team_id=TEAM_ID,
    )


def cmd_show(args, token):
    return work_item_commands.cmd_show(
        args,
        token,
        build_work_tracking_provider_func=lambda token_value: build_work_tracking_provider_for_args(token_value, args),
    )


def cmd_context(args, token):
    return work_item_commands.cmd_context(
        args,
        token,
        build_work_tracking_provider_func=lambda token_value: build_work_tracking_provider_for_args(token_value, args),
    )


def cmd_tree(args, token):
    return work_item_commands.cmd_tree(
        args,
        token,
        build_work_tracking_provider_func=build_work_tracking_provider,
    )


def cmd_comments(args, token):
    return work_item_commands.cmd_comments(
        args,
        token,
        build_work_tracking_provider_func=lambda token_value: build_work_tracking_provider_for_args(token_value, args),
    )


def cmd_attachments(args, token):
    return work_item_commands.cmd_attachments(
        args,
        token,
        build_evidence_provider_func=build_evidence_provider,
    )


def cmd_introduced_by(args, token):
    return work_item_commands.cmd_introduced_by(
        args,
        token,
        build_work_tracking_provider_func=build_work_tracking_provider,
    )


def cmd_triage(args, token):
    return work_item_commands.cmd_triage(
        args,
        token,
        build_work_tracking_provider_func=build_work_tracking_provider,
    )


def cmd_start(args, token):
    return work_item_commands.cmd_start(
        args,
        token,
        build_work_tracking_provider_func=lambda token_value: build_work_tracking_provider_for_args(token_value, args),
        cmd_show_func=cmd_show,
    )


def cmd_pick_next(args, token):
    return work_item_commands.cmd_pick_next(
        args,
        token,
        build_work_tracking_provider_func=build_work_tracking_provider,
        cmd_show_func=cmd_show,
        cmd_start_func=cmd_start,
    )


def cmd_review(args, token):
    return work_item_commands.cmd_review(
        args,
        token,
        build_work_tracking_provider_func=lambda token_value: build_work_tracking_provider_for_args(token_value, args),
    )


def cmd_testing(args, token):
    return work_item_commands.cmd_testing(
        args,
        token,
        build_work_tracking_provider_func=lambda token_value: build_work_tracking_provider_for_args(token_value, args),
        qa_email=QA_EMAIL,
    )


def cmd_handoff_to_qa(args, token):
    return work_item_commands.cmd_handoff_to_qa(args, token, cmd_testing_func=cmd_testing)


def cmd_comment(args, token):
    return work_item_commands.cmd_comment(
        args,
        token,
        build_work_tracking_provider_func=lambda token_value: build_work_tracking_provider_for_args(token_value, args),
    )


def cmd_draft_items(args, token):
    return work_item_commands.cmd_draft_items(
        args,
        token,
        build_work_tracking_provider_func=build_work_tracking_provider,
        me=ME,
    )


class GitLabCleanupProvider:
    def __init__(self, token: str | None, repo: str | None):
        self.work_tracking = GitLabWorkTrackingProvider(token, repo)
        self.review = GitLabReviewProvider(token)
        self.repo = repo

    def _resolve_project(self) -> tuple[str, str]:
        repo_ref = self.work_tracking._require_project()
        base_url = self.work_tracking._gitlab_base_url()
        project = self.review._request_json(
            base_url,
            f"/projects/{urllib.parse.quote(repo_ref, safe='')}",
            allow_not_found=True,
        )
        if project is None:
            raise CliError(f"ERROR: GitLab project '{repo_ref}' was not found.")
        project_id = project.get("id") if isinstance(project, dict) else None
        if isinstance(project_id, bool) or not isinstance(project_id, int) or project_id <= 0:
            raise CliError(f"ERROR: GitLab project '{repo_ref}' returned an invalid project id.")
        return base_url, str(project_id)

    def _branch_snapshot(self, base_url: str, project_id: str, branch: str) -> dict | None:
        response = self.review._request_json(
            base_url,
            f"/projects/{project_id}/repository/branches/{urllib.parse.quote(branch, safe='')}",
            allow_not_found=True,
        )
        if response is None:
            return None
        commit = response.get("commit") if isinstance(response, dict) else None
        commit_sha = commit.get("id") if isinstance(commit, dict) else None
        if not isinstance(commit_sha, str) or not commit_sha.strip():
            raise CliError(
                f"ERROR: GitLab branch '{branch}' returned an invalid commit id; cleanup was not prepared."
            )
        return {"name": branch, "commitSha": commit_sha}

    def prepare_cleanup(self, *, branches: list[str]) -> dict:
        """Resolve the immutable GitLab project and branch tips for approval."""
        base_url, project_id = self._resolve_project()
        branch_snapshots = []
        for branch in branches:
            snapshot = self._branch_snapshot(base_url, project_id, branch)
            if snapshot is None:
                raise CliError(
                    f"ERROR: GitLab branch '{branch}' was not found; cleanup was not prepared."
                )
            branch_snapshots.append(snapshot)
        return {
            "projectId": project_id,
            "branchSnapshots": branch_snapshots,
        }

    def cleanup_artifacts(
        self,
        *,
        project_id: str,
        issue_ids: list[int],
        merge_request_ids: list[int],
        branch_snapshots: list[dict],
        on_result=None,
    ) -> dict:
        base_url = self.work_tracking._gitlab_base_url()

        closed_issues = []
        for issue_id in issue_ids:
            issue = self.review._request_json(
                base_url,
                f"/projects/{project_id}/issues/{issue_id}",
                method="PUT",
                form_data={"state_event": "close"},
            )
            state = issue.get("state") or "unknown"
            closed_issues.append((issue_id, state))
            if on_result is not None:
                on_result("issue", issue_id, state)

        closed_merge_requests = []
        for merge_request_id in merge_request_ids:
            merge_request = self.review._request_json(
                base_url,
                f"/projects/{project_id}/merge_requests/{merge_request_id}",
                method="PUT",
                form_data={"state_event": "close"},
            )
            state = merge_request.get("state") or "unknown"
            closed_merge_requests.append((merge_request_id, state))
            if on_result is not None:
                on_result("mergeRequest", merge_request_id, state)

        deleted_branches = []
        for expected_snapshot in branch_snapshots:
            branch = expected_snapshot.get("name") if isinstance(expected_snapshot, dict) else None
            expected_commit_sha = (
                expected_snapshot.get("commitSha") if isinstance(expected_snapshot, dict) else None
            )
            if not isinstance(branch, str) or not branch or not isinstance(expected_commit_sha, str):
                raise CliError("ERROR: Approved GitLab branch cleanup snapshot is invalid.")
            current_snapshot = self._branch_snapshot(base_url, project_id, branch)
            if current_snapshot is None:
                raise CliError(
                    f"ERROR: GitLab branch '{branch}' no longer exists; it may have been deleted or recreated. "
                    "Generate and approve a fresh cleanup plan."
                )
            if current_snapshot["commitSha"] != expected_commit_sha:
                raise CliError(
                    f"ERROR: GitLab branch '{branch}' moved or was recreated since the cleanup preview "
                    f"({expected_commit_sha} -> {current_snapshot['commitSha']}); generate and approve a fresh plan."
                )
            self.review._request_json(
                base_url,
                f"/projects/{project_id}/repository/branches/{urllib.parse.quote(branch, safe='')}",
                method="DELETE",
            )
            deleted_branches.append(branch)
            if on_result is not None:
                on_result("branch", branch, "deleted")

        return {
            "issues": closed_issues,
            "mergeRequests": closed_merge_requests,
            "branches": deleted_branches,
        }


def cmd_cleanup_artifacts(args, token):
    if args.provider != "gitlab":
        raise CliError("ERROR: cleanup-artifacts currently supports only --provider gitlab.")
    return work_item_commands.cmd_cleanup_artifacts(
        args,
        token,
        cleanup_provider_factory=lambda token_value: GitLabCleanupProvider(token_value, args.repo),
    )


def cmd_repos(args, token):
    return review_commands.cmd_repos(args, token, build_review_provider_func=build_review_provider)


def cmd_create_pr(args, token):
    return review_commands.cmd_create_pr(
        args,
        token,
        build_review_provider_func=lambda token_value: build_review_provider_for_args(token_value, args),
    )


def cmd_pr_analyze(args, token):
    return review_commands.cmd_pr_analyze(
        args,
        token,
        build_review_provider_func=lambda token_value: build_review_provider_for_args(token_value, args),
    )


def cmd_pr_files(args, token):
    return review_commands.cmd_pr_files(
        args,
        token,
        build_review_provider_func=lambda token_value: build_review_provider_for_args(token_value, args),
    )


def cmd_pr_file(args, token):
    return review_commands.cmd_pr_file(
        args,
        token,
        build_review_provider_func=lambda token_value: build_review_provider_for_args(token_value, args),
    )


def cmd_pr_diff(args, token):
    return review_commands.cmd_pr_diff(
        args,
        token,
        build_review_provider_func=lambda token_value: build_review_provider_for_args(token_value, args),
    )


def cmd_pr_comments(args, token):
    return review_commands.cmd_pr_comments(
        args,
        token,
        build_review_provider_func=lambda token_value: build_review_provider_for_args(token_value, args),
    )


def cmd_pr_comment(args, token):
    return review_commands.cmd_pr_comment(
        args,
        token,
        build_review_provider_func=lambda token_value: build_review_provider_for_args(token_value, args),
    )


def cmd_pr_inline_comment(args, token):
    return review_commands.cmd_pr_inline_comment(
        args,
        token,
        build_review_provider_func=lambda token_value: build_review_provider_for_args(token_value, args),
    )


def cmd_pr_reply(args, token):
    return review_commands.cmd_pr_reply(
        args,
        token,
        build_review_provider_func=lambda token_value: build_review_provider_for_args(token_value, args),
    )


def cmd_pr_edit_comment(args, token):
    return review_commands.cmd_pr_edit_comment(
        args,
        token,
        build_review_provider_func=lambda token_value: build_review_provider_for_args(token_value, args),
    )


def cmd_pr_resolve(args, token):
    return review_commands.cmd_pr_resolve(
        args,
        token,
        build_review_provider_func=lambda token_value: build_review_provider_for_args(token_value, args),
    )


def cmd_pr_review_draft(args, token):
    return review_commands.cmd_pr_review_draft(
        args,
        token,
        build_review_provider_func=lambda token_value: build_review_provider_for_args(token_value, args),
    )


def cmd_pr_review_apply(args, token):
    return review_commands.cmd_pr_review_apply(
        args,
        token,
        build_review_provider_func=lambda token_value: build_review_provider_for_args(token_value, args),
    )


def cmd_builds(args, token):
    return build_commands.cmd_builds(
        args,
        token,
        build_build_provider_func=build_build_provider,
    )


def cmd_queue_build(args, token):
    return build_commands.cmd_queue_build(
        args,
        token,
        build_build_provider_func=build_build_provider,
        current_git_branch_func=current_git_branch,
        resolve_git_commit_func=resolve_git_commit,
    )


def cmd_build_logs(args, token):
    return build_commands.cmd_build_logs(
        args,
        token,
        build_build_provider_func=build_build_provider,
    )


def fetch_pending_approvals(token: str, project: str, build_id: int) -> list[dict]:
    return build_commands.fetch_pending_approvals(
        token,
        project,
        build_id,
        build_build_provider_func=build_build_provider,
    )


def cmd_build_approvals(args, token):
    return build_commands.cmd_build_approvals(
        args,
        token,
        build_build_provider_func=build_build_provider,
    )


def cmd_approve_gate(args, token):
    return build_commands.cmd_approve_gate(
        args,
        token,
        build_build_provider_func=build_build_provider,
    )


def cmd_build_status(args, token):
    return build_commands.cmd_build_status(
        args,
        token,
        build_build_provider_func=build_build_provider,
        get_token_func=get_token,
    )



def cmd_service_endpoints(args, token):
    return service_endpoint_commands.cmd_service_endpoints(
        args,
        token,
        build_service_endpoint_provider_func=build_service_endpoint_provider,
    )


def cmd_service_endpoint_show(args, token):
    return service_endpoint_commands.cmd_service_endpoint_show(
        args,
        token,
        build_service_endpoint_provider_func=build_service_endpoint_provider,
    )


def cmd_pr_statuses(args, token):
    return review_commands.cmd_pr_statuses(
        args,
        token,
        build_review_provider_func=lambda token_value: build_review_provider_for_args(token_value, args),
    )


def cmd_profiles(args, _token=None):
    return profile_commands.cmd_profiles(args, _token)


def cmd_use(args, _token=None):
    return profile_commands.cmd_use(args, _token)


def cmd_doctor(args, _token=None):
    return doctor_commands.cmd_doctor(
        args,
        _token,
        probe_azure_token_func=probe_azure_token,
        infer_git_repository_ref_func=infer_git_repository_ref,
        current_git_branch_func=current_git_branch,
        missing_required_config_func=missing_required_config,
        list_repositories_func=lambda token, project_name=None, org_name=None: review_commands.list_repositories(
            token,
            project_name=project_name,
            org_name=org_name,
            build_review_provider_func=build_review_provider,
        ),
        match_repository_func=review_commands.match_repository,
        org=ORG,
        project=PROJECT,
        team_id=TEAM_ID,
        me=ME,
        default_repo=DEFAULT_REPO,
        cli_error_cls=CliError,
    )


BASE_COMMAND_REQUIRED_CONFIG = ("AZURE_DEVOPS_ORG", "AZURE_DEVOPS_PROJECT")
COMMAND_REQUIRED_CONFIG: dict[str, tuple[str, ...]] = {
    "doctor": (),
    "profiles": (),
    "use": (),
    "builds": ("AZURE_DEVOPS_ORG",),
    "build-status": ("AZURE_DEVOPS_ORG",),
    "build-approvals": ("AZURE_DEVOPS_ORG",),
    "approve-gate": ("AZURE_DEVOPS_ORG",),
    "build-logs": ("AZURE_DEVOPS_ORG",),
    "queue-build": ("AZURE_DEVOPS_ORG",),
    "service-endpoints": ("AZURE_DEVOPS_ORG",),
    "service-endpoint-show": ("AZURE_DEVOPS_ORG",),
    "teams": BASE_COMMAND_REQUIRED_CONFIG,
    "team-members": BASE_COMMAND_REQUIRED_CONFIG,
    "sprint": BASE_COMMAND_REQUIRED_CONFIG + ("AZURE_DEVOPS_TEAM_ID",),
    "list": BASE_COMMAND_REQUIRED_CONFIG + ("AZURE_DEVOPS_TEAM_ID", "AZURE_DEVOPS_USER"),
    "ready-items": BASE_COMMAND_REQUIRED_CONFIG + ("AZURE_DEVOPS_TEAM_ID", "AZURE_DEVOPS_USER"),
    "pick-next": BASE_COMMAND_REQUIRED_CONFIG + ("AZURE_DEVOPS_TEAM_ID", "AZURE_DEVOPS_USER"),
}
URL_CAPABLE_REVIEW_COMMANDS = {
    "pr-analyze",
    "pr-files",
    "pr-file",
    "pr-diff",
    "pr-comments",
    "pr-statuses",
    "pr-comment",
    "pr-inline-comment",
    "pr-reply",
    "pr-edit-comment",
    "pr-resolve",
    "pr-review-draft",
}

GITLAB_WORK_TRACKING_COMMANDS = {
    "show",
    "comments",
    "context",
    "start",
    "review",
    "testing",
    "handoff-to-qa",
    "comment",
    "cleanup-artifacts",
}


def uses_gitlab_review_provider(command: str, args) -> bool:
    url = getattr(args, "url", None)
    if command in URL_CAPABLE_REVIEW_COMMANDS and url and is_gitlab_merge_request_url(url):
        return True
    if command == "create-pr" and getattr(args, "provider", None) == "gitlab":
        return True
    if command == "pr-review-apply" and getattr(args, "provider", None) == "gitlab":
        return True
    return False


def hydrate_review_draft_provider(args) -> None:
    if args.command != "pr-review-apply":
        return
    draft = review_commands.load_review_draft(args.draft_file)
    draft_provider = draft.get("provider") or "azure-devops"
    if draft_provider not in {"azure-devops", "gitlab"}:
        raise CliError(f"ERROR: Unsupported review draft provider '{draft_provider}'.")
    if getattr(args, "provider", None) and args.provider != draft_provider:
        raise CliError(
            f"ERROR: Draft provider '{draft_provider}' does not match --provider '{args.provider}'."
        )
    args.provider = draft_provider


def uses_gitlab_work_tracking_provider(command: str, args) -> bool:
    return bool(command in GITLAB_WORK_TRACKING_COMMANDS and getattr(args, "provider", None) == "gitlab")


def resolve_command_token(args):
    if args.command in {"doctor", "profiles", "use"}:
        return None
    if uses_gitlab_review_provider(args.command, args) or uses_gitlab_work_tracking_provider(args.command, args):
        return GITLAB_TOKEN or ""
    return get_token()


def required_config_for_command(command: str, args) -> tuple[str, ...]:
    required = list(COMMAND_REQUIRED_CONFIG.get(command, BASE_COMMAND_REQUIRED_CONFIG))
    if uses_gitlab_review_provider(command, args) or uses_gitlab_work_tracking_provider(command, args):
        required = []
    if command in {"testing", "handoff-to-qa"} and not getattr(args, "qa", None):
        required.append("AZURE_DEVOPS_QA_USER")
    if command == "draft-items" and getattr(args, "assign", None) == "me":
        required.append("AZURE_DEVOPS_USER")
    if command == "team-members" and not getattr(args, "team", None):
        required.append("AZURE_DEVOPS_TEAM_ID")
    return tuple(dict.fromkeys(required))


def ensure_command_configuration(command: str, args) -> None:
    if uses_gitlab_work_tracking_provider(command, args) and not getattr(args, "repo", None):
        raise CliError(
            f"ERROR: GitLab command '{command}' requires an explicit --repo project path or id."
        )
    missing = missing_required_config(required_config_for_command(command, args))
    if not missing:
        return
    raise CliError(
        f"ERROR: Missing required configuration for '{command}': {', '.join(missing)}. "
        "Set the values in .env.local, .env, or your shell environment and rerun './sg doctor' for details."
    )


# ── CLI Setup ─────────────────────────────────────────────────────────────────

def main():
    prog_name = os.environ.get("SG_PROG_NAME") or Path(sys.argv[0]).name or "sg"
    parser = argparse.ArgumentParser(
        prog=prog_name,
        description="Singularity CLI for agent-assisted delivery workflows",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)
    work_item_commands.register_work_item_subcommands(sub)
    review_commands.register_review_subcommands(sub)
    build_commands.register_build_subcommands(sub)
    service_endpoint_commands.register_service_endpoint_subcommands(sub)
    profile_commands.register_profile_subcommands(sub)
    doctor_commands.register_doctor_subcommands(sub)

    args = parser.parse_args()

    handlers = {
        "sprint":      cmd_sprint,
        "list":        cmd_list,
        "ready-items": cmd_list,
        "pick-next":   cmd_pick_next,
        "teams":       cmd_teams,
        "team-members": cmd_team_members,
        "show":        cmd_show,
        "comments":    cmd_comments,
        "context":     cmd_context,
        "tree":        cmd_tree,
        "attachments": cmd_attachments,
        "introduced-by": cmd_introduced_by,
        "triage":      cmd_triage,
        "start":       cmd_start,
        "review":      cmd_review,
        "testing":     cmd_testing,
        "handoff-to-qa": cmd_handoff_to_qa,
        "create-pr":   cmd_create_pr,
        "comment":     cmd_comment,
        "draft-items": cmd_draft_items,
        "cleanup-artifacts": cmd_cleanup_artifacts,
        "repos":       cmd_repos,
        "pr-analyze":  cmd_pr_analyze,
        "pr-files":    cmd_pr_files,
        "pr-file":     cmd_pr_file,
        "pr-diff":     cmd_pr_diff,
        "pr-comments": cmd_pr_comments,
        "pr-statuses": cmd_pr_statuses,
        "pr-comment":  cmd_pr_comment,
        "pr-inline-comment": cmd_pr_inline_comment,
        "pr-reply":    cmd_pr_reply,
        "pr-edit-comment": cmd_pr_edit_comment,
        "pr-resolve":  cmd_pr_resolve,
        "pr-review-draft": cmd_pr_review_draft,
        "pr-review-apply": cmd_pr_review_apply,
        "builds":       cmd_builds,
        "build-status": cmd_build_status,
        "build-approvals": cmd_build_approvals,
        "approve-gate": cmd_approve_gate,
        "build-logs":   cmd_build_logs,
        "queue-build":  cmd_queue_build,
        "service-endpoints": cmd_service_endpoints,
        "service-endpoint-show": cmd_service_endpoint_show,
        "profiles":     cmd_profiles,
        "use":          cmd_use,
        "doctor":       cmd_doctor,
    }
    try:
        hydrate_review_draft_provider(args)
        ensure_command_configuration(args.command, args)
        token = resolve_command_token(args)
        handlers[args.command](args, token)
    except CliError as exc:
        print(exc)
        apply_plan_id = getattr(args, "apply", None)
        if plan_id_was_consumed(apply_plan_id):
            print(
                "WARNING: This Plan ID was consumed before the external operation completed. "
                "If the provider outcome is uncertain, inspect Azure DevOps or GitLab before "
                "previewing and approving a retry."
            )
        raise SystemExit(exc.exit_code)


if __name__ == "__main__":
    main()
