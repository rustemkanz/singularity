import difflib
import json
import re
import urllib.parse
from html import unescape

from app_config import API_VER, ORG, PROJECT
from errors import CliError
from git_client import current_git_branch
from providers.azure_devops.http import api, api_with_headers
from workflow_models import (
    ChangeRequest,
    ChangeRequestReviewer,
    ChangeRequestStatus,
    ReviewComment,
    ReviewThread,
)


RESOLVED_PR_THREAD_STATUSES = {"fixed", "wontfix", "bydesign", "closed"}
REVIEW_DRAFT_FORMAT_VERSION = 1


def project_base_url(project_name: str | None = None, org_name: str | None = None) -> str:
    return (
        f"https://dev.azure.com/{org_name or ORG}/"
        f"{urllib.parse.quote(project_name or PROJECT)}"
    )


def short_branch_name(branch_ref: str | None) -> str:
    if not branch_ref:
        return "?"
    prefix = "refs/heads/"
    return branch_ref[len(prefix):] if branch_ref.startswith(prefix) else branch_ref


def normalize_branch_ref(branch: str | None) -> str:
    branch_name = branch or current_git_branch()
    return branch_name if branch_name.startswith("refs/") else f"refs/heads/{branch_name}"


def fetch_pull_request(
    token: str,
    repo_id: str,
    pr_id: int,
    *,
    project_name: str | None = None,
    org_name: str | None = None,
) -> dict:
    url = (
        f"{project_base_url(project_name, org_name)}/_apis/git/repositories/{repo_id}/pullrequests/{pr_id}"
        f"?api-version={API_VER}"
    )
    return api(token, "GET", url)


def list_pull_requests(
    token: str,
    repo_id: str,
    source_ref_name: str | None = None,
    status: str = "all",
    *,
    project_name: str | None = None,
    org_name: str | None = None,
) -> list[dict]:
    url = (
        f"{project_base_url(project_name, org_name)}/_apis/git/repositories/{repo_id}/pullrequests"
        f"?searchCriteria.status={urllib.parse.quote(status)}"
        f"&api-version={API_VER}"
    )
    if source_ref_name:
        url += f"&searchCriteria.sourceRefName={urllib.parse.quote(source_ref_name, safe='')}"
    return api(token, "GET", url).get("value", [])


def resolve_pull_request(
    token: str,
    repo: dict,
    pr_id: int | None,
    source_branch: str | None,
    *,
    project_name: str | None = None,
    org_name: str | None = None,
) -> dict:
    if pr_id is not None:
        return fetch_pull_request(
            token,
            repo["id"],
            pr_id,
            project_name=project_name,
            org_name=org_name,
        )

    source_ref_name = normalize_branch_ref(source_branch)
    matches = list_pull_requests(
        token,
        repo["id"],
        source_ref_name=source_ref_name,
        status="all",
        project_name=project_name,
        org_name=org_name,
    )
    if not matches:
        raise CliError(
            f"ERROR: No pull request found in {repo['name']} for source branch "
            f"'{short_branch_name(source_ref_name)}'."
        )

    active_matches = [pr for pr in matches if pr.get("status") == "active"]
    candidates = active_matches or matches
    if len(candidates) > 1:
        message_lines = [
            f"ERROR: Multiple pull requests found in {repo['name']} for source branch "
            f"'{short_branch_name(source_ref_name)}'. Pass --pr to choose one explicitly."
        ]
        for pr in candidates:
            message_lines.append(
                f"  {pr.get('pullRequestId')}  {pr.get('status', '?'):<10}  "
                f"{pr.get('title', '<untitled>')}"
            )
        raise CliError("\n".join(message_lines))

    return candidates[0]


def pr_threads_url(
    repo_id: str,
    pr_id: int,
    thread_id: int | None = None,
    *,
    project_name: str | None = None,
    org_name: str | None = None,
) -> str:
    url = f"{project_base_url(project_name, org_name)}/_apis/git/repositories/{repo_id}/pullrequests/{pr_id}/threads"
    if thread_id is not None:
        url += f"/{thread_id}"
    return f"{url}?api-version={API_VER}"


