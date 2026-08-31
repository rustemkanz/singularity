from dataclasses import dataclass


DEFAULT_PROVIDER = "unknown"


@dataclass(frozen=True)
class Sprint:
    id: str
    name: str
    path: str
    start_date: str | None
    finish_date: str | None
    provider: str = DEFAULT_PROVIDER

    def to_legacy_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "path": self.path,
            "attributes": {
                "startDate": self.start_date,
                "finishDate": self.finish_date,
            },
        }


@dataclass(frozen=True)
class CandidateWorkItem:
    id: int | None
    kind: str
    state: str
    title: str
    provider: str = DEFAULT_PROVIDER

    def to_legacy_dict(self) -> dict:
        return {
            "id": self.id,
            "workItemType": self.kind,
            "state": self.state,
            "title": self.title,
        }


@dataclass(frozen=True)
class TriageItem:
    id: int | None
    title: str
    concise_title: str
    kind: str
    state: str
    area: str
    owner: str
    scope: str
    severity: str
    product: str
    tags: list[str]
    keywords: list[str]

    def to_legacy_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "conciseTitle": self.concise_title,
            "workItemType": self.kind,
            "state": self.state,
            "areaPath": self.area,
            "owner": self.owner,
            "scope": self.scope,
            "severity": self.severity,
            "product": self.product,
            "tags": list(self.tags),
            "keywords": list(self.keywords),
        }


@dataclass(frozen=True)
class TriagePairing:
    left_id: int | None
    right_id: int | None
    suggest_same_group: bool
    reasons: list[str]

    def to_legacy_dict(self) -> dict:
        return {
            "leftId": self.left_id,
            "rightId": self.right_id,
            "suggestSameGroup": self.suggest_same_group,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class TriageGroup:
    ids: list[int]
    reason_summary: list[str]

    def to_legacy_dict(self) -> dict:
        return {
            "ids": list(self.ids),
            "reasonSummary": list(self.reason_summary),
        }


@dataclass(frozen=True)
class TriageReport:
    items: list[TriageItem]
    pairings: list[TriagePairing]
    groups: list[TriageGroup]

    def to_legacy_dict(self) -> dict:
        return {
            "items": [item.to_legacy_dict() for item in self.items],
            "pairings": [pairing.to_legacy_dict() for pairing in self.pairings],
            "groups": [group.to_legacy_dict() for group in self.groups],
        }


@dataclass(frozen=True)
class ReviewChangeSummary:
    iteration: int | None
    count: int
    by_type: dict[str, int]

    def to_legacy_dict(self) -> dict:
        return {
            "iteration": self.iteration,
            "count": self.count,
            "byType": dict(self.by_type),
        }


@dataclass(frozen=True)
class ReviewFileChange:
    path: str
    change_type: str

    def to_legacy_dict(self) -> dict:
        return {
            "path": self.path,
            "changeType": self.change_type,
        }


@dataclass(frozen=True)
class ExistingReviewComment:
    thread_id: int | str | None
    comment_id: int | None
    thread_status: str
    file_path: str | None
    line: int | None
    author: str
    content: str

    def to_legacy_dict(self) -> dict:
        return {
            "threadId": self.thread_id,
            "commentId": self.comment_id,
            "threadStatus": self.thread_status,
            "file": self.file_path,
            "line": self.line,
            "author": self.author,
            "content": self.content,
        }


@dataclass(frozen=True)
class QueuedBuild:
    id: int | None
    build_number: str
    source_branch: str
    provider: str = DEFAULT_PROVIDER

    def to_legacy_dict(self) -> dict:
        return {
            "id": self.id,
            "buildNumber": self.build_number,
            "sourceBranch": self.source_branch,
        }


@dataclass(frozen=True)
class PendingBuildApproval:
    id: str
    pipeline_id: str | None
    status: str

    def to_legacy_dict(self) -> dict:
        return {
            "approvalId": self.id,
            "pipelineId": self.pipeline_id,
            "status": self.status,
        }


@dataclass(frozen=True)
class TrackedWorkItem:
    id: int | None
    title: str
    kind: str
    state: str
    assignee: str
    iteration: str
    area: str
    estimate: int | float | None
    labels: list[str]
    provider: str = DEFAULT_PROVIDER

    def to_legacy_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "workItemType": self.kind,
            "state": self.state,
            "assignedTo": self.assignee,
            "iterationPath": self.iteration,
            "areaPath": self.area,
            "storyPoints": self.estimate,
            "tags": list(self.labels),
        }


