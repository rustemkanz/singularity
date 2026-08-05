import argparse
import json
import os

from errors import CliError
from app_config import ME, QA_EMAIL


def _require_provider(factory, provider_label: str):
    if factory is None:
        raise RuntimeError(f"Missing {provider_label} provider factory.")
    return factory


def _reference_flag(reference: dict, snake_case_name: str, camel_case_name: str) -> bool:
    return bool(reference.get(snake_case_name) or reference.get(camel_case_name))


def summarize_references(references: list[dict], *, limit: int = 3) -> tuple[str, str] | None:
    if not references:
        return None
    image_count = sum(1 for reference in references if _reference_flag(reference, "is_image", "isImage"))
    preview_items = []
    for reference in references[:limit]:
        kind = "image" if _reference_flag(reference, "is_image", "isImage") else "attachment"
        name = f" ({reference.get('name')})" if reference.get("name") else ""
        preview_items.append(f"[{kind}] {reference['label']}{name}")
    preview = f"  Preview     : {'; '.join(preview_items)}"
    remaining = len(references) - len(preview_items)
    if remaining > 0:
        preview += f"; ... and {remaining} more"
    summary = f"  Context Refs: {len(references)} total ({image_count} images, {len(references) - image_count} files/links)"
    return summary, preview


def best_introduction_candidate(development_artifacts: dict) -> dict | None:
    for pull_request in development_artifacts.get("pullRequests", []):
        if pull_request.get("status") == "completed":
            return {"type": "pullRequest", **pull_request}
    if development_artifacts.get("pullRequests"):
        return {"type": "pullRequest", **development_artifacts["pullRequests"][0]}
    if development_artifacts.get("commits"):
        return {"type": "commit", **development_artifacts["commits"][0]}
    return None


def truncate_text(text: str, limit: int = 1000) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def format_section(label: str, text: str) -> str:
    body = truncate_text(text or "(none)")
    return f"\n  {label}:\n    {body.replace(chr(10), chr(10) + '    ')}"


def cmd_sprint(args, token, *, build_work_tracking_provider_func=None):
    provider = _require_provider(build_work_tracking_provider_func, "work tracking")(token)
    sprint = provider.get_current_sprint()
    if args.json:
        print(json.dumps({
            "id": sprint.id,
            "name": sprint.name,
            "path": sprint.path,
            "startDate": sprint.start_date,
            "finishDate": sprint.finish_date,
        }, indent=2))
        return
    print(f"\nCurrent sprint : {sprint.name}")
    print(f"Dates          : {sprint.start_date or ''} -> {sprint.finish_date or ''}")
    print(f"Path           : {sprint.path}")
    print(f"ID             : {sprint.id}\n")


def cmd_list(args, token, *, build_work_tracking_provider_func=None, me: str | None = None):
    provider = _require_provider(build_work_tracking_provider_func, "work tracking")(token)
    sprint, items = provider.get_open_candidate_items()
    if getattr(args, "json", False):
        print(json.dumps({
            "sprint": {
                "id": sprint.id,
                "name": sprint.name,
                "path": sprint.path,
            },
            "items": [
                item.to_legacy_dict()
                for item in items
            ],
        }, indent=2))
        return
    if not items:
        print(f"\nNo open items assigned to you in {sprint.name}.\n")
        return

    assignee = ME if me is None else me
    print(f"\nOpen items in {sprint.name} assigned to {assignee}:\n")
    for item in items:
        print(
            f"  [{item.id:>6}]  "
            f"{item.kind:<12}  "
            f"{item.state:<24}  "
            f"{item.title}"
        )
    print()


def cmd_teams(args, token, *, build_work_tracking_provider_func=None):
    provider = _require_provider(build_work_tracking_provider_func, "work tracking")(token)
    teams = provider.list_teams()
    if args.json:
        print(json.dumps({
            "teams": [
                {
                    "id": team.id,
                    "name": team.name,
                    "description": team.description,
                }
                for team in teams
            ],
        }, indent=2))
        return

    print("\nProject teams:\n")
    for team in teams:
        suffix = f"  {team.description}" if team.description else ""
        print(f"  {team.id}  {team.name}{suffix}")
    print()


