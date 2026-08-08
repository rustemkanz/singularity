import json
import urllib.error
import urllib.request

from app_config import OPENER, ORG, PROJECT
from errors import CliError


def _raise_http_error(error: urllib.error.HTTPError) -> None:
    body = error.read().decode()[:500]
    message_lines = [f"HTTP {error.code} — {body}"]
    if error.code in (401, 403):
        message_lines.append("Hint: Refresh Azure authentication with 'az login' and verify the active account.")
        message_lines.append(f"Hint: Confirm AZURE_DEVOPS_ORG='{ORG}' and AZURE_DEVOPS_PROJECT='{PROJECT}' match the target resource.")
    elif error.code == 404:
        message_lines.append("Hint: Check the selected project, repository, branch, or work item id.")
    raise CliError("\n".join(message_lines))


def api_with_headers(
    token: str,
    method: str,
    url: str,
    body=None,
    *,
    content_type: str | None = None,
    allowed_status_codes: set[int] | None = None,
) -> tuple[dict | None, dict[str, str]]:
    data = json.dumps(body).encode() if body is not None else None
    if content_type is None:
        content_type = (
            "application/json-patch+json" if method == "PATCH" else "application/json"
        )
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": content_type,
            "Accept": "application/json",
        },
    )
    try:
        with OPENER.open(req) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw.strip() else {}, dict(resp.headers.items())
    except urllib.error.HTTPError as e:
        if allowed_status_codes and e.code in allowed_status_codes:
            _ = e.read().decode()
            return None, dict(e.headers.items())
        _raise_http_error(e)
    except urllib.error.URLError as e:
        raise CliError(f"ERROR: Azure DevOps API request failed: {e.reason}") from e


def api_text_with_headers(
    token: str,
    method: str,
    url: str,
    body=None,
    *,
    content_type: str | None = None,
    allowed_status_codes: set[int] | None = None,
    accept: str = "text/plain",
) -> tuple[str | None, dict[str, str]]:
    data = json.dumps(body).encode() if body is not None else None
    if content_type is None:
        content_type = (
            "application/json-patch+json" if method == "PATCH" else "application/json"
        )
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": content_type,
            "Accept": accept,
        },
    )
    try:
        with OPENER.open(req) as resp:
            return resp.read().decode(), dict(resp.headers.items())
    except urllib.error.HTTPError as e:
        if allowed_status_codes and e.code in allowed_status_codes:
            _ = e.read().decode()
            return None, dict(e.headers.items())
        _raise_http_error(e)
    except urllib.error.URLError as e:
        raise CliError(f"ERROR: Azure DevOps API request failed: {e.reason}") from e


def api(token: str, method: str, url: str, body=None, *, content_type: str | None = None) -> dict:
    return api_with_headers(token, method, url, body, content_type=content_type)[0]


def api_text(
    token: str,
    method: str,
    url: str,
    body=None,
    *,
    content_type: str | None = None,
    accept: str = "text/plain",
) -> str:
    return api_text_with_headers(
        token,
        method,
        url,
        body,
        content_type=content_type,
        accept=accept,
    )[0]
