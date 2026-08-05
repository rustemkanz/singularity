import argparse
import difflib
import json
import os

from app_config import DEFAULT_REPO, ORG, PROJECT
from errors import CliError
from git_client import infer_git_repository_ref


REVIEW_DRAFT_FORMAT_VERSION = 1


def _require_provider(factory, provider_label: str):
    if factory is None:
        raise RuntimeError(f"Missing {provider_label} provider factory.")
    return factory


def normalize_repo_path(path: str) -> str:
    if not path:
        return "/"
    return path if path.startswith("/") else f"/{path}"


def serialize_pr_summary(change_request) -> dict:
    return change_request.to_summary_dict()


def pr_browser_url(change_request) -> str:
    return change_request.browser_url


def print_pr_summary(change_request) -> None:
    print(f"PR {change_request.id}: {change_request.title}")
    print(f"  Repo   : {change_request.repo_name}")
    print(f"  Status : {change_request.status}")
    print(f"  Source : {change_request.source_branch}")
    print(f"  Target : {change_request.target_branch}\n")


def build_unified_diff(path: str, target_content: str | None, source_content: str | None, *, context_lines: int) -> str:
    target_lines = [] if target_content is None else target_content.splitlines(keepends=True)
    source_lines = [] if source_content is None else source_content.splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(
            target_lines,
            source_lines,
            fromfile=f"a{normalize_repo_path(path)}",
            tofile=f"b{normalize_repo_path(path)}",
            n=context_lines,
        )
    )