def cmd_show(args, token, *, build_work_tracking_provider_func=None):
    provider = _require_provider(build_work_tracking_provider_func, "work tracking")(token)
    snapshot = provider.get_work_item_context(item_id=args.id)
    if getattr(args, "json", False):
        print(json.dumps(snapshot.to_legacy_dict(), indent=2))
        return

    work_item = snapshot.work_item
    sections = [
        format_section(label, work_item.sections.get(key) or "(none)")
        for key, label in (
            ("description", "Description"),
            ("reproSteps", "Repro Steps"),
            ("acceptanceCriteria", "Acceptance Criteria"),
        )
    ]
    references_summary = summarize_references(snapshot.references)

    print(f"\n{'-' * 64}")
    print(f"  [{work_item.id}] {work_item.kind} - {work_item.state}")
    print(f"  Title       : {work_item.title}")
    print(f"  Assigned to : {work_item.assignee}")
    print(f"  Iteration   : {work_item.iteration}")
    if work_item.estimate:
        print(f"  Story Points: {work_item.estimate}")
    if references_summary:
        summary, preview = references_summary
        print(summary)
        print(preview)
        print(f"  Use         : ./sg attachments {args.id}")
    if snapshot.development_artifacts["pullRequests"] or snapshot.development_artifacts["commits"]:
        print(
            "  Dev Links   : "
            f"{len(snapshot.development_artifacts['pullRequests'])} PRs, "
            f"{len(snapshot.development_artifacts['commits'])} commits"
        )
        print(f"  Use         : ./sg context {args.id}")
    for section in sections:
        print(section)
    print(f"{'-' * 64}\n")


def cmd_context(args, token, *, build_work_tracking_provider_func=None):
    provider = _require_provider(build_work_tracking_provider_func, "work tracking")(token)
    snapshot = provider.get_work_item_context(item_id=args.id)
    if getattr(args, "json", False):
        print(json.dumps(snapshot.to_legacy_dict(), indent=2))
        return

    work_item = snapshot.work_item
    print(f"\n{'-' * 64}")
    print(f"  [{work_item.id}] {work_item.kind} - {work_item.state}")
    print(f"  Title       : {work_item.title}")
    print(f"  Assigned to : {work_item.assignee}")
    print(f"  Iteration   : {work_item.iteration}")
    print(f"  Area        : {work_item.area}")
    if work_item.tags:
        print(f"  Tags        : {', '.join(work_item.tags)}")
    references_summary = summarize_references(snapshot.references)
    if references_summary:
        summary, preview = references_summary
        print(summary)
        print(preview)
        print(f"  Use         : ./sg attachments {args.id}")
    for key, label in (
        ("description", "Description"),
        ("reproSteps", "Repro Steps"),
        ("acceptanceCriteria", "Acceptance Criteria"),
    ):
        print(format_section(label, work_item.sections.get(key) or "(none)"))

    print("\n  Development Links:")
    if not snapshot.development_artifacts["pullRequests"] and not snapshot.development_artifacts["commits"]:
        print("    (none)")
    for pull_request in snapshot.development_artifacts["pullRequests"]:
        print(
            f"    PR {pull_request['pullRequestId']} [{pull_request.get('status') or '?'}] {pull_request.get('repoName')} "
            f"{pull_request.get('title') or '<untitled>'}"
        )
    for commit in snapshot.development_artifacts["commits"]:
        print(
            f"    Commit {truncate_text(commit['commitId'], 12)} {commit.get('repoName')} "
            f"{truncate_text(commit.get('comment') or '(no comment)', 90)}"
        )

    print("\n  Related Items:")
    empty_related = True
    for label, key in (("Parents", "parents"), ("Children", "children"), ("Related", "related")):
        items = snapshot.related_items.get(key, [])
        if not items:
            continue
        empty_related = False
        print(f"    {label}:")
        for item in items:
            print(f"      - [{item['id']}] {item['workItemType']} - {item['state']} - {item['title']}")
    if empty_related:
        print("    (none)")

    print("\n  Recent Comments:")
    if not snapshot.recent_comments:
        print("    (none)")
    for comment in snapshot.recent_comments:
        published = (comment.published_date or "")[:19].replace("T", " ")
        print(f"    - {published} {comment.author}:")
        print(f"      {truncate_text(comment.text or '(empty comment)', 220)}")
    print(f"{'-' * 64}\n")


