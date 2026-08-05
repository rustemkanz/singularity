# Singularity

[![CI](https://github.com/rustemkanz/singularity/actions/workflows/ci.yml/badge.svg)](https://github.com/rustemkanz/singularity/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

Apache-2.0 local delivery workflow CLI for human-guided, agent-assisted delivery.

Singularity is designed for the practical developer loop around work items and pull requests: inspect the ticket, gather missing context, start the work, prepare the branch and PR flow, and respond to review feedback with explicit human control points.

## What it is

- A Python CLI for provider-backed delivery workflows, with Azure DevOps and GitLab support.
- A local operator tool that pairs well with coding agents such as Copilot.
- A reference implementation for agent-assisted delivery loops.
- An Apache-2.0 open core that teams can adopt directly or build internal and commercial layers around.

## What it is not

- Not a hosted service.
- Not a general replacement for engineers or project management.
- Not a fully autonomous system that should mutate external systems without approval.
- Not a generic GitHub workflow tool.

## Why the name

`Singularity` started as a sarcastic codename: if an AI model is always running somewhere, and it can inspect a user story or bug, ask questions when the ticket is unclear, start work when it is clear, raise a pull request, and respond to review, then it can cover most of the delivery loop a normal developer handles.

The repo does not claim general artificial superintelligence. The point is narrower and more useful: connect a coding agent to the operational steps around delivery.

## Current scope

The current implementation ships the broadest support for Azure DevOps today, alongside an initial GitLab workflow slice, and supports workflows such as:

- Finding the current sprint and listing candidate items.
- Inspecting work item details, comments, context, and attachments.
- Starting work and suggesting branch, note, and PR metadata.
- Creating pull requests and moving work items through review and testing states.
- Inspecting PR files, diffs, comments, and review threads.
- Drafting, posting, editing, replying to, and resolving review comments.
- Inspecting build status with failure-tail summaries, delta-oriented watch output, visible build reasons, task log ids, stage or active or failed filters, richer pending explanations, direct build step logs, latest matching builds by branch or commit, queueing builds with duplicate-run warnings, and optionally approving pending pipeline gates while watching.

The preferred command entrypoint is the local wrapper `./sg`, which delegates to `sg.py`.

GitLab support now covers a practical issue-plus-merge-request loop under the existing provider-neutral commands, while builds and some deeper workflow surfaces still remain Azure DevOps-only.

The repository also includes a repo-local Copilot skill at `.github/skills/azure-devops-devloop/SKILL.md` for on-demand workflow guidance around the helper.

For repeatable real-system validation and product demos, see `live-smoke-playbook.md`. It is the canonical disposable end-to-end validation and demo scenario for the current ADO implementation and future provider reference runs.

## How it differs

Singularity is not trying to replace the official Azure DevOps platform surfaces.

- The Azure DevOps CLI extension is a broad command surface for Azure DevOps administration and operations.
- The Azure DevOps Python API is a thin SDK for programmatic access to the REST APIs.
- Azure DevOps MCP exposes many Azure DevOps tools directly to agents.

Singularity sits one layer above those. Its value is opinionated workflow orchestration: inspect the work item, decide whether clarification is needed, gather attachment context, start work when ready, prepare the branch and PR flow, and handle review follow-up with explicit approval points.

Azure DevOps still has the broadest workflow surface today, but GitLab now supports helper-driven issue transitions, merge-request creation, top-level and inline review comments, replies, edits, resolution, and disposable-artifact cleanup.

The next platform milestone is to deepen the GitLab adapter while keeping the outer product surface honest: `Singularity` at the front door, provider-specific capabilities behind explicit adapters.

## Example workflow

```text
1. Inspect the next ready work item.
2. Read description, acceptance criteria, comments, and screenshot context.
3. If the ticket is unclear, add a clarification comment instead of guessing.
4. If the ticket is clear, move it to In Progress and prepare a branch.
5. Implement the change in the target repository.
6. Create the PR and move the work item to In Review.
7. Read PR comments, reply, edit, or resolve threads as needed.
8. Hand the item to QA when review is complete.
```

## Quick start

### Prerequisites

- Python 3.11+
- Azure CLI authenticated to a tenant that can access your Azure DevOps organization
- Access to the target Azure DevOps organization, project, and repositories

The project is currently tested in CI on Python 3.11, 3.12, and 3.13.

### Authentication

The helper acquires an Azure DevOps access token through the Azure CLI.

```bash
az login
```

### Configuration

Set these environment variables for your Azure DevOps environment:

```bash
export AZURE_DEVOPS_ORG="your-org"
export AZURE_DEVOPS_PROJECT="your-project"
export AZURE_DEVOPS_TEAM_ID="your-team-id"
export AZURE_DEVOPS_USER="you@example.com"
export AZURE_DEVOPS_QA_USER="qa@example.com"
```

Optional fallback when you are not running inside the target work repo:

```bash
export AZURE_DEVOPS_DEFAULT_REPO="your-repo-name"
```

Optional GitLab configuration for merge-request and issue workflows:

```bash
export GITLAB_TOKEN="your-gitlab-token"
```

`GITLAB_TOKEN` is optional for public read-only merge-request inspection. Set it when the target GitLab project is private or your instance requires authenticated API access.

For project-local settings, prefer an uncommitted repo file:

```bash
cp .env.local.example .env.local
```

`./sg` loads `.env.local` and `.env` from the repository root when present, and shell-exported values still win if both are set.

For repo-aware commands, Singularity first tries to infer the repo from the current git checkout. If you run the helper from another repo, you can pass `--repo` explicitly or set `AZURE_DEVOPS_DEFAULT_REPO` as the fallback.

To discover the team GUID required for `AZURE_DEVOPS_TEAM_ID`, run:

```bash
./sg teams
```

See `env.example.sh` for a shell-export starter template.

### Run the helper

```bash
./sg sprint
./sg ready-items
./sg show 123456
./sg comments 123456 --latest 5
./sg attachments 123456
./sg start 123456
./sg create-pr 123456 --repo your-repo --dry-run
```

### PR inspection

For read-only pull-request inspection, the helper can show changed files, reviewer state, review comments, and attached status checks:

```bash
./sg pr-analyze --url <ado-pr-url>
./sg pr-comments --url <ado-pr-url>
./sg pr-statuses --url <ado-pr-url>
```

GitLab preview uses the same review commands with a GitLab merge-request URL:

```bash
./sg show 42 --provider gitlab --repo group/project
./sg comments 42 --provider gitlab --repo group/project
./sg comment 42 --provider gitlab --repo group/project "Low-risk smoke comment."
./sg start-work 42 --provider gitlab --repo group/project
./sg start 42 --provider gitlab --repo group/project --branch issue/42-smoke
./sg review 42 --provider gitlab --repo group/project
./sg testing 42 --provider gitlab --repo group/project --qa gitlab-qa-username
./sg pr-analyze --url https://gitlab.example.com/group/project/-/merge_requests/123
./sg pr-files --url https://gitlab.example.com/group/project/-/merge_requests/123
./sg pr-file --url https://gitlab.example.com/group/project/-/merge_requests/123 --path /README.md
./sg pr-diff --url https://gitlab.example.com/group/project/-/merge_requests/123 --path /README.md
./sg pr-comment --url https://gitlab.example.com/group/project/-/merge_requests/123 "Please clarify this section."
./sg pr-inline-comment --url https://gitlab.example.com/group/project/-/merge_requests/123 --path /README.md --line 12 "Inline GitLab note."
./sg pr-reply --url https://gitlab.example.com/group/project/-/merge_requests/123 --thread <thread-id> "Thanks, updating this."
./sg create-pr 42 --provider gitlab --repo group/project --source test/42-smoke --work-item-title "Disposable smoke issue"
./sg cleanup-artifacts --provider gitlab --repo group/project --issue 42 --mr 123 --branch test/42-smoke
```

Current GitLab scope is intentionally narrower than Azure DevOps, but it is no longer read-only. GitLab issue inspection, comments, start-work planning, workflow transitions, merge-request creation, top-level review-thread mutations, inline diff comments, and disposable-artifact cleanup are now helper-backed when you pass `--provider gitlab` and an explicit GitLab project path in `--repo`. The existing positional `id` acts as the tracking-item or issue id for title and description defaults in `create-pr`. Public merge-request analysis, files, and diffs are still validated without `GITLAB_TOKEN` on public targets, while `pr-comments` and `pr-statuses` can still require `GITLAB_TOKEN` because GitLab may gate those API endpoints even when the merge request itself is public. Build flows still remain out of scope. The likely GitLab analogue for the ADO work-item loop is issue-plus-merge-request pairing; that loop is now helper-backed through review handoff, but GitLab's own issue APIs did not immediately surface a newly created related merge request during live validation.

`pr-statuses` is useful when a PR has no discussion comments but still carries build, policy, or coverage signals.

## Operating model

Singularity is intentionally local and approval-aware.

- It can inspect and prepare.
- It can automate repetitive Azure DevOps mutations.
- It should ask for clarification when requirements are incomplete.
- It should not silently guess business intent.
- It should keep human review points before high-impact external actions.

## Safety notes

- Review generated PR titles, descriptions, and external comments before posting when possible.
- Treat work-item context and linked screenshots as potentially sensitive.
- Prefer explicit environment configuration over editing source defaults.
- Keep normal network and certificate verification behavior intact.

## Troubleshooting

### TLS certificate errors

If Python fails to verify Azure DevOps certificates, treat that as a local interpreter trust-store problem to fix on the machine.

- Keep TLS verification enabled in normal use.
- Prefer repairing the Python CA bundle or trust store.
- Use `./sg doctor` to confirm whether the helper is still running in secure mode.

## License

This project is licensed under Apache-2.0.

Why Apache-2.0:

- It keeps adoption friction low for developers and companies.
- It allows commercial use and hosted products.
- It includes an explicit patent grant, which is stronger than MIT for enterprise consumers.
- It leaves room to build paid products, hosted services, or enterprise features around the open project later.

See `LICENSE`, `NOTICE`, `SECURITY.md`, and `CODE_OF_CONDUCT.md` for the repository policy surface.

## Repository layout

```text
sg                   Preferred repo-local entrypoint
sg.py                Main CLI implementation
pyproject.toml       Packaging metadata and the sg console entrypoint
cli_commands/        Command modules (work items, review, builds, doctor)
providers/           Provider contracts and adapters (Azure DevOps, GitLab)
azure-devops-workflow.md Azure DevOps workflow guide and command usage
live-smoke-playbook.md Reusable live validation and demo scenario
ARCHITECTURE.md      Architecture direction and keep/generalize/move classification
tests/test_sg.py     Entrypoint and parser coverage
tests/test_*.py      Module-scoped unit tests
CHANGELOG.md         Notable user-facing changes
```

## Status

This repository is best understood as a public-prototype candidate: useful today, intentionally scoped, and still being cleaned up for a broader open-source audience.

The intended first tagged public release is `v0.1.0` unless the scope changes materially.

See `RELEASING.md` for the tagged release checklist and `ARCHITECTURE.md` for the recommended path toward a modular CLI, a companion skill, and a possible future MCP wrapper.

See `CONTRIBUTING.md` for local development and contribution expectations.

## Maintainer

Maintained by Rustem Kanzafarov.

- GitHub: https://github.com/rustemkanz
- LinkedIn: https://www.linkedin.com/in/rustem-kanzafarov/

## Development

Run the tests with:

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

## Roadmap

- Keep the local CLI as the primary operator surface.
- Keep the repo-local skill so agents can load the workflow intentionally.
- Extract a reusable core service layer from the monolithic script.
- Deepen the GitLab adapter toward parity with the Azure DevOps workflow surface, including build visibility.
- Add an MCP wrapper only after the service boundaries are stable.

## Positioning

If you are evaluating this project for open source, the most accurate framing is:

> Singularity is a local, provider-backed delivery workflow CLI for agent-assisted development, not a generalized autonomous developer platform.

Azure DevOps has the broadest workflow coverage today and GitLab covers a practical issue-plus-merge-request loop, but `./sg` is the front door for both; provider-specific capabilities live behind explicit adapters rather than one platform being the default entrypoint. Singularity is also not intended to compete head-on with the official Azure DevOps CLI, Azure DevOps MCP, or the GitLab CLI. It is an opinionated workflow layer built around a narrower developer experience.