def load_review_draft(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as handle:
            draft = json.load(handle)
    except FileNotFoundError as exc:
        raise CliError(f"ERROR: Review draft file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise CliError(f"ERROR: Review draft file is not valid JSON: {exc}") from exc
    if draft.get("formatVersion") != REVIEW_DRAFT_FORMAT_VERSION:
        raise CliError(
            f"Unsupported review draft format version {draft.get('formatVersion')!r}; expected {REVIEW_DRAFT_FORMAT_VERSION}."
        )
    return draft


def truncate_text(text: str, limit: int = 1000) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def list_repositories(
    token: str,
    project_name: str | None = None,
    org_name: str | None = None,
    *,
    build_review_provider_func=None,
) -> list[dict]:
    provider = _require_provider(build_review_provider_func, "review")(token)
    return [
        {
            "id": repo.id,
            "name": repo.name,
            "project": repo.project,
        }
        for repo in provider.list_repositories()
    ]


def match_repository(repos: list[dict], repo_ref: str | None) -> dict | None:
    if repo_ref is None:
        return None
    for repo in repos:
        if repo_ref in (repo.get("id"), repo.get("name")):
            return repo
    return None


def repository_resolution_candidates(
    repo_ref: str | None,
    *,
    infer_git_repository_ref_func=None,
    default_repo: str | None = None,
) -> list[tuple[str, str]]:
    if repo_ref is not None:
        return [(repo_ref, "--repo")]

    candidates: list[tuple[str, str]] = []
    infer_repo = infer_git_repository_ref_func or infer_git_repository_ref
    current_repo_ref = infer_repo()
    if current_repo_ref:
        candidates.append((current_repo_ref, "current git repo"))

    fallback_repo = DEFAULT_REPO if default_repo is None else default_repo
    if fallback_repo and fallback_repo not in {candidate for candidate, _source in candidates}:
        candidates.append((fallback_repo, "AZURE_DEVOPS_DEFAULT_REPO"))
    return candidates


def resolve_repository(
    token: str,
    repo_ref: str | None,
    *,
    project_name: str | None = None,
    org_name: str | None = None,
    list_repositories_func=None,
    infer_git_repository_ref_func=None,
    default_repo: str | None = None,
    build_review_provider_func=None,
) -> dict:
    list_repositories_impl = list_repositories_func or (
        lambda token_value, project_name=None, org_name=None: list_repositories(
            token_value,
            project_name=project_name,
            org_name=org_name,
            build_review_provider_func=build_review_provider_func,
        )
    )
    repos = list_repositories_impl(token, project_name=project_name, org_name=org_name)
    if not repos:
        raise CliError("No repositories found in the configured ADO project.")

    candidates = repository_resolution_candidates(
        repo_ref,
        infer_git_repository_ref_func=infer_git_repository_ref_func,
        default_repo=default_repo,
    )
    for candidate, _source in candidates:
        repo = match_repository(repos, candidate)
        if repo:
            return repo

    if repo_ref is not None:
        raise CliError(f"ERROR: Repository '{repo_ref}' not found. Use './sg repos' to list options.")

    if len(repos) == 1:
        return repos[0]

    if candidates:
        attempted = ", ".join(f"{source} '{candidate}'" for candidate, source in candidates)
        raise CliError(
            f"ERROR: Could not resolve a repository from {attempted}. "
            "Pass --repo with repo name or repo id, or set AZURE_DEVOPS_DEFAULT_REPO."
        )

    raise CliError(
        "ERROR: Multiple repositories found. Pass --repo with repo name or repo id, "
        "or set AZURE_DEVOPS_DEFAULT_REPO."
    )


def resolve_pr_context(
    token: str,
    args,
    *,
    build_review_provider_func=None,
):
    provider = _require_provider(build_review_provider_func, "review")(token)
    return provider.resolve_review_context(
        repo_ref=args.repo,
        change_request_id=args.pr,
        source_branch=args.source,
        url=getattr(args, "url", None),
    )


def resolve_pr_analysis_context(
    token: str,
    args,
    *,
    build_review_provider_func=None,
):
    if args.url and any(value is not None for value in (args.repo, args.pr, args.source)):
        raise CliError("ERROR: Use either --url or --repo/--pr/--source, not both.")
    return resolve_pr_context(token, args, build_review_provider_func=build_review_provider_func)


def cmd_repos(args, token, *, build_review_provider_func=None):
    provider = _require_provider(build_review_provider_func, "review")(token)
    repositories = provider.list_repositories()
    if args.json:
        print(json.dumps({
            "repositories": [
                {
                    "id": repo.id,
                    "name": repo.name,
                    "project": repo.project,
                }
                for repo in repositories
            ],
        }, indent=2))
        return
    print("\nRepositories:\n")
    for repo in repositories:
        print(f"  {repo.id}  {repo.name}")
    print()


def cmd_create_pr(args, token, *, build_review_provider_func=None):
    provider = _require_provider(build_review_provider_func, "review")(token)
    repository, payload = provider.prepare_change_request(
        work_item_id=args.id,
        repo_ref=args.repo,
        source_branch=args.source,
        target_branch=args.target,
        title=args.title,
        description=args.description,
        work_item_title=args.work_item_title,
    )

    if args.dry_run:
        print("Dry run: create PR payload")
        print(json.dumps({
            "repository": {"id": repository.id, "name": repository.name},
            "payload": payload,
        }, indent=2))
        return

    change_request = provider.create_change_request(
        work_item_id=args.id,
        repo_ref=args.repo,
        source_branch=args.source,
        target_branch=args.target,
        title=args.title,
        description=args.description,
        work_item_title=args.work_item_title,
    )
    print(f"✓ Pull request created in {change_request.repo_name}.")
    print(f"  PR ID   : {change_request.id}")
    print(f"  Title   : {change_request.title}")
    print(f"  Status  : {change_request.status}")
    print(f"  URL     : {change_request.api_url}")


def cmd_prepare_review(
    args,
    token,
    *,
    build_review_provider_func=None,
    build_work_tracking_provider_func=None,
):
    review_provider = _require_provider(build_review_provider_func, "review")(token)
    repository, payload = review_provider.prepare_change_request(
        work_item_id=args.id,
        repo_ref=args.repo,
        source_branch=args.source,
        target_branch=args.target,
        title=args.title,
        description=args.description,
        work_item_title=args.work_item_title,
    )

    if args.dry_run:
        print("Dry run: prepare-review payload")
        print(json.dumps({
            "repository": {"id": repository.id, "name": repository.name},
            "pull_request": payload,
            "work_item_transition": {
                "id": args.id,
                "state": "In Review",
            },
        }, indent=2))
        return

    change_request = review_provider.create_change_request(
        work_item_id=args.id,
        repo_ref=args.repo,
        source_branch=args.source,
        target_branch=args.target,
        title=args.title,
        description=args.description,
        work_item_title=args.work_item_title,
    )
    work_tracking_provider = _require_provider(build_work_tracking_provider_func, "work tracking")(token)
    actual_state = work_tracking_provider.transition_work_item(item_id=args.id, state="In Review")
    rendered_state = actual_state if isinstance(actual_state, str) and actual_state else "In Review"
    print(f"✓ Pull request created in {change_request.repo_name} and work item {args.id} moved to '{rendered_state}'.")
    print(f"  PR ID   : {change_request.id}")
    print(f"  Title   : {change_request.title}")
    print(f"  Status  : {change_request.status}")
    print(f"  URL     : {change_request.api_url}")


def cmd_pr_analyze(args, token, *, build_review_provider_func=None):
    provider = _require_provider(build_review_provider_func, "review")(token)
    context = provider.resolve_review_context(
        repo_ref=args.repo,
        change_request_id=args.pr,
        source_branch=args.source,
        url=args.url,
    )
    analysis = provider.analyze_change_request(context)
    change_request = context.change_request
    reviewers = analysis.reviewers

    if args.json:
        print(json.dumps(analysis.to_legacy_dict(), indent=2))
        return

    print(f"PR {change_request.id}: {change_request.title}")
    print(f"  Repo      : {change_request.repo_name}")
    print(f"  Project   : {context.project}")
    print(f"  Status    : {change_request.status}")
    print(f"  Source    : {change_request.source_branch}")
    print(f"  Target    : {change_request.target_branch}")
    print(f"  Created by: {change_request.author}")
    print(f"  Browser   : {change_request.browser_url}\n")

    print("Reviewers:")
    if not reviewers:
        print("  (none)")
    else:
        for reviewer in reviewers:
            required = " required" if reviewer.is_required else ""
            print(f"  - {reviewer.name} [{reviewer.vote_label}]{required}")

    print("Changes:")
    print(f"  Iteration : {analysis.change_summary.iteration}")
    print(f"  Files     : {analysis.change_summary.count}")
    for change_type, count in sorted(analysis.change_summary.by_type.items()):
        print(f"  {change_type:<9}: {count}")

    print("\nFiles:")
    if not analysis.files:
        print("  (none)")
    else:
        for file_entry in analysis.files:
            print(f"  - [{file_entry.change_type}] {file_entry.path}")

    print("\nExisting non-system comments:")
    if not analysis.existing_comments:
        print("  (none)")
        return

    for comment in analysis.existing_comments:
        location = comment.file_path or "PR"
        if comment.line is not None:
            location = f"{location}:{comment.line}"
        print(
            f"  - thread={comment.thread_id} comment={comment.comment_id} "
            f"[{comment.thread_status}] {location} {comment.author}:"
        )
        print(f"    {truncate_text(comment.content, 500)}")


def cmd_pr_files(args, token, *, build_review_provider_func=None):
    provider = _require_provider(build_review_provider_func, "review")(token)
    context = provider.resolve_review_context(
        repo_ref=args.repo,
        change_request_id=args.pr,
        source_branch=args.source,
        url=args.url,
    )
    analysis = provider.analyze_change_request(context)
    change_request = context.change_request

    if args.json:
        print(json.dumps({
            "pullRequest": {
                **serialize_pr_summary(change_request),
                "browserUrl": pr_browser_url(change_request),
            },
            "changeSummary": analysis.change_summary.to_legacy_dict(),
            "files": [file_change.to_legacy_dict() for file_change in analysis.files],
        }, indent=2))
        return

    print_pr_summary(change_request)
    print(f"Iteration: {analysis.change_summary.iteration}")
    print(f"Files    : {analysis.change_summary.count}")
    for change_type, count in sorted(analysis.change_summary.by_type.items()):
        print(f"  {change_type:<9}: {count}")
    print()
    for file_entry in analysis.files:
        print(f"[{file_entry.change_type}] {file_entry.path}")


def cmd_pr_file(args, token, *, build_review_provider_func=None):
    provider = _require_provider(build_review_provider_func, "review")(token)
    context = provider.resolve_review_context(
        repo_ref=args.repo,
        change_request_id=args.pr,
        source_branch=args.source,
        url=args.url,
    )
    content = provider.get_change_request_file_content(context, file_path=args.path, version=args.version)
    if content is None:
        raise CliError(
            f"ERROR: File '{normalize_repo_path(args.path)}' does not exist in the "
            f"{args.version} branch of PR {context.change_request.id}."
        )

    lines = content.splitlines()
    if not lines:
        return

    start_line = args.start_line or 1
    end_line = args.end_line or len(lines)
    if start_line < 1 or end_line < start_line:
        raise CliError("ERROR: Invalid line range.")

    selected_lines = lines[start_line - 1:end_line]
    if args.number_lines:
        for index, line in enumerate(selected_lines, start=start_line):
            print(f"{index:>6}  {line}")
        return
    print("\n".join(selected_lines))


def cmd_pr_diff(args, token, *, build_review_provider_func=None):
    provider = _require_provider(build_review_provider_func, "review")(token)
    context = provider.resolve_review_context(
        repo_ref=args.repo,
        change_request_id=args.pr,
        source_branch=args.source,
        url=args.url,
    )
    target_content = provider.get_change_request_file_content(context, file_path=args.path, version="target")
    source_content = provider.get_change_request_file_content(context, file_path=args.path, version="source")
    if target_content is None and source_content is None:
        raise CliError(f"ERROR: File '{normalize_repo_path(args.path)}' does not exist in either PR branch.")

    diff_text = build_unified_diff(args.path, target_content, source_content, context_lines=args.context)
    if not diff_text:
        print(f"No diff for {normalize_repo_path(args.path)}.")
        return
    print(diff_text)


def cmd_pr_comments(args, token, *, build_review_provider_func=None):
    provider = _require_provider(build_review_provider_func, "review")(token)
    context = provider.resolve_review_context(
        repo_ref=args.repo,
        change_request_id=args.pr,
        source_branch=args.source,
        url=args.url,
    )
    review_threads = provider.list_review_threads(
        context,
        unresolved_only=args.unresolved_only,
    )

    if args.json:
        print(json.dumps({
            "pullRequest": context.change_request.to_summary_dict(),
            "threads": [thread.to_legacy_dict() for thread in review_threads],
        }, indent=2))
        return

    change_request = context.change_request
    print(f"PR {change_request.id}: {change_request.title}")
    print(f"  Repo   : {change_request.repo_name}")
    print(f"  Status : {change_request.status}")
    print(f"  Source : {change_request.source_branch}")
    print(f"  Target : {change_request.target_branch}\n")
    if not review_threads:
        label = "unresolved review comments" if args.unresolved_only else "review comments"
        print(f"No {label} found.")
        return

    for thread in review_threads:
        for comment in thread.comments:
            prefix = f"[{thread.status}] thread={thread.thread_id} comment={comment.comment_id}"
            if thread.location:
                prefix += f" {thread.location}"
            print(f"{prefix} {comment.author}:")
            print(f"  {truncate_text(comment.content, 500)}\n")


def cmd_pr_comment(args, token, *, build_review_provider_func=None):
    provider = _require_provider(build_review_provider_func, "review")(token)
    context = provider.resolve_review_context(
        repo_ref=args.repo,
        change_request_id=args.pr,
        source_branch=args.source,
        url=args.url,
    )
    preview = provider.prepare_review_comment(context, text=args.text)
    if args.dry_run:
        print("Dry run: create PR thread payload")
        print(json.dumps({
            "pullRequest": context.change_request.to_summary_dict(),
            "payload": preview.payload,
        }, indent=2))
        return

    result = provider.create_review_comment(context, text=args.text)
    print(
        f"✓ Comment thread {result.thread_id} created on PR {context.change_request.id} "
        f"in {context.change_request.repo_name}."
    )


def cmd_pr_inline_comment(args, token, *, build_review_provider_func=None):
    provider = _require_provider(build_review_provider_func, "review")(token)
    context = provider.resolve_review_context(
        repo_ref=args.repo,
        change_request_id=args.pr,
        source_branch=args.source,
        url=args.url,
    )
    preview = provider.prepare_inline_review_comment(
        context,
        text=args.text,
        file_path=args.path,
        line=args.line,
        end_line=args.end_line,
        start_offset=args.start_offset,
        end_offset=args.end_offset,
    )
    if args.dry_run:
        print("Dry run: create inline PR thread payload")
        print(json.dumps({
            "pullRequest": context.change_request.to_summary_dict(),
            "payload": preview.payload,
        }, indent=2))
        return

    result = provider.create_inline_review_comment(
        context,
        text=args.text,
        file_path=args.path,
        line=args.line,
        end_line=args.end_line,
        start_offset=args.start_offset,
        end_offset=args.end_offset,
    )
    print(
        f"✓ Inline thread {result.thread_id} created on PR {context.change_request.id} "
        f"for {normalize_repo_path(args.path)}:{args.line}."
    )


def cmd_pr_reply(args, token, *, build_review_provider_func=None):
    provider = _require_provider(build_review_provider_func, "review")(token)
    context = provider.resolve_review_context(
        repo_ref=args.repo,
        change_request_id=args.pr,
        source_branch=args.source,
        url=args.url,
    )
    preview = provider.prepare_review_reply(
        context,
        thread_id=args.thread,
        text=args.text,
        parent_comment_id=args.parent_comment,
    )
    if args.dry_run:
        print("Dry run: create PR reply payload")
        print(json.dumps({
            "pullRequest": context.change_request.to_summary_dict(),
            "threadId": preview.thread_id,
            "payload": preview.payload,
        }, indent=2))
        return

    result = provider.create_review_reply(
        context,
        thread_id=args.thread,
        text=args.text,
        parent_comment_id=args.parent_comment,
    )
    print(
        f"✓ Reply added to thread {result.thread_id} on PR {context.change_request.id} "
        f"(comment id: {result.comment_id})."
    )


def cmd_pr_edit_comment(args, token, *, build_review_provider_func=None):
    provider = _require_provider(build_review_provider_func, "review")(token)
    context = provider.resolve_review_context(
        repo_ref=args.repo,
        change_request_id=args.pr,
        source_branch=args.source,
        url=args.url,
    )
    preview = provider.prepare_review_comment_edit(
        context,
        thread_id=args.thread,
        comment_id=args.comment,
        text=args.text,
    )
    if args.dry_run:
        print("Dry run: edit PR comment payload")
        print(json.dumps({
            "pullRequest": context.change_request.to_summary_dict(),
            "threadId": preview.thread_id,
            "commentId": preview.comment_id,
            "currentContent": preview.current_content,
            "payload": preview.payload,
        }, indent=2))
        return

    result = provider.edit_review_comment(
        context,
        thread_id=args.thread,
        comment_id=args.comment,
        text=args.text,
    )
    print(
        f"✓ Comment {result.comment_id} updated in thread {result.thread_id} on PR "
        f"{context.change_request.id}."
    )


def cmd_pr_resolve(args, token, *, build_review_provider_func=None):
    provider = _require_provider(build_review_provider_func, "review")(token)
    context = provider.resolve_review_context(
        repo_ref=args.repo,
        change_request_id=args.pr,
        source_branch=args.source,
        url=args.url,
    )
    preview = provider.prepare_review_thread_resolution(context, thread_id=args.thread, status="fixed")
    if args.dry_run:
        print("Dry run: resolve PR thread payload")
        print(json.dumps({
            "pullRequest": context.change_request.to_summary_dict(),
            "threadId": preview.thread_id,
            "currentStatus": preview.current_status,
            "payload": preview.payload,
        }, indent=2))
        return

    result = provider.resolve_review_thread(
        context,
        thread_id=args.thread,
        status="fixed",
    )
    print(
        f"✓ Thread {result.thread_id} marked resolved on PR {context.change_request.id} "
        f"(status: {result.status})."
    )


def cmd_pr_review_draft(args, token, *, build_review_provider_func=None):
    provider = _require_provider(build_review_provider_func, "review")(token)
    context = provider.resolve_review_context(
        repo_ref=args.repo,
        change_request_id=args.pr,
        source_branch=args.source,
        url=args.url,
    )
    analysis = provider.analyze_change_request(context)
    draft = {
        "formatVersion": REVIEW_DRAFT_FORMAT_VERSION,
        "organization": context.organization,
        "project": context.project,
        "repo": {
            "name": context.repository.name,
            "id": context.repository.id,
        },
        "pullRequest": context.change_request.to_analysis_dict(),
        "analysis": {
            "changeSummary": analysis.change_summary.to_legacy_dict(),
            "files": [file_change.to_legacy_dict() for file_change in analysis.files],
            "existingComments": [comment.to_legacy_dict() for comment in analysis.existing_comments],
        },
        "draftComments": [],
    }
    rendered = json.dumps(draft, indent=2)
    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)) or ".", exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(rendered + "\n")
        print(f"✓ Review draft written to {args.output}")
        return
    print(rendered)


