import os
from pathlib import Path
import ssl
import importlib
import urllib.parse
import urllib.request


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
}


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


load_local_env_defaults()


def configured_value(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name, "").strip()
    return value or default


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
BASE_URL = f"https://dev.azure.com/{ORG}/{PROJECT_ENC}" if ORG and PROJECT else ""
