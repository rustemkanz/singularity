import http.client
import ipaddress
import mimetypes
import os
import queue
import re
import socket
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from html import unescape
from html.parser import HTMLParser

from app_config import (
    API_VER,
    AZURE_DEVOPS_EXTERNAL_MEDIA_ORIGINS,
    BASE_URL,
    ORG,
    PROJECT,
    SSL_CTX,
    normalize_https_origin,
)
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
MAX_DOWNLOAD_REFERENCES = 50
MAX_TOTAL_DOWNLOAD_BYTES = 100 * 1024 * 1024
MAX_VALIDATED_ADDRESS_ATTEMPTS = 16
MAX_TOTAL_DOWNLOAD_SECONDS = 120
MAX_DOWNLOAD_REDIRECTS = 5
DOWNLOAD_READ_CHUNK_BYTES = 64 * 1024
DOWNLOAD_REDIRECT_STATUS_CODES = frozenset((301, 302, 303, 307, 308))
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


class NoDownloadRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Expose redirects to the caller so every destination can be validated."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


DOWNLOAD_OPENER = urllib.request.build_opener(
    urllib.request.HTTPSHandler(context=SSL_CTX),
    NoDownloadRedirectHandler(),
)
DEFAULT_DOWNLOAD_OPENER = DOWNLOAD_OPENER


class DownloadLimitError(ValueError):
    """Raised when a shared media-download safety budget is exhausted."""


class DownloadBudget:
    """Shared byte, connection-attempt, and wall-clock budget for one operation."""

    def __init__(
        self,
        *,
        max_total_bytes: int = MAX_TOTAL_DOWNLOAD_BYTES,
        max_address_attempts: int = MAX_VALIDATED_ADDRESS_ATTEMPTS,
        max_total_seconds: float = MAX_TOTAL_DOWNLOAD_SECONDS,
        clock=None,
    ):
        self.max_total_bytes = max_total_bytes
        self.max_address_attempts = max_address_attempts
        self.clock = time.monotonic if clock is None else clock
        self.deadline = self.clock() + max_total_seconds
        self.bytes_received = 0
        self.address_attempts = 0

    @property
    def remaining_bytes(self) -> int:
        return max(0, self.max_total_bytes - self.bytes_received)

    def remaining_timeout(self) -> float:
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise DownloadLimitError("Media download exceeded the total wall-clock time limit.")
        return min(float(DOWNLOAD_TIMEOUT_SECONDS), remaining)

    def record_bytes(self, size: int) -> None:
        if size < 0 or size > self.remaining_bytes:
            self.bytes_received = self.max_total_bytes
            raise DownloadLimitError(
                f"Media downloads exceed the {self.max_total_bytes}-byte aggregate size limit."
            )
        self.bytes_received += size

    def reserve_address_attempt(self) -> float:
        timeout = self.remaining_timeout()
        if self.address_attempts >= self.max_address_attempts:
            raise DownloadLimitError(
                f"Media download exceeded the {self.max_address_attempts}-address attempt limit."
            )
        self.address_attempts += 1
        return timeout


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    """TLS connection whose socket destination is a pre-validated IP address."""

    def __init__(self, host: str, port: int, *, pinned_address: str, timeout: float):
        self.pinned_address = pinned_address
        super().__init__(host, port=port, timeout=timeout, context=SSL_CTX)

    def connect(self) -> None:
        self.sock = socket.create_connection(
            (self.pinned_address, self.port),
            self.timeout,
            self.source_address,
        )
        if self._tunnel_host:
            self._tunnel()
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)


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
    def __init__(self, *, max_references: int | None = None):
        super().__init__()
        self.max_references = (
            MAX_DOWNLOAD_REFERENCES if max_references is None else max_references
        )
        self.reference_count = 0
        self.image_urls: list[str] = []
        self.link_urls: list[str] = []

    def append_reference(self, collection: list[str], url: str) -> None:
        if self.reference_count >= self.max_references:
            raise DownloadLimitError(
                f"Media reference collection exceeded the "
                f"{self.max_references}-reference limit."
            )
        self.reference_count += 1
        collection.append(unescape(url))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        attr_map = {
            key.lower(): value
            for key, value in attrs
            if key and value
        }
        tag_name = tag.lower()
        if tag_name == "img" and attr_map.get("src"):
            self.append_reference(self.image_urls, attr_map["src"])
        if tag_name == "a" and attr_map.get("href"):
            self.append_reference(self.link_urls, attr_map["href"])