def cmd_pr_review_apply(args, token, *, build_review_provider_func=None):
    draft = load_review_draft(args.draft_file)
    org_name = draft.get("organization", ORG)
    project_name = draft.get("project", PROJECT)
    repo_ref = (draft.get("repo") or {}).get("id") or (draft.get("repo") or {}).get("name")
    pr_id = (draft.get("pullRequest") or {}).get("pullRequestId")
    if not repo_ref or pr_id is None:
        raise CliError("ERROR: Review draft is missing repo or pullRequest.pullRequestId metadata.")

    provider = _require_provider(build_review_provider_func, "review")(token)
    context = provider.resolve_review_context(
        repo_ref=repo_ref,
        change_request_id=int(pr_id),
        source_branch=None,
        url=None,
        project_name=project_name,
        org_name=org_name,
    )
    results: list[dict] = []

    for index, entry in enumerate(draft.get("draftComments", []), start=1):
        entry_type = (entry.get("type") or "general").lower()
        if entry_type in ("general", "comment"):
            if not entry.get("text"):
                raise CliError(f"ERROR: Draft comment #{index} is missing text.")
            preview = provider.prepare_review_comment(context, text=entry["text"])
            if args.dry_run:
                results.append({"index": index, "type": entry_type, "payload": preview.payload})
                continue
            result = provider.create_review_comment(context, text=entry["text"])
            results.append({"index": index, "type": entry_type, "threadId": result.thread_id})
            continue

        if entry_type == "inline":
            if not entry.get("text") or not entry.get("path") or entry.get("line") is None:
                raise CliError(f"ERROR: Inline draft comment #{index} requires path, line, and text.")
            preview = provider.prepare_inline_review_comment(
                context,
                text=entry["text"],
                file_path=entry["path"],
                line=int(entry["line"]),
                end_line=int(entry["endLine"]) if entry.get("endLine") is not None else None,
                start_offset=int(entry.get("startOffset", 1)),
                end_offset=int(entry["endOffset"]) if entry.get("endOffset") is not None else None,
            )
            if args.dry_run:
                results.append({"index": index, "type": entry_type, "payload": preview.payload})
                continue
            result = provider.create_inline_review_comment(
                context,
                text=entry["text"],
                file_path=entry["path"],
                line=int(entry["line"]),
                end_line=int(entry["endLine"]) if entry.get("endLine") is not None else None,
                start_offset=int(entry.get("startOffset", 1)),
                end_offset=int(entry["endOffset"]) if entry.get("endOffset") is not None else None,
            )
            results.append({"index": index, "type": entry_type, "threadId": result.thread_id})
            continue

        if entry_type == "reply":
            if entry.get("threadId") is None or not entry.get("text"):
                raise CliError(f"ERROR: Reply draft comment #{index} requires threadId and text.")
            preview = provider.prepare_review_reply(
                context,
                thread_id=entry["threadId"],
                text=entry["text"],
                parent_comment_id=int(entry["parentCommentId"]) if entry.get("parentCommentId") is not None else None,
            )
            if args.dry_run:
                results.append({"index": index, "type": entry_type, "payload": preview.payload})
                continue
            result = provider.create_review_reply(
                context,
                thread_id=entry["threadId"],
                text=entry["text"],
                parent_comment_id=int(entry["parentCommentId"]) if entry.get("parentCommentId") is not None else None,
            )
            results.append({"index": index, "type": entry_type, "commentId": result.comment_id})
            continue

        if entry_type == "edit":
            if entry.get("threadId") is None or entry.get("commentId") is None or not entry.get("text"):
                raise CliError(f"ERROR: Edit draft comment #{index} requires threadId, commentId, and text.")
            preview = provider.prepare_review_comment_edit(
                context,
                thread_id=entry["threadId"],
                comment_id=int(entry["commentId"]),
                text=entry["text"],
            )
            if args.dry_run:
                results.append({"index": index, "type": entry_type, "payload": preview.payload})
                continue
            result = provider.edit_review_comment(
                context,
                thread_id=entry["threadId"],
                comment_id=int(entry["commentId"]),
                text=entry["text"],
            )
            results.append({"index": index, "type": entry_type, "commentId": result.comment_id})
            continue

        if entry_type == "resolve":
            if entry.get("threadId") is None:
                raise CliError(f"ERROR: Resolve draft comment #{index} requires threadId.")
            preview = provider.prepare_review_thread_resolution(
                context,
                thread_id=entry["threadId"],
                status="fixed",
            )
            if args.dry_run:
                results.append({"index": index, "type": entry_type, "payload": preview.payload})
                continue
            result = provider.resolve_review_thread(
                context,
                thread_id=entry["threadId"],
                status="fixed",
            )
            results.append({"index": index, "type": entry_type, "status": result.status})
            continue

        raise CliError(f"ERROR: Unsupported draft comment type '{entry_type}' in item #{index}.")

    if args.dry_run:
        print(json.dumps({
            "pullRequest": {
                **serialize_pr_summary(context.change_request),
                "browserUrl": pr_browser_url(context.change_request),
            },
            "plannedActions": results,
        }, indent=2))
        return

    for result in results:
        print(json.dumps(result))


