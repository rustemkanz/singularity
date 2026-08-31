import urllib.parse
import copy

from errors import CliError
from providers.azure_devops.work_item_context import (
    build_work_item_comments,
    build_work_item_context,
    build_work_item_tree,
)
from app_config import API_VER, BASE_URL, ORG, PROJECT
from providers.azure_devops.http import api
from providers.azure_devops.work_items import (
    build_triage_report,
    current_sprint,
    fetch_items,
    fetch_work_item,
    resolve_transition_state_name,
    open_candidate_items,
    patch_item,
    sprint_required,
    suggest_start_work_plan,
)
from providers.interfaces import (
    TeamRef,
    WorkItemCommentsSnapshot,
    WorkItemContextSnapshot,
    WorkItemTransitionPreview,
    WorkItemTreePreview,
    WorkItemTreeSnapshot,
    WorkTrackingProvider,
)
from workflow_models import (
    CandidateWorkItem,
    Sprint,
    StartWorkPlan,
    TriageGroup,
    TriageItem,
    TriagePairing,
    TriageReport,
    TrackedWorkItem,
    WorkItemComment,
    WorkItemSummary,
)


def _deserialize_sprint(data: dict) -> Sprint:
    attributes = data.get("attributes") or {}
    return Sprint(
        id=data.get("id") or "",
        name=data.get("name") or "",
        path=data.get("path") or "",
        start_date=(attributes.get("startDate") or "")[:10] or None,
        finish_date=(attributes.get("finishDate") or "")[:10] or None,
    )


def _deserialize_candidate_work_item(data: dict) -> CandidateWorkItem:
    return CandidateWorkItem(
        id=data.get("System.Id"),
        kind=data.get("System.WorkItemType") or "",
        state=data.get("System.State") or "",
        title=data.get("System.Title") or "",
    )


def _deserialize_triage_report(data: dict) -> TriageReport:
    return TriageReport(
        items=[
            TriageItem(
                id=item.get("id"),
                title=item.get("title") or "",
                concise_title=item.get("conciseTitle") or "",
                kind=item.get("workItemType") or "",
                state=item.get("state") or "",
                area=item.get("areaPath") or "",
                owner=item.get("owner") or "",
                scope=item.get("scope") or "",
                severity=item.get("severity") or "",
                product=item.get("product") or "",
                tags=list(item.get("tags") or []),
                keywords=list(item.get("keywords") or []),
            )
            for item in data.get("items") or []
        ],
        pairings=[
            TriagePairing(
                left_id=pairing.get("leftId"),
                right_id=pairing.get("rightId"),
                suggest_same_group=bool(pairing.get("suggestSameGroup")),
                reasons=list(pairing.get("reasons") or []),
            )
            for pairing in data.get("pairings") or []
        ],
        groups=[
            TriageGroup(
                ids=list(group.get("ids") or []),
                reason_summary=list(group.get("reasonSummary") or []),
            )
            for group in data.get("groups") or []
        ],
    )


def _deserialize_work_item_summary(data: dict) -> WorkItemSummary:
    return WorkItemSummary(
        id=data.get("id"),
        title=data.get("title") or "",
        kind=data.get("workItemType") or "",
        state=data.get("state") or "",
        assignee=data.get("assignedTo") or "",
        iteration=data.get("iterationPath") or "",
        area=data.get("areaPath") or "",
        estimate=data.get("storyPoints"),
        tags=list(data.get("tags") or []),
        sections=dict(data.get("sections") or {}),
    )


def _deserialize_work_item_comment(data: dict) -> WorkItemComment:
    return WorkItemComment(
        id=data.get("id"),
        author=data.get("author") or "",
        published_date=data.get("publishedDate"),
        text=data.get("text") or "",
    )


def _deserialize_tracked_work_item(data: dict) -> TrackedWorkItem:
    return TrackedWorkItem(
        id=data.get("id"),
        title=data.get("title") or "",
        kind=data.get("workItemType") or "",
        state=data.get("state") or "",
        assignee=data.get("assignedTo") or "",
        iteration=data.get("iterationPath") or "",
        area=data.get("areaPath") or "",
        estimate=data.get("storyPoints"),
        labels=list(data.get("tags") or []),
    )


def _deserialize_start_work_plan(data: dict) -> StartWorkPlan:
    return StartWorkPlan(
        work_item=_deserialize_tracked_work_item(data.get("workItem") or {}),
        branch_name=data.get("branchName") or "",
        note_path=data.get("notePath") or "",
        commit_prefix=data.get("commitPrefix") or "",
        change_request_title=data.get("prTitle") or "",
        concise_title=data.get("conciseTitle") or "",
        change_request_body=data.get("prBody") or "",
        commands=list(data.get("commands") or []),
    )


