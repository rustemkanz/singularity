import copy
from dataclasses import dataclass
from typing import Protocol

from workflow_models import (
    BuildLogsSnapshot,
    BuildRun,
    BuildStatusSnapshot,
    CandidateWorkItem,
    ChangeRequest,
    ChangeRequestReviewer,
    ChangeRequestStatus,
    ExistingReviewComment,
    PendingBuildApproval,
    QueuedBuild,
    ReviewChangeSummary,
    ReviewFileChange,
    ReviewThread,
    ServiceEndpointSummary,
    Sprint,
    StartWorkPlan,
    TriageReport,
    WorkItemComment,
    WorkItemSummary,
)


@dataclass(frozen=True)
class RepositoryRef:
    id: str | None
    name: str
    project: str | None = None


@dataclass(frozen=True)
class TeamRef:
    id: str
    name: str
    description: str | None = None


@dataclass(frozen=True)
class TeamMemberRef:
    id: str
    display_name: str
    unique_name: str
    is_admin: bool = False

    def to_legacy_dict(self) -> dict:
        return {
            "id": self.id,
            "displayName": self.display_name,
            "uniqueName": self.unique_name,
            "isTeamAdmin": self.is_admin,
        }


@dataclass(frozen=True)
class ReviewContext:
    organization: str
    project: str
    repository: RepositoryRef
    change_request: ChangeRequest


@dataclass(frozen=True)
class ReviewAnalysis:
    context: ReviewContext
    reviewers: list[ChangeRequestReviewer]
    change_summary: ReviewChangeSummary
    files: list[ReviewFileChange]
    existing_comments: list[ExistingReviewComment]

    def to_legacy_dict(self) -> dict:
        return {
            "organization": self.context.organization,
            "project": self.context.project,
            "repo": {
                "name": self.context.repository.name,
                "id": self.context.repository.id,
            },
            "pullRequest": {
                **self.context.change_request.to_analysis_dict(),
                "reviewers": [reviewer.to_legacy_dict() for reviewer in self.reviewers],
            },
            "changeSummary": self.change_summary.to_legacy_dict(),
            "files": [file_change.to_legacy_dict() for file_change in self.files],
            "existingComments": [comment.to_legacy_dict() for comment in self.existing_comments],
        }


@dataclass(frozen=True)
class ReviewMutationPreview:
    payload: dict
    thread_id: int | str | None = None
    comment_id: int | None = None
    current_content: str | None = None
    current_status: str | None = None


@dataclass(frozen=True)
class ReviewMutationResult:
    thread_id: int | str | None = None
    comment_id: int | None = None
    status: str | None = None


