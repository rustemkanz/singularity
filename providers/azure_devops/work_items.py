from datetime import date
import re
import urllib.parse

from app_config import API_VER, BASE_URL, ME, TEAM_ID
from errors import CliError
from providers.azure_devops.http import api
from workflow_models import StartWorkPlan, TrackedWorkItem


TITLE_TOKEN_STOPWORDS = {
    "a", "an", "and", "as", "at", "be", "by", "for", "from", "in", "into", "is", "it",
    "active", "after", "alert", "button", "critical", "db", "does", "global", "lead", "leads",
    "longer", "market", "mm", "modal", "no", "non", "of", "on", "or", "step", "submit",
    "successful", "summary", "the", "to", "with",
}


WORKFLOW_STATE_PREFERENCES = {
    "In Progress": {
        "preferred_names": ["In Progress", "Active", "Committed"],
        "preferred_categories": ["InProgress"],
    },
    "In Review": {
        "preferred_names": ["In Review", "Code Review", "Review", "Resolved"],
        "preferred_categories": ["Resolved", "InProgress"],
    },
    "In Testing": {
        "preferred_names": ["In Testing", "Testing", "Ready for Test", "Ready for QA", "QA", "Resolved"],
        "preferred_categories": ["Resolved", "InProgress"],
    },
}


def current_sprint(token: str) -> dict | None:
    url = f"{BASE_URL}/{TEAM_ID}/_apis/work/teamsettings/iterations?api-version={API_VER}"
    data = api(token, "GET", url)
    today = date.today()
    for it in data.get("value", []):
        attrs = it.get("attributes", {})
        start = (attrs.get("startDate") or "")[:10]
        end = (attrs.get("finishDate") or "")[:10]
        if start and end and date.fromisoformat(start) <= today <= date.fromisoformat(end):
            return it
    return None


def sprint_required(token: str) -> dict:
    sprint = current_sprint(token)
    if not sprint:
        raise CliError("No active sprint found for today.", exit_code=0)
    return sprint


def backlog_iteration_context() -> dict:
    return {
        "id": "no-active-sprint",
        "name": "Project backlog",
        "path": "",
        "attributes": {
            "startDate": None,
            "finishDate": None,
        },
    }


def wiql(token: str, query: str) -> list[int]:
    url = f"{BASE_URL}/_apis/wit/wiql?api-version={API_VER}"
    result = api(token, "POST", url, {"query": query})
    return [w["id"] for w in result.get("workItems", [])]


def fetch_items(token: str, ids: list[int], fields: list[str]) -> list[dict]:
    if not ids:
        return []
    url = (
        f"{BASE_URL}/_apis/wit/workitems"
        f"?ids={','.join(str(i) for i in ids)}"
        f"&fields={','.join(fields)}"
        f"&api-version={API_VER}"
    )
    return [item["fields"] for item in api(token, "GET", url).get("value", [])]


def patch_item(token: str, item_id: int, ops: list[dict]):
    url = f"{BASE_URL}/_apis/wit/workitems/{item_id}?api-version={API_VER}"
    api(token, "PATCH", url, ops)


def fetch_work_item_title(token: str, item_id: int) -> str:
    item = fetch_items(token, [item_id], ["System.Id", "System.Title"])
    if not item:
        raise CliError(f"Work item {item_id} not found.")
    return item[0]["System.Title"]


def fetch_work_item(token: str, item_id: int, *, expand: str | None = None, fields: list[str] | None = None) -> dict:
    query: list[tuple[str, str]] = [("api-version", API_VER)]
    if expand:
        query.append(("$expand", expand))
    if fields:
        query.append(("fields", ",".join(fields)))
    url = f"{BASE_URL}/_apis/wit/workitems/{item_id}?{urllib.parse.urlencode(query)}"
    return api(token, "GET", url)


def fetch_work_item_type_states(token: str, work_item_type: str) -> list[dict]:
    type_name = urllib.parse.quote(work_item_type, safe="")
    url = f"{BASE_URL}/_apis/wit/workitemtypes/{type_name}/states?api-version={API_VER}"
    return api(token, "GET", url).get("value", [])


def resolve_transition_state_name(token: str, *, item_id: int, desired_state: str) -> str:
    work_item = fetch_work_item(token, item_id, fields=["System.WorkItemType", "System.State"])
    work_item_type = (work_item.get("fields") or {}).get("System.WorkItemType") or "Work Item"
    supported_states = fetch_work_item_type_states(token, work_item_type)
    if not supported_states:
        return desired_state

    states_by_casefold = {
        (state.get("name") or "").casefold(): state.get("name") or ""
        for state in supported_states
        if state.get("name")
    }
    if desired_state.casefold() in states_by_casefold:
        return states_by_casefold[desired_state.casefold()]

    preferences = WORKFLOW_STATE_PREFERENCES.get(desired_state)
    if preferences:
        for candidate_name in preferences["preferred_names"]:
            resolved_name = states_by_casefold.get(candidate_name.casefold())
            if resolved_name:
                return resolved_name
        for category in preferences["preferred_categories"]:
            for state in supported_states:
                if (state.get("category") or "") == category and state.get("name"):
                    return state["name"]

    supported_names = ", ".join(state.get("name") or "?" for state in supported_states)
    raise CliError(
        f"ERROR: Work item {item_id} does not support workflow state '{desired_state}'. "
        f"Supported states for {work_item_type}: {supported_names}."
    )


