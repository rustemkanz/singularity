import json
import time

from errors import CliError
from git_client import current_git_branch, current_git_commit


def _require_provider(factory):
    if factory is None:
        raise RuntimeError("Missing build provider factory.")
    return factory


def cmd_builds(args, token, *, build_build_provider_func=None):
    latest_for_branch = getattr(args, "latest_for_branch", None)
    branch = latest_for_branch or getattr(args, "branch", None)
    commit = getattr(args, "commit", None)
    if latest_for_branch and getattr(args, "branch", None):
        raise CliError("ERROR: Use either --branch or --latest-for-branch, not both.")
    limit = 1 if latest_for_branch else args.limit
    provider = _require_provider(build_build_provider_func)(token)
    builds = provider.list_recent_builds(
        definition=args.definition,
        project=args.project,
        branch=branch,
        commit=commit,
        limit=limit,
    )
    if args.json:
        print(json.dumps({
            "definition": args.definition,
            "project": args.project,
            "branch": branch,
            "commit": commit,
            "builds": [build.to_legacy_dict() for build in builds],
        }, indent=2))
        return
    for build in builds:
        queued = (build.queued_at or "?")[:19].replace("T", " ")
        reason = (build.reason or "")[:12]
        print(
            f"{build.id}  {build.status:<12}  {str(build.result or ''):<10}  {reason:<12}  "
            f"{build.build_number:<14}  {queued}  {build.source_branch}"
        )


def cmd_build_logs(args, token, *, build_build_provider_func=None):
    provider = _require_provider(build_build_provider_func)(token)
    snapshot = provider.get_build_logs(
        build_id=args.build_id,
        project=args.project,
        stage_name=args.stage,
        job_name=args.job,
        step_name=args.step,
        failed_only=args.failed,
    )
    if args.json:
        print(json.dumps(snapshot.to_legacy_dict(), indent=2))
        return

    build = snapshot.build
    print(
        f"Build {build.build_number}: status={build.status}, "
        f"result={build.result or 'in-progress'}, branch={build.source_branch}"
    )
    print()
    for index, match in enumerate(snapshot.matches):
        print(match.header_line())
        print(match.content.rstrip() or "<empty log>")
        if index < len(snapshot.matches) - 1:
            print()


def cmd_queue_build(
    args,
    token,
    *,
    build_build_provider_func=None,
    current_git_branch_func=None,
    current_git_commit_func=None,
):
    current_branch = current_git_branch_func or current_git_branch
    current_commit = current_git_commit_func or current_git_commit
    provider = _require_provider(build_build_provider_func)(token)
    branch = args.branch or current_branch()
    source_branch = branch if branch.startswith("refs/") else f"refs/heads/{branch}"
    parameters = None
    if args.parameters:
        try:
            parameters = json.loads(args.parameters)
        except json.JSONDecodeError as exc:
            raise CliError(f"ERROR: --parameters is not valid JSON: {exc}")

    if args.branch is None:
        duplicate_builds = provider.list_recent_builds(
            definition=args.definition,
            project=args.project,
            branch=source_branch,
            commit=current_commit(),
            limit=5,
        )
        if duplicate_builds:
            print("Warning: recent builds already exist for this branch and commit:")
            for duplicate in duplicate_builds:
                reason = duplicate.reason or "unknown"
                result = duplicate.result or "in-progress"
                print(
                    f"  - {duplicate.id} reason={reason} status={duplicate.status} "
                    f"result={result} build={duplicate.build_number}"
                )
            print()

    build = provider.queue_build(
        definition=args.definition,
        project=args.project,
        source_branch=source_branch,
        parameters=parameters,
    )
    print(f"Queued build {build.id} ({build.build_number}) on {build.source_branch}")


def fetch_pending_approvals(token: str, project: str, build_id: int, *, build_build_provider_func=None) -> list[dict]:
    provider = _require_provider(build_build_provider_func)(token)
    return [approval.to_legacy_dict() for approval in provider.list_pending_approvals(project=project, build_id=build_id)]


def approve_pipeline(
    token: str,
    project: str,
    approval_id: str,
    comment: str = "Approved via CLI",
    *,
    build_build_provider_func=None,
) -> bool:
    provider = _require_provider(build_build_provider_func)(token)
    return provider.approve_pending_approval(project=project, approval_id=approval_id, comment=comment)