class ReviewProvider(Protocol):
    def list_repositories(self) -> list[RepositoryRef]:
        ...

    def resolve_review_context(
        self,
        *,
        repo_ref: str | None,
        change_request_id: int | None,
        source_branch: str | None,
        url: str | None,
        project_name: str | None = None,
        org_name: str | None = None,
    ) -> ReviewContext:
        ...

    def analyze_change_request(self, context: ReviewContext) -> ReviewAnalysis:
        ...

    def list_review_threads(self, context: ReviewContext, *, unresolved_only: bool = False) -> list[ReviewThread]:
        ...

    def list_statuses(self, context: ReviewContext) -> list[ChangeRequestStatus]:
        ...

    def get_change_request_file_content(
        self,
        context: ReviewContext,
        *,
        file_path: str,
        version: str,
    ) -> str | None:
        ...

    def prepare_change_request(
        self,
        *,
        work_item_id: int,
        repo_ref: str | None,
        source_branch: str | None,
        target_branch: str | None,
        title: str | None,
        description: str | None,
        work_item_title: str | None,
    ) -> tuple[RepositoryRef, dict]:
        ...

    def create_change_request(
        self,
        *,
        work_item_id: int,
        repo_ref: str | None,
        source_branch: str | None,
        target_branch: str | None,
        title: str | None,
        description: str | None,
        work_item_title: str | None,
    ) -> ChangeRequest:
        ...

    def create_prepared_change_request(
        self,
        repository: RepositoryRef,
        payload: dict,
    ) -> ChangeRequest:
        ...

    def prepare_review_comment(
        self,
        context: ReviewContext,
        *,
        text: str,
    ) -> ReviewMutationPreview:
        ...

    def create_review_comment(
        self,
        context: ReviewContext,
        *,
        text: str,
    ) -> ReviewMutationResult:
        ...

    def create_prepared_review_comment(
        self,
        context: ReviewContext,
        preview: ReviewMutationPreview,
    ) -> ReviewMutationResult:
        ...

    def prepare_inline_review_comment(
        self,
        context: ReviewContext,
        *,
        text: str,
        file_path: str,
        line: int,
        end_line: int | None,
        start_offset: int,
        end_offset: int | None,
    ) -> ReviewMutationPreview:
        ...

    def create_inline_review_comment(
        self,
        context: ReviewContext,
        *,
        text: str,
        file_path: str,
        line: int,
        end_line: int | None,
        start_offset: int,
        end_offset: int | None,
    ) -> ReviewMutationResult:
        ...

    def create_prepared_inline_review_comment(
        self,
        context: ReviewContext,
        preview: ReviewMutationPreview,
    ) -> ReviewMutationResult:
        ...

    def prepare_review_reply(
        self,
        context: ReviewContext,
        *,
        thread_id: int | str,
        text: str,
        parent_comment_id: int | None,
    ) -> ReviewMutationPreview:
        ...

    def create_review_reply(
        self,
        context: ReviewContext,
        *,
        thread_id: int | str,
        text: str,
        parent_comment_id: int | None,
    ) -> ReviewMutationResult:
        ...

    def create_prepared_review_reply(
        self,
        context: ReviewContext,
        preview: ReviewMutationPreview,
    ) -> ReviewMutationResult:
        ...

    def prepare_review_comment_edit(
        self,
        context: ReviewContext,
        *,
        thread_id: int | str,
        comment_id: int,
        text: str,
    ) -> ReviewMutationPreview:
        ...

    def edit_review_comment(
        self,
        context: ReviewContext,
        *,
        thread_id: int | str,
        comment_id: int,
        text: str,
    ) -> ReviewMutationResult:
        ...

    def edit_prepared_review_comment(
        self,
        context: ReviewContext,
        preview: ReviewMutationPreview,
    ) -> ReviewMutationResult:
        ...

    def prepare_review_thread_resolution(
        self,
        context: ReviewContext,
        *,
        thread_id: int | str,
        status: str,
    ) -> ReviewMutationPreview:
        ...

    def resolve_review_thread(
        self,
        context: ReviewContext,
        *,
        thread_id: int | str,
        status: str,
    ) -> ReviewMutationResult:
        ...

    def resolve_prepared_review_thread(
        self,
        context: ReviewContext,
        preview: ReviewMutationPreview,
    ) -> ReviewMutationResult:
        ...


class BuildProvider(Protocol):
    def list_recent_builds(
        self,
        *,
        definition: int,
        project: str,
        branch: str | None,
        commit: str | None,
        limit: int,
    ) -> list[BuildRun]:
        ...

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
        ...

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
        ...

    def queue_build(
        self,
        *,
        definition: int,
        project: str,
        source_branch: str,
        source_version: str,
        parameters: dict | None,
    ) -> QueuedBuild:
        ...

    def list_pending_approvals(
        self,
        *,
        project: str,
        build_id: int,
    ) -> list[PendingBuildApproval]:
        ...

    def approve_pending_approval(
        self,
        *,
        project: str,
        approval_id: str,
        comment: str,
    ) -> bool:
        ...


class ServiceEndpointProvider(Protocol):
    def list_service_endpoints(
        self,
        *,
        project: str,
        endpoint_names: list[str] | None = None,
        endpoint_type: str | None = None,
    ) -> list[ServiceEndpointSummary]:
        ...

    def get_service_endpoint(
        self,
        *,
        project: str,
        name: str | None = None,
        endpoint_id: str | None = None,
    ) -> ServiceEndpointSummary:
        ...


@dataclass(frozen=True)
class WorkItemContextSnapshot:
    work_item: WorkItemSummary
    reference_summary: dict | None
    references: list[dict]
    comment_count: int
    recent_comments: list[WorkItemComment]
    related_items: dict
    development_artifacts: dict

    def to_legacy_dict(self) -> dict:
        return {
            "workItem": self.work_item.to_legacy_dict(),
            "referenceSummary": self.reference_summary,
            "references": list(self.references),
            "commentCount": self.comment_count,
            "recentComments": [comment.to_legacy_dict() for comment in self.recent_comments],
            "relatedItems": self.related_items,
            "developmentArtifacts": self.development_artifacts,
        }


@dataclass(frozen=True)
class WorkItemCommentsSnapshot:
    work_item: WorkItemSummary
    comment_count: int
    comments: list[WorkItemComment]

    def to_legacy_dict(self) -> dict:
        return {
            "workItem": self.work_item.to_legacy_dict(),
            "commentCount": self.comment_count,
            "comments": [comment.to_legacy_dict() for comment in self.comments],
        }


@dataclass(frozen=True)
class WorkItemTreeSnapshot:
    """A work item's parent chain plus its descendants to a bounded depth."""

    root: dict
    ancestors: list[dict]
    depth: int

    def to_legacy_dict(self) -> dict:
        return {
            "root": self.root,
            "ancestors": list(self.ancestors),
            "depth": self.depth,
        }