def fetch_work_item_comments(token: str, item_id: int) -> list[dict]:
    url = (
        f"{BASE_URL}/_apis/wit/workItems/{item_id}/comments"
        f"?api-version=7.1-preview.3"
    )
    return api(token, "GET", url).get("comments", [])


def split_tags(tags: str | None) -> list[str]:
    return [tag.strip() for tag in (tags or "").split(";") if tag.strip()]


def assigned_display_name(assigned) -> str:
    if isinstance(assigned, dict):
        return assigned.get("displayName") or assigned.get("uniqueName") or "?"
    return str(assigned or "?")


def slugify_text(text: str, *, max_length: int = 60) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    slug = re.sub(r"-+", "-", slug)
    if len(slug) <= max_length:
        return slug or "work-item"
    truncated = slug[:max_length].rstrip("-")
    safe_truncated = truncated.rsplit("-", 1)[0]
    return (safe_truncated or truncated or "work-item").rstrip("-")


def parse_title_facets(title: str) -> dict:
    parts = [part.strip() for part in (title or "").split("|")]
    if len(parts) >= 4:
        return {
            "severity": parts[0],
            "scope": parts[1],
            "product": parts[2],
            "tailTitle": " | ".join(parts[3:]).strip(),
        }
    return {
        "severity": "",
        "scope": "",
        "product": "",
        "tailTitle": (title or "").strip(),
    }


def canonical_work_title(title: str) -> str:
    facets = parse_title_facets(title)
    return facets["tailTitle"] or (title or "").strip()


def branch_prefix_for_work_item_type(work_item_type: str) -> str:
    return "fix" if work_item_type == "Bug" else "feat"


def suggest_start_work_plan(item: dict) -> dict:
    fields = item.get("fields", {})
    work_item = TrackedWorkItem(
        id=fields.get("System.Id"),
        title=fields.get("System.Title", ""),
        kind=fields.get("System.WorkItemType", ""),
        state=fields.get("System.State", ""),
        assignee=assigned_display_name(fields.get("System.AssignedTo")),
        iteration=fields.get("System.IterationPath", ""),
        area=fields.get("System.AreaPath", ""),
        estimate=fields.get("Microsoft.VSTS.Scheduling.StoryPoints"),
        labels=split_tags(fields.get("System.Tags", "")),
    )
    concise_title = canonical_work_title(work_item.title)
    slug = slugify_text(concise_title)
    branch_prefix = branch_prefix_for_work_item_type(work_item.kind)
    branch_name = f"{branch_prefix}/{work_item.id}-{slug}"
    note_path = f".agent-notes/{work_item.id}-{slug}.md"
    commit_prefix = f"{branch_prefix}: {work_item.id} "
    plan = StartWorkPlan(
        work_item=work_item,
        branch_name=branch_name,
        note_path=note_path,
        commit_prefix=commit_prefix,
        change_request_title=f"[{work_item.id}] {work_item.title}",
        concise_title=concise_title,
        change_request_body=f"Closes #{work_item.id}",
        commands=[
            "git checkout main && git pull",
            f"git checkout -b {branch_name}",
            f"git push -u origin {branch_name}",
        ],
    )
    return plan.to_legacy_dict()


def title_keywords(title: str) -> list[str]:
    normalized_title = canonical_work_title(title)
    return [
        token for token in re.findall(r"[a-z0-9]+", normalized_title.lower())
        if len(token) >= 3 and token not in TITLE_TOKEN_STOPWORDS and not token.isdigit()
    ]


def triage_entry(fields: dict) -> dict:
    facets = parse_title_facets(fields.get("System.Title", ""))
    return {
        "id": fields.get("System.Id"),
        "title": fields.get("System.Title", ""),
        "conciseTitle": facets["tailTitle"] or fields.get("System.Title", ""),
        "workItemType": fields.get("System.WorkItemType", ""),
        "state": fields.get("System.State", ""),
        "areaPath": fields.get("System.AreaPath", ""),
        "owner": fields.get("System.AreaPath", "").split("\\")[-1] if fields.get("System.AreaPath") else "",
        "scope": facets["scope"],
        "severity": facets["severity"],
        "product": facets["product"],
        "tags": split_tags(fields.get("System.Tags", "")),
        "keywords": title_keywords(fields.get("System.Title", "")),
    }