@dataclass(frozen=True)
class StartWorkPlan:
    work_item: TrackedWorkItem
    branch_name: str
    note_path: str
    commit_prefix: str
    change_request_title: str
    concise_title: str
    change_request_body: str
    commands: list[str]

    def to_legacy_dict(self) -> dict:
        return {
            "workItem": self.work_item.to_legacy_dict(),
            "branchName": self.branch_name,
            "notePath": self.note_path,
            "commitPrefix": self.commit_prefix,
            "prTitle": self.change_request_title,
            "conciseTitle": self.concise_title,
            "prBody": self.change_request_body,
            "commands": list(self.commands),
        }


@dataclass(frozen=True)
class ChangeRequest:
    id: int | None
    title: str
    status: str
    source_branch: str
    target_branch: str
    author: str
    repo_name: str
    repo_id: str | None
    api_url: str
    browser_url: str
    provider: str = DEFAULT_PROVIDER

    def to_summary_dict(self) -> dict:
        return {
            "repoId": self.repo_id,
            "repoName": self.repo_name,
            "pullRequestId": self.id,
            "title": self.title,
            "status": self.status,
            "source": self.source_branch,
            "target": self.target_branch,
            "url": self.api_url,
        }

    def to_analysis_dict(self) -> dict:
        return {
            **self.to_summary_dict(),
            "browserUrl": self.browser_url,
            "createdBy": self.author,
        }


@dataclass(frozen=True)
class ChangeRequestReviewer:
    name: str
    vote: int | None
    vote_label: str
    is_required: bool

    def to_legacy_dict(self) -> dict:
        return {
            "name": self.name,
            "vote": self.vote,
            "voteLabel": self.vote_label,
            "isRequired": self.is_required,
        }


@dataclass(frozen=True)
class ChangeRequestStatus:
    id: int | None
    state: str
    description: str
    context_name: str
    context_kind: str
    target_url: str
    created_by: str
    creation_date: str | None
    updated_date: str | None

    def display_state(self) -> str:
        return {
            "unknown": "queued",
            "pending": "running",
            "success": "passed",
            "succeeded": "passed",
            "failed": "failed",
            "error": "failed",
            "notApplicable": "skipped",
        }.get(self.state, self.state or "unknown")

    def context_label(self) -> str:
        if self.context_kind and self.context_name:
            return f"{self.context_kind}/{self.context_name}"
        return self.context_name or "(unnamed)"

    def to_legacy_dict(self) -> dict:
        return {
            "id": self.id,
            "state": self.state,
            "description": self.description,
            "contextName": self.context_name,
            "contextGenre": self.context_kind,
            "targetUrl": self.target_url,
            "createdBy": self.created_by,
            "creationDate": self.creation_date,
            "updatedDate": self.updated_date,
        }


@dataclass(frozen=True)
class ReviewComment:
    comment_id: int | None
    parent_comment_id: int | None
    author: str
    content: str
    published_date: str | None
    last_updated_date: str | None

    def to_legacy_dict(self) -> dict:
        return {
            "commentId": self.comment_id,
            "parentCommentId": self.parent_comment_id,
            "author": self.author,
            "content": self.content,
            "publishedDate": self.published_date,
            "lastUpdatedDate": self.last_updated_date,
        }


@dataclass(frozen=True)
class ReviewThread:
    thread_id: int | str | None
    status: str
    is_deleted: bool
    file_path: str | None
    line: int | None
    location: str | None
    comments: list[ReviewComment]

    def to_legacy_dict(self) -> dict:
        return {
            "threadId": self.thread_id,
            "status": self.status,
            "isDeleted": self.is_deleted,
            "filePath": self.file_path,
            "line": self.line,
            "location": self.location,
            "comments": [comment.to_legacy_dict() for comment in self.comments],
        }


