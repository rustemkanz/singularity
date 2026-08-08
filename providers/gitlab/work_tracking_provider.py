from __future__ import annotations

import urllib.parse

from errors import CliError
from providers.azure_devops import work_items as azure_devops_work_items
from providers.gitlab.review_provider import GitLabReviewProvider
from providers.interfaces import WorkItemCommentsSnapshot, WorkItemContextSnapshot, WorkTrackingProvider
from workflow_models import (
    CandidateWorkItem,
    Sprint,
    StartWorkPlan,
    TriageReport,
    TrackedWorkItem,
    WorkItemComment,
    WorkItemSummary,
)


WORKFLOW_LABEL_PREFIX = "sg:state:"
WORKFLOW_STATE_LABELS = {
    "In Progress": f"{WORKFLOW_LABEL_PREFIX}in-progress",
    "In Review": f"{WORKFLOW_LABEL_PREFIX}in-review",
    "In Testing": f"{WORKFLOW_LABEL_PREFIX}in-testing",
}


def _gitlab_workflow_state(issue: dict) -> str:
    if _issue_state(issue) == "closed":
        return "closed"
    labels = list(issue.get("labels") or [])
    for preferred_state, label in WORKFLOW_STATE_LABELS.items():
        if label in labels:
            return preferred_state
    return _issue_state(issue)


def _issue_state(issue: dict) -> str:
    return issue.get("state") or "unknown"


def _issue_kind(issue: dict) -> str:
    issue_type = issue.get("issue_type") or issue.get("type") or "issue"
    return str(issue_type).replace("_", " ").title()


def _work_item_summary(issue: dict) -> WorkItemSummary:
    labels = list(issue.get("labels") or [])
    return WorkItemSummary(
        id=issue.get("iid"),
        title=issue.get("title") or "",
        kind=_issue_kind(issue),
        state=_gitlab_workflow_state(issue),
        assignee=((issue.get("assignee") or {}).get("username") or (issue.get("author") or {}).get("username") or ""),
        iteration=(issue.get("iteration") or {}).get("title") or "",
        area=issue.get("references", {}).get("full") or "",
        estimate=None,
        tags=labels,
        sections={
            "description": issue.get("description") or "",
            "reproSteps": "",
            "acceptanceCriteria": "",
        },
        provider="gitlab",
    )


def _tracked_work_item(issue: dict) -> TrackedWorkItem:
    summary = _work_item_summary(issue)
    return TrackedWorkItem(
        id=summary.id,
        title=summary.title,
        kind=summary.kind,
        state=summary.state,
        assignee=summary.assignee,
        iteration=summary.iteration,
        area=summary.area,
        estimate=summary.estimate,
        labels=list(summary.tags),
        provider="gitlab",
    )


def _comment(note: dict) -> WorkItemComment:
    return WorkItemComment(
        id=note.get("id"),
        author=(note.get("author") or {}).get("username") or (note.get("author") or {}).get("name") or "unknown",
        published_date=note.get("created_at"),
        text=note.get("body") or "",
    )


def _serialize_pull_request(merge_request: dict) -> dict:
    return {
        "pullRequestId": merge_request.get("iid"),
        "repoName": (merge_request.get("references") or {}).get("full", "").split("!", 1)[0] or merge_request.get("source_project_id"),
        "title": merge_request.get("title") or "",
        "status": merge_request.get("state") or "unknown",
        "url": merge_request.get("web_url") or "",
    }