def cmd_comments(args, token, *, build_work_tracking_provider_func=None):
    provider = _require_provider(build_work_tracking_provider_func, "work tracking")(token)
    comment_snapshot = provider.get_work_item_comments(item_id=args.id)
    comment_data = comment_snapshot.to_legacy_dict()
    comments = comment_data["comments"]
    if args.latest is not None:
        comments = comments[:args.latest]

    if args.json:
        print(json.dumps({
            **comment_data,
            "comments": comments,
        }, indent=2))
        return

    work_item = comment_data["workItem"]
    latest_label = (
        f"latest {len(comments)} of {comment_data['commentCount']}"
        if args.latest is not None
        else str(comment_data["commentCount"])
    )
    print(f"\n{'-' * 64}")
    print(f"  [{work_item['id']}] {work_item['workItemType']} - {work_item['state']}")
    print(f"  Title      : {work_item['title']}")
    print(f"  Comments   : {latest_label}")
    print(f"  Use        : ./sg context {args.id}")

    print("\n  Comments:")
    if not comments:
        print("    (none)")
        print(f"{'-' * 64}\n")
        return

    for comment in comments:
        published = (comment.get("publishedDate") or "")[:19].replace("T", " ")
        print(f"    - #{comment.get('id', '?')} {published} {comment['author']}:")
        print(f"      {comment['text'] or '(empty comment)'}")
    print(f"{'-' * 64}\n")


def cmd_attachments(args, token, *, build_evidence_provider_func=None):
    if args.open and args.no_download:
        raise CliError("ERROR: --open requires downloads. Remove --no-download or omit --open.")

    provider = _require_provider(build_evidence_provider_func, "evidence")(token)
    evidence = provider.get_work_item_evidence(item_id=args.id)
    references = evidence.references
    if args.images_only:
        references = [reference for reference in references if reference.is_image]

    download_targets = references if args.download_all else [reference for reference in references if reference.is_image]
    download_dir = os.path.abspath(args.download_dir or provider.default_download_dir(item_id=args.id))
    download_result = provider.download_references(
        references=download_targets,
        download_dir=download_dir,
        open_after_download=args.open,
    ) if not args.no_download and download_targets else None

    downloaded = [entry.to_legacy_dict() for entry in (download_result.downloaded if download_result else [])]
    failures = list(download_result.failures) if download_result else []
    opened = download_result.opened if download_result else False
    open_error = download_result.open_error if download_result else None

    if args.json:
        print(json.dumps({
            "workItemId": args.id,
            "title": evidence.title,
            "references": [reference.to_legacy_dict() for reference in references],
            "downloaded": downloaded,
            "downloadDir": download_result.download_dir if download_result else None,
            "failures": failures,
            "opened": opened,
            "openError": open_error,
        }, indent=2))
        if failures:
            raise CliError("ERROR: One or more downloads failed.")
        return

    print(f"\n[{args.id}] {evidence.title}")

    if not references:
        scope = "image references" if args.images_only else "attachments or image references"
        print(f"No {scope} found.\n")
        return

    print("\nReferences:\n")
    for index, reference in enumerate(references, start=1):
        kind = "image" if reference.is_image else "attachment"
        name = f" ({reference.name})" if reference.name else ""
        print(f"  {index}. [{kind}] {reference.label}{name}")
        print(f"     {reference.url}")

    if args.no_download or not download_targets:
        print()
        return

    print(f"\nDownloading context to: {download_dir}\n")
    for entry in downloaded:
        action = "reused" if entry["reused"] else "saved"
        print(f"  - {entry['label']}: {action} {entry['path']}")

    if opened:
        print(f"\nOpened {download_dir} in the system file browser.")
    elif open_error:
        print(f"\nOpen failed: {open_error}")

    print()
    if failures:
        print("Download failures:")
        for failure in failures:
            print(f"  - {failure}")
        raise CliError("ERROR: One or more downloads failed.")


