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


class WorkTrackingProvider(Protocol):
    def list_teams(self) -> list[TeamRef]:
        ...

    def get_current_sprint(self) -> Sprint:
        ...

    def get_open_candidate_items(self) -> tuple[Sprint, list[CandidateWorkItem]]:
        ...

    def get_work_item_context(self, *, item_id: int) -> WorkItemContextSnapshot:
        ...

    def get_work_item_comments(self, *, item_id: int) -> WorkItemCommentsSnapshot:
        ...

    def get_start_work_plan(self, *, item_id: int) -> StartWorkPlan:
        ...

    def transition_work_item(self, *, item_id: int, state: str, assignee: str | None = None) -> None:
        ...

    def get_triage_report(self, *, item_ids: list[int]) -> TriageReport:
        ...

    def add_work_item_comment(self, *, item_id: int, text: str) -> int | None:
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