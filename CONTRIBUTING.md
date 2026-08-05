# Contributing

## Scope

This project is a local delivery workflow helper for agent-assisted development, with the broadest workflow coverage currently on Azure DevOps and growing GitLab support. Keep changes focused, reviewable, and explicit about any external side effects.

## Development setup

### Prerequisites

- Python 3.11+
- Azure CLI
- Access to an Azure DevOps org, project, and repositories for any live testing

The repository is currently tested in CI on Python 3.11, 3.12, and 3.13.

### Configuration

Set the required environment variables before running the helper. For repo-local settings, copy `.env.local.example` to `.env.local`; `./sg` loads that file automatically without overriding values you already exported in the shell. Use `env.example.sh` if you prefer shell exports.

If you do not know your team GUID, run `./sg teams` and use the matching id as `AZURE_DEVOPS_TEAM_ID`.

Required variables:

- `AZURE_DEVOPS_ORG`
- `AZURE_DEVOPS_PROJECT`
- `AZURE_DEVOPS_TEAM_ID`
- `AZURE_DEVOPS_USER`
- `AZURE_DEVOPS_QA_USER`

Optional:

- `AZURE_DEVOPS_DEFAULT_REPO` as the fallback repo for PR and other repo-aware commands when you are not running inside the target work repo.

If you hit TLS verification failures, fix the local Python trust store or CA bundle instead of working around verification in the helper.

### Running tests

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

## Contribution expectations

- Preserve the local CLI as the primary operator surface.
- Prefer explicit human approval points around comments, PRs, review replies, and state changes.
- When changing user-visible behavior, update `CHANGELOG.md` in the same change.
- Keep examples and defaults tenant-neutral.
- Do not reintroduce insecure TLS behavior as a default.

## Pull request guidance

- Explain the user-facing workflow impact.
- Include the narrowest relevant validation you ran.
- Keep the change scoped to one concern when practical.