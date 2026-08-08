import json
import time

from app_config import ORG
from errors import CliError
from git_client import current_git_branch, resolve_git_commit
from mutation_plans import MutationPlan, render_plan_preview, require_approved_plan


def _require_provider(factory):
    if factory is None:
        raise RuntimeError("Missing build provider factory.")
    return factory


def _preview_or_apply(args, plan: MutationPlan) -> bool:
    should_apply = require_approved_plan(plan, getattr(args, "apply", None))
    if not should_apply:
        print(render_plan_preview(plan, json_output=getattr(args, "json", False)))
    return should_apply


def _add_apply_plan_arguments(parser) -> None:
    parser.add_argument(
        "--apply",
        metavar="PLAN_ID",
        help="Apply only the exact previewed plan identified by PLAN_ID",
    )


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
    resolve_git_commit_func=None,
):
    current_branch = current_git_branch_func or current_git_branch
    commit_resolver = resolve_git_commit_func or resolve_git_commit
    branch = args.branch or current_branch()
    if not branch or branch == "HEAD":
        raise CliError("ERROR: Could not resolve a source branch. Pass --branch explicitly.")
    source_branch = branch if branch.startswith("refs/") else f"refs/heads/{branch}"
    parameters = None
    if args.parameters:
        try:
            parameters = json.loads(args.parameters)
        except json.JSONDecodeError as exc:
            raise CliError(f"ERROR: --parameters is not valid JSON: {exc}")
        if not isinstance(parameters, dict):
            raise CliError("ERROR: --parameters must be a JSON object.")
    commit_ref = getattr(args, "commit", None) or (branch if args.branch else "HEAD")
    source_version = commit_resolver(commit_ref)
    provider = _require_provider(build_build_provider_func)(token)
    duplicate_builds = provider.list_recent_builds(
        definition=args.definition,
        project=args.project,
        branch=source_branch,
        commit=source_version,
        limit=5,
    )
    duplicate_snapshot = [
        {
            "id": duplicate.id,
            "buildNumber": duplicate.build_number,
            "status": duplicate.status,
            "result": duplicate.result,
            "reason": duplicate.reason,
        }
        for duplicate in duplicate_builds
    ]
    allow_duplicate = bool(getattr(args, "allow_duplicate", False))
    if duplicate_builds and not allow_duplicate:
        duplicate_ids = ", ".join(str(build.id) for build in duplicate_builds)
        raise CliError(
            "ERROR: A recent build already exists for this branch and commit "
            f"({duplicate_ids}); no build was queued. Inspect it first, or preview an intentional "
            "duplicate with --allow-duplicate."
        )

    mutation_plan = MutationPlan(
        action="build.queue",
        target={
            "provider": "azure-devops",
            "organization": ORG,
            "project": args.project,
            "definitionId": args.definition,
        },
        payload={
            "sourceBranch": source_branch,
            "sourceVersion": source_version,
            "parameters": parameters,
            "allowDuplicate": allow_duplicate,
            "recentDuplicates": duplicate_snapshot,
        },
    )
    if not _preview_or_apply(args, mutation_plan):
        return

    if duplicate_builds:
        print("Approved duplicate queue; recent builds already exist for this branch and commit:")
        for duplicate in duplicate_builds:
            reason = duplicate.reason or "unknown"
            result = duplicate.result or "in-progress"
            print(
                f"  - {duplicate.id} reason={reason} status={duplicate.status} "
                f"result={result} build={duplicate.build_number}"
            )
        print()

    build = provider.queue_build(
        definition=mutation_plan.target["definitionId"],
        project=mutation_plan.target["project"],
        source_branch=mutation_plan.payload["sourceBranch"],
        source_version=mutation_plan.payload["sourceVersion"],
        parameters=mutation_plan.payload["parameters"],
    )
    print(f"Queued build {build.id} ({build.build_number}) on {build.source_branch}")


def fetch_pending_approvals(token: str, project: str, build_id: int, *, build_build_provider_func=None) -> list[dict]:
    provider = _require_provider(build_build_provider_func)(token)
    return [approval.to_legacy_dict() for approval in provider.list_pending_approvals(project=project, build_id=build_id)]


