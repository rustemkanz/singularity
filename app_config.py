import importlib
import ipaddress
import os
import re
import ssl
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from profiles import profile_env_file, resolve_active_profile


REPO_ROOT = Path(__file__).resolve().parent
LOCAL_ENV_FILES = (".env.local", ".env")
REQUIRED_ENV_VARS = (
    "AZURE_DEVOPS_ORG",
    "AZURE_DEVOPS_PROJECT",
    "AZURE_DEVOPS_TEAM_ID",
    "AZURE_DEVOPS_USER",
    "AZURE_DEVOPS_QA_USER",
)
OPTIONAL_ENV_DEFAULTS = {
    "AZURE_DEVOPS_RESOURCE": "499b84ac-1321-427f-aa17-267ca6975798",
    "AZURE_DEVOPS_API_VERSION": "7.1",
    "AZURE_DEVOPS_EXTERNAL_MEDIA_ORIGINS": "",
}

HOST_LABEL_PATTERN = re.compile(
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z",
    re.IGNORECASE,
)


def parse_env_assignment(raw_line: str) -> tuple[str, str] | None:
    line = raw_line.strip()
    if not line or line.startswith("#"):
        return None
    if line.startswith("export "):
        line = line[7:].strip()
    if "=" not in line:
        return None

    name, value = line.split("=", 1)
    name = name.strip()
    value = value.strip()
    if not name:
        return None
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return name, value


def load_local_env_defaults(
    base_dir: Path | None = None,
    file_names: tuple[str, ...] = LOCAL_ENV_FILES,
) -> list[str]:
    loaded_files = []
    root_dir = base_dir or REPO_ROOT
    for file_name in file_names:
        env_path = root_dir / file_name
        if not env_path.is_file():
            continue
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            parsed = parse_env_assignment(raw_line)
            if not parsed:
                continue
            name, value = parsed
            if name not in os.environ:
                os.environ[name] = value
        loaded_files.append(env_path.name)
    return loaded_files


def apply_active_profile_env() -> tuple[str | None, str | None]:
    """Layer the active profile's ``<name>.env`` under real environment variables."""
    name, source = resolve_active_profile()
    if not name:
        return None, None
    env_file = profile_env_file(name)
    if not env_file.is_file():
        print(
            f"WARNING: Active Singularity profile '{name}' has no env file at {env_file}.",
            file=sys.stderr,
        )
        return name, source
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        parsed = parse_env_assignment(raw_line)
        if not parsed:
            continue
        key, value = parsed
        if key not in os.environ:
            os.environ[key] = value
    return name, source


ACTIVE_PROFILE, ACTIVE_PROFILE_SOURCE = apply_active_profile_env()
load_local_env_defaults()


def configured_value(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name, "").strip()
    return value or default


def normalize_https_origin(raw_origin: str) -> str:
    """Validate and canonicalize a configured public HTTPS origin."""
    origin = raw_origin.strip()
    try:
        parsed = urllib.parse.urlsplit(origin)
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"Invalid HTTPS origin: {raw_origin!r}") from exc

    if parsed.scheme.casefold() != "https":
        raise ValueError(f"External media origin must use HTTPS: {raw_origin!r}")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"External media origin must not include userinfo: {raw_origin!r}")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError(f"External media origin must not include a path, query, or fragment: {raw_origin!r}")
    if parsed.netloc.endswith(":"):
        raise ValueError(f"External media origin has an invalid port: {raw_origin!r}")
    if port == 0:
        raise ValueError(f"External media origin has an invalid port: {raw_origin!r}")

    hostname = parsed.hostname or ""
    if not hostname or hostname.endswith("."):
        raise ValueError(f"External media origin must contain an exact hostname: {raw_origin!r}")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise ValueError(f"External media origin must not use an IP literal: {raw_origin!r}")

    try:
        ascii_hostname = hostname.encode("idna").decode("ascii").casefold()
    except UnicodeError as exc:
        raise ValueError(f"External media origin has an invalid hostname: {raw_origin!r}") from exc
    labels = ascii_hostname.split(".")
    if len(ascii_hostname) > 253 or len(labels) < 2 or any(
        not HOST_LABEL_PATTERN.fullmatch(label) for label in labels
    ):
        raise ValueError(f"External media origin has an invalid hostname: {raw_origin!r}")

    port_suffix = "" if port in (None, 443) else f":{port}"
    return f"https://{ascii_hostname}{port_suffix}"


def parse_https_origins(raw_value: str | None) -> tuple[str, ...]:
    """Parse a comma-separated allowlist of exact public HTTPS origins."""
    normalized: list[str] = []
    for raw_origin in (raw_value or "").split(","):
        if not raw_origin.strip():
            continue
        origin = normalize_https_origin(raw_origin)
        if origin not in normalized:
            normalized.append(origin)
    return tuple(normalized)


def missing_required_config(required_names: tuple[str, ...] = REQUIRED_ENV_VARS) -> list[str]:
    return [name for name in required_names if not configured_value(name)]


def configuration_warnings(required_names: tuple[str, ...] = REQUIRED_ENV_VARS) -> list[str]:
    return [f"{name} is not set." for name in missing_required_config(required_names)]


def placeholder_configuration_warnings() -> list[str]:
    return configuration_warnings()


def build_ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    try:
        certifi = importlib.import_module("certifi")
    except ImportError:
        return context

    cafile = certifi.where()
    if cafile:
        context.load_verify_locations(cafile=cafile)
    return context


SSL_CTX = build_ssl_context()
HTTPS_HANDLER = urllib.request.HTTPSHandler(context=SSL_CTX)
OPENER = urllib.request.build_opener(HTTPS_HANDLER)

ORG = configured_value("AZURE_DEVOPS_ORG")
PROJECT = configured_value("AZURE_DEVOPS_PROJECT")
PROJECT_ENC = urllib.parse.quote(PROJECT or "")
TEAM_ID = configured_value("AZURE_DEVOPS_TEAM_ID")
ME = configured_value("AZURE_DEVOPS_USER")
QA_EMAIL = configured_value("AZURE_DEVOPS_QA_USER")
DEFAULT_REPO = configured_value("AZURE_DEVOPS_DEFAULT_REPO")
GITLAB_TOKEN = configured_value("GITLAB_TOKEN")
GITLAB_BASE_URL = configured_value("GITLAB_BASE_URL", "https://gitlab.com")
AZURE_DEVOPS_RESOURCE = configured_value("AZURE_DEVOPS_RESOURCE", OPTIONAL_ENV_DEFAULTS["AZURE_DEVOPS_RESOURCE"])
API_VER = configured_value("AZURE_DEVOPS_API_VERSION", OPTIONAL_ENV_DEFAULTS["AZURE_DEVOPS_API_VERSION"])
AZURE_DEVOPS_EXTERNAL_MEDIA_ORIGINS = parse_https_origins(
    configured_value(
        "AZURE_DEVOPS_EXTERNAL_MEDIA_ORIGINS",
        OPTIONAL_ENV_DEFAULTS["AZURE_DEVOPS_EXTERNAL_MEDIA_ORIGINS"],
    )
)
BASE_URL = f"https://dev.azure.com/{ORG}/{PROJECT_ENC}" if ORG and PROJECT else ""