def pair_triage_reasons(left: dict, right: dict) -> list[str]:
    shared_keywords = sorted(set(left["keywords"]) & set(right["keywords"]))
    shared_tags = sorted(set(left["tags"]) & set(right["tags"]))
    reasons: list[str] = []
    if left["areaPath"] and left["areaPath"] == right["areaPath"]:
        reasons.append(f"same area path ({left['areaPath']})")
    elif left["owner"] and left["owner"] == right["owner"]:
        reasons.append(f"same owner ({left['owner']})")
    if left["scope"] and left["scope"] == right["scope"]:
        reasons.append(f"same scope ({left['scope']})")
    if left["product"] and left["product"] == right["product"]:
        reasons.append(f"same product ({left['product']})")
    if shared_keywords:
        reasons.append(f"shared title keywords: {', '.join(shared_keywords[:4])}")
    if shared_tags:
        reasons.append(f"shared tags: {', '.join(shared_tags[:4])}")
    return reasons


def should_group_triage_items(left: dict, right: dict) -> bool:
    shared_keywords = set(left["keywords"]) & set(right["keywords"])
    shared_tags = set(left["tags"]) & set(right["tags"])
    same_area = bool(left["areaPath"] and left["areaPath"] == right["areaPath"])
    same_scope = bool(left["scope"] and left["scope"] == right["scope"])
    same_product = bool(left["product"] and left["product"] == right["product"])

    if same_area and same_scope and (shared_keywords or shared_tags):
        return True
    if same_scope and same_product and len(shared_keywords) >= 2:
        return True
    if same_scope and len(shared_tags) >= 2:
        return True
    return False


def build_triage_report(item_fields: list[dict]) -> dict:
    items = [triage_entry(fields) for fields in item_fields]
    edges: dict[int, set[int]] = {entry["id"]: set() for entry in items}
    pairings: list[dict] = []

    for index, left in enumerate(items):
        for right in items[index + 1:]:
            reasons = pair_triage_reasons(left, right)
            grouped = should_group_triage_items(left, right)
            pairings.append({
                "leftId": left["id"],
                "rightId": right["id"],
                "suggestSameGroup": grouped,
                "reasons": reasons,
            })
            if grouped:
                edges[left["id"]].add(right["id"])
                edges[right["id"]].add(left["id"])

    groups: list[list[int]] = []
    visited: set[int] = set()
    for item in items:
        item_id = item["id"]
        if item_id in visited:
            continue
        stack = [item_id]
        component: list[int] = []
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            component.append(current)
            stack.extend(sorted(edges[current] - visited))
        groups.append(sorted(component))
    groups.sort(key=lambda group: (len(group) * -1, group[0]))

    grouped_output = []
    for group in groups:
        reasons = []
        for pairing in pairings:
            if pairing["suggestSameGroup"] and pairing["leftId"] in group and pairing["rightId"] in group:
                reasons.extend(pairing["reasons"])
        unique_reasons = []
        for reason in reasons:
            if reason not in unique_reasons:
                unique_reasons.append(reason)
        grouped_output.append({
            "ids": group,
            "reasonSummary": unique_reasons or ["No strong overlap signals; keep this work item isolated for review."],
        })

    return {"items": items, "pairings": pairings, "groups": grouped_output}


def work_item_sort_key(fields: dict) -> tuple:
    state_rank = {
        "Ready for development": 0,
        "New": 1,
    }
    type_rank = {
        "Bug": 0,
        "User Story": 1,
        "Task": 2,
        "Spike": 3,
    }
    return (
        state_rank.get(fields.get("System.State", ""), 99),
        type_rank.get(fields.get("System.WorkItemType", ""), 99),
        fields.get("System.Id", 0),
    )


def open_candidate_items(token: str) -> tuple[dict, list[dict]]:
    sprint = current_sprint(token)
    if sprint:
        path = sprint["path"].replace("\\", "\\\\")
        iteration_clause = f"AND [System.IterationPath] UNDER '{path}' "
    else:
        sprint = backlog_iteration_context()
        iteration_clause = ""
    query = (
        f"SELECT [System.Id] FROM WorkItems "
        f"WHERE [System.AssignedTo] = '{ME}' "
        f"{iteration_clause}"
        f"AND [System.State] IN ('New','Ready for development') "
        f"AND [System.WorkItemType] IN ('Bug','User Story','Task','Spike') "
        f"ORDER BY [System.Id]"
    )
    ids = wiql(token, query)
    items = fetch_items(token, ids, [
        "System.Id", "System.WorkItemType", "System.State", "System.Title",
    ])
    items.sort(key=work_item_sort_key)
    return sprint, items