def cmd_build_approvals(args, token, *, build_build_provider_func=None):
    provider = _require_provider(build_build_provider_func)(token)
    approvals = provider.list_pending_approvals(project=args.project, build_id=args.build_id)
    if getattr(args, "json", False):
        print(json.dumps({
            "buildId": args.build_id,
            "project": args.project,
            "approvals": [approval.to_legacy_dict() for approval in approvals],
        }, indent=2))
        return
    if not approvals:
        print(f"No pending approvals for build {args.build_id}.")
        return
    for approval in approvals:
        pipeline_text = f" pipeline={approval.pipeline_id}" if approval.pipeline_id else ""
        print(f"  - approval={approval.id}{pipeline_text} status={approval.status}")


def cmd_approve_gate(args, token, *, build_build_provider_func=None):
    provider = _require_provider(build_build_provider_func)(token)
    approvals = provider.list_pending_approvals(project=args.project, build_id=args.build_id)
    approval = next((entry for entry in approvals if str(entry.id) == str(args.approval)), None)
    if approval is None:
        raise CliError(
            f"ERROR: Approval '{args.approval}' is not pending for build {args.build_id}. "
            "Run build-approvals again to refresh the available approval IDs."
        )
    mutation_plan = MutationPlan(
        action="build.gate.approve",
        target={
            "provider": "azure-devops",
            "organization": ORG,
            "project": args.project,
            "buildId": args.build_id,
            "approvalId": approval.id,
        },
        payload={
            "status": "approved",
            "comment": args.comment,
            "pendingApproval": approval.to_legacy_dict(),
        },
    )
    if not _preview_or_apply(args, mutation_plan):
        return
    approved = provider.approve_pending_approval(
        project=mutation_plan.target["project"],
        approval_id=mutation_plan.target["approvalId"],
        comment=mutation_plan.payload["comment"],
    )
    if not approved:
        raise CliError(f"ERROR: Approval '{approval.id}' did not report success.")
    print(f"Approved {approval.id} for build {args.build_id}.")


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
    verbose_enabled = getattr(args, "verbose", False)
    if args.json and watch_enabled:
        raise CliError("ERROR: --json is only supported for a single build-status snapshot. Omit --watch.")
    provider_factory = _require_provider(build_build_provider_func)
    refresh_token = get_token_func or (lambda: token)
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

    p = sub.add_parser("build-approvals", help="List pending approvals for a build")
    p.add_argument("build_id", type=int, help="Azure DevOps build ID")
    p.add_argument("--project", required=True, metavar="NAME", help="ADO project name")
    p.add_argument("--json", action="store_true", help="Emit structured JSON output for scripting")

    p = sub.add_parser("approve-gate", help="Preview approval of one pending pipeline gate")
    p.add_argument("build_id", type=int, help="Azure DevOps build ID")
    p.add_argument("--project", required=True, metavar="NAME", help="ADO project name")
    p.add_argument("--approval", required=True, metavar="APPROVAL_ID", help="Pending approval ID")
    p.add_argument("--comment", default="Approved via CLI", metavar="TEXT", help="Approval comment")
    p.add_argument("--json", action="store_true", help="Emit the mutation plan as structured JSON")
    _add_apply_plan_arguments(p)

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

    p = sub.add_parser("queue-build", help="Preview queueing a new pipeline run")
    p.add_argument("--definition", type=int, required=True, metavar="DEF_ID",
                   help="Pipeline definition ID")
    p.add_argument("--project", required=True, metavar="NAME",
                   help="ADO project name")
    p.add_argument("--branch", metavar="BRANCH",
                   help="Source branch (default: current git branch)")
    p.add_argument("--commit", metavar="REF",
                   help="Commit/ref to resolve (default: source branch, or HEAD on the current branch)")
    p.add_argument("--allow-duplicate", action="store_true",
                   help="Preview an intentional duplicate when an exact branch/commit run already exists; apply still requires approval")
    p.add_argument("--parameters", metavar="JSON",
                   help="Template parameters as a JSON object, e.g. '{\"env\": \"dev\"}'")
    p.add_argument("--json", action="store_true", help="Emit the mutation plan as structured JSON")
    _add_apply_plan_arguments(p)
