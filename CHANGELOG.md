# Changelog

All notable changes to Singularity are tracked in this file. The project intends to follow [Keep a Changelog](https://keepachangelog.com/) and [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Repo-local Codex guidance in `AGENTS.md` and `.agents/skills/azure-devops-devloop/`, including target-checkout verification and explicit repository/source selection for pull requests.

### Changed

- `comment`, `start`, `create-pr`, `prepare-review`, and `review` now preview their external action by default and require `--apply` to mutate provider state. Existing PR `--dry-run` usage remains supported.
- `start` and read-only `start-work` now use the same provider-generated branch and state-transition plan, including `fix/<id>-...` branches for bugs; only `start --apply` changes provider state.

### Security

- Azure DevOps bearer tokens are scoped to exact trusted HTTPS organization URLs when downloading work-item media, are excluded from redirects, and downloads now have timeout and size limits.
- GitLab tokens are scoped to the exact configured `GITLAB_BASE_URL` HTTPS origin, are excluded from redirects, and are no longer exposed through authenticated curl fallback arguments.

### Fixed

- Authenticated GitLab branch cleanup now uses the scoped urllib transport and correctly handles GitLab's empty successful DELETE response.

## [0.1.0] - 2026-08-05

Initial public release: a local, approval-aware delivery workflow CLI for agent-assisted development.

### Added

- `./sg` as the single provider-backed CLI entrypoint, with Azure DevOps as the broadest-coverage provider and GitLab as a second supported provider, selected per-command with `--provider`.
- Work-item inspection commands: `show`, `context`, `comments`, `attachments` (including screenshot/evidence download), `introduced-by`, and `triage`.
- Workflow commands with explicit human approval points: `sprint`, `list`/`ready-items`, `pick-next`, `start`/`start-work`, `review`, `testing`/`handoff-to-qa`, `comment`, and `cleanup-artifacts`.
- Pull/merge request workflows: `create-pr`, `prepare-review`, `pr-analyze`, `pr-files`, `pr-file`, `pr-diff`, `pr-comments`, `pr-statuses`, `pr-comment`, `pr-inline-comment`, `pr-reply`, `pr-edit-comment`, `pr-resolve`, and draft-first review flows (`pr-review-draft` / `pr-review-apply`).
- Azure DevOps build visibility: `builds`, `build-status` (stage grouping, failure-tail summaries, delta-oriented `--watch`), `build-logs`, and `queue-build` with duplicate-run warnings and interactive pending-approval handling.
- `./sg doctor` for authentication, configuration, and repo-resolution diagnostics, and `./sg teams` to discover the Azure DevOps team GUID.
- A provider-neutral internal architecture (`providers/interfaces.py`, `providers/azure_devops/`, `providers/gitlab/`, `workflow_models.py`) behind the CLI, with command modules under `cli_commands/`.
- Packaging via `pyproject.toml`, exposing the `sg` console entrypoint for `pip`/`pipx` installs.
- Open-source policy surface: Apache-2.0 `LICENSE`, `NOTICE`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, `CONTRIBUTING.md`, `RELEASING.md`, and a repo-local Copilot skill at `.github/skills/azure-devops-devloop/SKILL.md`.
- GitHub Actions CI running the unit test suite on Python 3.11, 3.12, and 3.13.

### Changed

- Generalized the CLI from an Azure-DevOps-only tool into a provider-neutral core, with Azure DevOps and GitLab as concrete adapters behind shared `WorkTrackingProvider`, `ReviewProvider`, `BuildProvider`, and `EvidenceProvider` interfaces.
- Centralized process-exit and error handling behind typed CLI errors instead of ad hoc printing and exiting from inside helper code.
- HTTPS certificate verification is always enabled; TLS bypass is documented only as local troubleshooting guidance, never a default.
- Configuration uses explicit, documented `AZURE_DEVOPS_*` / `GITLAB_*` environment variables with neutral placeholder examples, loadable from an uncommitted `.env.local`.

### Fixed

- `./sg doctor` no longer crashes when Azure CLI authentication succeeds but the Azure DevOps org/project are not yet configured; it now reports the missing configuration instead.