def cmd_pr_statuses(args, token, *, build_review_provider_func=None):
    provider = _require_provider(build_review_provider_func, "review")(token)
    context = provider.resolve_review_context(
        repo_ref=args.repo,
        change_request_id=args.pr,
        source_branch=args.source,
        url=args.url,
    )
    change_request = context.change_request
    statuses = provider.list_statuses(context)

    if args.json:
        print(json.dumps({
            "pullRequest": change_request.to_analysis_dict(),
            "statuses": [status.to_legacy_dict() for status in statuses],
        }, indent=2))
        return

    print(f"PR {change_request.id}: {change_request.title}")
    print(f"  Repo   : {change_request.repo_name}")
    print(f"  Status : {change_request.status}")
    print(f"  Source : {change_request.source_branch}")
    print(f"  Target : {change_request.target_branch}\n")
    if not statuses:
        print("No PR statuses found.")
        return

    for status in statuses:
        print(f"[{status.display_state()}] {status.context_label()}")
        if status.description:
            print(f"  {status.description}")
        if status.target_url:
            print(f"  {status.target_url}")
        if status.created_by or status.creation_date:
            details = []
            if status.created_by:
                details.append(status.created_by)
            if status.creation_date:
                details.append(status.creation_date)
            print(f"  {' | '.join(details)}")