def cmd_introduced_by(args, token, *, build_work_tracking_provider_func=None):
    provider = _require_provider(build_work_tracking_provider_func, "work tracking")(token)
    snapshot = provider.get_work_item_context(item_id=args.id)
    candidate = best_introduction_candidate(snapshot.development_artifacts)
    payload = {
        "workItem": snapshot.work_item.to_legacy_dict(),
        "candidate": candidate,
        "developmentArtifacts": snapshot.development_artifacts,
        "note": "Based on work-item development links; validate against code history before treating as definitive.",
    }
    if args.json:
        print(json.dumps(payload, indent=2))
        return

    print(f"\n[{snapshot.work_item.id}] {snapshot.work_item.title}")
    if candidate:
        if candidate["type"] == "pullRequest":
            print(
                f"Likely primary candidate: PR {candidate['pullRequestId']} in {candidate.get('repoName')} "
                f"[{candidate.get('status') or '?'}]"
            )
            if candidate.get("closedDate"):
                print(f"  Closed     : {candidate['closedDate']}")
            if candidate.get("lastMergeCommitId"):
                print(f"  Merge Commit: {candidate['lastMergeCommitId']}")
        else:
            print(
                f"Likely primary candidate: commit {candidate['commitId']} in {candidate.get('repoName')}"
            )
            if candidate.get("date"):
                print(f"  Date       : {candidate['date']}")
    else:
        print("No linked PR or commit artifacts were found on this work item.")

    if snapshot.development_artifacts["pullRequests"]:
        print("\nLinked pull requests:")
        for pull_request in snapshot.development_artifacts["pullRequests"]:
            print(
                f"  - PR {pull_request['pullRequestId']} [{pull_request.get('status') or '?'}] {pull_request.get('repoName')}: "
                f"{pull_request.get('title') or '<untitled>'}"
            )
    if snapshot.development_artifacts["commits"]:
        print("\nLinked commits:")
        for commit in snapshot.development_artifacts["commits"]:
            print(
                f"  - {commit['commitId']} {commit.get('repoName')}: "
                f"{truncate_text(commit.get('comment') or '(no comment)', 100)}"
            )
    print("\nNote: based on work-item development links; validate against code history before treating as definitive.\n")


def cmd_triage(args, token, *, build_work_tracking_provider_func=None):
    provider = _require_provider(build_work_tracking_provider_func, "work tracking")(token)
    report = provider.get_triage_report(item_ids=args.ids)
    if args.json:
        print(json.dumps(report.to_legacy_dict(), indent=2))
        return

    print("\nItems:\n")
    for item in report.items:
        print(f"  [{item.id}] {item.kind} - {item.state} - {item.title}")
        print(f"     Scope  : {item.scope or '(none)'}")
        print(f"     Area   : {item.area or '(none)'}")
        print(f"     Owner  : {item.owner or '(unknown)'}")
        print(f"     Signals: {', '.join(item.keywords[:6]) or '(none)'}")

    print("\nSuggested Groups:\n")
    for index, group in enumerate(report.groups, start=1):
        print(f"  Group {index}: {', '.join(str(item_id) for item_id in group.ids)}")
        print(f"     Why   : {'; '.join(group.reason_summary)}")
    print()


def cmd_start_work(args, token, *, build_work_tracking_provider_func=None):
    provider = _require_provider(build_work_tracking_provider_func, "work tracking")(token)
    plan = provider.get_start_work_plan(item_id=args.id)
    if args.json:
        print(json.dumps(plan.to_legacy_dict(), indent=2))
        return

    print(f"\n[{plan.work_item.id}] {plan.work_item.title}")
    print(f"  Type         : {plan.work_item.kind}")
    print(f"  Concise title   : {plan.concise_title}")
    print(f"  Suggested branch : {plan.branch_name}")
    print(f"  Note path        : {plan.note_path}")
    print(f"  Commit prefix    : {plan.commit_prefix}")
    print(f"  PR title         : {plan.change_request_title}")
    print(f"  PR body          : {plan.change_request_body}")
    print("\n  Suggested commands:")
    for command in plan.commands:
        print(f"    {command}")
    print()


