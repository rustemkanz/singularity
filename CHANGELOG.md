# Changelog

All notable changes to Singularity are tracked in this file. The project intends to follow [Keep a Changelog](https://keepachangelog.com/) and [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `draft-items`: preview a batch of child work items from a Markdown or JSON plan file and create them under one parent by exact Plan ID. Create-only, single parent, inherited area/iteration, and a fixed field set (type, title, description, tags, assigned-to); partial failure is reported per row.
- `tree`: show a work item's parent chain and its child items (1-3 levels) with state, assignee, tags, and iteration.
- `team-members`: list the configured team's members (or `--team <id>`), with `--json`.
- `profiles` / `use`: named environment profiles under `~/.config/singularity/profiles/<name>.env`, selected by `SG_PROFILE` or a persisted marker and layered under real environment variables.
- Work-item commands now accept a full Azure DevOps `_workitems/edit/<id>` URL (or a GitLab issue URL) anywhere a bare work-item id is taken.

### Changed

- `context` shows assignee and tags on each related item.
- `doctor` reports the active project profile, lists available profiles when none is set, flags a pip CA-bundle configuration that points at a missing file, and rewords the git-repo line so `[OK]` no longer precedes a "could not infer" sentence.

### Fixed

- `pip install -e .` produced an `sg` entrypoint that crashed on every command because `pyproject.toml` `[tool.setuptools] py-modules` omitted `mutation_plans`. A packaging test now keeps that list in sync with the top-level modules.

## [0.2.0] - 2026-08-25

### Added

- Repo-local Codex guidance in `AGENTS.md` and `.agents/skills/azure-devops-devloop/`, including target-checkout verification and explicit repository/source selection for pull requests.
- Immutable, provider-neutral mutation plans with full nonce-backed SHA-256 Plan IDs and local one-shot approval records.
- Read-only `build-approvals` and approval-gated `approve-gate` commands for pipeline gates.
- `AZURE_DEVOPS_EXTERNAL_MEDIA_ORIGINS` for explicit exact-origin media trust decisions.
- Read-only `service-endpoints` and `service-endpoint-show` commands for Azure DevOps service connection/endpoint introspection (id, type, readiness, owner, and the app/service-principal and tenant IDs used for CI-auth debugging).

### Changed

- Every external mutator now previews by default and requires `--apply <PLAN_ID>` matching the exact current action, target, provider-derived state, and payload. Plan IDs are bound to the Git worktree identity, expire after one hour, and are consumed by the first validated provider-dispatch attempt. Existing `--dry-run` usage remains a compatibility alias where previously available.
- `start` is now the single canonical start-plan command, supports `--json`, and retains provider-generated `fix/<id>-...` branches for bugs. The redundant `start-work` command was removed; `start` still never creates a local branch.
- PR creation and the work-item review transition now require separate `create-pr` and `review` plans and approvals. The non-transactional `prepare-review` composite was removed.
- `build-status` is read-only; interactive gate mutation moved to the plan-first `approve-gate` command.
- `queue-build` resolves a full Git commit before preview, binds it as `sourceVersion` plus a recent matching-build snapshot, and blocks duplicate runs unless `--allow-duplicate` is part of the separately approved plan.
- Pull-request creation plans bind the exact provider request and current remote source-branch tip; Azure DevOps and GitLab reject apply when that branch moved after preview and verify the source SHA returned after creation.
- Review drafts record their provider so GitLab drafts no longer route through the Azure DevOps provider.
- Multi-action review drafts now require `--entry <INDEX>` and apply one external mutation per preview and approval.
- Work-item transition plans now include the provider's concrete request and current state snapshot. Azure DevOps applies a revision-guarded JSON Patch, while GitLab rejects an apply if its issue snapshot changed.
- GitLab cleanup plans now bind branch deletion to the resolved numeric project and exact branch-tip commit; apply rechecks each tip before deletion and reports completed targets immediately if a later target fails.

### Security

- Azure DevOps bearer tokens are scoped to exact trusted HTTPS organization URLs when downloading work-item media. Off-origin media is denied by default; allowlisted origins must be exact public HTTPS origins, every redirect and DNS result is validated, and connections are address-pinned against DNS rebinding. Each operation is capped at 50 references, 100 MiB aggregate data, 16 address attempts, and 120 seconds; artifact creation rejects symlink/junction escapes and uses exclusive files.
- Pending and consumed approval records are private, payload-free, Git-worktree-identity-bound, short-lived, and integrity-checked; only hashes and approval metadata are persisted.
- GitLab tokens are scoped to the exact configured `GITLAB_BASE_URL` HTTPS origin, are excluded from redirects, and are no longer exposed through authenticated curl fallback arguments.

### Fixed

- Authenticated GitLab branch cleanup now uses the scoped urllib transport and correctly handles GitLab's empty successful DELETE response.
- Mutation-plan store validation and work-item attachment download-directory validation (including the non-`dir_fd` fallback path) no longer reject a directory solely because a benign ancestor path component is a symlink (e.g. macOS's `/var` → `/private/var`); every mutation-gated command and `attachments` downloads failed unconditionally on stock macOS. Each check still rejects the directory itself being a symlink.
- `queue-build` (and other commands that resolve a Git ref) now hints that the failure likely means `sg` was run outside a Git working directory, instead of surfacing only git's raw `fatal:` message.

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