def _prompt_to_approve_pending_approvals(
    provider,
    *,
    project: str,
    build_id: int,
    input_func,
) -> None:
    approvals = provider.list_pending_approvals(project=project, build_id=build_id)
    if not approvals:
        return

    print("Pending approvals:\n")
    for approval in approvals:
        pipeline_text = f" pipeline={approval.pipeline_id}" if approval.pipeline_id else ""
        print(f"  - approval={approval.id}{pipeline_text} status={approval.status}")
        response = input_func(f"Approve {approval.id}? [y/N]: ").strip().lower()
        if response not in {"y", "yes"}:
            print(f"Skipped approval {approval.id}.\n")
            continue
        approved = provider.approve_pending_approval(
            project=project,
            approval_id=approval.id,
            comment="Approved via CLI",
        )
        if approved:
            print(f"Approved {approval.id}.\n")
        else:
            print(f"Approval {approval.id} did not report success.\n")


def _snapshot_display_entries(snapshot, *, show_log_ids: bool = False) -> list[tuple[str, str]]:
    build = snapshot.build
    reason_text = f", reason={build.reason}" if build.reason else ""
    entries = [(
        "build",
        f"Build {build.build_number}: status={build.status}, "
        f"result={build.result or 'in-progress'}{reason_text}, branch={build.source_branch}",
    )]
    for stage in snapshot.stages:
        approval_note = " ⏳ WAITING FOR APPROVAL" if stage.approval_required else ""
        entries.append((
            f"stage:{stage.name}",
            f"[{stage.name}] state={stage.state}, result={stage.result}{approval_note}",
        ))
        if stage.pending_reason:
            entries.append((f"stage:{stage.name}:reason", f"  reason: {stage.pending_reason}"))
        for task in stage.tasks:
            issue_text = f" | issue={task.issue}" if task.issue else ""
            log_text = f" | log={task.log_id}" if show_log_ids and task.log_id is not None else ""
            task_key = f"task:{stage.name}:{task.name}"
            entries.append((
                task_key,
                f"  - {task.name}: state={task.state}, result={task.result}{log_text}{issue_text}",
            ))
            for index, detail in enumerate(task.failure_details or []):
                entries.append((f"{task_key}:failure:{index}", f"    failure: {detail}"))
    for index, task in enumerate(snapshot.orphan_tasks):
        issue_text = f" | issue={task.issue}" if task.issue else ""
        log_text = f" | log={task.log_id}" if show_log_ids and task.log_id is not None else ""
        task_key = f"orphan:{index}:{task.name}"
        entries.append((
            task_key,
            f"  - {task.name}: state={task.state}, result={task.result}{log_text}{issue_text}",
        ))
        for detail_index, detail in enumerate(task.failure_details or []):
            entries.append((f"{task_key}:failure:{detail_index}", f"    failure: {detail}"))
    return entries


def _render_snapshot(snapshot, *, previous_entries=None, delta_only: bool = False, show_log_ids: bool = False) -> list[str]:
    entries = _snapshot_display_entries(snapshot, show_log_ids=show_log_ids)
    if not delta_only or previous_entries is None:
        return [line for _key, line in entries]

    previous_map = {key: line for key, line in previous_entries}
    return [line for key, line in entries if previous_map.get(key) != line]


def cmd_build_status(
    args,
    token,
    *,
    build_build_provider_func=None,
    get_token_func=None,
    input_func=None,
    sleep_func=None,
):
    watch_enabled = getattr(args, "watch", False)
    approve_enabled = getattr(args, "approve", False)
    verbose_enabled = getattr(args, "verbose", False)
    if args.json and watch_enabled:
        raise CliError("ERROR: --json is only supported for a single build-status snapshot. Omit --watch.")
    if approve_enabled and not watch_enabled:
        raise CliError("ERROR: --approve requires --watch.")
    provider_factory = _require_provider(build_build_provider_func)
    refresh_token = get_token_func or (lambda: token)
    prompt_for_input = input if input_func is None else input_func
    pause = time.sleep if sleep_func is None else sleep_func
    previous_entries = None
    if args.json:
        snapshot = provider_factory(token).get_build_status_snapshot(
            build_id=args.build_id,
            project=args.project,
            limit=args.limit,
            stage_name=args.stage,
            only_failed=args.only_failed,
            only_active=args.only_active,
        )
        print(json.dumps(snapshot.to_legacy_dict(show_log_ids=args.show_log_ids), indent=2))
        return
    while True:
        token = refresh_token()
        provider = provider_factory(token)
        snapshot = provider.get_build_status_snapshot(
            build_id=args.build_id,
            project=args.project,
            limit=args.limit,
            stage_name=args.stage,
            only_failed=args.only_failed,
            only_active=args.only_active,
        )
        build = snapshot.build
        lines_to_print = _render_snapshot(
            snapshot,
            previous_entries=previous_entries,
            delta_only=watch_enabled and not verbose_enabled,
            show_log_ids=args.show_log_ids,
        )
        if lines_to_print:
            for line in lines_to_print:
                print(line)
            print()
        previous_entries = _snapshot_display_entries(snapshot, show_log_ids=args.show_log_ids)

        if approve_enabled:
            _prompt_to_approve_pending_approvals(
                provider,
                project=args.project,
                build_id=args.build_id,
                input_func=prompt_for_input,
            )

        if not watch_enabled or build.status == "completed":
            return
        pause(args.interval)