def pr_statuses_url(
    repo_id: str,
    pr_id: int,
    *,
    project_name: str | None = None,
    org_name: str | None = None,
) -> str:
    return (
        f"{project_base_url(project_name, org_name)}/_apis/git/repositories/{repo_id}"
        f"/pullrequests/{pr_id}/statuses?api-version={API_VER}"
    )


def pr_thread_comments_url(
    repo_id: str,
    pr_id: int,
    thread_id: int,
    *,
    project_name: str | None = None,
    org_name: str | None = None,
) -> str:
    return (
        f"{project_base_url(project_name, org_name)}/_apis/git/repositories/{repo_id}/pullrequests/{pr_id}"
        f"/threads/{thread_id}/comments?api-version={API_VER}"
    )


def pr_thread_comment_url(
    repo_id: str,
    pr_id: int,
    thread_id: int,
    comment_id: int,
    *,
    project_name: str | None = None,
    org_name: str | None = None,
) -> str:
    return (
        f"{project_base_url(project_name, org_name)}/_apis/git/repositories/{repo_id}/pullrequests/{pr_id}"
        f"/threads/{thread_id}/comments/{comment_id}?api-version={API_VER}"
    )


def fetch_pr_threads(
    token: str,
    repo_id: str,
    pr_id: int,
    *,
    project_name: str | None = None,
    org_name: str | None = None,
) -> list[dict]:
    threads: list[dict] = []
    continuation_token: str | None = None
    while True:
        url = pr_threads_url(repo_id, pr_id, project_name=project_name, org_name=org_name)
        if continuation_token:
            url += f"&continuationToken={urllib.parse.quote(continuation_token, safe='')}"
        data, headers = api_with_headers(token, "GET", url)
        threads.extend(data.get("value", []))
        continuation_token = headers.get("x-ms-continuationtoken") or headers.get("X-MS-ContinuationToken")
        if not continuation_token:
            return threads


def fetch_pr_statuses(
    token: str,
    repo_id: str,
    pr_id: int,
    *,
    project_name: str | None = None,
    org_name: str | None = None,
) -> list[dict]:
    return api(
        token,
        "GET",
        pr_statuses_url(repo_id, pr_id, project_name=project_name, org_name=org_name),
    ).get("value", [])


def fetch_pr_thread(
    token: str,
    repo_id: str,
    pr_id: int,
    thread_id: int,
    *,
    project_name: str | None = None,
    org_name: str | None = None,
) -> dict:
    return api(token, "GET", pr_threads_url(repo_id, pr_id, thread_id, project_name=project_name, org_name=org_name))


def fetch_pr_comment(
    token: str,
    repo_id: str,
    pr_id: int,
    thread_id: int,
    comment_id: int,
    *,
    project_name: str | None = None,
    org_name: str | None = None,
) -> dict:
    return api(
        token,
        "GET",
        pr_thread_comment_url(
            repo_id,
            pr_id,
            thread_id,
            comment_id,
            project_name=project_name,
            org_name=org_name,
        ),
    )


def build_change_request(
    repo: dict,
    pr: dict,
    *,
    project_name: str | None = None,
    org_name: str | None = None,
) -> ChangeRequest:
    return ChangeRequest(
        id=pr.get("pullRequestId"),
        title=pr.get("title", "<untitled>"),
        status=pr.get("status", "?"),
        source_branch=short_branch_name(pr.get("sourceRefName")),
        target_branch=short_branch_name(pr.get("targetRefName")),
        author=(pr.get("createdBy") or {}).get("displayName") or "?",
        repo_name=repo.get("name", "?"),
        repo_id=repo.get("id"),
        api_url=pr.get("url") or "",
        browser_url=pr_browser_url(
            repo.get("name", "?"),
            pr.get("pullRequestId"),
            project_name=project_name,
            org_name=org_name,
        ) if pr.get("pullRequestId") is not None else "",
        provider="azure-devops",
    )