def normalize_reference_url(url: str) -> str:
    return urllib.parse.urljoin(f"{BASE_URL}/", url)


def extract_html_references(
    raw_html: str,
    *,
    max_references: int | None = None,
) -> tuple[list[str], list[str]]:
    parser = HtmlReferenceParser(max_references=max_references)
    parser.feed(raw_html or "")
    parser.close()
    return parser.image_urls, parser.link_urls


def extract_plain_image_urls(
    text: str,
    *,
    max_references: int | None = None,
) -> list[str]:
    reference_limit = (
        MAX_DOWNLOAD_REFERENCES if max_references is None else max_references
    )
    urls: list[str] = []
    for match in IMAGE_URL_PATTERN.finditer(text or ""):
        if len(urls) >= reference_limit:
            raise DownloadLimitError(
                f"Media reference collection exceeded the "
                f"{reference_limit}-reference limit."
            )
        urls.append(match.group(0))
    return urls


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


def download_directory_anchor(absolute_dir: str) -> tuple[str, list[str], bool]:
    """Return the trusted anchor and relative components for an artifact directory."""
    current_dir = os.path.abspath(os.getcwd())
    try:
        inside_current_dir = os.path.commonpath((current_dir, absolute_dir)) == current_dir
    except ValueError:
        inside_current_dir = False

    if inside_current_dir:
        anchor_dir = current_dir
    else:
        anchor_dir = os.path.dirname(absolute_dir)
        while not os.path.exists(anchor_dir):
            parent_dir = os.path.dirname(anchor_dir)
            if parent_dir == anchor_dir:
                break
            anchor_dir = parent_dir
    relative_parts = [
        part
        for part in os.path.relpath(absolute_dir, anchor_dir).split(os.sep)
        if part not in ("", ".")
    ]
    return anchor_dir, relative_parts, inside_current_dir


def validate_existing_download_directory(directory: str) -> str:
    """Reject an existing directory that is itself a symlink or junction."""
    absolute_dir = os.path.abspath(directory)
    if not os.path.isdir(absolute_dir) or os.path.islink(absolute_dir):
        raise ValueError("Download directory must not contain symlinks or junctions.")
    return absolute_dir


def secure_directory_creation_supported() -> bool:
    """Return whether directory components can be created and opened by dir-fd."""
    return (
        os.open in os.supports_dir_fd
        and os.mkdir in os.supports_dir_fd
        and hasattr(os, "O_DIRECTORY")
        and hasattr(os, "O_NOFOLLOW")
    )


def prepare_download_directory(download_dir: str) -> str:
    """Create a download root and reject paths containing symlinks or junctions."""
    absolute_dir = os.path.abspath(download_dir)
    anchor_dir, relative_parts, _inside_current_dir = download_directory_anchor(absolute_dir)
    anchor_dir = validate_existing_download_directory(anchor_dir)

    if secure_directory_creation_supported():
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        try:
            directory_fd = os.open(anchor_dir, flags)
        except OSError as exc:
            raise ValueError(
                "Download directory must not contain symlinks or junctions."
            ) from exc
        try:
            for part in relative_parts:
                try:
                    os.mkdir(part, dir_fd=directory_fd)
                except FileExistsError:
                    pass
                try:
                    next_fd = os.open(part, flags, dir_fd=directory_fd)
                except OSError as exc:
                    raise ValueError(
                        "Download directory must not contain symlinks or junctions."
                    ) from exc
                os.close(directory_fd)
                directory_fd = next_fd
        finally:
            os.close(directory_fd)
        return absolute_dir

    candidate_dir = anchor_dir
    for part in relative_parts:
        if part in ("", "."):
            continue
        candidate_dir = os.path.join(candidate_dir, part)
        try:
            os.mkdir(candidate_dir)
        except FileExistsError:
            pass
        validate_existing_download_directory(candidate_dir)
    return absolute_dir