def register_build_subcommands(sub):
    p = sub.add_parser("builds", help="List recent builds for a pipeline definition")
    p.add_argument("--definition", type=int, required=True, metavar="DEF_ID",
                   help="Pipeline definition ID")
    p.add_argument("--project", required=True, metavar="NAME",
                   help="ADO project name")
    p.add_argument("--branch", metavar="BRANCH",
                   help="Filter by branch name or ref (e.g. main or refs/heads/main)")
    p.add_argument("--latest-for-branch", metavar="BRANCH",
                   help="Shortcut for the latest build on a branch")
    p.add_argument("--commit", metavar="SHA",
                   help="Filter by source commit SHA (full or prefix)")
    p.add_argument("--limit", type=int, default=5, metavar="COUNT",
                   help="Number of builds to show (default: 5)")
    p.add_argument("--json", action="store_true", help="Emit structured JSON output for scripting")

    p = sub.add_parser("build-status", help="Show build status grouped by stage")
    p.add_argument("build_id", type=int, help="Azure DevOps build ID")
    p.add_argument("--project", required=True, metavar="NAME",
                   help="ADO project name")
    p.add_argument("--watch", action="store_true",
                   help="Poll until the build completes")
    p.add_argument("--approve", action="store_true",
                   help="Interactively prompt to approve pending gates while watching (requires --watch)")
    p.add_argument("--verbose", action="store_true",
                   help="When using --watch, print full snapshots every poll instead of only changed lines")
    p.add_argument("--show-log-ids", action="store_true",
                   help="Show timeline log ids next to task lines when available")
    p.add_argument("--stage", metavar="NAME",
                   help="Only show timeline entries for matching stage names")
    p.add_argument("--only-failed", action="store_true",
                   help="Only show failed stages or tasks")
    p.add_argument("--only-active", action="store_true",
                   help="Only show pending or in-progress stages or tasks")
    p.add_argument("--interval", type=int, default=60, metavar="SECONDS",
                   help="Polling interval when using --watch (default: 60)")
    p.add_argument("--limit", type=int, default=8, metavar="COUNT",
                   help="How many tasks per stage to show (default: 8)")
    p.add_argument("--json", action="store_true", help="Emit structured JSON output for scripting")

    p = sub.add_parser("build-logs", help="Show build logs for matching stages, jobs, or steps")
    p.add_argument("build_id", type=int, help="Azure DevOps build ID")
    p.add_argument("--project", required=True, metavar="NAME",
                   help="ADO project name")
    p.add_argument("--stage", metavar="NAME",
                   help="Filter logs to records under a stage name")
    p.add_argument("--job", metavar="NAME",
                   help="Filter logs to records under a job name")
    p.add_argument("--step", metavar="NAME",
                   help="Filter logs to matching step/task names")
    p.add_argument("--failed", action="store_true",
                   help="Only include failed records")
    p.add_argument("--json", action="store_true", help="Emit structured JSON output for scripting")

    p = sub.add_parser("queue-build", help="Queue a new pipeline run")
    p.add_argument("--definition", type=int, required=True, metavar="DEF_ID",
                   help="Pipeline definition ID")
    p.add_argument("--project", required=True, metavar="NAME",
                   help="ADO project name")
    p.add_argument("--branch", metavar="BRANCH",
                   help="Source branch (default: current git branch)")
    p.add_argument("--parameters", metavar="JSON",
                   help="Template parameters as a JSON object, e.g. '{\"env\": \"dev\"}'")