class AzureDevOpsWorkTrackingProvider(WorkTrackingProvider):
    def __init__(self, token: str):
        self.token = token

    def list_teams(self) -> list[TeamRef]:
        project_name = urllib.parse.quote(PROJECT)
        url = f"https://dev.azure.com/{ORG}/_apis/projects/{project_name}/teams?api-version=7.1-preview.3"
        result = api(self.token, "GET", url)
        return [
            TeamRef(
                id=team.get("id") or "",
                name=team.get("name") or "",
                description=team.get("description"),
            )
            for team in result.get("value", [])
            if team.get("id") and team.get("name")
        ]

    def get_current_sprint(self) -> Sprint:
        return _deserialize_sprint(sprint_required(self.token))

    def get_open_candidate_items(self) -> tuple[Sprint, list[CandidateWorkItem]]:
        sprint, items = open_candidate_items(self.token)
        return _deserialize_sprint(sprint), [_deserialize_candidate_work_item(item) for item in items]

    def get_work_item_context(self, *, item_id: int) -> WorkItemContextSnapshot:
        context = build_work_item_context(self.token, item_id)
        return WorkItemContextSnapshot(
            work_item=_deserialize_work_item_summary(context["workItem"]),
            reference_summary=context.get("referenceSummary"),
            references=list(context.get("references") or []),
            comment_count=context.get("commentCount", 0),
            recent_comments=[
                _deserialize_work_item_comment(comment)
                for comment in context.get("recentComments") or []
            ],
            related_items=dict(context.get("relatedItems") or {}),
            development_artifacts=dict(context.get("developmentArtifacts") or {}),
        )

    def get_work_item_tree(self, *, item_id: int, depth: int = 1) -> WorkItemTreeSnapshot:
        data = build_work_item_tree(self.token, item_id, depth=depth)
        return WorkItemTreeSnapshot(
            root=data["root"],
            ancestors=list(data["ancestors"]),
            depth=data["depth"],
        )

    def get_work_item_comments(self, *, item_id: int) -> WorkItemCommentsSnapshot:
        comment_data = build_work_item_comments(self.token, item_id)
        return WorkItemCommentsSnapshot(
            work_item=_deserialize_work_item_summary(comment_data["workItem"]),
            comment_count=comment_data.get("commentCount", 0),
            comments=[
                _deserialize_work_item_comment(comment)
                for comment in comment_data.get("comments") or []
            ],
        )

    def get_start_work_plan(self, *, item_id: int) -> StartWorkPlan:
        item = fetch_work_item(
            self.token,
            item_id,
            fields=[
                "System.Id",
                "System.Title",
                "System.WorkItemType",
                "System.State",
                "System.AreaPath",
                "System.IterationPath",
                "System.AssignedTo",
                "System.Tags",
            ],
        )
        return _deserialize_start_work_plan(suggest_start_work_plan(item))

    def prepare_work_item_transition(
        self,
        *,
        item_id: int,
        state: str,
        assignee: str | None = None,
    ) -> WorkItemTransitionPreview:
        work_item = fetch_work_item(
            self.token,
            item_id,
            fields=["System.WorkItemType", "System.State"],
        )
        revision = work_item.get("rev")
        if not isinstance(revision, int) or isinstance(revision, bool):
            raise CliError(
                f"ERROR: Work item {item_id} did not include a valid revision; "
                "refusing to prepare an unguarded transition."
            )
        fields = work_item.get("fields") or {}
        resolved_state = resolve_transition_state_name(
            self.token,
            item_id=item_id,
            desired_state=state,
            work_item=work_item,
        )
        operations = [
            {"op": "test", "path": "/rev", "value": revision},
            {"op": "replace", "path": "/fields/System.State", "value": resolved_state},
        ]
        if assignee is not None:
            operations.append({"op": "replace", "path": "/fields/System.AssignedTo", "value": assignee})
        return WorkItemTransitionPreview(
            provider="azure-devops",
            item_id=item_id,
            requested_state=state,
            concrete_state=resolved_state,
            current_snapshot={
                "state": fields.get("System.State"),
                "revision": revision,
            },
            request={
                "method": "PATCH",
                "operations": operations,
            },
        )

    def apply_prepared_work_item_transition(self, preview: WorkItemTransitionPreview) -> str:
        if preview.provider != "azure-devops":
            raise CliError(
                f"ERROR: Cannot apply a {preview.provider!r} transition with the Azure DevOps provider."
            )
        if preview.request.get("method") != "PATCH":
            raise CliError("ERROR: Prepared Azure DevOps transition does not contain a PATCH request.")
        operations = preview.request.get("operations")
        if not isinstance(operations, list):
            raise CliError("ERROR: Prepared Azure DevOps transition does not contain JSON Patch operations.")
        patch_item(self.token, preview.item_id, copy.deepcopy(operations))
        return preview.concrete_state

    def transition_work_item(self, *, item_id: int, state: str, assignee: str | None = None) -> str:
        preview = self.prepare_work_item_transition(
            item_id=item_id,
            state=state,
            assignee=assignee,
        )
        return self.apply_prepared_work_item_transition(preview)

    def get_triage_report(self, *, item_ids: list[int]) -> TriageReport:
        fields = fetch_items(
            self.token,
            item_ids,
            [
                "System.Id",
                "System.Title",
                "System.WorkItemType",
                "System.State",
                "System.AreaPath",
                "System.Tags",
            ],
        )
        return _deserialize_triage_report(build_triage_report(fields))

    def add_work_item_comment(self, *, item_id: int, text: str) -> int | None:
        url = f"{BASE_URL}/_apis/wit/workitems/{item_id}/comments?api-version=7.1-preview.3"
        result = api(self.token, "POST", url, {"text": text})
        return result.get("id")

    def prepare_work_item_tree(
        self,
        *,
        parent_id: int,
        items: list,
        tags: list[str] | None = None,
        assignee: str | None = None,
    ) -> WorkItemTreePreview:
        parent = fetch_work_item(
            self.token,
            parent_id,
            fields=[
                "System.Id",
                "System.Title",
                "System.WorkItemType",
                "System.TeamProject",
                "System.AreaPath",
                "System.IterationPath",
            ],
        )
        fields = parent.get("fields") or {}
        area_path = fields.get("System.AreaPath")
        iteration_path = fields.get("System.IterationPath")
        parent_url = f"{BASE_URL}/_apis/wit/workItems/{parent_id}"
        default_tags = tuple(tags or ())

        requests: list[dict] = []
        for spec in items:
            spec_tags = spec.tags if spec.tags is not None else default_tags
            spec_assignee = spec.assigned_to if spec.assigned_to is not None else assignee
            operations: list[dict] = [
                {"op": "add", "path": "/fields/System.Title", "value": spec.title},
            ]
            if spec.description_html:
                operations.append(
                    {"op": "add", "path": "/fields/System.Description", "value": spec.description_html}
                )
            if spec_tags:
                operations.append(
                    {"op": "add", "path": "/fields/System.Tags", "value": "; ".join(spec_tags)}
                )
            if spec_assignee:
                operations.append(
                    {"op": "add", "path": "/fields/System.AssignedTo", "value": spec_assignee}
                )
            if area_path:
                operations.append(
                    {"op": "add", "path": "/fields/System.AreaPath", "value": area_path}
                )
            if iteration_path:
                operations.append(
                    {"op": "add", "path": "/fields/System.IterationPath", "value": iteration_path}
                )
            operations.append(
                {
                    "op": "add",
                    "path": "/relations/-",
                    "value": {
                        "rel": "System.LinkTypes.Hierarchy-Reverse",
                        "url": parent_url,
                    },
                }
            )
            requests.append({"type": spec.type, "title": spec.title, "operations": operations})

        return WorkItemTreePreview(
            provider="azure-devops",
            parent_id=parent_id,
            parent_snapshot={
                "id": fields.get("System.Id") or parent_id,
                "title": fields.get("System.Title") or "",
                "type": fields.get("System.WorkItemType") or "",
                "areaPath": area_path or "",
                "iterationPath": iteration_path or "",
            },
            requests=requests,
        )

    def apply_prepared_work_item_tree(
        self,
        preview: WorkItemTreePreview,
        *,
        on_result=None,
    ) -> list[dict]:
        if preview.provider != "azure-devops":
            raise CliError(
                f"ERROR: Cannot apply a {preview.provider!r} work-item tree with the Azure DevOps provider."
            )
        results: list[dict] = []
        for request in preview.requests:
            work_item_type = request.get("type")
            title = request.get("title")
            operations = request.get("operations")
            if not isinstance(work_item_type, str) or not isinstance(operations, list):
                raise CliError("ERROR: Prepared work-item tree request is invalid.")
            url = (
                f"{BASE_URL}/_apis/wit/workitems/"
                f"${urllib.parse.quote(work_item_type)}?api-version={API_VER}"
            )
            try:
                created = api(
                    self.token,
                    "POST",
                    url,
                    copy.deepcopy(operations),
                    content_type="application/json-patch+json",
                )
            except CliError as exc:
                entry = {"title": title, "ok": False, "error": str(exc)}
            else:
                entry = {"title": title, "ok": True, "id": created.get("id")}
            results.append(entry)
            if on_result is not None:
                on_result(entry)
        return results
