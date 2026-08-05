import shutil
import subprocess

from app_config import AZURE_DEVOPS_RESOURCE, ORG, PROJECT
from errors import CliError


def get_token() -> str:
    probe = probe_azure_token()
    if probe["ok"]:
        return probe["token"]
    message_lines = [f"ERROR: {probe['error']}"]
    detail = probe.get("detail")
    if detail:
        message_lines.append(detail)
    for hint in probe.get("hints", []):
        message_lines.append(f"Hint: {hint}")
    raise CliError("\n".join(message_lines))


def probe_azure_token() -> dict:
    az_path = shutil.which("az")
    if not az_path:
        return {
            "ok": False,
            "error": "Azure CLI ('az') is not installed or not on PATH.",
            "hints": [
                "Install Azure CLI and run 'az login'.",
                f"This helper expects access to org '{ORG}' and project '{PROJECT}'.",
            ],
        }
    result = subprocess.run(
        [
            "az",
            "account",
            "get-access-token",
            "--resource",
            AZURE_DEVOPS_RESOURCE,
            "--query",
            "accessToken",
            "-o",
            "tsv",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        return {
            "ok": False,
            "error": "Could not get Azure DevOps access token from Azure CLI.",
            "detail": detail,
            "hints": [
                "Run 'az login' and confirm the session is still valid.",
                "Run 'az account show --output table' to confirm the active Azure account/subscription.",
                f"Verify AZURE_DEVOPS_ORG='{ORG}' and AZURE_DEVOPS_PROJECT='{PROJECT}' if you overrode defaults.",
            ],
            "azPath": az_path,
        }
    return {"ok": True, "token": result.stdout.strip(), "azPath": az_path}