def cmd_start(args, token, *, build_work_tracking_provider_func=None, cmd_show_func=None):
    show_func = cmd_show_func or cmd_show
    show_func(args, token)
    provider = _require_provider(build_work_tracking_provider_func, "work tracking")(token)
    actual_state = provider.transition_work_item(item_id=args.id, state="In Progress")
    rendered_state = actual_state if isinstance(actual_state, str) and actual_state else "In Progress"
    print(f"✓ Work item {args.id} moved to '{rendered_state}'.")
    branch = args.branch or f"feature/{args.id}-work-item"
    print("\nNext - create your branch:\n")
    print("    git checkout main && git pull")
    print(f"    git checkout -b {branch}")
    print(f"    git push -u origin {branch}\n")


def cmd_pick_next(
    args,
    token,
    *,
    build_work_tracking_provider_func=None,
    cmd_show_func=None,
    cmd_start_func=None,
):
    provider = _require_provider(build_work_tracking_provider_func, "work tracking")(token)
    sprint, items = provider.get_open_candidate_items()
    if not items:
        print(f"\nNo candidate items assigned to you in {sprint.name}.\n")
        return

    next_item = items[0]
    item_id = next_item.id
    print(f"\nNext candidate in {sprint.name}:\n")
    print(
        f"  [{item_id}] {next_item.kind} | "
        f"{next_item.state} | {next_item.title}\n"
    )
    show_func = cmd_show_func or cmd_show
    show_func(
        argparse.Namespace(
            id=item_id,
            command="show",
            provider="azure-devops",
            repo=None,
            json=False,
        ),
        token,
    )

    if args.start:
        start_func = cmd_start_func or cmd_start
        start_func(
            argparse.Namespace(
                id=item_id,
                command="start",
                provider="azure-devops",
                repo=None,
                branch=args.branch,
            ),
            token,
        )
    else:
        print("Use '--start' to move it to 'In Progress' and print the branch command.\n")


def cmd_review(args, token, *, build_work_tracking_provider_func=None):
    provider = _require_provider(build_work_tracking_provider_func, "work tracking")(token)
    actual_state = provider.transition_work_item(item_id=args.id, state="In Review")
    rendered_state = actual_state if isinstance(actual_state, str) and actual_state else "In Review"
    print(f"✓ Work item {args.id} moved to '{rendered_state}'.")


def cmd_testing(args, token, *, build_work_tracking_provider_func=None, qa_email: str | None = None):
    qa = args.qa or QA_EMAIL if qa_email is None else args.qa or qa_email
    if not qa:
        raise CliError("ERROR: No QA email set. Use --qa <email> or set QA_EMAIL in the script.")
    provider = _require_provider(build_work_tracking_provider_func, "work tracking")(token)
    actual_state = provider.transition_work_item(item_id=args.id, state="In Testing", assignee=qa)
    rendered_state = actual_state if isinstance(actual_state, str) and actual_state else "In Testing"
    print(f"✓ Work item {args.id} moved to '{rendered_state}' and assigned to {qa}.")


def cmd_handoff_to_qa(args, token, *, cmd_testing_func=None):
    testing_func = cmd_testing_func or cmd_testing
    testing_func(args, token)


def cmd_comment(args, token, *, build_work_tracking_provider_func=None):
    provider = _require_provider(build_work_tracking_provider_func, "work tracking")(token)
    comment_id = provider.add_work_item_comment(item_id=args.id, text=args.text)
    print(f"✓ Comment added to work item {args.id} (comment id: {comment_id}).")