def open_download_directory(download_dir: str) -> int | None:
    """Open the artifact directory for race-resistant relative file creation."""
    if (
        os.open not in os.supports_dir_fd
        or not hasattr(os, "O_DIRECTORY")
        or not hasattr(os, "O_NOFOLLOW")
    ):
        return None
    anchor_dir, relative_parts, inside_current_dir = download_directory_anchor(download_dir)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory_fd = os.open("." if inside_current_dir else anchor_dir, flags)
    try:
        for part in relative_parts:
            next_fd = os.open(part, flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        return directory_fd
    except Exception:
        os.close(directory_fd)
        raise


def write_unique_download(
    download_dir: str,
    filename: str,
    payload: bytes,
    *,
    budget: DownloadBudget | None = None,
) -> str:
    """Create one contained artifact exclusively, without following a final symlink."""
    if budget is not None:
        budget.remaining_timeout()
    safe_dir = prepare_download_directory(download_dir)
    directory_fd = open_download_directory(safe_dir)
    if budget is not None:
        budget.remaining_timeout()
    base, ext = os.path.splitext(filename)
    counter = 1
    try:
        while True:
            candidate_name = filename if counter == 1 else f"{base}-{counter}{ext}"
            candidate_path = os.path.abspath(os.path.join(safe_dir, candidate_name))
            try:
                if os.path.commonpath((safe_dir, candidate_path)) != safe_dir:
                    raise ValueError("Download filename escapes the artifact directory.")
            except ValueError as exc:
                raise ValueError("Download filename escapes the artifact directory.") from exc

            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_BINARY"):
                flags |= os.O_BINARY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            try:
                if directory_fd is None:
                    if os.path.islink(safe_dir):
                        raise ValueError("Download directory changed before artifact creation.")
                    file_fd = os.open(candidate_path, flags, 0o600)
                else:
                    file_fd = os.open(candidate_name, flags, 0o600, dir_fd=directory_fd)
            except FileExistsError:
                counter += 1
                continue

            try:
                with os.fdopen(file_fd, "wb") as handle:
                    for offset in range(0, len(payload), DOWNLOAD_READ_CHUNK_BYTES):
                        if budget is not None:
                            budget.remaining_timeout()
                        handle.write(payload[offset:offset + DOWNLOAD_READ_CHUNK_BYTES])
                    if budget is not None:
                        budget.remaining_timeout()
            except Exception:
                try:
                    if directory_fd is not None and os.unlink in os.supports_dir_fd:
                        os.unlink(candidate_name, dir_fd=directory_fd)
                    else:
                        os.unlink(candidate_path)
                except OSError:
                    pass
                raise
            return candidate_path
    finally:
        if directory_fd is not None:
            os.close(directory_fd)


def fully_unquote_url_segment(segment: str) -> str:
    """Decode nested percent escapes so path traversal cannot hide behind re-encoding."""
    decoded = segment
    for _ in range(len(segment) + 1):
        next_value = urllib.parse.unquote(decoded)
        if next_value == decoded:
            return decoded
        decoded = next_value
    return decoded


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
        raw_segments = parsed.path.split("/")[1:]
        if not raw_segments:
            return False
        decoded_segments = [fully_unquote_url_segment(segment) for segment in raw_segments]
        if any(
            segment in (".", "..") or "/" in segment or "\\" in segment
            for segment in decoded_segments
        ):
            return False
        return decoded_segments[0].casefold() == org

    return hostname == f"{configured_org}.visualstudio.com".casefold()


def validate_public_hostname(
    hostname: str,
    port: int,
    *,
    resolver=None,
    budget: DownloadBudget | None = None,
) -> tuple[str, ...]:
    """Reject hostnames unless every resolved address is globally routable."""
    hostname_resolver = socket.getaddrinfo if resolver is None else resolver
    try:
        if budget is None:
            addresses = hostname_resolver(hostname, port, type=socket.SOCK_STREAM)
        else:
            resolver_result: queue.Queue = queue.Queue(maxsize=1)

            def resolve() -> None:
                try:
                    resolver_result.put((True, hostname_resolver(
                        hostname,
                        port,
                        type=socket.SOCK_STREAM,
                    )))
                except Exception as exc:
                    resolver_result.put((False, exc))

            threading.Thread(target=resolve, daemon=True).start()
            try:
                succeeded, result = resolver_result.get(timeout=budget.remaining_timeout())
            except queue.Empty as exc:
                raise DownloadLimitError(
                    "Media download exceeded the total wall-clock time limit during DNS resolution."
                ) from exc
            budget.remaining_timeout()
            if not succeeded:
                raise result
            addresses = result
    except OSError as exc:
        raise ValueError(f"Could not resolve media hostname {hostname!r}.") from exc
    if not addresses:
        raise ValueError(f"Media hostname {hostname!r} did not resolve to an address.")

    public_addresses: list[str] = []
    address_limit = (
        MAX_VALIDATED_ADDRESS_ATTEMPTS
        if budget is None
        else budget.max_address_attempts
    )
    for address_index, address_info in enumerate(addresses):
        if budget is not None and address_index % 16 == 0:
            budget.remaining_timeout()
        try:
            address_text = address_info[4][0].split("%", 1)[0]
            address = ipaddress.ip_address(address_text)
        except (IndexError, TypeError, ValueError) as exc:
            raise ValueError(f"Media hostname {hostname!r} resolved to an invalid address.") from exc
        if (
            not address.is_global
            or address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
        ):
            raise ValueError(
                f"Media hostname {hostname!r} resolved to non-public address {address_text!r}."
            )
        if address_text not in public_addresses and len(public_addresses) < address_limit:
            public_addresses.append(address_text)
    if budget is not None:
        budget.remaining_timeout()
    return tuple(public_addresses)


def validate_media_download_url(
    url: str,
    *,
    allowed_origins: tuple[str, ...] | None = None,
    org_name: str | None = None,
    resolver=None,
    budget: DownloadBudget | None = None,
) -> tuple[str, tuple[str, ...]]:
    """Validate one media URL hop and return its origin and pinned public addresses."""
    if not url or "\\" in url or any(ord(character) < 32 or ord(character) == 127 for character in url):
        raise ValueError(f"Invalid media download URL: {url!r}")
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"Invalid media download URL: {url!r}") from exc

    if parsed.scheme.casefold() != "https":
        raise ValueError(f"Media download URL must use HTTPS: {url}")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"Media download URL must not include userinfo: {url}")

    raw_origin = f"https://{parsed.netloc}"
    origin = normalize_https_origin(raw_origin)
    hostname = urllib.parse.urlsplit(origin).hostname or ""
    configured_origins = (
        AZURE_DEVOPS_EXTERNAL_MEDIA_ORIGINS if allowed_origins is None else allowed_origins
    )
    if not is_trusted_azure_devops_url(url, org_name) and origin not in configured_origins:
        raise ValueError(
            f"External media origin {origin!r} is not allowed. "
            "Configure AZURE_DEVOPS_EXTERNAL_MEDIA_ORIGINS to allow it explicitly."
        )

    public_addresses = validate_public_hostname(
        hostname,
        port or 443,
        resolver=resolver,
        budget=budget,
    )
    return origin, public_addresses


