import mimetypes
import os
import re
import shutil
import subprocess
import urllib.parse
import urllib.request
from html import unescape
from html.parser import HTMLParser

from app_config import API_VER, BASE_URL, OPENER, ORG, PROJECT
from errors import CliError
from providers.azure_devops.http import api, api_with_headers
from providers.azure_devops.pull_requests import pr_browser_url, project_base_url, short_branch_name
from providers.azure_devops.work_items import (
    assigned_display_name,
    fetch_items,
    fetch_work_item,
    fetch_work_item_comments,
    split_tags,
)
from workflow_models import (
    LinkedChangeRequest,
    LinkedCommit,
    RelatedWorkItem,
    WorkItemComment,
    WorkItemSummary,
)


IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg")
DOWNLOAD_TIMEOUT_SECONDS = 30
MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024
IMAGE_URL_PATTERN = re.compile(
    r"https?://[^\"'\s>]+?(?:\.png|\.jpg|\.jpeg|\.gif|\.webp|\.bmp|\.svg)(?:\?[^\"'\s>]*)?",
    re.IGNORECASE,
)
WORK_ITEM_TEXT_FIELDS = (
    ("System.Description", "Description"),
    ("Microsoft.VSTS.TCM.ReproSteps", "Repro Steps"),
    ("Microsoft.VSTS.Common.AcceptanceCriteria", "Acceptance Criteria"),
)
WORK_ITEM_RELATION_GROUPS = {
    "System.LinkTypes.Hierarchy-Reverse": "parents",
    "System.LinkTypes.Hierarchy-Forward": "children",
    "System.LinkTypes.Related": "related",
}


def render_html_text(text: str) -> str:
    rendered = text or ""
    rendered = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", rendered)
    rendered = re.sub(r"(?i)</\s*(p|div|li|tr|h[1-6])\s*>", "\n", rendered)
    rendered = re.sub(r"(?i)<\s*li\b[^>]*>", "- ", rendered)
    rendered = re.sub(r"<[^>]+>", "", rendered)
    rendered = unescape(rendered)
    rendered = rendered.replace("\r", "")
    rendered = re.sub(r"[ \t]+\n", "\n", rendered)
    rendered = re.sub(r"\n{3,}", "\n\n", rendered)
    return rendered.strip()


def summarize_references(references: list[dict], *, limit: int = 3) -> tuple[str, str] | None:
    summary_data = reference_summary_data(references, limit=limit)
    if not summary_data:
        return None
    summary = (
        f"  Context Refs: {summary_data['total']} total "
        f"({summary_data['imageCount']} images, {summary_data['fileCount']} files/links)"
    )
    preview_items = []
    for reference in summary_data["preview"]:
        name = f" ({reference['name']})" if reference.get("name") else ""
        preview_items.append(f"[{reference['kind']}] {reference['label']}{name}")
    preview = f"  Preview     : {'; '.join(preview_items)}"
    if summary_data["remaining"]:
        preview += f"; ... and {summary_data['remaining']} more"
    return summary, preview


def reference_summary_data(references: list[dict], *, limit: int = 3) -> dict | None:
    if not references:
        return None
    image_count = sum(1 for reference in references if reference["is_image"])
    preview = []
    for reference in references[:limit]:
        preview.append({
            "kind": "image" if reference["is_image"] else "attachment",
            "label": reference["label"],
            "name": reference.get("name", ""),
            "url": reference["url"],
        })
    return {
        "total": len(references),
        "imageCount": image_count,
        "fileCount": len(references) - image_count,
        "preview": preview,
        "remaining": max(0, len(references) - len(preview)),
    }


class HtmlReferenceParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.image_urls: list[str] = []
        self.link_urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        attr_map = {
            key.lower(): value
            for key, value in attrs
            if key and value
        }
        tag_name = tag.lower()
        if tag_name == "img" and attr_map.get("src"):
            self.image_urls.append(unescape(attr_map["src"]))
        if tag_name == "a" and attr_map.get("href"):
            self.link_urls.append(unescape(attr_map["href"]))