@dataclass(frozen=True)
class BuildRun:
    id: int | None
    build_number: str
    status: str
    result: str
    source_branch: str
    queued_at: str | None
    reason: str | None = None
    source_version: str | None = None
    provider: str = DEFAULT_PROVIDER

    def to_legacy_dict(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "result": self.result,
            "buildNumber": self.build_number,
            "queueTime": self.queued_at,
            "sourceBranch": self.source_branch,
            "reason": self.reason,
            "sourceVersion": self.source_version,
        }


@dataclass(frozen=True)
class BuildTaskSummary:
    name: str
    state: str
    result: str
    issue: str
    log_id: int | None = None
    failure_details: list[str] | None = None

    def to_display_lines(self, *, show_log_ids: bool = False) -> list[str]:
        issue_text = f" | issue={self.issue}" if self.issue else ""
        log_text = f" | log={self.log_id}" if show_log_ids and self.log_id is not None else ""
        lines = [f"  - {self.name}: state={self.state}, result={self.result}{log_text}{issue_text}"]
        for detail in self.failure_details or []:
            lines.append(f"    failure: {detail}")
        return lines


@dataclass(frozen=True)
class BuildStageSummary:
    name: str
    state: str
    result: str
    approval_required: bool
    tasks: list[BuildTaskSummary]
    pending_reason: str | None = None

    def to_display_lines(self, *, show_log_ids: bool = False) -> list[str]:
        approval_note = " ⏳ WAITING FOR APPROVAL" if self.approval_required else ""
        lines = [f"[{self.name}] state={self.state}, result={self.result}{approval_note}"]
        if self.pending_reason:
            lines.append(f"  reason: {self.pending_reason}")
        for task in self.tasks:
            lines.extend(task.to_display_lines(show_log_ids=show_log_ids))
        return lines


@dataclass(frozen=True)
class BuildStatusSnapshot:
    build: BuildRun
    stages: list[BuildStageSummary]
    orphan_tasks: list[BuildTaskSummary]

    def timeline_lines(self, *, show_log_ids: bool = False) -> list[str]:
        lines: list[str] = []
        for stage in self.stages:
            lines.extend(stage.to_display_lines(show_log_ids=show_log_ids))
        for task in self.orphan_tasks:
            lines.extend(task.to_display_lines(show_log_ids=show_log_ids))
        return lines

    def to_legacy_dict(self, *, show_log_ids: bool = False) -> dict:
        return {
            "build": self.build.to_legacy_dict(),
            "timeline": self.timeline_lines(show_log_ids=show_log_ids),
        }


@dataclass(frozen=True)
class BuildLogMatch:
    log_id: int
    stage_name: str | None
    job_name: str | None
    record_name: str
    record_type: str
    state: str
    result: str
    issue: str
    content: str

    def header_line(self) -> str:
        parts: list[str] = [f"log={self.log_id}"]
        if self.stage_name:
            parts.append(f"stage={self.stage_name}")
        if self.job_name:
            parts.append(f"job={self.job_name}")
        record_label = "step" if self.record_type == "Task" else self.record_type.lower()
        if self.record_type != "Job" or self.job_name != self.record_name:
            parts.append(f"{record_label}={self.record_name}")
        parts.append(f"state={self.state}")
        parts.append(f"result={self.result}")
        if self.issue:
            parts.append(f"issue={self.issue}")
        return " | ".join(parts)

    def to_legacy_dict(self) -> dict:
        return {
            "logId": self.log_id,
            "stage": self.stage_name,
            "job": self.job_name,
            "recordName": self.record_name,
            "recordType": self.record_type,
            "state": self.state,
            "result": self.result,
            "issue": self.issue,
            "content": self.content,
        }