def response_status(response) -> int | None:
    status = getattr(response, "status", None)
    if status is not None:
        return status
    getcode = getattr(response, "getcode", None)
    return getcode() if getcode else None


def close_download_response(response) -> None:
    close = getattr(response, "close", None)
    if close:
        close()


def open_pinned_https_request(
    request,
    public_addresses: tuple[str, ...],
    *,
    budget: DownloadBudget | None = None,
):
    """Open an HTTPS request without performing a second, attacker-controlled DNS lookup."""
    download_budget = DownloadBudget() if budget is None else budget
    parsed = urllib.parse.urlsplit(request.full_url)
    hostname = parsed.hostname or ""
    port = parsed.port or 443
    request_path = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
    headers = dict(request.header_items())
    last_error: Exception | None = None
    for address in public_addresses:
        timeout = download_budget.reserve_address_attempt()
        connection = PinnedHTTPSConnection(
            hostname,
            port,
            pinned_address=address,
            timeout=timeout,
        )
        try:
            connection.request(request.get_method(), request_path, headers=headers)
            response = connection.getresponse()
            try:
                download_budget.remaining_timeout()
            except DownloadLimitError:
                response.close()
                connection.close()
                raise
            return response
        except (OSError, http.client.HTTPException) as exc:
            last_error = exc
            connection.close()
    if last_error is not None:
        raise last_error
    raise ValueError(f"Media hostname {hostname!r} had no validated public addresses.")


