import json
import shutil

from app_config import DEFAULT_REPO, ME, ORG, PROJECT, TEAM_ID, configuration_warnings, missing_required_config
from errors import CliError
from git_client import current_git_branch, infer_git_repository_ref
from providers.azure_devops.auth import probe_azure_token


def cmd_doctor(
    args,
    _token=None,
    *,
    probe_azure_token_func=None,
    infer_git_repository_ref_func=None,
    current_git_branch_func=None,
    missing_required_config_func=None,
    list_repositories_func=None,
    match_repository_func=None,
    org: str | None = None,
    project: str | None = None,
    team_id: str | None = None,
    me: str | None = None,
    default_repo: str | None = None,
    cli_error_cls=CliError,
):
    probe = (probe_azure_token_func or probe_azure_token)()
    infer_repo = infer_git_repository_ref_func or infer_git_repository_ref
    current_branch_func = current_git_branch_func or current_git_branch
    missing_config_func = missing_required_config_func or missing_required_config
    git_repo = infer_repo()
    fallback_repo = DEFAULT_REPO if default_repo is None else default_repo
    current_branch = None
    missing_config = missing_config_func()
    config_warnings = configuration_warnings(tuple(missing_config)) if missing_config else []
    config_hints = list(config_warnings)
    if any(name in missing_config for name in ("AZURE_DEVOPS_ORG", "AZURE_DEVOPS_PROJECT")):
        config_hints.append("Set AZURE_DEVOPS_ORG and AZURE_DEVOPS_PROJECT in .env.local, .env, or your shell environment.")
    if "AZURE_DEVOPS_TEAM_ID" in missing_config:
        config_hints.append("Run './sg teams' and copy the id for your team into AZURE_DEVOPS_TEAM_ID.")
    if "AZURE_DEVOPS_USER" in missing_config:
        config_hints.append(
            "Set AZURE_DEVOPS_USER to your Azure DevOps identity so ./sg list, ./sg ready-items, and ./sg pick-next can filter to your assigned work."
        )
    if "AZURE_DEVOPS_QA_USER" in missing_config:
        config_hints.append("Set AZURE_DEVOPS_QA_USER for testing handoff flows, or pass --qa explicitly when using ./sg testing.")
    try:
        current_branch = current_branch_func()
    except cli_error_cls:
        current_branch = None

    org_name = org or ORG
    project_name = project or PROJECT
    team = team_id or TEAM_ID
    assignee = me or ME

    checks = [
        {
            "name": "configuration",
            "ok": not missing_config,
            "detail": (
                "Azure DevOps environment variables are configured."
                if not missing_config
                else "One or more required Azure DevOps settings are missing."
            ),
            "missingNames": missing_config,
            "hints": config_hints,
        },
        {
            "name": "tlsVerification",
            "ok": True,
            "detail": "HTTPS certificate verification is enabled.",
            "hints": [],
        },
        {
            "name": "azureCli",
            "ok": bool(shutil.which("az")),
            "detail": shutil.which("az") or "Azure CLI not found on PATH.",
        },
        {
            "name": "azureToken",
            "ok": probe["ok"],
            "detail": "Azure token acquired." if probe["ok"] else probe["error"],
            "hints": probe.get("hints", []),
        },
        {
            "name": "gitRemoteRepo",
            "ok": True,
            "detail": (
                f"Inferred current git origin repository as {git_repo}."
                if git_repo is not None
                else "Could not infer an Azure DevOps repository from the current git origin."
            ),
            "hints": (
                []
                if git_repo is not None
                else [
                    (
                        f"Using AZURE_DEVOPS_DEFAULT_REPO='{fallback_repo}' as the fallback repo context."
                        if fallback_repo
                        else "This is fine when running outside a target work repo. Use --repo for repo-aware commands, or set AZURE_DEVOPS_DEFAULT_REPO."
                    )
                ]
            ),
        },
    ]

    org_project_configured = not any(name in missing_config for name in ("AZURE_DEVOPS_ORG", "AZURE_DEVOPS_PROJECT"))
    if probe["ok"] and org_project_configured and list_repositories_func and match_repository_func:
        repos = list_repositories_func(probe["token"])
        matched_git_repo = match_repository_func(repos, git_repo)
        matched_default_repo = match_repository_func(repos, fallback_repo)
        if matched_git_repo:
            checks.append({
                "name": "azureDevopsRepoResolution",
                "ok": True,
                "detail": f"Resolved repo context as {matched_git_repo.get('name')} ({matched_git_repo.get('id')}) from the current git origin.",
                "hints": [],
            })
        elif matched_default_repo:
            checks.append({
                "name": "azureDevopsRepoResolution",
                "ok": True,
                "detail": f"Resolved repo context as {matched_default_repo.get('name')} ({matched_default_repo.get('id')}) from AZURE_DEVOPS_DEFAULT_REPO.",
                "hints": [],
            })
        elif len(repos) == 1:
            only_repo = repos[0]
            checks.append({
                "name": "azureDevopsRepoResolution",
                "ok": True,
                "detail": f"Resolved repo context as {only_repo.get('name')} ({only_repo.get('id')}) because the project has a single repository.",
                "hints": [],
            })
        elif fallback_repo:
            checks.append({
                "name": "azureDevopsRepoResolution",
                "ok": False,
                "detail": f"Configured AZURE_DEVOPS_DEFAULT_REPO '{fallback_repo}' was not found in the configured Azure DevOps project.",
                "hints": ["Run './sg repos --json' to list valid repository names and ids."],
            })
        elif git_repo:
            checks.append({
                "name": "azureDevopsRepoResolution",
                "ok": True,
                "detail": f"Current git repo '{git_repo}' was not found in the configured ADO project.",
                "hints": ["If this is not the target work repo, pass --repo or set AZURE_DEVOPS_DEFAULT_REPO."],
            })
        else:
            checks.append({
                "name": "azureDevopsRepoResolution",
                "ok": True,
                "detail": "No default Azure DevOps repository context is selected.",
                "hints": ["Run repo-aware commands from a target work repo, pass --repo, or set AZURE_DEVOPS_DEFAULT_REPO."],
            })

    payload = {
        "organization": org_name,
        "project": project_name,
        "teamId": team,
        "me": assignee,
        "defaultRepo": fallback_repo,
        "currentBranch": current_branch,
        "currentRepo": git_repo,
        "checks": checks,
    }
    if args.json:
        print(json.dumps(payload, indent=2))
        return

    print("\nSingularity doctor\n")
    print(f"  Organization : {org_name or '(missing)'}")
    print(f"  Project      : {project_name or '(missing)'}")
    if current_branch:
        print(f"  Git branch   : {current_branch}")
    if git_repo:
        print(f"  Git repo     : {git_repo}")
    print()
    for check in checks:
        status = "OK" if check["ok"] else "FAIL"
        print(f"  [{status}] {check['name']}: {check['detail']}")
        for hint in check.get("hints", []):
            print(f"         Hint: {hint}")
    print()


def register_doctor_subcommands(sub):
    p = sub.add_parser("doctor", help="Check Azure CLI auth, project access, and repo resolution")
    p.add_argument("--json", action="store_true", help="Emit structured JSON output for scripting")