def normalize_reference_url(url: str) -> str:
    return urllib.parse.urljoin(f"{BASE_URL}/", url)


def extract_html_references(raw_html: str) -> tuple[list[str], list[str]]:
    parser = HtmlReferenceParser()
    parser.feed(raw_html or "")
    parser.close()
    return parser.image_urls, parser.link_urls


def extract_plain_image_urls(text: str) -> list[str]:
    return IMAGE_URL_PATTERN.findall(text or "")


def filename_from_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)
    for key, values in query.items():
        if values and key.lower() in ("filename", "name"):
            return values[0]
    return urllib.parse.unquote(os.path.basename(parsed.path))


def is_image_name(name: str | None) -> bool:
    if not name:
        return False
    lowered = name.lower()
    return any(lowered.endswith(ext) for ext in IMAGE_EXTENSIONS)


def is_image_url(url: str) -> bool:
    return is_image_name(filename_from_url(normalize_reference_url(url)))


def default_download_dir(item_id: int) -> str:
    return os.path.join(os.getcwd(), ".sg-artifacts", f"work-item-{item_id}")


def sanitize_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", name.strip())
    return cleaned.strip("-.") or "attachment"


def filename_from_headers(headers) -> str:
    disposition = headers.get("Content-Disposition", "")
    match = re.search(r"filename\*=UTF-8''([^;]+)", disposition, re.IGNORECASE)
    if match:
        return urllib.parse.unquote(match.group(1))
    match = re.search(r'filename="?([^";]+)"?', disposition, re.IGNORECASE)
    if match:
        return urllib.parse.unquote(match.group(1))
    return ""


def extension_from_headers(headers) -> str:
    content_type = headers.get("Content-Type", "").split(";", 1)[0].strip()
    return mimetypes.guess_extension(content_type) or ""


def unique_download_path(download_dir: str, filename: str) -> str:
    base, ext = os.path.splitext(filename)
    candidate = os.path.join(download_dir, filename)
    counter = 2
    while os.path.exists(candidate):
        candidate = os.path.join(download_dir, f"{base}-{counter}{ext}")
        counter += 1
    return candidate


def is_trusted_azure_devops_url(url: str, org_name: str | None = None) -> bool:
    """Return whether an URL is inside the configured Azure DevOps auth scope."""
    configured_org = ORG if org_name is None else org_name
    if not configured_org:
        return False

    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError:
        return False

    if parsed.scheme.lower() != "https":
        return False
    if parsed.username is not None or parsed.password is not None:
        return False
    if port not in (None, 443):
        return False

    hostname = (parsed.hostname or "").casefold()
    org = configured_org.casefold()
    if hostname == "dev.azure.com":
        path_segments = parsed.path.split("/")
        if len(path_segments) < 2:
            return False
        return urllib.parse.unquote(path_segments[1]).casefold() == org

    return hostname == f"{configured_org}.visualstudio.com".casefold()


def read_bounded_download(response, *, max_bytes: int = MAX_DOWNLOAD_BYTES) -> bytes:
    content_length = response.headers.get("Content-Length")
    if content_length:
        try:
            declared_size = int(content_length)
        except (TypeError, ValueError):
            declared_size = None
        if declared_size is not None and declared_size > max_bytes:
            raise ValueError(f"Download exceeds the {max_bytes}-byte size limit.")

    payload = response.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise ValueError(f"Download exceeds the {max_bytes}-byte size limit.")
    return payload


def build_reference(source: str, label: str, url: str, *, name: str = "", is_image: bool) -> dict:
    return {
        "source": source,
        "label": label,
        "url": normalize_reference_url(url),
        "name": name,
        "is_image": is_image,
    }