def open_validated_download(
    token: str,
    url: str,
    *,
    opener=None,
    allowed_origins: tuple[str, ...] | None = None,
    org_name: str | None = None,
    resolver=None,
    max_redirects: int = MAX_DOWNLOAD_REDIRECTS,
    budget: DownloadBudget | None = None,
):
    """Open a media URL after validating the initial request and each redirect."""
    download_budget = DownloadBudget() if budget is None else budget
    download_opener = DOWNLOAD_OPENER if opener is None else opener
    current_url = urllib.parse.urldefrag(url)[0]
    visited_urls: set[str] = set()

    for redirect_count in range(max_redirects + 1):
        download_budget.remaining_timeout()
        if current_url in visited_urls:
            raise ValueError(f"Media download redirect loop detected at {current_url}")
        visited_urls.add(current_url)
        _origin, public_addresses = validate_media_download_url(
            current_url,
            allowed_origins=allowed_origins,
            org_name=org_name,
            resolver=resolver,
            budget=download_budget,
        )
        download_budget.remaining_timeout()

        request = urllib.request.Request(current_url, headers={"Accept": "*/*"})
        if is_trusted_azure_devops_url(current_url, org_name):
            request.add_unredirected_header("Authorization", f"Bearer {token}")

        if opener is None and download_opener is DEFAULT_DOWNLOAD_OPENER:
            response = open_pinned_https_request(
                request,
                public_addresses,
                budget=download_budget,
            )
        else:
            try:
                response = download_opener.open(
                    request,
                    timeout=download_budget.remaining_timeout(),
                )
            except urllib.error.HTTPError as exc:
                if exc.code not in DOWNLOAD_REDIRECT_STATUS_CODES:
                    raise
                response = exc

        try:
            download_budget.remaining_timeout()
        except DownloadLimitError:
            close_download_response(response)
            raise
        status = response_status(response)
        if status is not None and status >= 400:
            reason = getattr(response, "reason", "HTTP error")
            headers = response.headers
            close_download_response(response)
            raise urllib.error.HTTPError(
                current_url,
                status,
                reason,
                headers,
                None,
            )
        if status not in DOWNLOAD_REDIRECT_STATUS_CODES:
            return response, current_url

        location = response.headers.get("Location", "")
        close_download_response(response)
        if not location:
            raise ValueError(f"Media download redirect from {current_url} has no Location header.")
        if redirect_count >= max_redirects:
            raise ValueError(f"Media download exceeded the {max_redirects}-redirect limit.")
        current_url = urllib.parse.urldefrag(urllib.parse.urljoin(current_url, location))[0]

    raise ValueError(f"Media download exceeded the {max_redirects}-redirect limit.")