def register_work_item_subcommands(sub):
    p = sub.add_parser("sprint", help="Show the current sprint")
    p.add_argument("--json", action="store_true", help="Emit structured JSON output for scripting")

    p = sub.add_parser("list", help="List my open items (New / Ready for development)")
    p.add_argument("--json", action="store_true", help="Emit structured JSON output for scripting")

    p = sub.add_parser("ready-items", help="Alias for list")
    p.add_argument("--json", action="store_true", help="Emit structured JSON output for scripting")

    p = sub.add_parser("teams", help="List Azure DevOps teams in the project")
    p.add_argument("--json", action="store_true", help="Emit structured JSON output for scripting")

    p = sub.add_parser("show", help="Show full details of a work item")
    p.add_argument("id", type=int)
    p.add_argument("--provider", choices=("azure-devops", "gitlab"), default="azure-devops",
                   help="Work-tracking provider to use (default: azure-devops)")
    p.add_argument("--repo", metavar="REPO", help="GitLab project path or id when --provider gitlab")
    p.add_argument("--json", action="store_true", help="Emit structured JSON output for scripting")

    p = sub.add_parser("comments", help="Show work-item comments")
    p.add_argument("id", type=int)
    p.add_argument("--provider", choices=("azure-devops", "gitlab"), default="azure-devops",
                   help="Work-tracking provider to use (default: azure-devops)")
    p.add_argument("--repo", metavar="REPO", help="GitLab project path or id when --provider gitlab")
    p.add_argument("--latest", type=int, metavar="COUNT", help="Show only the most recent COUNT comments")
    p.add_argument("--json", action="store_true", help="Emit structured JSON output for scripting")

    p = sub.add_parser("context", help="Show full work-item context including comments and linked dev artifacts")
    p.add_argument("id", type=int)
    p.add_argument("--provider", choices=("azure-devops", "gitlab"), default="azure-devops",
                   help="Work-tracking provider to use (default: azure-devops)")
    p.add_argument("--repo", metavar="REPO", help="GitLab project path or id when --provider gitlab")
    p.add_argument("--json", action="store_true", help="Emit structured JSON output for scripting")

    p = sub.add_parser("attachments", help="Show or download attachment and screenshot context for a work item")
    p.add_argument("id", type=int)
    p.add_argument("--images-only", action="store_true", help="Only include image and screenshot references")
    p.add_argument("--no-download", action="store_true", help="List references without downloading image context")
    p.add_argument("--download-all", action="store_true", help="Download all attachments/links that the CLI can fetch, not just images")
    p.add_argument("--open", action="store_true", help="Open the download directory in the system file browser after download")
    p.add_argument("--download-dir", metavar="DIR", help="Directory for downloaded image context (default: ./.sg-artifacts/work-item-<id>)")
    p.add_argument("--json", action="store_true", help="Emit structured JSON output for scripting")

    p = sub.add_parser("introduced-by", help="Show linked PR/commit candidates that likely introduced a bug")
    p.add_argument("id", type=int)
    p.add_argument("--json", action="store_true", help="Emit structured JSON output for scripting")

    p = sub.add_parser("triage", help="Summarize several work items and suggest grouping for PRs")
    p.add_argument("ids", nargs="+", type=int, metavar="ID")
    p.add_argument("--json", action="store_true", help="Emit structured JSON output for scripting")

    p = sub.add_parser("pick-next", help="Show or start the next best candidate item")
    p.add_argument("--start", action="store_true", help="Move the selected item to 'In Progress'")
    p.add_argument("--branch", "-b", metavar="NAME", help="Branch name to use when combined with --start")

    p = sub.add_parser("start", help="Move item to 'In Progress' (prints branch command)")
    p.add_argument("id", type=int)
    p.add_argument("--provider", choices=("azure-devops", "gitlab"), default="azure-devops",
                   help="Work-tracking provider to use (default: azure-devops)")
    p.add_argument("--repo", metavar="REPO", help="GitLab project path or id when --provider gitlab")
    p.add_argument("--branch", "-b", metavar="NAME", help="Branch name (default: feature/<id>-work-item)")

    p = sub.add_parser("start-work", help="Suggest a branch, note path, commit prefix, and PR draft")
    p.add_argument("id", type=int)
    p.add_argument("--provider", choices=("azure-devops", "gitlab"), default="azure-devops",
                   help="Work-tracking provider to use (default: azure-devops)")
    p.add_argument("--repo", metavar="REPO", help="GitLab project path or id when --provider gitlab")
    p.add_argument("--json", action="store_true", help="Emit structured JSON output for scripting")

    p = sub.add_parser("review", help="Move item to 'In Review'")
    p.add_argument("id", type=int)
    p.add_argument("--provider", choices=("azure-devops", "gitlab"), default="azure-devops",
                   help="Work-tracking provider to use (default: azure-devops)")
    p.add_argument("--repo", metavar="REPO", help="GitLab project path or id when --provider gitlab")

    p = sub.add_parser("testing", help="Move item to 'In Testing' and assign to QA")
    p.add_argument("id", type=int)
    p.add_argument("--provider", choices=("azure-devops", "gitlab"), default="azure-devops",
                   help="Work-tracking provider to use (default: azure-devops)")
    p.add_argument("--repo", metavar="REPO", help="GitLab project path or id when --provider gitlab")
    p.add_argument("--qa", metavar="ASSIGNEE", help="QA assignee identity (ADO email or GitLab username)")

    p = sub.add_parser("handoff-to-qa", help="Alias for testing")
    p.add_argument("id", type=int)
    p.add_argument("--provider", choices=("azure-devops", "gitlab"), default="azure-devops",
                   help="Work-tracking provider to use (default: azure-devops)")
    p.add_argument("--repo", metavar="REPO", help="GitLab project path or id when --provider gitlab")
    p.add_argument("--qa", metavar="ASSIGNEE", help="QA assignee identity (ADO email or GitLab username)")

    p = sub.add_parser("comment", help="Add a comment to a work item")
    p.add_argument("id", type=int)
    p.add_argument("--provider", choices=("azure-devops", "gitlab"), default="azure-devops",
                   help="Work-tracking provider to use (default: azure-devops)")
    p.add_argument("--repo", metavar="REPO", help="GitLab project path or id when --provider gitlab")
    p.add_argument("text", help="Comment text")

    p = sub.add_parser("cleanup-artifacts", help="Close disposable tracking/review artifacts and delete disposable branches")
    p.add_argument("--provider", choices=("gitlab",), required=True,
                   help="Cleanup provider to use")
    p.add_argument("--repo", required=True, metavar="REPO", help="GitLab project path or id")
    p.add_argument("--issue", dest="issues", action="append", type=int, default=[], metavar="ID",
                   help="GitLab issue iid to close (repeatable)")
    p.add_argument("--mr", dest="merge_requests", action="append", type=int, default=[], metavar="ID",
                   help="GitLab merge request iid to close (repeatable)")
    p.add_argument("--branch", dest="branches", action="append", default=[], metavar="NAME",
                   help="GitLab branch name to delete (repeatable)")
    p.add_argument("--dry-run", action="store_true", help="Print the cleanup plan instead of applying it")