@dataclass(frozen=True)
class WorkItemTreePreview:
    """A previewed batch of child work items to create under one parent."""

    provider: str
    parent_id: int
    parent_snapshot: dict
    requests: list

    def to_plan_payload(self) -> dict:
        return {
            "provider": self.provider,
            "parentWorkItemId": self.parent_id,
            "parentSnapshot": copy.deepcopy(self.parent_snapshot),
            "requests": copy.deepcopy(self.requests),
        }


@dataclass(frozen=True)
class WorkItemTransitionPreview:
    provider: str
    item_id: int
    requested_state: str
    concrete_state: str
    current_snapshot: dict
    request: dict

    def to_plan_payload(self) -> dict:
        return {
            "provider": self.provider,
            "workItemId": self.item_id,
            "requestedState": self.requested_state,
            "concreteState": self.concrete_state,
            "currentSnapshot": copy.deepcopy(self.current_snapshot),
            "request": copy.deepcopy(self.request),
        }


class WorkTrackingProvider(Protocol):
    def list_teams(self) -> list[TeamRef]:
        ...

    def list_team_members(self, *, team_id: str) -> list[TeamMemberRef]:
        ...

    def get_current_sprint(self) -> Sprint:
        ...

    def get_open_candidate_items(self) -> tuple[Sprint, list[CandidateWorkItem]]:
        ...

    def get_work_item_context(self, *, item_id: int) -> WorkItemContextSnapshot:
        ...

    def get_work_item_comments(self, *, item_id: int) -> WorkItemCommentsSnapshot:
        ...

    def get_work_item_tree(self, *, item_id: int, depth: int = 1) -> WorkItemTreeSnapshot:
        ...

    def get_start_work_plan(self, *, item_id: int) -> StartWorkPlan:
        ...

    def prepare_work_item_transition(
        self,
        *,
        item_id: int,
        state: str,
        assignee: str | None = None,
    ) -> WorkItemTransitionPreview:
        ...

    def apply_prepared_work_item_transition(self, preview: WorkItemTransitionPreview) -> str:
        ...

    def transition_work_item(self, *, item_id: int, state: str, assignee: str | None = None) -> str:
        ...

    def get_triage_report(self, *, item_ids: list[int]) -> TriageReport:
        ...

    def add_work_item_comment(self, *, item_id: int, text: str) -> int | None:
        ...

    def prepare_work_item_tree(
        self,
        *,
        parent_id: int,
        items: list,
        tags: list[str] | None = None,
        assignee: str | None = None,
    ) -> WorkItemTreePreview:
        ...

    def apply_prepared_work_item_tree(self, preview: WorkItemTreePreview, *, on_result=None) -> list[dict]:
        ...


@dataclass(frozen=True)
class EvidenceReference:
    source: str
    label: str
    url: str
    name: str
    is_image: bool

    def to_legacy_dict(self) -> dict:
        return {
            "source": self.source,
            "label": self.label,
            "url": self.url,
            "name": self.name,
            "isImage": self.is_image,
        }


@dataclass(frozen=True)
class WorkItemEvidenceSnapshot:
    work_item_id: int
    title: str
    references: list[EvidenceReference]

    def to_legacy_dict(self) -> dict:
        return {
            "workItemId": self.work_item_id,
            "title": self.title,
            "references": [reference.to_legacy_dict() for reference in self.references],
        }


@dataclass(frozen=True)
class EvidenceDownloadEntry:
    label: str
    url: str
    path: str
    reused: bool

    def to_legacy_dict(self) -> dict:
        return {
            "label": self.label,
            "url": self.url,
            "path": self.path,
            "reused": self.reused,
        }


@dataclass(frozen=True)
class EvidenceDownloadResult:
    downloaded: list[EvidenceDownloadEntry]
    download_dir: str | None
    failures: list[str]
    opened: bool
    open_error: str | None

    def to_legacy_dict(self) -> dict:
        return {
            "downloaded": [entry.to_legacy_dict() for entry in self.downloaded],
            "downloadDir": self.download_dir,
            "failures": list(self.failures),
            "opened": self.opened,
            "openError": self.open_error,
        }


class EvidenceProvider(Protocol):
    def get_work_item_evidence(self, *, item_id: int) -> WorkItemEvidenceSnapshot:
        ...

    def default_download_dir(self, *, item_id: int) -> str:
        ...

    def download_references(
        self,
        *,
        references: list[EvidenceReference],
        download_dir: str,
        open_after_download: bool,
    ) -> EvidenceDownloadResult:
        ...