def dedupe_references(references: list[dict]) -> list[dict]:
    unique: list[dict] = []
    seen: set[tuple[str, str, str, str, bool]] = set()
    for reference in references:
        key = (
            reference["source"],
            reference["label"],
            reference["url"],
            reference.get("name", ""),
            reference["is_image"],
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(reference)
    return unique


def collect_attachment_references(item: dict, comments: list[dict]) -> list[dict]:
    references: list[dict] = []
    for relation in item.get("relations", []):
        rel_type = relation.get("rel", "")
        url = relation.get("url", "")
        if not url:
            continue
        name = relation.get("attributes", {}).get("name", "")
        image_relation = is_image_name(name) or is_image_url(url)
        if rel_type == "AttachedFile" or image_relation:
            references.append(
                build_reference(
                    "relation",
                    rel_type or "Relation",
                    url,
                    name=name,
                    is_image=image_relation,
                )
            )

    for field_name, label in WORK_ITEM_TEXT_FIELDS:
        raw_html = item.get("fields", {}).get(field_name, "")
        image_urls, link_urls = extract_html_references(raw_html)
        for url in image_urls:
            references.append(build_reference("field", label, url, name=filename_from_url(url), is_image=True))
        for url in link_urls:
            if is_image_url(url):
                references.append(build_reference("field", label, url, name=filename_from_url(url), is_image=True))
        for url in extract_plain_image_urls(raw_html):
            references.append(build_reference("field", label, url, name=filename_from_url(url), is_image=True))

    for comment in comments:
        label = f"Comment {comment.get('id', '?')}"
        raw_html = comment.get("text", "") or ""
        image_urls, link_urls = extract_html_references(raw_html)
        for url in image_urls:
            references.append(build_reference("comment", label, url, name=filename_from_url(url), is_image=True))
        for url in link_urls:
            if is_image_url(url):
                references.append(build_reference("comment", label, url, name=filename_from_url(url), is_image=True))
        for url in extract_plain_image_urls(raw_html):
            references.append(build_reference("comment", label, url, name=filename_from_url(url), is_image=True))

    return dedupe_references(references)


def serialize_reference(reference: dict) -> dict:
    return {
        "source": reference["source"],
        "label": reference["label"],
        "url": reference["url"],
        "name": reference.get("name", ""),
        "isImage": reference["is_image"],
    }


def work_item_text_sections(fields: dict) -> dict:
    return {
        "description": render_html_text(fields.get("System.Description", "")) or "",
        "reproSteps": render_html_text(fields.get("Microsoft.VSTS.TCM.ReproSteps", "")) or "",
        "acceptanceCriteria": render_html_text(fields.get("Microsoft.VSTS.Common.AcceptanceCriteria", "")) or "",
    }


def serialize_work_item_summary(item: dict) -> WorkItemSummary:
    fields = item.get("fields", {})
    return WorkItemSummary(
        id=fields.get("System.Id"),
        title=fields.get("System.Title", ""),
        kind=fields.get("System.WorkItemType", ""),
        state=fields.get("System.State", ""),
        assignee=assigned_display_name(fields.get("System.AssignedTo")),
        iteration=fields.get("System.IterationPath", ""),
        area=fields.get("System.AreaPath", ""),
        estimate=fields.get("Microsoft.VSTS.Scheduling.StoryPoints"),
        tags=split_tags(fields.get("System.Tags", "")),
        sections=work_item_text_sections(fields),
    )


def serialize_work_item_comment(comment: dict) -> WorkItemComment:
    identity = comment.get("createdBy") or comment.get("modifiedBy") or {}
    return WorkItemComment(
        id=comment.get("id"),
        author=assigned_display_name(identity),
        published_date=comment.get("publishedDate") or comment.get("createdDate"),
        text=render_html_text(comment.get("text") or ""),
    )


def extract_work_item_id_from_relation_url(url: str) -> int | None:
    match = re.search(r"/workItems/(\d+)", url or "")
    return int(match.group(1)) if match else None


def serialize_related_item(fields: dict) -> RelatedWorkItem:
    return RelatedWorkItem(
        id=fields.get("System.Id"),
        title=fields.get("System.Title", ""),
        kind=fields.get("System.WorkItemType", ""),
        state=fields.get("System.State", ""),
    )


def collect_related_items(token: str, item: dict) -> dict[str, list[dict]]:
    grouped_ids: dict[str, list[int]] = {"parents": [], "children": [], "related": []}
    for relation in item.get("relations", []):
        group = WORK_ITEM_RELATION_GROUPS.get(relation.get("rel", ""))
        if not group:
            continue
        related_id = extract_work_item_id_from_relation_url(relation.get("url", ""))
        if related_id is not None:
            grouped_ids[group].append(related_id)

    all_ids = [item_id for ids in grouped_ids.values() for item_id in ids]
    if not all_ids:
        return {"parents": [], "children": [], "related": []}

    fields_by_id = {
        fields.get("System.Id"): serialize_related_item(fields)
        for fields in fetch_items(
            token,
            all_ids,
            ["System.Id", "System.Title", "System.WorkItemType", "System.State"],
        )
    }
    return {
        group: [fields_by_id[item_id].to_legacy_dict() for item_id in ids if item_id in fields_by_id]
        for group, ids in grouped_ids.items()
    }


def parse_git_artifact_link(url: str) -> dict | None:
    parsed = urllib.parse.urlparse(url or "")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 3 or parts[0] != "Git":
        return None

    artifact_type = parts[1]
    artifact_value = urllib.parse.unquote("/".join(parts[2:]))
    segments = artifact_value.split("/")
    if artifact_type == "PullRequestId" and len(segments) >= 3:
        try:
            pr_id = int(segments[2])
        except ValueError:
            return None
        return {
            "kind": "pullRequest",
            "projectRef": segments[0],
            "repoRef": segments[1],
            "pullRequestId": pr_id,
        }
    if artifact_type == "Commit" and len(segments) >= 3:
        return {
            "kind": "commit",
            "projectRef": segments[0],
            "repoRef": segments[1],
            "commitId": segments[2],
        }
    return {
        "kind": "other",
        "artifactType": artifact_type,
        "raw": artifact_value,
    }


def commit_browser_url(
    repo_name: str,
    commit_id: str,
    *,
    project_name: str | None = None,
    org_name: str | None = None,
) -> str:
    return (
        f"https://dev.azure.com/{org_name or ORG}/"
        f"{urllib.parse.quote(project_name or PROJECT)}/_git/"
        f"{urllib.parse.quote(repo_name)}/commit/{commit_id}"
    )


def list_repositories(token: str, project_name: str | None = None, org_name: str | None = None) -> list[dict]:
    url = f"{project_base_url(project_name, org_name)}/_apis/git/repositories?api-version={API_VER}"
    return api(token, "GET", url).get("value", [])


def fetch_pull_request_optional(
    token: str,
    repo_id: str,
    pr_id: int,
    *,
    project_name: str | None = None,
    org_name: str | None = None,
) -> dict | None:
    url = (
        f"{project_base_url(project_name, org_name)}/_apis/git/repositories/{repo_id}/pullrequests/{pr_id}"
        f"?api-version={API_VER}"
    )
    return api_with_headers(token, "GET", url, allowed_status_codes={404})[0]


def fetch_commit_optional(
    token: str,
    repo_id: str,
    commit_id: str,
    *,
    project_name: str | None = None,
    org_name: str | None = None,
) -> dict | None:
    url = (
        f"{project_base_url(project_name, org_name)}/_apis/git/repositories/{repo_id}/commits/{commit_id}"
        f"?api-version={API_VER}"
    )
    return api_with_headers(token, "GET", url, allowed_status_codes={404})[0]


def resolve_repo_metadata(token: str, project_ref: str, repo_ref: str, cache: dict[tuple[str, str], dict]) -> dict:
    cache_key = (project_ref, repo_ref)
    if cache_key in cache:
        return cache[cache_key]

    repo = None
    for candidate in list_repositories(token, project_name=project_ref):
        if repo_ref in (candidate.get("id"), candidate.get("name")):
            repo = candidate
            break
    if repo is None:
        repo = {"id": repo_ref, "name": repo_ref, "project": {"name": project_ref}}
    cache[cache_key] = repo
    return repo


def collect_development_artifacts(token: str, item: dict) -> dict:
    repo_cache: dict[tuple[str, str], dict] = {}
    pull_requests: list[LinkedChangeRequest] = []
    commits: list[LinkedCommit] = []
    other: list[dict] = []
    seen: set[tuple[str, str, str]] = set()

    for relation in item.get("relations", []):
        if relation.get("rel") != "ArtifactLink":
            continue
        artifact = parse_git_artifact_link(relation.get("url", ""))
        if not artifact:
            continue
        if artifact["kind"] == "pullRequest":
            key = (artifact["kind"], artifact["projectRef"], f"{artifact['repoRef']}:{artifact['pullRequestId']}")
            if key in seen:
                continue
            seen.add(key)
            repo = resolve_repo_metadata(token, artifact["projectRef"], artifact["repoRef"], repo_cache)
            project_name = (repo.get("project") or {}).get("name") or artifact["projectRef"]
            pr = fetch_pull_request_optional(
                token,
                repo["id"],
                artifact["pullRequestId"],
                project_name=project_name,
            )
            pull_requests.append(LinkedChangeRequest(
                repo_name=repo.get("name") or artifact["repoRef"],
                repo_id=repo.get("id") or artifact["repoRef"],
                project=project_name,
                change_request_id=artifact["pullRequestId"],
                title=(pr or {}).get("title"),
                status=(pr or {}).get("status"),
                source_branch=short_branch_name((pr or {}).get("sourceRefName")),
                target_branch=short_branch_name((pr or {}).get("targetRefName")),
                author=((pr or {}).get("createdBy") or {}).get("displayName"),
                created_at=(pr or {}).get("creationDate"),
                closed_at=(pr or {}).get("closedDate"),
                merge_commit_id=((pr or {}).get("lastMergeCommit") or {}).get("commitId"),
                browser_url=pr_browser_url(
                    repo.get("name") or artifact["repoRef"],
                    artifact["pullRequestId"],
                    project_name=project_name,
                ),
            ))
            continue
        if artifact["kind"] == "commit":
            key = (artifact["kind"], artifact["projectRef"], f"{artifact['repoRef']}:{artifact['commitId']}")
            if key in seen:
                continue
            seen.add(key)
            repo = resolve_repo_metadata(token, artifact["projectRef"], artifact["repoRef"], repo_cache)
            project_name = (repo.get("project") or {}).get("name") or artifact["projectRef"]
            commit = fetch_commit_optional(
                token,
                repo["id"],
                artifact["commitId"],
                project_name=project_name,
            )
            author = ((commit or {}).get("author") or {}).get("name")
            commit_id = (commit or {}).get("commitId") or artifact["commitId"]
            commits.append(LinkedCommit(
                repo_name=repo.get("name") or artifact["repoRef"],
                repo_id=repo.get("id") or artifact["repoRef"],
                project=project_name,
                commit_id=commit_id,
                comment=(commit or {}).get("comment"),
                author=author,
                authored_at=((commit or {}).get("author") or {}).get("date"),
                browser_url=commit_browser_url(
                    repo.get("name") or artifact["repoRef"],
                    commit_id,
                    project_name=project_name,
                ),
            ))
            continue
        other.append({
            "artifactType": artifact.get("artifactType"),
            "raw": artifact.get("raw"),
            "url": relation.get("url"),
        })

    pull_requests.sort(key=lambda entry: entry.closed_at or entry.created_at or "", reverse=True)
    commits.sort(key=lambda entry: entry.authored_at or "", reverse=True)
    return {
        "pullRequests": [entry.to_legacy_dict() for entry in pull_requests],
        "commits": [entry.to_legacy_dict() for entry in commits],
        "other": other,
    }


def build_work_item_context(token: str, item_id: int) -> dict:
    item = fetch_work_item(token, item_id, expand="all")
    if not item:
        raise CliError(f"Work item {item_id} not found.")
    comments = fetch_work_item_comments(token, item_id)
    references = collect_attachment_references(item, comments)
    serialized_comments = [serialize_work_item_comment(comment) for comment in comments]
    serialized_comments.sort(key=lambda comment: comment.published_date or "", reverse=True)
    work_item = serialize_work_item_summary(item)
    return {
        "workItem": work_item.to_legacy_dict(),
        "referenceSummary": reference_summary_data(references),
        "references": [serialize_reference(reference) for reference in references],
        "commentCount": len(serialized_comments),
        "recentComments": [comment.to_legacy_dict() for comment in serialized_comments[:3]],
        "relatedItems": collect_related_items(token, item),
        "developmentArtifacts": collect_development_artifacts(token, item),
    }


def build_work_item_comments(token: str, item_id: int) -> dict:
    item = fetch_work_item(token, item_id, expand="all")
    if not item:
        raise CliError(f"Work item {item_id} not found.")

    comments = [serialize_work_item_comment(comment) for comment in fetch_work_item_comments(token, item_id)]
    comments.sort(key=lambda comment: comment.published_date or "", reverse=True)
    work_item = serialize_work_item_summary(item)
    return {
        "workItem": work_item.to_legacy_dict(),
        "commentCount": len(comments),
        "comments": [comment.to_legacy_dict() for comment in comments],
    }


def best_introduction_candidate(development_artifacts: dict) -> dict | None:
    for pr in development_artifacts.get("pullRequests", []):
        if pr.get("status") == "completed":
            return {"type": "pullRequest", **pr}
    if development_artifacts.get("pullRequests"):
        return {"type": "pullRequest", **development_artifacts["pullRequests"][0]}
    if development_artifacts.get("commits"):
        return {"type": "commit", **development_artifacts["commits"][0]}
    return None


def open_with_default_app(path: str) -> tuple[bool, str | None]:
    opener = shutil.which("open") or shutil.which("xdg-open")
    if not opener:
        return False, "No system opener command found (expected 'open' or 'xdg-open')."
    result = subprocess.run([opener, path], capture_output=True, text=True)
    if result.returncode != 0:
        return False, result.stderr.strip() or result.stdout.strip() or f"Failed to open {path}."
    return True, None


def download_reference(
    token: str,
    reference: dict,
    download_dir: str,
    sequence: int,
    downloaded_urls: dict[str, str],
) -> tuple[str, bool]:
    url = reference["url"]
    if url in downloaded_urls:
        return downloaded_urls[url], True

    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Unsupported download URL: {url}")

    req = urllib.request.Request(url, headers={"Accept": "*/*"})
    if is_trusted_azure_devops_url(url):
        # Keep credentials on the initial request only. urllib's redirect handler
        # copies regular headers, but intentionally omits unredirected headers.
        req.add_unredirected_header("Authorization", f"Bearer {token}")
    with OPENER.open(req, timeout=DOWNLOAD_TIMEOUT_SECONDS) as resp:
        payload = read_bounded_download(resp)
        headers = resp.headers

    filename_candidates = [
        reference.get("name", ""),
        filename_from_headers(headers),
        filename_from_url(url),
    ]
    filename = ""
    for candidate in filename_candidates:
        if candidate:
            filename = sanitize_filename(candidate)
            if filename:
                break
    if not filename:
        prefix = "image" if reference["is_image"] else "attachment"
        filename = f"{prefix}-{sequence}"

    if not os.path.splitext(filename)[1]:
        extension = extension_from_headers(headers)
        if not extension and reference["is_image"]:
            extension = ".bin"
        filename = f"{filename}{extension}"

    os.makedirs(download_dir, exist_ok=True)
    target_path = unique_download_path(download_dir, filename)
    with open(target_path, "wb") as handle:
        handle.write(payload)

    downloaded_urls[url] = target_path
    return target_path, False