class GitLabWorkTrackingProvider(GitLabReviewProvider, WorkTrackingProvider):
    def __init__(self, token: str | None, project_ref: str | None):
        super().__init__(token)
        self.project_ref = project_ref

    def _require_project(self) -> str:
        if not self.project_ref:
            raise CliError("ERROR: GitLab issue commands require --repo with a GitLab project path or numeric project id.")
        return self.project_ref

    def _project_path(self) -> str:
        project = self._resolve_repository(self._require_project())
        return project.get("path_with_namespace") or self._require_project()

    def _issue_path(self, item_id: int) -> str:
        return f"/projects/{urllib.parse.quote(self._require_project(), safe='')}/issues/{item_id}"

    def _issue_notes_path(self, item_id: int) -> str:
        return f"{self._issue_path(item_id)}/notes"

    def _build_transition_labels(self, labels: list[str], state: str) -> list[str]:
        desired_label = WORKFLOW_STATE_LABELS.get(state)
        if desired_label is None:
            raise CliError(f"ERROR: GitLab issue commands do not support workflow state '{state}'.")

        next_labels = [label for label in labels if label not in WORKFLOW_STATE_LABELS.values()]
        next_labels.append(desired_label)
        return next_labels

    def list_teams(self) -> list:
        raise CliError("ERROR: GitLab issue commands do not support team listing.")

    def get_current_sprint(self) -> Sprint:
        raise CliError("ERROR: GitLab issue commands do not support sprint discovery.")

    def get_open_candidate_items(self) -> tuple[Sprint, list[CandidateWorkItem]]:
        raise CliError("ERROR: GitLab issue commands do not support candidate-item listing yet.")

    def _fetch_issue(self, item_id: int) -> dict:
        issue = self._request_json(
            self._base_url_for_work_tracking(),
            self._issue_path(item_id),
            allow_not_found=True,
        )
        if issue is None:
            raise CliError(f"ERROR: GitLab issue '{item_id}' was not found in project '{self._require_project()}'.")
        return issue

    def _fetch_notes(self, item_id: int) -> list[dict]:
        notes = self._request_json(
            self._base_url_for_work_tracking(),
            self._issue_notes_path(item_id),
            query={"per_page": 100},
        ) or []
        return [note for note in notes if not note.get("system")]

    def _fetch_related_merge_requests(self, item_id: int) -> list[dict]:
        return self._request_json(
            self._base_url_for_work_tracking(),
            f"{self._issue_path(item_id)}/related_merge_requests",
        ) or []

    def _base_url_for_work_tracking(self) -> str:
        return self._gitlab_base_url()

    def _gitlab_base_url(self) -> str:
        from app_config import GITLAB_BASE_URL
        return GITLAB_BASE_URL

    def get_work_item_context(self, *, item_id: int) -> WorkItemContextSnapshot:
        issue = self._fetch_issue(item_id)
        comments = [_comment(note) for note in self._fetch_notes(item_id)]
        merge_requests = [_serialize_pull_request(merge_request) for merge_request in self._fetch_related_merge_requests(item_id)]
        return WorkItemContextSnapshot(
            work_item=_work_item_summary(issue),
            reference_summary=None,
            references=[],
            comment_count=len(comments),
            recent_comments=comments[:5],
            related_items={"parents": [], "children": [], "related": []},
            development_artifacts={"pullRequests": merge_requests, "commits": [], "other": []},
        )

    def get_work_item_comments(self, *, item_id: int) -> WorkItemCommentsSnapshot:
        issue = self._fetch_issue(item_id)
        comments = [_comment(note) for note in self._fetch_notes(item_id)]
        return WorkItemCommentsSnapshot(
            work_item=_work_item_summary(issue),
            comment_count=len(comments),
            comments=comments,
        )

    def get_start_work_plan(self, *, item_id: int) -> StartWorkPlan:
        issue = self._fetch_issue(item_id)
        tracked = _tracked_work_item(issue)
        concise_title = azure_devops_work_items.parse_title_facets(tracked.title).get("tailTitle") or tracked.title
        slug = azure_devops_work_items.slugify_text(concise_title) or f"issue-{item_id}"
        branch_name = f"issue/{item_id}-{slug}"
        project_path = self._project_path()
        pr_title = f"[{item_id}] {tracked.title}"
        pr_body = f"Closes #{item_id}"
        return StartWorkPlan(
            work_item=tracked,
            branch_name=branch_name,
            note_path=f".agent-notes/gitlab-issue-{item_id}-{slug}.md",
            commit_prefix=f"feat: {item_id} ",
            change_request_title=pr_title,
            concise_title=concise_title,
            change_request_body=pr_body,
            commands=[
                f"git checkout -b {branch_name}",
                f"sg create-pr {item_id} --provider gitlab --repo {project_path} --source {branch_name} --work-item-title {tracked.title!r}",
            ],
        )

    def transition_work_item(self, *, item_id: int, state: str, assignee: str | None = None) -> str:
        issue = self._fetch_issue(item_id)
        form_data = {
            "labels": ",".join(self._build_transition_labels(list(issue.get("labels") or []), state)),
        }
        if assignee:
            form_data["assignee_username"] = assignee

        updated_issue = self._request_json(
            self._base_url_for_work_tracking(),
            self._issue_path(item_id),
            method="PUT",
            form_data=form_data,
        )
        return _gitlab_workflow_state(updated_issue)

    def get_triage_report(self, *, item_ids: list[int]) -> TriageReport:
        raise CliError("ERROR: GitLab issue commands do not support triage yet.")

    def add_work_item_comment(self, *, item_id: int, text: str) -> int | None:
        note = self._request_json(
            self._base_url_for_work_tracking(),
            self._issue_notes_path(item_id),
            method="POST",
            form_data={"body": text},
        )
        return note.get("id")

    def close_issue(self, *, item_id: int) -> str:
        issue = self._request_json(
            self._base_url_for_work_tracking(),
            self._issue_path(item_id),
            method="PUT",
            form_data={"state_event": "close"},
        )
        return issue.get("state") or "unknown"