@dataclass(frozen=True)
class BuildLogsSnapshot:
    build: BuildRun
    matches: list[BuildLogMatch]

    def to_legacy_dict(self) -> dict:
        return {
            "build": self.build.to_legacy_dict(),
            "logs": [match.to_legacy_dict() for match in self.matches],
        }


@dataclass(frozen=True)
class WorkItemSummary:
    id: int | None
    title: str
    kind: str
    state: str
    assignee: str
    iteration: str
    area: str
    estimate: int | float | None
    tags: list[str]
    sections: dict[str, str]
    provider: str = DEFAULT_PROVIDER

    def to_legacy_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "workItemType": self.kind,
            "state": self.state,
            "assignedTo": self.assignee,
            "iterationPath": self.iteration,
            "areaPath": self.area,
            "storyPoints": self.estimate,
            "tags": list(self.tags),
            "sections": dict(self.sections),
        }


@dataclass(frozen=True)
class WorkItemComment:
    id: int | None
    author: str
    published_date: str | None
    text: str

    def to_legacy_dict(self) -> dict:
        return {
            "id": self.id,
            "author": self.author,
            "publishedDate": self.published_date,
            "text": self.text,
        }


@dataclass(frozen=True)
class RelatedWorkItem:
    id: int | None
    title: str
    kind: str
    state: str
    assignee: str = ""
    tags: tuple[str, ...] = ()
    iteration: str = ""

    def to_legacy_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "workItemType": self.kind,
            "state": self.state,
            "assignedTo": self.assignee,
            "tags": list(self.tags),
            "iterationPath": self.iteration,
        }


@dataclass(frozen=True)
class LinkedChangeRequest:
    repo_name: str
    repo_id: str | None
    project: str
    change_request_id: int | None
    title: str | None
    status: str | None
    source_branch: str
    target_branch: str
    author: str | None
    created_at: str | None
    closed_at: str | None
    merge_commit_id: str | None
    browser_url: str

    def to_legacy_dict(self) -> dict:
        return {
            "repoName": self.repo_name,
            "repoId": self.repo_id,
            "project": self.project,
            "pullRequestId": self.change_request_id,
            "title": self.title,
            "status": self.status,
            "source": self.source_branch,
            "target": self.target_branch,
            "createdBy": self.author,
            "creationDate": self.created_at,
            "closedDate": self.closed_at,
            "lastMergeCommitId": self.merge_commit_id,
            "browserUrl": self.browser_url,
        }


@dataclass(frozen=True)
class ServiceEndpointSummary:
    id: str
    name: str
    type: str
    url: str | None
    is_ready: bool
    is_shared: bool
    owner: str | None
    description: str | None
    created_by: str | None
    authorization_scheme: str | None
    service_principal_id: str | None
    tenant_id: str | None
    subscription_id: str | None
    subscription_name: str | None

    def to_legacy_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "url": self.url,
            "isReady": self.is_ready,
            "isShared": self.is_shared,
            "owner": self.owner,
            "description": self.description,
            "createdBy": self.created_by,
            "authorizationScheme": self.authorization_scheme,
            "servicePrincipalId": self.service_principal_id,
            "tenantId": self.tenant_id,
            "subscriptionId": self.subscription_id,
            "subscriptionName": self.subscription_name,
        }

    def to_display_line(self) -> str:
        ready_text = "ready" if self.is_ready else "not-ready"
        spn_text = f" spn={self.service_principal_id}" if self.service_principal_id else ""
        return f"{self.id}  {self.name:<40}  {self.type:<16}  {ready_text}{spn_text}"


@dataclass(frozen=True)
class LinkedCommit:
    repo_name: str
    repo_id: str | None
    project: str
    commit_id: str
    comment: str | None
    author: str | None
    authored_at: str | None
    browser_url: str

    def to_legacy_dict(self) -> dict:
        return {
            "repoName": self.repo_name,
            "repoId": self.repo_id,
            "project": self.project,
            "commitId": self.commit_id,
            "comment": self.comment,
            "author": self.author,
            "date": self.authored_at,
            "browserUrl": self.browser_url,
        }