def work_item_command_handlers() -> dict[str, callable]:
    return {
        "sprint": cmd_sprint,
        "list": cmd_list,
        "ready-items": cmd_list,
        "pick-next": cmd_pick_next,
        "teams": cmd_teams,
        "show": cmd_show,
        "comments": cmd_comments,
        "context": cmd_context,
        "attachments": cmd_attachments,
        "introduced-by": cmd_introduced_by,
        "triage": cmd_triage,
        "start": cmd_start,
        "start-work": cmd_start_work,
        "review": cmd_review,
        "testing": cmd_testing,
        "handoff-to-qa": cmd_handoff_to_qa,
        "comment": cmd_comment,
        "cleanup-artifacts": cmd_cleanup_artifacts,
    }


def cmd_cleanup_artifacts(args, token, *, cleanup_provider_factory=None):
    provider_factory = cleanup_provider_factory or _require_provider
    provider = provider_factory(token)
    plan = {
        "provider": args.provider,
        "repo": args.repo,
        "issues": list(args.issues),
        "mergeRequests": list(args.merge_requests),
        "branches": list(args.branches),
    }
    if args.dry_run:
        print("Dry run: cleanup artifacts payload")
        print(json.dumps(plan, indent=2))
        return

    results = provider.cleanup_artifacts(
        issue_ids=list(args.issues),
        merge_request_ids=list(args.merge_requests),
        branches=list(args.branches),
    )
    print(f"✓ Cleanup applied in {args.repo}.")
    for issue_id, state in results.get("issues", []):
        print(f"  Issue  {issue_id} -> {state}")
    for merge_request_id, state in results.get("mergeRequests", []):
        print(f"  MR     {merge_request_id} -> {state}")
    for branch in results.get("branches", []):
        print(f"  Branch deleted: {branch}")