def register_review_subcommands(sub):
    p = sub.add_parser("repos", help="List git repositories in the project")
    p.add_argument("--json", action="store_true", help="Emit structured JSON output for scripting")

    p = sub.add_parser("create-pr", help="Create a pull request or merge request for a tracking item")
    p.add_argument("id", type=int)
    p.add_argument("--provider", choices=("azure-devops", "gitlab"), default="azure-devops",
                   help="Review provider to use for change-request creation (default: azure-devops)")
    p.add_argument("--repo", metavar="REPO", help="Repository name or id")
    p.add_argument("--source", metavar="BRANCH", help="Source branch name (default: current git branch)")
    p.add_argument("--target", metavar="BRANCH", default="main",
                   help="Target branch name (default: main)")
    p.add_argument("--title", metavar="TEXT", help="PR title (default: derived from work item)")
    p.add_argument("--description", metavar="TEXT", help="PR description (default: Closes #<id>)")
    p.add_argument("--work-item-title", metavar="TEXT",
                   help="Optional pre-fetched tracking-item title to avoid an extra API call")
    p.add_argument("--dry-run", action="store_true",
                   help="Print the payload instead of creating the PR")

    p = sub.add_parser("prepare-review", help="Create a pull request and move the work item to In Review")
    p.add_argument("id", type=int)
    p.add_argument("--repo", metavar="REPO", help="Repository name or id")
    p.add_argument("--source", metavar="BRANCH", help="Source branch name (default: current git branch)")
    p.add_argument("--target", metavar="BRANCH", default="main",
                   help="Target branch name (default: main)")
    p.add_argument("--title", metavar="TEXT", help="PR title (default: derived from work item)")
    p.add_argument("--description", metavar="TEXT", help="PR description (default: Closes #<id>)")
    p.add_argument("--work-item-title", metavar="TEXT",
                   help="Optional pre-fetched work item title to avoid an extra API call")
    p.add_argument("--dry-run", action="store_true",
                   help="Print the PR and state-transition payload instead of applying changes")

    p = sub.add_parser("pr-analyze", help="Summarize a PR from a URL or repo/PR reference")
    p.add_argument("--url", metavar="PR_URL",
                   help="Pull request or merge request URL")
    p.add_argument("--repo", metavar="REPO",
                   help="Repository name or id when not using --url")
    p.add_argument("--pr", type=int, metavar="PR_ID",
                   help="Pull request id when not using --url")
    p.add_argument("--source", metavar="BRANCH",
                   help="Source branch name or ref when not using --url and --pr is omitted")
    p.add_argument("--json", action="store_true",
                   help="Emit structured JSON output for scripting")

    p = sub.add_parser("pr-files", help="List changed files for a PR")
    p.add_argument("--url", metavar="PR_URL",
                   help="Pull request or merge request URL")
    p.add_argument("--repo", metavar="REPO",
                   help="Repository name or id when not using --url")
    p.add_argument("--pr", type=int, metavar="PR_ID",
                   help="Pull request id when not using --url")
    p.add_argument("--source", metavar="BRANCH",
                   help="Source branch name or ref when not using --url and --pr is omitted")
    p.add_argument("--json", action="store_true",
                   help="Emit structured JSON output for scripting")

    p = sub.add_parser("pr-file", help="Show PR file contents from the source or target branch")
    p.add_argument("--url", metavar="PR_URL",
                   help="Pull request or merge request URL")
    p.add_argument("--repo", metavar="REPO",
                   help="Repository name or id when not using --url")
    p.add_argument("--pr", type=int, metavar="PR_ID",
                   help="Pull request id when not using --url")
    p.add_argument("--source", metavar="BRANCH",
                   help="Source branch name or ref when not using --url and --pr is omitted")
    p.add_argument("--path", required=True, metavar="FILE_PATH",
                   help="Repository-relative file path in the PR")
    p.add_argument("--version", choices=["source", "target"], default="source",
                   help="Which branch version to show (default: source)")
    p.add_argument("--start-line", type=int, metavar="LINE",
                   help="First line to print (1-based)")
    p.add_argument("--end-line", type=int, metavar="LINE",
                   help="Last line to print (1-based, inclusive)")
    p.add_argument("--number-lines", action="store_true",
                   help="Prefix output lines with 1-based line numbers")

    p = sub.add_parser("pr-diff", help="Show a unified diff for one PR file")
    p.add_argument("--url", metavar="PR_URL",
                   help="Pull request or merge request URL")
    p.add_argument("--repo", metavar="REPO",
                   help="Repository name or id when not using --url")
    p.add_argument("--pr", type=int, metavar="PR_ID",
                   help="Pull request id when not using --url")
    p.add_argument("--source", metavar="BRANCH",
                   help="Source branch name or ref when not using --url and --pr is omitted")
    p.add_argument("--path", required=True, metavar="FILE_PATH",
                   help="Repository-relative file path in the PR")
    p.add_argument("--context", type=int, default=3, metavar="LINES",
                   help="Number of context lines to include (default: 3)")

    p = sub.add_parser("pr-comments", help="Show review thread comments on a PR")
    p.add_argument("--url", metavar="PR_URL",
                   help="Pull request or merge request URL")
    p.add_argument("--repo", metavar="REPO",
                   help="Repository name or id (default: current git repo)")
    p.add_argument("--pr", type=int, metavar="PR_ID",
                   help="Pull request id (default: resolve from --source or current branch)")
    p.add_argument("--source", metavar="BRANCH",
                   help="Source branch name or ref (default: current git branch when --pr is omitted)")
    p.add_argument("--unresolved-only", action="store_true",
                   help="Only include threads that are not resolved/closed")
    p.add_argument("--json", action="store_true",
                   help="Emit structured JSON output for scripting")

    p = sub.add_parser("pr-statuses", help="Show read-only PR statuses such as build or policy checks")
    p.add_argument("--url", metavar="PR_URL",
                   help="Pull request or merge request URL")
    p.add_argument("--repo", metavar="REPO",
                   help="Repository name or id (default: current git repo)")
    p.add_argument("--pr", type=int, metavar="PR_ID",
                   help="Pull request id (default: resolve from --source or current branch)")
    p.add_argument("--source", metavar="BRANCH",
                   help="Source branch name or ref (default: current git branch when --pr is omitted)")
    p.add_argument("--json", action="store_true",
                   help="Emit structured JSON output for scripting")

    p = sub.add_parser("pr-comment", help="Add a new discussion thread to a PR")
    p.add_argument("--url", metavar="PR_URL",
                   help="Pull request or merge request URL")
    p.add_argument("--repo", metavar="REPO",
                   help="Repository name or id (default: current git repo)")
    p.add_argument("--pr", type=int, metavar="PR_ID",
                   help="Pull request id (default: resolve from --source or current branch)")
    p.add_argument("--source", metavar="BRANCH",
                   help="Source branch name or ref (default: current git branch when --pr is omitted)")
    p.add_argument("--dry-run", action="store_true",
                   help="Print the payload instead of posting the comment")
    p.add_argument("text", help="Comment text")

    p = sub.add_parser("pr-inline-comment", help="Add a new inline review thread on a PR file/line")
    p.add_argument("--url", metavar="PR_URL",
                   help="Pull request or merge request URL")
    p.add_argument("--repo", metavar="REPO",
                   help="Repository name or id (default: current git repo)")
    p.add_argument("--pr", type=int, metavar="PR_ID",
                   help="Pull request id (default: resolve from --source or current branch)")
    p.add_argument("--source", metavar="BRANCH",
                   help="Source branch name or ref (default: current git branch when --pr is omitted)")
    p.add_argument("--path", required=True, metavar="FILE_PATH",
                   help="Repository-relative file path in the PR")
    p.add_argument("--line", required=True, type=int, metavar="LINE",
                   help="1-based line number for the inline comment")
    p.add_argument("--end-line", type=int, metavar="LINE",
                   help="Optional 1-based end line for a range comment")
    p.add_argument("--start-offset", type=int, default=1, metavar="COL",
                   help="1-based start column/offset within the start line (default: 1)")
    p.add_argument("--end-offset", type=int, metavar="COL",
                   help="1-based end column/offset within the end line")
    p.add_argument("--dry-run", action="store_true",
                   help="Print the payload instead of posting the inline comment")
    p.add_argument("text", help="Inline comment text")

    p = sub.add_parser(
        "pr-reply",
        help="Reply to an existing PR review thread",
        description=(
            "Reply to an existing PR review thread.\n\n"
            "The reply body is passed as the final positional text argument."
        ),
        epilog=(
            "Examples:\n"
            "  ./sg pr-reply --url <ado-pr-url> --thread <thread-id> \"Thanks, I will adjust this.\"\n"
            "  ./sg pr-reply --repo <repo> --pr <pr-id> --thread <thread-id> \"Updated in the latest commit.\""
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--url", metavar="PR_URL",
                   help="Pull request or merge request URL")
    p.add_argument("--repo", metavar="REPO",
                   help="Repository name or id (default: current git repo)")
    p.add_argument("--pr", type=int, metavar="PR_ID",
                   help="Pull request id (default: resolve from --source or current branch)")
    p.add_argument("--source", metavar="BRANCH",
                   help="Source branch name or ref (default: current git branch when --pr is omitted)")
    p.add_argument("--thread", required=True, metavar="THREAD_ID",
                   help="Thread id from pr-comments output")
    p.add_argument("--parent-comment", type=int, metavar="COMMENT_ID",
                   help="Parent comment id inside the thread (default: root text comment)")
    p.add_argument("--dry-run", action="store_true",
                   help="Print the payload instead of posting the reply")
    p.add_argument("text", help="Reply text (final positional argument)")

    p = sub.add_parser("pr-edit-comment", help="Edit an existing PR thread comment")
    p.add_argument("--url", metavar="PR_URL",
                   help="Pull request or merge request URL")
    p.add_argument("--repo", metavar="REPO",
                   help="Repository name or id (default: current git repo)")
    p.add_argument("--pr", type=int, metavar="PR_ID",
                   help="Pull request id (default: resolve from --source or current branch)")
    p.add_argument("--source", metavar="BRANCH",
                   help="Source branch name or ref (default: current git branch when --pr is omitted)")
    p.add_argument("--thread", required=True, metavar="THREAD_ID",
                   help="Thread id from pr-comments output")
    p.add_argument("--comment", required=True, type=int, metavar="COMMENT_ID",
                   help="Comment id from ado pr-comments output or --json output")
    p.add_argument("--dry-run", action="store_true",
                   help="Print the payload instead of updating the comment")
    p.add_argument("text", help="Updated comment text")

    p = sub.add_parser("pr-resolve", help="Mark a PR review thread as resolved")
    p.add_argument("--url", metavar="PR_URL",
                   help="Pull request or merge request URL")
    p.add_argument("--repo", metavar="REPO",
                   help="Repository name or id (default: current git repo)")
    p.add_argument("--pr", type=int, metavar="PR_ID",
                   help="Pull request id (default: resolve from --source or current branch)")
    p.add_argument("--source", metavar="BRANCH",
                   help="Source branch name or ref (default: current git branch when --pr is omitted)")
    p.add_argument("--thread", required=True, metavar="THREAD_ID",
                   help="Thread id from pr-comments output")
    p.add_argument("--dry-run", action="store_true",
                   help="Print the payload instead of resolving the thread")

    p = sub.add_parser("pr-review-draft", help="Create a JSON review draft bundle for later approval/posting")
    p.add_argument("--url", metavar="PR_URL",
                   help="Pull request or merge request URL")
    p.add_argument("--repo", metavar="REPO",
                   help="Repository name or id when not using --url")
    p.add_argument("--pr", type=int, metavar="PR_ID",
                   help="Pull request id when not using --url")
    p.add_argument("--source", metavar="BRANCH",
                   help="Source branch name or ref when not using --url and --pr is omitted")
    p.add_argument("--output", metavar="FILE",
                   help="Optional path to write the draft JSON instead of stdout")

    p = sub.add_parser("pr-review-apply", help="Apply review comments from a draft JSON file")
    p.add_argument("draft_file", metavar="FILE",
                   help="Review draft JSON file created by pr-review-draft or edited manually")
    p.add_argument("--dry-run", action="store_true",
                   help="Validate and print planned review actions instead of posting them")


def review_command_handlers() -> dict[str, callable]:
    return {
        "repos": cmd_repos,
        "create-pr": cmd_create_pr,
        "prepare-review": cmd_prepare_review,
        "pr-analyze": cmd_pr_analyze,
        "pr-files": cmd_pr_files,
        "pr-file": cmd_pr_file,
        "pr-diff": cmd_pr_diff,
        "pr-comments": cmd_pr_comments,
        "pr-statuses": cmd_pr_statuses,
        "pr-comment": cmd_pr_comment,
        "pr-inline-comment": cmd_pr_inline_comment,
        "pr-reply": cmd_pr_reply,
        "pr-edit-comment": cmd_pr_edit_comment,
        "pr-resolve": cmd_pr_resolve,
        "pr-review-draft": cmd_pr_review_draft,
        "pr-review-apply": cmd_pr_review_apply,
    }