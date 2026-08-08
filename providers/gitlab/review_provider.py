from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import ssl
import subprocess
import urllib.error
import urllib.parse
import urllib.request

from app_config import GITLAB_BASE_URL, OPENER
from errors import CliError
from git_client import current_git_branch
from providers.interfaces import RepositoryRef, ReviewAnalysis, ReviewContext, ReviewMutationPreview, ReviewMutationResult
from workflow_models import (
    ChangeRequest,
    ChangeRequestReviewer,
    ChangeRequestStatus,
    ExistingReviewComment,
    ReviewChangeSummary,
    ReviewComment,
    ReviewFileChange,
    ReviewThread,
)


def _normalized_https_origin(url: str, *, origin_only: bool = False) -> str:
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise CliError(f"ERROR: Invalid GitLab URL '{url}'.") from exc

    if parsed.scheme.lower() != "https" or not parsed.netloc or not parsed.hostname:
        raise CliError(f"ERROR: GitLab URLs must use HTTPS: '{url}'.")
    if parsed.username is not None or parsed.password is not None:
        raise CliError(f"ERROR: GitLab URLs must not contain user information: '{url}'.")
    if origin_only and (parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
        raise CliError(f"ERROR: GitLab base URLs must contain only an HTTPS origin: '{url}'.")

    try:
        hostname = parsed.hostname.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise CliError(f"ERROR: Invalid GitLab URL hostname in '{url}'.") from exc
    if not hostname:
        raise CliError(f"ERROR: Invalid GitLab URL hostname in '{url}'.")

    host = f"[{hostname}]" if ":" in hostname else hostname
    normalized_port = 443 if port is None else port
    return f"https://{host}" if normalized_port == 443 else f"https://{host}:{normalized_port}"


def parse_gitlab_merge_request_url(url: str) -> dict[str, object]:
    parsed = urllib.parse.urlsplit(url)
    path = urllib.parse.unquote(parsed.path or "").rstrip("/")
    base_url = _normalized_https_origin(url)

    marker = "/-/merge_requests/"
    if marker in path:
        project_path, iid_text = path.split(marker, 1)
    elif "/merge_requests/" in path:
        project_path, iid_text = path.split("/merge_requests/", 1)
    else:
        raise CliError(f"ERROR: Unsupported GitLab merge request URL '{url}'.")

    project_path = project_path.lstrip("/")
    if not project_path or not iid_text.isdigit():
        raise CliError(f"ERROR: Unsupported GitLab merge request URL '{url}'.")

    return {
        "base_url": base_url,
        "project_path": project_path,
        "merge_request_iid": int(iid_text),
    }


def is_gitlab_merge_request_url(url: str | None) -> bool:
    if not url:
        return False
    try:
        parse_gitlab_merge_request_url(url)
    except CliError:
        return False
    return True


def _display_path(path: str | None) -> str | None:
    if not path:
        return None
    return path if path.startswith("/") else f"/{path}"


def _api_url(base_url: str, path: str, query: dict | None = None) -> str:
    url = f"{base_url}/api/v4{path}"
    if query:
        url += "?" + urllib.parse.urlencode(query, doseq=True)
    return url


def _change_type(diff: dict) -> str:
    if diff.get("new_file"):
        return "add"
    if diff.get("deleted_file"):
        return "delete"
    if diff.get("renamed_file"):
        return "rename"
    return "edit"


def _form_payload(data: dict | None) -> dict[str, str]:
    payload: dict[str, str] = {}
    for key, value in (data or {}).items():
        if value is None:
            continue
        if isinstance(value, bool):
            payload[key] = "true" if value else "false"
        else:
            payload[key] = str(value)
    return payload


COMMON_CURL_PATHS = (
    "/usr/bin/curl",
    "/bin/curl",
)

HUNK_HEADER_RE = re.compile(r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? \+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@")


def _line_code(file_path: str, *, old_line: int | None, new_line: int | None) -> str:
    old_part = "" if old_line is None else str(old_line)
    new_part = "" if new_line is None else str(new_line)
    return f"{hashlib.sha1(file_path.encode('utf-8')).hexdigest()}_{old_part}_{new_part}"


def _parse_diff_lines(diff_text: str) -> list[dict[str, int | None]]:
    positions: list[dict[str, int | None]] = []
    old_line = None
    new_line = None

    for raw_line in diff_text.splitlines():
        header = HUNK_HEADER_RE.match(raw_line)
        if header:
            old_line = int(header.group("old_start"))
            new_line = int(header.group("new_start"))
            continue
        if old_line is None or new_line is None:
            continue
        if raw_line.startswith("\\"):
            continue
        if raw_line.startswith("+"):
            positions.append({"old_line": None, "new_line": new_line})
            new_line += 1
            continue
        if raw_line.startswith("-"):
            positions.append({"old_line": old_line, "new_line": None})
            old_line += 1
            continue

        positions.append({"old_line": old_line, "new_line": new_line})
        old_line += 1
        new_line += 1

    return positions


def _position_payload_entry(file_path: str, position: dict[str, int | None], side: str) -> dict[str, str | int]:
    payload: dict[str, str | int] = {
        "line_code": _line_code(file_path, old_line=position.get("old_line"), new_line=position.get("new_line")),
        "type": side,
    }
    if position.get("old_line") is not None:
        payload["old_line"] = int(position["old_line"])
    if position.get("new_line") is not None:
        payload["new_line"] = int(position["new_line"])
    return payload


class GitLabReviewProvider:
    def __init__(self, token: str | None):
        self.token = token
        self._credential_origin = (
            _normalized_https_origin(GITLAB_BASE_URL, origin_only=True)
            if token
            else None
        )

    def _request_headers(self, *, accept: str) -> dict[str, str]:
        return {"Accept": accept}

    def _validated_base_url(self, base_url: str) -> str:
        request_origin = _normalized_https_origin(base_url, origin_only=True)
        if self._credential_origin and request_origin != self._credential_origin:
            raise CliError(
                "ERROR: Refusing to send the GitLab token outside the configured "
                f"GITLAB_BASE_URL origin '{self._credential_origin}' (requested '{request_origin}')."
            )
        return request_origin

    def _add_authentication(self, request: urllib.request.Request) -> None:
        if self.token:
            # urllib copies normal headers when following redirects. Unredirected
            # headers apply only to this request, so a redirect cannot carry the token.
            request.add_unredirected_header("PRIVATE-TOKEN", self.token)

    def _curl_binary(self) -> str | None:
        curl_path = shutil.which("curl")
        if curl_path:
            return curl_path
        for candidate in COMMON_CURL_PATHS:
            if os.path.exists(candidate):
                return candidate
        return None

    def _request_with_curl(self, base_url: str, path: str, *, method: str = "GET", accept: str, query: dict | None = None, form_data: dict | None = None, allow_not_found: bool = False, allowed_status_codes: set[int] | None = None) -> str | None:
        if self.token:
            raise CliError(
                "ERROR: Authenticated GitLab requests do not use the curl TLS fallback. "
                "Repair the local Python CA trust configuration and retry."
            )
        base_url = self._validated_base_url(base_url)
        curl_path = self._curl_binary()
        if not curl_path:
            raise CliError(
                "ERROR: GitLab API request failed because local Python TLS verification failed and 'curl' is not available for fallback."
            )

        marker = "__SG_HTTP_CODE__:"
        command = [
            curl_path,
            "-sS",
            "-L",
            "-X",
            method,
            _api_url(base_url, path, query),
            "-H",
            f"Accept: {accept}",
            "-w",
            f"\n{marker}%{{http_code}}",
        ]
        payload = _form_payload(form_data)
        if payload:
            command.extend(["-H", "Content-Type: application/x-www-form-urlencoded"])
            command.extend(["--data-raw", urllib.parse.urlencode(payload)])

        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise CliError(f"ERROR: GitLab API request failed via curl: {detail or 'unknown curl error'}")

        if marker not in result.stdout:
            raise CliError("ERROR: GitLab API request via curl returned no HTTP status marker.")

        body, status_text = result.stdout.rsplit(marker, 1)
        status_code = int(status_text.strip())
        if allow_not_found and status_code == 404:
            return None
        if allowed_status_codes and status_code in allowed_status_codes:
            return None
        if status_code >= 400:
            detail = body.strip()
            raise CliError(f"ERROR: GitLab API request failed ({status_code}): {detail or 'request failed'}")
        return body

    def _read_response_body(self, request: urllib.request.Request, *, method: str = "GET", allow_not_found: bool = False, allowed_status_codes: set[int] | None = None, accept: str, base_url: str, path: str, query: dict | None = None, form_data: dict | None = None) -> str | None:
        try:
            with OPENER.open(request) as response:
                return response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            if allow_not_found and exc.code == 404:
                return None
            if allowed_status_codes and exc.code in allowed_status_codes:
                _ = exc.read().decode("utf-8", errors="replace")
                return None
            detail = exc.read().decode("utf-8", errors="replace").strip()
            raise CliError(f"ERROR: GitLab API request failed ({exc.code}): {detail or exc.reason}") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, ssl.SSLCertVerificationError):
                if self.token:
                    raise CliError(
                        "ERROR: Authenticated GitLab API request failed TLS certificate verification. "
                        "The curl fallback is disabled for authenticated requests; repair the local "
                        "Python CA trust configuration and retry."
                    ) from exc
                return self._request_with_curl(
                    base_url,
                    path,
                    method=method,
                    accept=accept,
                    query=query,
                    form_data=form_data,
                    allow_not_found=allow_not_found,
                    allowed_status_codes=allowed_status_codes,
                )
            raise CliError(f"ERROR: GitLab API request failed: {exc.reason}") from exc

    def _request_json(self, base_url: str, path: str, *, method: str = "GET", query: dict | None = None, form_data: dict | None = None, allow_not_found: bool = False, allowed_status_codes: set[int] | None = None):
        base_url = self._validated_base_url(base_url)
        payload = _form_payload(form_data)
        request = urllib.request.Request(
            _api_url(base_url, path, query),
            data=urllib.parse.urlencode(payload).encode("utf-8") if payload else None,
            headers=self._request_headers(accept="application/json"),
            method=method,
        )
        self._add_authentication(request)
        if payload:
            request.add_header("Content-Type", "application/x-www-form-urlencoded")
        body = self._read_response_body(
            request,
            method=method,
            allow_not_found=allow_not_found,
            allowed_status_codes=allowed_status_codes,
            accept="application/json",
            base_url=base_url,
            path=path,
            query=query,
            form_data=payload,
        )
        if body is None or not body.strip():
            return None
        return json.loads(body)

    def _request_text(self, base_url: str, path: str, *, query: dict | None = None, allow_not_found: bool = False, allowed_status_codes: set[int] | None = None):
        base_url = self._validated_base_url(base_url)
        request = urllib.request.Request(
            _api_url(base_url, path, query),
            headers=self._request_headers(accept="text/plain"),
            method="GET",
        )
        self._add_authentication(request)
        return self._read_response_body(
            request,
            allow_not_found=allow_not_found,
            allowed_status_codes=allowed_status_codes,
            accept="text/plain",
            base_url=base_url,
            path=path,
            query=query,
        )

    def _discussion_path(self, context: ReviewContext, thread_id: str | int) -> str:
        project_ref = urllib.parse.quote(self._project_api_ref(context), safe="")
        return f"/projects/{project_ref}/merge_requests/{context.change_request.id}/discussions/{thread_id}"

    def _discussion_notes_path(self, context: ReviewContext, thread_id: str | int) -> str:
        return f"{self._discussion_path(context, thread_id)}/notes"

    def _discussion_note_path(self, context: ReviewContext, thread_id: str | int, comment_id: int) -> str:
        return f"{self._discussion_notes_path(context, thread_id)}/{comment_id}"

    def _fetch_discussion(self, context: ReviewContext, thread_id: str | int) -> dict:
        discussion = self._request_json(
            context.organization,
            self._discussion_path(context, thread_id),
            allow_not_found=True,
        )
        if discussion is None:
            raise CliError(
                f"ERROR: Discussion '{thread_id}' was not found on GitLab merge request {context.change_request.id}."
            )
        return discussion

    def _find_note(self, discussion: dict, comment_id: int) -> dict:
        for note in discussion.get("notes", []):
            if note.get("id") == comment_id:
                return note
        raise CliError(f"ERROR: Comment '{comment_id}' was not found in GitLab discussion '{discussion.get('id')}'.")

    def _project_api_ref(self, context: ReviewContext) -> str:
        return context.repository.id or context.project

    def _resolve_repository(self, repo_ref: str | None) -> dict:
        if not repo_ref:
            raise CliError(
                "ERROR: GitLab merge-request creation requires --repo with a GitLab project path or numeric project id."
            )

        project = self._request_json(
            GITLAB_BASE_URL,
            f"/projects/{urllib.parse.quote(repo_ref, safe='')}",
            allow_not_found=True,
        )
        if project is None:
            raise CliError(
                f"ERROR: GitLab project '{repo_ref}' was not found at {GITLAB_BASE_URL}."
            )
        return project

    def _fetch_source_ref_tip(self, repository: RepositoryRef, source_ref_name: str) -> str:
        project_ref = repository.id or repository.project
        if not project_ref:
            raise CliError("ERROR: GitLab project identity is required to resolve a source branch.")
        branch = self._request_json(
            GITLAB_BASE_URL,
            f"/projects/{urllib.parse.quote(project_ref, safe='')}/repository/branches/"
            f"{urllib.parse.quote(source_ref_name, safe='')}",
            allow_not_found=True,
        )
        commit_id = ((branch or {}).get("commit") or {}).get("id")
        if branch is None or (branch.get("name") and branch.get("name") != source_ref_name) or not commit_id:
            raise CliError(
                f"ERROR: GitLab source branch '{source_ref_name}' was not found in "
                f"project '{repository.project or repository.name}'."
            )
        return commit_id

    def _build_change_request(self, merge_request: dict, *, base_url: str, project_path: str) -> ChangeRequest:
        project_ref = urllib.parse.quote(project_path, safe="")
        author = (merge_request.get("author") or {}).get("name") or (merge_request.get("author") or {}).get("username") or "unknown"
        return ChangeRequest(
            id=merge_request.get("iid"),
            title=merge_request.get("title") or "",
            status=merge_request.get("state") or "unknown",
            source_branch=merge_request.get("source_branch") or "",
            target_branch=merge_request.get("target_branch") or "",
            author=author,
            repo_name=project_path.rsplit("/", 1)[-1],
            repo_id=str(merge_request.get("project_id")) if merge_request.get("project_id") is not None else None,
            api_url=_api_url(base_url, f"/projects/{project_ref}/merge_requests/{merge_request.get('iid') or 0}"),
            browser_url=merge_request.get("web_url") or "",
            provider="gitlab",
        )

    def _merge_request_details(self, context: ReviewContext) -> dict:
        return self._request_json(
            context.organization,
            f"/projects/{urllib.parse.quote(self._project_api_ref(context), safe='')}/merge_requests/{context.change_request.id}",
        )

    def _latest_merge_request_version(self, context: ReviewContext) -> dict:
        project_ref = urllib.parse.quote(self._project_api_ref(context), safe="")
        versions = self._request_json(
            context.organization,
            f"/projects/{project_ref}/merge_requests/{context.change_request.id}/versions",
            allowed_status_codes={401},
        ) or []
        if not versions:
            raise CliError(
                f"ERROR: Could not resolve diff refs for GitLab merge request {context.change_request.id}."
            )
        return versions[0]

    def _merge_request_diffs(self, context: ReviewContext) -> list[dict]:
        project_ref = urllib.parse.quote(self._project_api_ref(context), safe="")
        return self._request_json(
            context.organization,
            f"/projects/{project_ref}/merge_requests/{context.change_request.id}/diffs",
            query={"per_page": 100},
        ) or []

    def _find_diff_for_path(self, context: ReviewContext, file_path: str) -> dict:
        normalized_path = file_path.lstrip("/")
        for diff in self._merge_request_diffs(context):
            if normalized_path in {diff.get("new_path"), diff.get("old_path")}:
                return diff
        display_path = _display_path(normalized_path) or file_path
        raise CliError(f"ERROR: File '{display_path}' is not part of the MR diff.")

    def _resolve_diff_position(self, diff: dict, requested_line: int, *, side: str | None = None) -> tuple[dict[str, int | None], str]:
        positions = _parse_diff_lines(diff.get("diff") or "")
        if side == "new":
            for position in positions:
                if position.get("new_line") == requested_line:
                    return position, "new"
        elif side == "old":
            for position in positions:
                if position.get("old_line") == requested_line:
                    return position, "old"
        else:
            for position in positions:
                if position.get("new_line") == requested_line:
                    return position, "new"
            for position in positions:
                if position.get("old_line") == requested_line:
                    return position, "old"

        display_path = _display_path(diff.get("new_path") or diff.get("old_path")) or "/"
        raise CliError(f"ERROR: Line {requested_line} is not part of the MR diff for '{display_path}'.")

    def _build_inline_discussion_payload(
        self,
        context: ReviewContext,
        *,
        text: str,
        file_path: str,
        line: int,
        end_line: int | None,
    ) -> dict:
        diff = self._find_diff_for_path(context, file_path)
        version = self._latest_merge_request_version(context)
        start_position, side = self._resolve_diff_position(diff, line)

        payload: dict[str, object] = {
            "body": text,
            "position[position_type]": "text",
            "position[base_sha]": version.get("base_commit_sha"),
            "position[start_sha]": version.get("start_commit_sha"),
            "position[head_sha]": version.get("head_commit_sha"),
            "position[old_path]": diff.get("old_path") or diff.get("new_path"),
            "position[new_path]": diff.get("new_path") or diff.get("old_path"),
        }
        if start_position.get("old_line") is not None:
            payload["position[old_line]"] = start_position.get("old_line")
        if start_position.get("new_line") is not None:
            payload["position[new_line]"] = start_position.get("new_line")

        if end_line is not None and end_line != line:
            end_position, end_side = self._resolve_diff_position(diff, end_line, side=side)
            if end_side != side:
                raise CliError("ERROR: GitLab multiline inline comments must stay on one diff side.")
            start_entry = _position_payload_entry(str(payload["position[new_path]"] or payload["position[old_path]"]), start_position, side)
            end_entry = _position_payload_entry(str(payload["position[new_path]"] or payload["position[old_path]"]), end_position, side)
            for entry_name, entry in (("start", start_entry), ("end", end_entry)):
                for key, value in entry.items():
                    payload[f"position[line_range][{entry_name}][{key}]"] = value

        return payload

    def _discussion_status(self, discussion: dict) -> str:
        notes = [note for note in discussion.get("notes", []) if not note.get("system")]
        resolvable_notes = [note for note in notes if note.get("resolvable")]
        if resolvable_notes and all(note.get("resolved") for note in resolvable_notes):
            return "resolved"
        return "active"

    def _note_location(self, note: dict) -> tuple[str | None, int | None, str | None]:
        position = note.get("position") or {}
        file_path = _display_path(position.get("new_path") or position.get("old_path"))
        line = position.get("new_line") or position.get("old_line")
        if file_path and line is not None:
            return file_path, line, f"{file_path}:{line}"
        return file_path, line, file_path

    def _build_review_thread(self, discussion: dict) -> ReviewThread | None:
        comments: list[ReviewComment] = []
        file_path = None
        line = None
        location = None

        for note in discussion.get("notes", []):
            if note.get("system"):
                continue
            note_file_path, note_line, note_location = self._note_location(note)
            if note_file_path and file_path is None:
                file_path = note_file_path
                line = note_line
                location = note_location
            comments.append(
                ReviewComment(
                    comment_id=note.get("id"),
                    parent_comment_id=None,
                    author=(note.get("author") or {}).get("name") or (note.get("author") or {}).get("username") or "unknown",
                    content=note.get("body") or "",
                    published_date=note.get("created_at"),
                    last_updated_date=note.get("updated_at"),
                )
            )

        if not comments:
            return None

        return ReviewThread(
            thread_id=discussion.get("id"),
            status=self._discussion_status(discussion),
            is_deleted=False,
            file_path=file_path,
            line=line,
            location=location,
            comments=comments,
        )

    def list_repositories(self) -> list[RepositoryRef]:
        raise CliError("ERROR: GitLab preview currently requires merge-request URLs; repository listing is still Azure DevOps-only.")

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
        if not url:
            raise CliError("ERROR: GitLab preview currently requires --url with a GitLab merge request URL.")

        locator = parse_gitlab_merge_request_url(url)
        merge_request = self._request_json(
            locator["base_url"],
            f"/projects/{urllib.parse.quote(locator['project_path'], safe='')}/merge_requests/{locator['merge_request_iid']}",
        )
        project_path = str(locator["project_path"])
        repository = RepositoryRef(
            id=str(merge_request.get("project_id")) if merge_request.get("project_id") is not None else project_path,
            name=project_path.rsplit("/", 1)[-1],
            project=project_path,
        )
        return ReviewContext(
            organization=str(locator["base_url"]),
            project=project_path,
            repository=repository,
            change_request=self._build_change_request(
                merge_request,
                base_url=str(locator["base_url"]),
                project_path=project_path,
            ),
        )

    def analyze_change_request(self, context: ReviewContext) -> ReviewAnalysis:
        project_ref = urllib.parse.quote(self._project_api_ref(context), safe="")
        merge_request = self._merge_request_details(context)
        reviewers_payload = self._request_json(
            context.organization,
            f"/projects/{project_ref}/merge_requests/{context.change_request.id}/reviewers",
            allow_not_found=True,
        ) or []
        versions = self._request_json(
            context.organization,
            f"/projects/{project_ref}/merge_requests/{context.change_request.id}/versions",
            allow_not_found=True,
            allowed_status_codes={401},
        ) or []
        diffs = self._request_json(
            context.organization,
            f"/projects/{project_ref}/merge_requests/{context.change_request.id}/diffs",
            query={"per_page": 100},
        ) or []
        discussions = self._request_json(
            context.organization,
            f"/projects/{project_ref}/merge_requests/{context.change_request.id}/discussions",
            query={"per_page": 100},
            allowed_status_codes={401},
        ) or []

        reviewers = [
            ChangeRequestReviewer(
                name=(entry.get("user") or {}).get("name") or (entry.get("user") or {}).get("username") or "unknown",
                vote=None,
                vote_label=entry.get("state") or "reviewer",
                is_required=False,
            )
            for entry in reviewers_payload
        ]

        files = [
            ReviewFileChange(
                path=_display_path(diff.get("new_path") or diff.get("old_path")) or "/",
                change_type=_change_type(diff),
            )
            for diff in diffs
        ]

        by_type: dict[str, int] = {}
        for file_change in files:
            by_type[file_change.change_type] = by_type.get(file_change.change_type, 0) + 1

        existing_comments: list[ExistingReviewComment] = []
        for discussion in discussions:
            thread_status = self._discussion_status(discussion)
            for note in discussion.get("notes", []):
                if note.get("system"):
                    continue
                file_path, line, _location = self._note_location(note)
                existing_comments.append(
                    ExistingReviewComment(
                        thread_id=discussion.get("id"),
                        comment_id=note.get("id"),
                        thread_status=thread_status,
                        file_path=file_path,
                        line=line,
                        author=(note.get("author") or {}).get("name") or (note.get("author") or {}).get("username") or "unknown",
                        content=note.get("body") or "",
                    )
                )

        return ReviewAnalysis(
            context=ReviewContext(
                organization=context.organization,
                project=context.project,
                repository=context.repository,
                change_request=self._build_change_request(
                    merge_request,
                    base_url=context.organization,
                    project_path=context.project,
                ),
            ),
            reviewers=reviewers,
            change_summary=ReviewChangeSummary(
                iteration=len(versions) or None,
                count=len(files),
                by_type=by_type,
            ),
            files=files,
            existing_comments=existing_comments,
        )

    def list_review_threads(self, context: ReviewContext, *, unresolved_only: bool = False) -> list[ReviewThread]:
        project_ref = urllib.parse.quote(self._project_api_ref(context), safe="")
        discussions = self._request_json(
            context.organization,
            f"/projects/{project_ref}/merge_requests/{context.change_request.id}/discussions",
            query={"per_page": 100},
            allowed_status_codes={401} if not self.token else None,
        ) or []
        if not discussions and not self.token:
            raise CliError(
                "ERROR: GitLab review-thread listing for this merge request requires authenticated API access. "
                "Set GITLAB_TOKEN and rerun './sg pr-comments --url <gitlab-mr-url>'."
            )
        threads: list[ReviewThread] = []
        for discussion in discussions:
            thread = self._build_review_thread(discussion)
            if thread is None:
                continue
            if unresolved_only and thread.status == "resolved":
                continue
            threads.append(thread)
        return threads

    def list_statuses(self, context: ReviewContext) -> list[ChangeRequestStatus]:
        merge_request = self._merge_request_details(context)
        sha = merge_request.get("sha")
        if not sha:
            return []
        project_ref = urllib.parse.quote(self._project_api_ref(context), safe="")
        statuses = self._request_json(
            context.organization,
            f"/projects/{project_ref}/repository/commits/{urllib.parse.quote(sha, safe='')}/statuses",
            query={"per_page": 100},
            allow_not_found=True,
            allowed_status_codes={401} if not self.token else None,
        ) or []
        if not statuses and not self.token:
            raise CliError(
                "ERROR: GitLab commit-status listing for this merge request requires authenticated API access. "
                "Set GITLAB_TOKEN and rerun './sg pr-statuses --url <gitlab-mr-url>'."
            )
        return [
            ChangeRequestStatus(
                id=status.get("id"),
                state=status.get("status") or "unknown",
                description=status.get("description") or "",
                context_name=status.get("name") or status.get("context") or "(unnamed)",
                context_kind="gitlab-ci",
                target_url=status.get("target_url") or "",
                created_by=(status.get("author") or {}).get("name") or (status.get("author") or {}).get("username") or "",
                creation_date=status.get("created_at"),
                updated_date=status.get("finished_at") or status.get("started_at"),
            )
            for status in statuses
        ]

    def get_change_request_file_content(
        self,
        context: ReviewContext,
        *,
        file_path: str,
        version: str,
    ) -> str | None:
        ref = context.change_request.source_branch if version == "source" else context.change_request.target_branch
        normalized_path = file_path.lstrip("/")
        project_ref = urllib.parse.quote(self._project_api_ref(context), safe="")
        return self._request_text(
            context.organization,
            f"/projects/{project_ref}/repository/files/{urllib.parse.quote(normalized_path, safe='')}/raw",
            query={"ref": ref},
            allow_not_found=True,
        )

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
        project = self._resolve_repository(repo_ref)
        source = source_branch or current_git_branch() or ""
        if source.startswith("refs/heads/"):
            source = source[len("refs/heads/"):]
        if not source or source == "HEAD":
            raise CliError("ERROR: Could not resolve a source branch for GitLab merge-request creation.")

        target = target_branch or project.get("default_branch") or "main"
        resolved_title = title or (f"[{work_item_id}] {work_item_title}" if work_item_title else f"Issue #{work_item_id}")
        form_data = {
            "title": resolved_title,
            "description": description or f"Closes #{work_item_id}",
            "source_branch": source,
            "target_branch": target,
            "remove_source_branch": True,
        }
        project_path = project.get("path_with_namespace") or repo_ref or "?"
        repository = RepositoryRef(
            id=str(project.get("id")) if project.get("id") is not None else project_path,
            name=project.get("path") or project_path.rsplit("/", 1)[-1],
            project=project_path,
        )
        source_commit_id = self._fetch_source_ref_tip(repository, source)
        return repository, {
            "request": {
                "method": "POST",
                "formData": form_data,
            },
            "sourceRef": {
                "name": source,
                "commitId": source_commit_id,
            },
        }

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
        repository, payload = self.prepare_change_request(
            work_item_id=work_item_id,
            repo_ref=repo_ref,
            source_branch=source_branch,
            target_branch=target_branch,
            title=title,
            description=description,
            work_item_title=work_item_title,
        )
        return self.create_prepared_change_request(repository, payload)

    def create_prepared_change_request(
        self,
        repository: RepositoryRef,
        payload: dict,
    ) -> ChangeRequest:
        request = payload.get("request") or {}
        source_ref = payload.get("sourceRef") or {}
        form_data = request.get("formData")
        source_ref_name = source_ref.get("name")
        expected_commit_id = source_ref.get("commitId")
        if request.get("method") != "POST" or not isinstance(form_data, dict):
            raise CliError("ERROR: Prepared GitLab change request does not contain exact POST form data.")
        if (
            not isinstance(source_ref_name, str)
            or not isinstance(expected_commit_id, str)
            or form_data.get("source_branch") != source_ref_name
        ):
            raise CliError("ERROR: Prepared GitLab change request has an invalid source-ref precondition.")
        current_commit_id = self._fetch_source_ref_tip(repository, source_ref_name)
        if current_commit_id != expected_commit_id:
            raise CliError(
                f"ERROR: GitLab source branch '{source_ref_name}' moved after preview; "
                "generate and approve a fresh plan."
            )
        result = self._request_json(
            GITLAB_BASE_URL,
            f"/projects/{urllib.parse.quote(repository.id or repository.project or '', safe='')}/merge_requests",
            method="POST",
            form_data=form_data,
        )
        created_commit_id = result.get("sha") if isinstance(result, dict) else None
        if created_commit_id != expected_commit_id:
            merge_request_id = result.get("iid", "?") if isinstance(result, dict) else "?"
            observed = created_commit_id or "not returned by GitLab"
            raise CliError(
                f"ERROR: GitLab created merge request {merge_request_id}, but its source commit "
                f"is {observed} instead of approved commit {expected_commit_id}. Do not retry; "
                "inspect the created merge request and source branch first."
            )
        return self._build_change_request(
            result,
            base_url=GITLAB_BASE_URL,
            project_path=repository.project or repository.name,
        )

    def prepare_review_comment(self, context: ReviewContext, *, text: str) -> ReviewMutationPreview:
        return ReviewMutationPreview(payload={"body": text})

    def create_review_comment(self, context: ReviewContext, *, text: str) -> ReviewMutationResult:
        preview = self.prepare_review_comment(context, text=text)
        return self.create_prepared_review_comment(context, preview)

    def create_prepared_review_comment(
        self,
        context: ReviewContext,
        preview: ReviewMutationPreview,
    ) -> ReviewMutationResult:
        discussion = self._request_json(
            context.organization,
            f"/projects/{urllib.parse.quote(self._project_api_ref(context), safe='')}/merge_requests/{context.change_request.id}/discussions",
            method="POST",
            form_data=preview.payload,
        )
        first_note = (discussion.get("notes") or [{}])[0]
        return ReviewMutationResult(thread_id=discussion.get("id"), comment_id=first_note.get("id"))

    def prepare_inline_review_comment(self, context: ReviewContext, *, text: str, file_path: str, line: int, end_line: int | None, start_offset: int, end_offset: int | None) -> ReviewMutationPreview:
        return ReviewMutationPreview(
            payload=self._build_inline_discussion_payload(
                context,
                text=text,
                file_path=file_path,
                line=line,
                end_line=end_line,
            )
        )

    def create_inline_review_comment(self, context: ReviewContext, *, text: str, file_path: str, line: int, end_line: int | None, start_offset: int, end_offset: int | None) -> ReviewMutationResult:
        preview = self.prepare_inline_review_comment(
            context,
            text=text,
            file_path=file_path,
            line=line,
            end_line=end_line,
            start_offset=start_offset,
            end_offset=end_offset,
        )
        return self.create_prepared_inline_review_comment(context, preview)

    def create_prepared_inline_review_comment(
        self,
        context: ReviewContext,
        preview: ReviewMutationPreview,
    ) -> ReviewMutationResult:
        discussion = self._request_json(
            context.organization,
            f"/projects/{urllib.parse.quote(self._project_api_ref(context), safe='')}/merge_requests/{context.change_request.id}/discussions",
            method="POST",
            form_data=preview.payload,
        )
        first_note = (discussion.get("notes") or [{}])[0]
        return ReviewMutationResult(thread_id=discussion.get("id"), comment_id=first_note.get("id"))

    def prepare_review_reply(self, context: ReviewContext, *, thread_id: int | str, text: str, parent_comment_id: int | None) -> ReviewMutationPreview:
        return ReviewMutationPreview(payload={"body": text}, thread_id=thread_id, comment_id=parent_comment_id)

    def create_review_reply(self, context: ReviewContext, *, thread_id: int | str, text: str, parent_comment_id: int | None) -> ReviewMutationResult:
        preview = self.prepare_review_reply(
            context,
            thread_id=thread_id,
            text=text,
            parent_comment_id=parent_comment_id,
        )
        return self.create_prepared_review_reply(context, preview)

    def create_prepared_review_reply(
        self,
        context: ReviewContext,
        preview: ReviewMutationPreview,
    ) -> ReviewMutationResult:
        if preview.thread_id is None:
            raise CliError("ERROR: Prepared review reply is missing a thread id.")
        note = self._request_json(
            context.organization,
            self._discussion_notes_path(context, preview.thread_id),
            method="POST",
            form_data=preview.payload,
        )
        return ReviewMutationResult(thread_id=preview.thread_id, comment_id=note.get("id"))

    def prepare_review_comment_edit(self, context: ReviewContext, *, thread_id: int | str, comment_id: int, text: str) -> ReviewMutationPreview:
        discussion = self._fetch_discussion(context, thread_id)
        note = self._find_note(discussion, comment_id)
        return ReviewMutationPreview(
            payload={"body": text},
            thread_id=thread_id,
            comment_id=comment_id,
            current_content=note.get("body") or "",
        )

    def edit_review_comment(self, context: ReviewContext, *, thread_id: int | str, comment_id: int, text: str) -> ReviewMutationResult:
        self._request_json(
            context.organization,
            self._discussion_note_path(context, thread_id, comment_id),
            method="PUT",
            form_data={"body": text},
        )
        return ReviewMutationResult(thread_id=thread_id, comment_id=comment_id)

    def edit_prepared_review_comment(
        self,
        context: ReviewContext,
        preview: ReviewMutationPreview,
    ) -> ReviewMutationResult:
        if preview.thread_id is None or preview.comment_id is None:
            raise CliError("ERROR: Prepared comment edit is missing thread or comment identity.")
        self._request_json(
            context.organization,
            self._discussion_note_path(context, preview.thread_id, preview.comment_id),
            method="PUT",
            form_data=preview.payload,
        )
        return ReviewMutationResult(thread_id=preview.thread_id, comment_id=preview.comment_id)

    def prepare_review_thread_resolution(self, context: ReviewContext, *, thread_id: int | str, status: str) -> ReviewMutationPreview:
        discussion = self._fetch_discussion(context, thread_id)
        current_status = "resolved" if discussion.get("resolved") else "active"
        return ReviewMutationPreview(
            payload={"resolved": True},
            thread_id=thread_id,
            current_status=current_status,
        )

    def resolve_review_thread(self, context: ReviewContext, *, thread_id: int | str, status: str) -> ReviewMutationResult:
        discussion = self._request_json(
            context.organization,
            self._discussion_path(context, thread_id),
            method="PUT",
            form_data={"resolved": True},
        )
        return ReviewMutationResult(
            thread_id=thread_id,
            status="resolved" if discussion.get("resolved") else "active",
        )

    def resolve_prepared_review_thread(
        self,
        context: ReviewContext,
        preview: ReviewMutationPreview,
    ) -> ReviewMutationResult:
        if preview.thread_id is None:
            raise CliError("ERROR: Prepared thread resolution is missing a thread id.")
        discussion = self._request_json(
            context.organization,
            self._discussion_path(context, preview.thread_id),
            method="PUT",
            form_data=preview.payload,
        )
        return ReviewMutationResult(
            thread_id=preview.thread_id,
            status="resolved" if discussion.get("resolved") else "active",
        )