def set_download_response_timeout(response, timeout: float) -> None:
    """Tighten the active response socket timeout to the shared deadline."""
    fp = getattr(response, "fp", None)
    raw = getattr(fp, "raw", None)
    response_socket = getattr(raw, "_sock", None)
    if response_socket is not None:
        response_socket.settimeout(timeout)


def read_bounded_download(
    response,
    *,
    max_bytes: int = MAX_DOWNLOAD_BYTES,
    budget: DownloadBudget | None = None,
) -> bytes:
    download_budget = DownloadBudget() if budget is None else budget
    download_budget.remaining_timeout()
    content_length = response.headers.get("Content-Length")
    if content_length:
        try:
            declared_size = int(content_length)
        except (TypeError, ValueError):
            declared_size = None
        if declared_size is not None and declared_size > max_bytes:
            raise ValueError(f"Download exceeds the {max_bytes}-byte size limit.")
        if declared_size is not None and declared_size > download_budget.remaining_bytes:
            raise DownloadLimitError(
                f"Media downloads exceed the {download_budget.max_total_bytes}-byte aggregate size limit."
            )

    chunks: list[bytes] = []
    file_bytes = 0
    read_func = getattr(response, "read1", None) or response.read
    while True:
        timeout = download_budget.remaining_timeout()
        set_download_response_timeout(response, timeout)
        remaining_file = max(0, max_bytes - file_bytes)
        remaining_aggregate = download_budget.remaining_bytes
        read_size = min(
            DOWNLOAD_READ_CHUNK_BYTES,
            remaining_file + 1,
            remaining_aggregate + 1,
        )
        chunk = read_func(read_size)
        download_budget.remaining_timeout()
        if not chunk:
            break
        download_budget.record_bytes(len(chunk))
        file_bytes += len(chunk)
        if file_bytes > max_bytes:
            raise ValueError(f"Download exceeds the {max_bytes}-byte size limit.")
        chunks.append(chunk)
    return b"".join(chunks)


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


def collect_attachment_references(
    item: dict,
    comments: list[dict],
    *,
    max_references: int | None = None,
) -> list[dict]:
    reference_limit = (
        MAX_DOWNLOAD_REFERENCES if max_references is None else max_references
    )
    references: list[dict] = []
    seen: set[tuple[str, str, str, str, bool]] = set()

    def add_reference(reference: dict) -> None:
        key = (
            reference["source"],
            reference["label"],
            reference["url"],
            reference.get("name", ""),
            reference["is_image"],
        )
        if key in seen:
            return
        if len(references) >= reference_limit:
            raise DownloadLimitError(
                f"Media reference collection exceeded the "
                f"{reference_limit}-reference limit."
            )
        seen.add(key)
        references.append(reference)

    for relation in item.get("relations", []):
        rel_type = relation.get("rel", "")
        url = relation.get("url", "")
        if not url:
            continue
        name = relation.get("attributes", {}).get("name", "")
        image_relation = is_image_name(name) or is_image_url(url)
        if rel_type == "AttachedFile" or image_relation:
            add_reference(
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
        image_urls, link_urls = extract_html_references(
            raw_html,
            max_references=reference_limit,
        )
        for url in image_urls:
            add_reference(build_reference("field", label, url, name=filename_from_url(url), is_image=True))
        for url in link_urls:
            if is_image_url(url):
                add_reference(build_reference("field", label, url, name=filename_from_url(url), is_image=True))
        for url in extract_plain_image_urls(raw_html, max_references=reference_limit):
            add_reference(build_reference("field", label, url, name=filename_from_url(url), is_image=True))

    for comment in comments:
        label = f"Comment {comment.get('id', '?')}"
        raw_html = comment.get("text", "") or ""
        image_urls, link_urls = extract_html_references(
            raw_html,
            max_references=reference_limit,
        )
        for url in image_urls:
            add_reference(build_reference("comment", label, url, name=filename_from_url(url), is_image=True))
        for url in link_urls:
            if is_image_url(url):
                add_reference(build_reference("comment", label, url, name=filename_from_url(url), is_image=True))
        for url in extract_plain_image_urls(raw_html, max_references=reference_limit):
            add_reference(build_reference("comment", label, url, name=filename_from_url(url), is_image=True))

    return references


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
        assignee=assigned_display_name(fields.get("System.AssignedTo")) if fields.get("System.AssignedTo") else "",
        tags=tuple(split_tags(fields.get("System.Tags", ""))),
        iteration=fields.get("System.IterationPath", ""),
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
            [
                "System.Id",
                "System.Title",
                "System.WorkItemType",
                "System.State",
                "System.AssignedTo",
                "System.Tags",
                "System.IterationPath",
            ],
        )
    }
    return {
        group: [fields_by_id[item_id].to_legacy_dict() for item_id in ids if item_id in fields_by_id]
        for group, ids in grouped_ids.items()
    }