def fetch_pr_iterations(
    token: str,
    repo_id: str,
    pr_id: int,
    *,
    project_name: str | None = None,
    org_name: str | None = None,
) -> list[dict]:
    url = (
        f"{project_base_url(project_name, org_name)}/_apis/git/repositories/{repo_id}/pullrequests/{pr_id}/iterations"
        f"?api-version={API_VER}"
    )
    return api(token, "GET", url).get("value", [])


def latest_pr_iteration_id(iterations: list[dict]) -> int:
    return max((iteration.get("id", 0) for iteration in iterations), default=1)


def fetch_pr_iteration_changes(
    token: str,
    repo_id: str,
    pr_id: int,
    iteration_id: int,
    *,
    project_name: str | None = None,
    org_name: str | None = None,
    page_size: int = 2000,
) -> list[dict]:
    changes: list[dict] = []
    skip = 0
    while True:
        url = (
            f"{project_base_url(project_name, org_name)}/_apis/git/repositories/{repo_id}/pullrequests/{pr_id}"
            f"/iterations/{iteration_id}/changes?api-version={API_VER}&$top={page_size}&$skip={skip}"
        )
        data = api(token, "GET", url)
        page = data.get("changeEntries") or data.get("value") or []
        if not page:
            return changes
        changes.extend(page)
        total = data.get("count") or len(page)
        if len(page) < page_size or skip + len(page) >= total:
            return changes
        skip += len(page)


