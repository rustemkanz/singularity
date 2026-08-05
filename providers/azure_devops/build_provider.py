import urllib.parse

from app_config import API_VER, ORG, PROJECT
from errors import CliError
from providers.azure_devops.http import api, api_text
from providers.interfaces import BuildProvider
from workflow_models import (
    BuildLogMatch,
    BuildLogsSnapshot,
    BuildRun,
    BuildStageSummary,
    BuildStatusSnapshot,
    BuildTaskSummary,
    PendingBuildApproval,
    QueuedBuild,
)


class AzureDevOpsBuildProvider(BuildProvider):
    def __init__(self, token: str):
        self.token = token

    def list_recent_builds(
        self,
        *,
        definition: int,
        project: str,
        branch: str | None,
        commit: str | None,
        limit: int,
    ) -> list[BuildRun]:
        project_name = urllib.parse.quote(project)
        branch_filter = branch
        commit_filter = commit.lower() if commit else None
        if branch_filter and not branch_filter.startswith("refs/"):
            branch_filter = f"refs/heads/{branch_filter}"
        fetch_top = max(limit * 10, 50) if branch_filter or commit_filter else limit
        url = (
            f"https://dev.azure.com/{ORG}/{project_name}/_apis/build/builds"
            f"?definitions={definition}&$top={fetch_top}"
            f"&queryOrder=queueTimeDescending&api-version={API_VER}"
        )
        data = api(self.token, "GET", url)
        builds: list[BuildRun] = []
        for build in data.get("value", []):
            if branch_filter and build.get("sourceBranch") != branch_filter:
                continue
            source_version = build.get("sourceVersion") or ""
            if commit_filter and not source_version.lower().startswith(commit_filter):
                continue
            builds.append(BuildRun(
                id=build.get("id"),
                status=build.get("status") or "unknown",
                result=build.get("result") or "",
                build_number=build.get("buildNumber") or "?",
                queued_at=build.get("queueTime"),
                source_branch=build.get("sourceBranch") or "?",
                reason=build.get("reason"),
                source_version=source_version or None,
            ))
            if len(builds) >= limit:
                break
        return builds

    def get_build_status_snapshot(
        self,
        *,
        build_id: int,
        project: str,
        limit: int,
        stage_name: str | None = None,
        only_failed: bool = False,
        only_active: bool = False,
    ) -> BuildStatusSnapshot:
        build = self._fetch_build(build_id, project)
        timeline = self._fetch_timeline(build_id, project)
        stages, orphan_tasks = self._build_timeline_summary(
            build_id,
            project,
            timeline,
            limit,
            stage_name=stage_name,
            only_failed=only_failed,
            only_active=only_active,
        )
        return BuildStatusSnapshot(
            build=BuildRun(
                id=build.get("id"),
                build_number=build.get("buildNumber") or "?",
                status=build.get("status") or "unknown",
                result=build.get("result") or "",
                source_branch=build.get("sourceBranch") or "?",
                queued_at=build.get("queueTime"),
                reason=build.get("reason"),
                source_version=build.get("sourceVersion"),
            ),
            stages=stages,
            orphan_tasks=orphan_tasks,
        )

    def get_build_logs(
        self,
        *,
        build_id: int,
        project: str,
        stage_name: str | None,
        job_name: str | None,
        step_name: str | None,
        failed_only: bool,
    ) -> BuildLogsSnapshot:
        build = self._fetch_build(build_id, project)
        timeline = self._fetch_timeline(build_id, project)
        matches = self._collect_log_matches(
            build_id,
            project,
            timeline,
            stage_name=stage_name,
            job_name=job_name,
            step_name=step_name,
            failed_only=failed_only,
        )
        if not matches:
            filter_descriptions = []
            if failed_only:
                filter_descriptions.append("failed records")
            if stage_name:
                filter_descriptions.append(f"stage '{stage_name}'")
            if job_name:
                filter_descriptions.append(f"job '{job_name}'")
            if step_name:
                filter_descriptions.append(f"step '{step_name}'")
            scope = f" matching {' and '.join(filter_descriptions)}" if filter_descriptions else ""
            raise CliError(f"ERROR: No build logs found for build {build_id}{scope}.")
        return BuildLogsSnapshot(
            build=BuildRun(
                id=build.get("id"),
                build_number=build.get("buildNumber") or "?",
                status=build.get("status") or "unknown",
                result=build.get("result") or "",
                source_branch=build.get("sourceBranch") or "?",
                queued_at=build.get("queueTime"),
                reason=build.get("reason"),
                source_version=build.get("sourceVersion"),
            ),
            matches=matches,
        )

    def queue_build(
        self,
        *,
        definition: int,
        project: str,
        source_branch: str,
        parameters: dict | None,
    ) -> QueuedBuild:
        project_name = urllib.parse.quote(project)
        payload: dict = {
            "definition": {"id": definition},
            "sourceBranch": source_branch,
        }
        if parameters is not None:
            payload["templateParameters"] = parameters
        url = (
            f"https://dev.azure.com/{ORG}/{project_name}/_apis/build/builds"
            f"?api-version={API_VER}"
        )
        build = api(self.token, "POST", url, payload)
        return QueuedBuild(
            id=build.get("id"),
            build_number=build.get("buildNumber") or "?",
            source_branch=build.get("sourceBranch") or "?",
        )

    def list_pending_approvals(
        self,
        *,
        project: str,
        build_id: int,
    ) -> list[PendingBuildApproval]:
        project_name = urllib.parse.quote(project or PROJECT)
        url = (
            f"https://dev.azure.com/{ORG}/{project_name}/_apis/pipelines/approvals"
            f"?api-version=7.1-preview.1"
        )
        data = api(self.token, "GET", url)
        approvals = []
        for approval in data.get("value", []):
            if approval.get("status") != "pending":
                continue
            if str(approval.get("pipeline", {}).get("id", "")) != str(build_id):
                continue
            approvals.append(PendingBuildApproval(
                id=str(approval.get("approvalId") or approval.get("id") or ""),
                pipeline_id=str(approval.get("pipeline", {}).get("id", "")) or None,
                status=approval.get("status") or "unknown",
            ))
        return approvals

    def approve_pending_approval(
        self,
        *,
        project: str,
        approval_id: str,
        comment: str,
    ) -> bool:
        project_name = urllib.parse.quote(project or PROJECT)
        url = (
            f"https://dev.azure.com/{ORG}/{project_name}/_apis/pipelines/approvals"
            f"?api-version=7.1-preview.1"
        )
        payload = [{"approvalId": approval_id, "status": "approved", "comment": comment}]
        result = api(self.token, "PATCH", url, payload)
        updated = result.get("value", [result]) if isinstance(result, dict) else result
        return any(approval.get("status") == "approved" for approval in updated)

    def _fetch_build(self, build_id: int, project: str | None = None) -> dict:
        project_name = urllib.parse.quote(project or PROJECT)
        url = (
            f"https://dev.azure.com/{ORG}/{project_name}/_apis/build/builds/{build_id}"
            f"?api-version={API_VER}"
        )
        return api(self.token, "GET", url)

    def _fetch_timeline(self, build_id: int, project: str | None = None) -> dict:
        project_name = urllib.parse.quote(project or PROJECT)
        url = (
            f"https://dev.azure.com/{ORG}/{project_name}/_apis/build/builds/{build_id}/timeline"
            f"?api-version={API_VER}"
        )
        return api(self.token, "GET", url)

    def _fetch_log_text(self, build_id: int, log_id: int, project: str | None = None) -> str:
        project_name = urllib.parse.quote(project or PROJECT)
        url = (
            f"https://dev.azure.com/{ORG}/{project_name}/_apis/build/builds/{build_id}/logs/{log_id}"
            f"?api-version={API_VER}"
        )
        return api_text(self.token, "GET", url)

    def _collect_log_matches(
        self,
        build_id: int,
        project: str,
        timeline: dict,
        *,
        stage_name: str | None,
        job_name: str | None,
        step_name: str | None,
        failed_only: bool,
    ) -> list[BuildLogMatch]:
        all_records = {record["id"]: record for record in timeline.get("records", [])}
        seen_log_ids: set[int] = set()
        matches: list[BuildLogMatch] = []
        for record in sorted(all_records.values(), key=lambda item: item.get("order", 0)):
            log = record.get("log") or {}
            log_id = log.get("id")
            if log_id is None:
                continue
            if int(log_id) in seen_log_ids:
                continue
            resolved_stage_name = self._find_ancestor_name(all_records, record, "Stage")
            resolved_job_name = self._find_ancestor_name(all_records, record, "Job")
            record_type = record.get("type") or "Record"
            record_name = record.get("name") or "<unnamed>"
            if not self._matches_filter(resolved_stage_name, stage_name):
                continue
            if not self._matches_filter(resolved_job_name, job_name):
                continue
            if step_name and (record_type != "Task" or not self._matches_filter(record_name, step_name)):
                continue
            result = record.get("result") or "pending"
            if failed_only and result != "failed":
                continue
            seen_log_ids.add(int(log_id))
            issues = record.get("issues") or []
            matches.append(BuildLogMatch(
                log_id=int(log_id),
                stage_name=resolved_stage_name,
                job_name=resolved_job_name,
                record_name=record_name,
                record_type=record_type,
                state=record.get("state") or "unknown",
                result=result,
                issue=issues[0].get("message", "") if issues else "",
                content=self._fetch_log_text(build_id, int(log_id), project),
            ))
        return matches

    def _find_ancestor_name(self, all_records: dict[str, dict], record: dict, record_type: str) -> str | None:
        parent_id = record.get("parentId")
        while parent_id:
            parent = all_records.get(parent_id)
            if not parent:
                return None
            if parent.get("type") == record_type:
                return parent.get("name")
            parent_id = parent.get("parentId")
        return None

    def _matches_filter(self, actual_value: str | None, expected_value: str | None) -> bool:
        if not expected_value:
            return True
        if not actual_value:
            return False
        return expected_value.strip().lower() in actual_value.strip().lower()

    def _build_timeline_summary(
        self,
        build_id: int,
        project: str,
        timeline: dict,
        limit: int,
        *,
        stage_name: str | None,
        only_failed: bool,
        only_active: bool,
    ) -> tuple[list[BuildStageSummary], list[BuildTaskSummary]]:
        all_records = {record["id"]: record for record in timeline.get("records", [])}

        def find_stage_id(record: dict) -> str | None:
            parent_id = record.get("parentId")
            if not parent_id:
                return None
            parent = all_records.get(parent_id)
            if not parent:
                return None
            if parent.get("type") == "Stage":
                return parent_id
            return find_stage_id(parent)

        stages = sorted(
            [record for record in all_records.values() if record.get("type") == "Stage"],
            key=lambda record: record.get("order", 0),
        )

        tasks_by_stage: dict[str, list[dict]] = {}
        jobs_by_stage: dict[str, list[dict]] = {}
        checkpoints_by_stage: dict[str, list[dict]] = {}
        orphan_tasks: list[dict] = []
        for record in all_records.values():
            record_type = record.get("type", "")
            if record_type in ("Checkpoint", "Checkpoint.Authorization"):
                stage_id = find_stage_id(record)
                if stage_id:
                    checkpoints_by_stage.setdefault(stage_id, []).append(record)
            elif record_type == "Job":
                stage_id = find_stage_id(record)
                if stage_id:
                    jobs_by_stage.setdefault(stage_id, []).append(record)
            elif record_type == "Task":
                stage_id = find_stage_id(record)
                if stage_id:
                    tasks_by_stage.setdefault(stage_id, []).append(record)
                else:
                    orphan_tasks.append(record)

        stage_summaries: list[BuildStageSummary] = []
        for stage in stages:
            stage_id = stage["id"]
            stage_title = stage.get("name", "<stage>")
            if not self._matches_filter(stage_title, stage_name):
                continue
            approval_required = any(
                checkpoint.get("state") not in ("completed",)
                and checkpoint.get("result") not in ("succeeded", "failed", "canceled")
                for checkpoint in checkpoints_by_stage.get(stage_id, [])
            )
            tasks = sorted(
                tasks_by_stage.get(stage_id, []),
                key=lambda record: record.get("lastModified", ""),
                reverse=True,
            )
            task_summaries: list[BuildTaskSummary] = []
            for task in tasks[:limit]:
                if only_failed and (task.get("result") or "pending") != "failed":
                    continue
                if only_active and (task.get("state") or "unknown") not in {"inProgress", "pending"}:
                    continue
                issues = task.get("issues") or []
                issue_message = issues[0].get("message", "") if issues else ""
                log = task.get("log") or {}
                task_summaries.append(BuildTaskSummary(
                    name=task.get("name", "<unnamed>"),
                    state=task.get("state", "unknown"),
                    result=task.get("result") or "pending",
                    issue=issue_message,
                    log_id=int(log.get("id")) if log.get("id") is not None else None,
                    failure_details=self._build_failure_details(
                        build_id,
                        project,
                        task,
                        issue_message=issue_message,
                    ),
                ))
            if only_failed and stage.get("result") != "failed" and not task_summaries:
                continue
            if only_active and stage.get("state") not in {"inProgress", "pending"} and not task_summaries:
                continue
            stage_summaries.append(BuildStageSummary(
                name=stage_title,
                state=stage.get("state", "unknown"),
                result=stage.get("result") or "pending",
                approval_required=approval_required,
                pending_reason=self._describe_stage_pending_reason(
                    stage,
                    checkpoints_by_stage.get(stage_id, []),
                    jobs_by_stage.get(stage_id, []),
                    task_summaries,
                ),
                tasks=task_summaries,
            ))

        orphan_summaries: list[BuildTaskSummary] = []
        if orphan_tasks:
            orphan_tasks.sort(key=lambda record: record.get("lastModified", ""), reverse=True)
            for task in orphan_tasks[:limit]:
                if only_failed and (task.get("result") or "pending") != "failed":
                    continue
                if only_active and (task.get("state") or "unknown") not in {"inProgress", "pending"}:
                    continue
                issues = task.get("issues") or []
                issue_message = issues[0].get("message", "") if issues else ""
                log = task.get("log") or {}
                orphan_summaries.append(BuildTaskSummary(
                    name=task.get("name", "<unnamed>"),
                    state=task.get("state", "unknown"),
                    result=task.get("result") or "pending",
                    issue=issue_message,
                    log_id=int(log.get("id")) if log.get("id") is not None else None,
                    failure_details=self._build_failure_details(
                        build_id,
                        project,
                        task,
                        issue_message=issue_message,
                    ),
                ))

        return stage_summaries, orphan_summaries

    def _describe_stage_pending_reason(
        self,
        stage: dict,
        checkpoints: list[dict],
        jobs: list[dict],
        tasks: list[BuildTaskSummary],
    ) -> str | None:
        active_checkpoints = [
            checkpoint
            for checkpoint in checkpoints
            if checkpoint.get("state") not in ("completed",)
            and checkpoint.get("result") not in ("succeeded", "failed", "canceled")
        ]
        if active_checkpoints:
            names = [checkpoint.get("name") for checkpoint in active_checkpoints if checkpoint.get("name")]
            waiting_text = "waiting for approval" if any(
                checkpoint.get("type") == "Checkpoint.Authorization"
                or "approval" in (checkpoint.get("name") or "").lower()
                or "manual" in (checkpoint.get("name") or "").lower()
                for checkpoint in active_checkpoints
            ) else "waiting on stage check"
            if names:
                return f"{waiting_text}: {', '.join(dict.fromkeys(names))}"
            return waiting_text

        stage_state = stage.get("state") or "unknown"
        if stage_state == "pending":
            pending_task_names = [task.name for task in tasks if task.state in {"pending", "inProgress"}]
            if pending_task_names:
                return f"waiting on task execution: {', '.join(dict.fromkeys(pending_task_names[:3]))}"

            waiting_jobs = [job.get("name") for job in jobs if (job.get("state") or "") in {"pending", "inProgress"} and job.get("name")]
            if waiting_jobs:
                return f"waiting on job: {', '.join(dict.fromkeys(waiting_jobs[:3]))}"

            return "waiting on upstream dependency, agent capacity, or deployment checks"
        return None

    def _build_failure_details(
        self,
        build_id: int,
        project: str,
        record: dict,
        *,
        issue_message: str,
    ) -> list[str]:
        result = (record.get("result") or "").lower()
        if result != "failed":
            return []
        log = record.get("log") or {}
        log_id = log.get("id")
        if log_id is None:
            return [issue_message] if issue_message else []
        log_text = self._fetch_log_text(build_id, int(log_id), project)
        return self._extract_failure_details(log_text, issue_message=issue_message)

    def _extract_failure_details(self, log_text: str, *, issue_message: str) -> list[str]:
        lines = [line.strip() for line in log_text.splitlines() if line.strip()]
        if not lines:
            return [issue_message] if issue_message else []

        traceback_index = next(
            (index for index, line in enumerate(lines) if "Traceback (most recent call last):" in line),
            None,
        )
        if traceback_index is not None:
            traceback_lines = lines[traceback_index:]
            if len(traceback_lines) > 6:
                summary = [traceback_lines[0], *traceback_lines[-5:]]
            else:
                summary = traceback_lines
        else:
            error_markers = (
                "##[error]",
                "traceback",
                "exception",
                "error:",
                "exited with code",
                "non-zero exit",
                "command failed",
                "returned exit code",
            )
            relevant = [
                line for line in lines
                if any(marker in line.lower() for marker in error_markers)
            ]
            summary = relevant[-5:] if relevant else lines[-5:]

        combined = []
        if issue_message:
            combined.append(issue_message)
        combined.extend(summary)

        deduped: list[str] = []
        seen: set[str] = set()
        for line in combined:
            normalized = line.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(normalized)
        return deduped