_TREE_HIERARCHY_FORWARD = "System.LinkTypes.Hierarchy-Forward"
_TREE_HIERARCHY_REVERSE = "System.LinkTypes.Hierarchy-Reverse"
_TREE_NODE_BUDGET = 200
_TREE_ANCESTOR_LIMIT = 10


def _hierarchy_related_ids(item: dict, rel: str) -> list[int]:
    ids: list[int] = []
    for relation in item.get("relations", []):
        if relation.get("rel") == rel:
            related_id = extract_work_item_id_from_relation_url(relation.get("url", ""))
            if related_id is not None:
                ids.append(related_id)
    return ids


def _tree_node_dict(item: dict, children: list[dict]) -> dict:
    node = serialize_related_item(item.get("fields") or {}).to_legacy_dict()
    node["children"] = children
    return node


def build_work_item_tree(token: str, item_id: int, *, depth: int = 1) -> dict:
    """Return the item's parent chain plus its descendants to ``depth`` levels."""
    budget = {"remaining_nodes": _TREE_NODE_BUDGET}
    root_item = fetch_work_item(token, item_id, expand="relations")
    if not root_item:
        raise CliError(f"Work item {item_id} not found.")

    def build_subtree(item: dict, levels_left: int) -> dict:
        budget["remaining_nodes"] -= 1
        children: list[dict] = []
        if levels_left > 0:
            for child_id in _hierarchy_related_ids(item, _TREE_HIERARCHY_FORWARD):
                if budget["remaining_nodes"] <= 0:
                    break
                child_item = fetch_work_item(token, child_id, expand="relations")
                if child_item:
                    children.append(build_subtree(child_item, levels_left - 1))
        return _tree_node_dict(item, children)

    root = build_subtree(root_item, max(0, depth))

    ancestors: list[dict] = []
    current = root_item
    for _ in range(_TREE_ANCESTOR_LIMIT):
        parent_ids = _hierarchy_related_ids(current, _TREE_HIERARCHY_REVERSE)
        if not parent_ids:
            break
        parent_item = fetch_work_item(token, parent_ids[0], expand="relations")
        if not parent_item:
            break
        ancestors.append(_tree_node_dict(parent_item, []))
        current = parent_item
    ancestors.reverse()

    return {"root": root, "ancestors": ancestors, "depth": max(0, depth)}


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
    *,
    budget: DownloadBudget | None = None,
) -> tuple[str, bool]:
    url = reference["url"]
    if url in downloaded_urls:
        return downloaded_urls[url], True

    download_budget = DownloadBudget() if budget is None else budget
    download_budget.remaining_timeout()
    response, final_url = open_validated_download(
        token,
        url,
        budget=download_budget,
    )
    with response as resp:
        payload = read_bounded_download(resp, budget=download_budget)
        headers = resp.headers

    filename_candidates = [
        reference.get("name", ""),
        filename_from_headers(headers),
        filename_from_url(final_url),
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

    download_budget.remaining_timeout()
    target_path = write_unique_download(
        download_dir,
        filename,
        payload,
        budget=download_budget,
    )

    downloaded_urls[url] = target_path
    return target_path, False