def parse_azure_devops_pr_url(pr_url: str) -> dict:
    parsed = urllib.parse.urlparse(pr_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise CliError(f"ERROR: Invalid PR URL '{pr_url}'.")

    path_parts = [urllib.parse.unquote(part) for part in parsed.path.split("/") if part]
    if parsed.netloc == "dev.azure.com":
        if len(path_parts) < 6 or path_parts[2] != "_git" or path_parts[4].lower() != "pullrequest":
            raise CliError(f"ERROR: Unsupported Azure DevOps PR URL '{pr_url}'.")
        org_name, project_name, _, repo_ref, _, pr_id_text = path_parts[:6]
    elif parsed.netloc.endswith(".visualstudio.com"):
        if len(path_parts) < 5 or path_parts[1] != "_git" or path_parts[3].lower() != "pullrequest":
            raise CliError(f"ERROR: Unsupported Azure DevOps PR URL '{pr_url}'.")
        org_name = parsed.netloc.split(".", 1)[0]
        project_name, _, repo_ref, _, pr_id_text = path_parts[:5]
    else:
        raise CliError(f"ERROR: Unsupported PR host '{parsed.netloc}'.")

    try:
        pr_id = int(pr_id_text)
    except ValueError:
        raise CliError(f"ERROR: Could not parse pull request id from '{pr_url}'.")

    return {
        "org": org_name,
        "project": project_name,
        "repo": repo_ref,
        "pr_id": pr_id,
    }


def normalize_repo_path(path: str) -> str:
    normalized = (path or "").strip()
    if not normalized:
        raise CliError("ERROR: File path cannot be empty.")
    return normalized if normalized.startswith("/") else f"/{normalized}"


def repo_item_url(
    repo_id: str,
    path: str,
    *,
    version: str,
    version_type: str = "branch",
    include_content: bool = True,
    project_name: str | None = None,
    org_name: str | None = None,
) -> str:
    query = urllib.parse.urlencode({
        "path": normalize_repo_path(path),
        "versionDescriptor.versionType": version_type,
        "versionDescriptor.version": version,
        "includeContent": "true" if include_content else "false",
        "resolveLfs": "true",
        "api-version": API_VER,
    })
    return f"{project_base_url(project_name, org_name)}/_apis/git/repositories/{repo_id}/items?{query}"


def fetch_repo_text_item(
    token: str,
    repo_id: str,
    path: str,
    *,
    version: str,
    version_type: str = "branch",
    project_name: str | None = None,
    org_name: str | None = None,
) -> str | None:
    item, _ = api_with_headers(
        token,
        "GET",
        repo_item_url(
            repo_id,
            path,
            version=version,
            version_type=version_type,
            include_content=True,
            project_name=project_name,
            org_name=org_name,
        ),
        allowed_status_codes={404},
    )
    if not item:
        return None
    return item.get("content") or ""


def serialize_pr_change(change: dict) -> dict:
    item = change.get("item") or {}
    return {
        "path": item.get("path") or item.get("serverItem") or item.get("objectId") or "<unknown>",
        "changeType": change.get("changeType", "unknown"),
    }


def summarize_pr_changes(changes: list[dict]) -> tuple[list[dict], dict[str, int]]:
    files = [serialize_pr_change(change) for change in changes]
    files.sort(key=lambda file_entry: file_entry["path"])
    stats: dict[str, int] = {}
    for file_entry in files:
        stats[file_entry["changeType"]] = stats.get(file_entry["changeType"], 0) + 1
    return files, stats


def collect_pr_change_context(
    token: str,
    repo: dict,
    pr: dict,
    *,
    project_name: str | None = None,
    org_name: str | None = None,
) -> dict:
    iteration_id = latest_pr_iteration_id(
        fetch_pr_iterations(
            token,
            repo["id"],
            pr["pullRequestId"],
            project_name=project_name,
            org_name=org_name,
        )
    )
    changes = fetch_pr_iteration_changes(
        token,
        repo["id"],
        pr["pullRequestId"],
        iteration_id,
        project_name=project_name,
        org_name=org_name,
    )
    files, stats = summarize_pr_changes(changes)
    return {
        "iterationId": iteration_id,
        "changes": changes,
        "files": files,
        "stats": stats,
    }


def find_pr_change_for_path(changes: list[dict], path: str) -> dict | None:
    normalized_path = normalize_repo_path(path)
    for change in changes:
        if serialize_pr_change(change)["path"] == normalized_path:
            return change
    return None


def fetch_pr_file_content(
    token: str,
    repo: dict,
    pr: dict,
    path: str,
    *,
    version: str = "source",
    project_name: str | None = None,
    org_name: str | None = None,
) -> str | None:
    if version not in ("source", "target"):
        raise CliError(f"ERROR: Unsupported PR file version '{version}'.")
    ref_name = pr["sourceRefName"] if version == "source" else pr["targetRefName"]
    return fetch_repo_text_item(
        token,
        repo["id"],
        normalize_repo_path(path),
        version=short_branch_name(ref_name),
        project_name=project_name,
        org_name=org_name,
    )


def build_unified_diff(path: str, target_content: str | None, source_content: str | None, *, context_lines: int = 3) -> str:
    normalized_path = normalize_repo_path(path)
    target_lines = (target_content or "").splitlines()
    source_lines = (source_content or "").splitlines()
    return "\n".join(
        difflib.unified_diff(
            target_lines,
            source_lines,
            fromfile=f"a{normalized_path}",
            tofile=f"b{normalized_path}",
            lineterm="",
            n=context_lines,
        )
    )


def build_pr_thread_payload(text: str) -> dict:
    return {
        "comments": [{
            "parentCommentId": 0,
            "content": text,
            "commentType": "text",
        }],
        "status": "active",
    }


def build_inline_thread_payload(
    text: str,
    file_path: str,
    start_line: int,
    *,
    end_line: int | None = None,
    start_offset: int = 1,
    end_offset: int | None = None,
    iteration_id: int | None = None,
    change_tracking_id: int | None = None,
) -> dict:
    final_end_line = end_line or start_line
    final_end_offset = end_offset if end_offset is not None else start_offset
    payload = build_pr_thread_payload(text)
    payload["threadContext"] = {
        "filePath": normalize_repo_path(file_path),
        "rightFileStart": {"line": start_line, "offset": start_offset},
        "rightFileEnd": {"line": final_end_line, "offset": final_end_offset},
    }
    if iteration_id is not None and change_tracking_id is not None:
        payload["pullRequestThreadContext"] = {
            "changeTrackingId": change_tracking_id,
            "iterationContext": {
                "firstComparingIteration": iteration_id,
                "secondComparingIteration": iteration_id,
            },
        }
    return payload


def create_pr_thread(
    token: str,
    repo: dict,
    pr: dict,
    payload: dict,
    *,
    project_name: str | None = None,
    org_name: str | None = None,
) -> dict:
    return api(
        token,
        "POST",
        pr_threads_url(
            repo["id"],
            pr["pullRequestId"],
            project_name=project_name,
            org_name=org_name,
        ),
        payload,
    )


def post_pr_comment_thread(
    token: str,
    repo: dict,
    pr: dict,
    text: str,
    *,
    project_name: str | None = None,
    org_name: str | None = None,
) -> dict:
    return create_pr_thread(
        token,
        repo,
        pr,
        build_pr_thread_payload(text),
        project_name=project_name,
        org_name=org_name,
    )


def post_pr_inline_comment_thread(
    token: str,
    repo: dict,
    pr: dict,
    change_context: dict,
    text: str,
    file_path: str,
    start_line: int,
    *,
    end_line: int | None = None,
    start_offset: int = 1,
    end_offset: int | None = None,
    project_name: str | None = None,
    org_name: str | None = None,
) -> dict:
    change = find_pr_change_for_path(change_context["changes"], file_path)
    if not change:
        raise CliError(f"ERROR: File '{normalize_repo_path(file_path)}' is not part of the PR diff.")
    payload = build_inline_thread_payload(
        text,
        file_path,
        start_line,
        end_line=end_line,
        start_offset=start_offset,
        end_offset=end_offset,
        iteration_id=change_context["iterationId"],
        change_tracking_id=change.get("changeTrackingId"),
    )
    return create_pr_thread(
        token,
        repo,
        pr,
        payload,
        project_name=project_name,
        org_name=org_name,
    )


def post_pr_reply(
    token: str,
    repo: dict,
    pr: dict,
    thread: dict,
    text: str,
    *,
    parent_comment_id: int,
    project_name: str | None = None,
    org_name: str | None = None,
) -> dict:
    payload = {
        "parentCommentId": parent_comment_id,
        "content": text,
        "commentType": "text",
    }
    return api(
        token,
        "POST",
        pr_thread_comments_url(
            repo["id"],
            pr["pullRequestId"],
            thread["id"],
            project_name=project_name,
            org_name=org_name,
        ),
        payload,
    )


def update_pr_comment_text(
    token: str,
    repo: dict,
    pr: dict,
    thread: dict,
    comment: dict,
    text: str,
    *,
    project_name: str | None = None,
    org_name: str | None = None,
) -> dict:
    payload = {
        "parentCommentId": comment.get("parentCommentId", 0),
        "content": text,
        "commentType": comment.get("commentType") or "text",
    }
    return api(
        token,
        "PATCH",
        pr_thread_comment_url(
            repo["id"],
            pr["pullRequestId"],
            thread["id"],
            comment["id"],
            project_name=project_name,
            org_name=org_name,
        ),
        payload,
        content_type="application/json",
    )


def update_pr_thread_status(
    token: str,
    repo: dict,
    pr: dict,
    thread: dict,
    status: str,
    *,
    project_name: str | None = None,
    org_name: str | None = None,
) -> dict:
    return api(
        token,
        "PATCH",
        pr_threads_url(
            repo["id"],
            pr["pullRequestId"],
            thread["id"],
            project_name=project_name,
            org_name=org_name,
        ),
        {"status": status},
        content_type="application/json",
    )


def _render_html_text(text: str) -> str:
    rendered = text or ""
    rendered = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", rendered)
    rendered = re.sub(r"(?i)</\s*(p|div|li|tr|h[1-6])\s*>", "\n", rendered)
    rendered = re.sub(r"(?i)<\s*li\b[^>]*>", "- ", rendered)
    rendered = re.sub(r"<[^>]+>", "", rendered)
    rendered = unescape(rendered)
    rendered = rendered.replace("\r", "")
    rendered = re.sub(r"[ \t]+\n", "\n", rendered)
    rendered = re.sub(r"\n{3,}", "\n\n", rendered)
    return rendered.strip()


def thread_status_name(thread: dict) -> str:
    return thread.get("status") or "unknown"


def thread_is_resolved(thread: dict) -> bool:
    return thread_status_name(thread).replace("-", "").lower() in RESOLVED_PR_THREAD_STATUSES


def thread_line_number(thread: dict) -> int | None:
    context = thread.get("threadContext") or {}
    for key in ("rightFileStart", "leftFileStart"):
        position = context.get(key) or {}
        if position.get("line") is not None:
            return position["line"]
    return None


def format_thread_location(thread: dict) -> str | None:
    context = thread.get("threadContext") or {}
    file_path = context.get("filePath")
    if not file_path:
        return None
    line = thread_line_number(thread)
    return f"{file_path}:{line}" if line is not None else file_path


def visible_thread_comments(thread: dict) -> list[dict]:
    visible: list[dict] = []
    for comment in thread.get("comments", []):
        if comment.get("commentType") == "system":
            continue
        text = _render_html_text(comment.get("content") or "")
        if not text:
            continue
        visible.append({**comment, "_renderedContent": text})
    return visible


def default_reply_parent_comment_id(thread: dict) -> int:
    comments = visible_thread_comments(thread)
    if not comments:
        return 0
    return comments[0].get("id") or 0


def serialize_pr_comment(comment: dict) -> ReviewComment:
    return ReviewComment(
        comment_id=comment.get("id"),
        parent_comment_id=comment.get("parentCommentId"),
        author=(comment.get("author") or {}).get("displayName", "?"),
        content=comment.get("_renderedContent") or _render_html_text(comment.get("content") or ""),
        published_date=comment.get("publishedDate"),
        last_updated_date=comment.get("lastUpdatedDate"),
    )


def serialize_pr_thread(thread: dict) -> ReviewThread:
    context = thread.get("threadContext") or {}
    comments = [serialize_pr_comment(comment) for comment in visible_thread_comments(thread)]
    return ReviewThread(
        thread_id=thread.get("id"),
        status=thread_status_name(thread),
        is_deleted=bool(thread.get("isDeleted")),
        file_path=context.get("filePath"),
        line=thread_line_number(thread),
        location=format_thread_location(thread),
        comments=comments,
    )


def collect_pr_review_threads(threads: list[dict], *, unresolved_only: bool = False) -> list[ReviewThread]:
    collected: list[ReviewThread] = []
    for thread in threads:
        if thread.get("isDeleted"):
            continue
        if unresolved_only and thread_is_resolved(thread):
            continue
        serialized = serialize_pr_thread(thread)
        if not serialized.comments:
            continue
        collected.append(serialized)
    return collected


def serialize_pr_summary(
    repo: dict,
    pr: dict,
    *,
    project_name: str | None = None,
    org_name: str | None = None,
) -> dict:
    return build_change_request(
        repo,
        pr,
        project_name=project_name,
        org_name=org_name,
    ).to_summary_dict()


def reviewer_vote_label(vote: int | None) -> str:
    return {
        10: "approved",
        5: "approvedWithSuggestions",
        0: "noVote",
        -5: "waitingForAuthor",
        -10: "rejected",
    }.get(vote, str(vote if vote is not None else "unknown"))


def collect_pr_reviewers(pr: dict) -> list[dict]:
    reviewers: list[ChangeRequestReviewer] = []
    for reviewer in pr.get("reviewers", []):
        reviewers.append(ChangeRequestReviewer(
            name=reviewer.get("displayName") or reviewer.get("uniqueName") or "(unknown)",
            vote=reviewer.get("vote"),
            vote_label=reviewer_vote_label(reviewer.get("vote")),
            is_required=bool(reviewer.get("isRequired")),
        ))
    return reviewers


def serialize_pr_status(status: dict) -> ChangeRequestStatus:
    context = status.get("context") or {}
    return ChangeRequestStatus(
        id=status.get("id"),
        state=status.get("state") or "unknown",
        description=status.get("description") or "",
        context_name=context.get("name") or "",
        context_kind=context.get("genre") or "",
        target_url=status.get("targetUrl") or "",
        created_by=(status.get("createdBy") or {}).get("displayName") or "",
        creation_date=status.get("creationDate"),
        updated_date=status.get("updatedDate"),
    )


def flatten_pr_review_threads(review_threads: list[ReviewThread]) -> list[dict]:
    flattened: list[dict] = []
    for thread in review_threads:
        for comment in thread.comments:
            flattened.append({
                "threadId": thread.thread_id,
                "threadStatus": thread.status,
                "commentId": comment.comment_id,
                "file": thread.file_path,
                "line": thread.line,
                "author": comment.author,
                "publishedDate": comment.published_date,
                "content": comment.content,
            })
    return flattened


def build_review_draft(org_name: str, project_name: str, repo: dict, pr: dict, analysis: dict) -> dict:
    return {
        "formatVersion": REVIEW_DRAFT_FORMAT_VERSION,
        "organization": org_name,
        "project": project_name,
        "repo": {"name": repo.get("name"), "id": repo.get("id")},
        "pullRequest": {
            **serialize_pr_summary(repo, pr, project_name=project_name, org_name=org_name),
            "browserUrl": analysis["browserUrl"],
        },
        "analysis": {
            "changeSummary": analysis["changeSummary"],
            "files": analysis["files"],
            "existingComments": analysis["existingComments"],
        },
        "draftComments": [],
    }


def load_review_draft(file_path: str) -> dict:
    with open(file_path, "r", encoding="utf-8") as handle:
        draft = json.load(handle)
    if draft.get("formatVersion") != REVIEW_DRAFT_FORMAT_VERSION:
        raise CliError(
            f"ERROR: Unsupported review draft format version '{draft.get('formatVersion')}'. "
            f"Expected {REVIEW_DRAFT_FORMAT_VERSION}."
        )
    if not isinstance(draft.get("draftComments"), list):
        raise CliError("ERROR: Review draft must contain a draftComments array.")
    return draft


def pr_browser_url(repo_name: str, pr_id: int, *, project_name: str | None = None, org_name: str | None = None) -> str:
    return (
        f"https://dev.azure.com/{org_name or ORG}/"
        f"{urllib.parse.quote(project_name or PROJECT)}/_git/"
        f"{urllib.parse.quote(repo_name)}/pullrequest/{pr_id}"
    )


def collect_pr_analysis_bundle(token: str, org_name: str, project_name: str, repo: dict, pr: dict) -> dict:
    change_context = collect_pr_change_context(
        token,
        repo,
        pr,
        project_name=project_name,
        org_name=org_name,
    )
    review_threads = collect_pr_review_threads(
        fetch_pr_threads(
            token,
            repo["id"],
            pr["pullRequestId"],
            project_name=project_name,
            org_name=org_name,
        )
    )
    existing_comments = flatten_pr_review_threads(review_threads)
    browser_url = pr_browser_url(
        repo["name"],
        pr["pullRequestId"],
        project_name=project_name,
        org_name=org_name,
    )
    return {
        "browserUrl": browser_url,
        "changeSummary": {
            "iteration": change_context["iterationId"],
            "count": len(change_context["files"]),
            "byType": change_context["stats"],
        },
        "files": change_context["files"],
        "changes": change_context["changes"],
        "existingComments": existing_comments,
        "reviewThreads": [thread.to_legacy_dict() for thread in review_threads],
    }


def print_pr_summary(repo: dict, pr: dict):
    change_request = build_change_request(repo, pr)
    print(f"PR {change_request.id}: {change_request.title}")
    print(f"  Repo   : {change_request.repo_name}")
    print(f"  Status : {change_request.status}")
    print(f"  Source : {change_request.source_branch}")
    print(f"  Target : {change_request.target_branch}\n")
