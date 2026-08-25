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
- Inspecting build status with failure-tail summaries, delta-oriented watch output, visible build reasons, task log ids, stage or active or failed filters, richer pending explanations, direct build step logs, latest matching builds by branch or commit, duplicate-safe build queueing, and optionally approving pending pipeline gates while watching.

The source checkout includes a local `./sg` wrapper. For agent-assisted work in another repository, install the package so the `sg` entrypoint can be run from the target code checkout.

GitLab support now covers a practical issue-plus-merge-request loop under the existing provider-neutral commands, while builds and some deeper workflow surfaces still remain Azure DevOps-only.

The repository includes matching repo-local workflow guidance for Codex (`.agents/skills/azure-devops-devloop/SKILL.md` and `AGENTS.md`) and Copilot (`.github/skills/azure-devops-devloop/SKILL.md`). Both keep external mutations behind explicit approval.

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

### Install the target-checkout entrypoint

From a Singularity clone, install an editable development entrypoint:

```bash
python -m pip install -e /path/to/singularity
```

Then change to the code repository that should receive the work and run `sg` there. This keeps git remote and branch inference anchored to the target checkout.

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

Off-origin work-item media is blocked by default. If a trusted CDN is required, allow only its exact public HTTPS origin:

```bash
export AZURE_DEVOPS_EXTERNAL_MEDIA_ORIGINS="https://media.example.com,https://cdn.example.com"
```

Paths, wildcard domains, HTTP origins, IP literals, and private/link-local DNS destinations are rejected. Every redirect is checked again, and Azure credentials remain limited to the configured Azure DevOps organization.

Optional GitLab configuration for merge-request and issue workflows:

```bash
export GITLAB_TOKEN="your-gitlab-token"
export GITLAB_BASE_URL="https://gitlab.com"
```

`GITLAB_TOKEN` is optional for public read-only merge-request inspection. Set it when the target GitLab project is private or your instance requires authenticated API access. When configured, authenticated requests are allowed only to the exact HTTPS origin in `GITLAB_BASE_URL`; a different origin is rejected before network access. Without a token, public read-only requests may use another valid HTTPS GitLab origin anonymously.

For project-local settings, prefer an uncommitted repo file:

```bash
cp .env.local.example .env.local
```

The source-checkout `./sg` wrapper loads `.env.local` and `.env` from the Singularity repository root when present, and shell-exported values still win. When running an installed `sg` from another checkout, prefer shell-exported configuration rather than copying provider credentials into the target repository.

For repo-aware commands, run an installed `sg` entrypoint from the target code checkout. Singularity can infer the repo from that checkout, but mutation workflows should pass `--repo` explicitly; PR workflows should also pass `--source`. Do not run the Singularity source checkout's `./sg` and accidentally infer Singularity itself as the target repository.

To discover the team GUID required for `AZURE_DEVOPS_TEAM_ID`, run:

```bash
./sg teams
```

See `env.example.sh` for a shell-export starter template.

### Run the helper

```bash
sg sprint
sg ready-items
sg show 123456
sg comments 123456 --latest 5
sg attachments 123456
sg start 123456
# After approving that exact displayed Plan ID:
sg start 123456 --apply <PLAN_ID>
sg create-pr 123456 --repo your-repo --source fix/123456-example
# After separately approving that exact PR Plan ID:
sg create-pr 123456 --repo your-repo --source fix/123456-example --apply <PLAN_ID>
```

Every mutation follows the same two-invocation contract: preview without `--apply`, then rerun the otherwise unchanged command with `--apply <PLAN_ID>`. The full `sha256:` ID binds the action, provider target, payload, verified Git worktree identity, and a short-lived nonce. It is stored locally as a one-shot approval grant, expires after one hour, and is consumed before the provider request begins. If any material input or provider-derived default changes, the command needs a fresh preview and approval. If a provider request times out or otherwise has an ambiguous result, inspect provider state before previewing a retry so an action that actually succeeded is not duplicated.

### Starting work

`start <id>` is the single canonical start command. It prints branch, note, commit, PR, and `In Progress` transition metadata, supports `--json`, and is read-only without an exact approved Plan ID. Its optional `--branch` value deliberately overrides the displayed branch plan. It never creates or checks out a local git branch; the agent or developer creates the exact planned branch in the verified target checkout.

### PR inspection

For read-only pull-request inspection, the helper can show changed files, reviewer state, review comments, and attached status checks:

```bash
sg pr-analyze --url <ado-pr-url>
sg pr-comments --url <ado-pr-url>
sg pr-statuses --url <ado-pr-url>
```

### Builds and approvals

Build inspection is read-only. Queueing a run and approving a gate use separate immutable plans:

```bash
sg build-status <build-id> --project <project> --watch
sg build-approvals <build-id> --project <project>
sg approve-gate <build-id> --project <project> --approval <approval-id>
sg approve-gate <build-id> --project <project> --approval <approval-id> --apply <PLAN_ID>
sg queue-build --definition <definition-id> --project <project> --branch <branch> --commit <commit-ref>
sg queue-build --definition <definition-id> --project <project> --branch <branch> --commit <commit-ref> --apply <PLAN_ID>
# Only after inspecting an existing run and intentionally requesting a duplicate:
sg queue-build --definition <definition-id> --project <project> --branch <branch> --commit <commit-ref> --allow-duplicate
# After approving that exact intentional-duplicate plan:
sg queue-build --definition <definition-id> --project <project> --branch <branch> --commit <commit-ref> --allow-duplicate --apply <PLAN_ID>
```

`queue-build` resolves `--commit` (or the selected branch/`HEAD` by default) to a full SHA before previewing. That exact `sourceVersion` and the recent matching-build snapshot are part of the Plan ID, so a moved ref or changed duplicate snapshot requires a fresh preview and approval; the Azure DevOps queue request receives the pinned `sourceVersion`. Existing matching runs block queueing by default, and `--allow-duplicate` must itself be present in both the preview and approved apply.

Service connections (service endpoints) are read-only:

```bash
sg service-endpoints --project <project>
sg service-endpoint-show <endpoint-name> --project <project>
sg service-endpoint-show --id <endpoint-id> --project <project>
```

GitLab preview uses the same review commands with a GitLab merge-request URL:

```bash
sg show 42 --provider gitlab --repo group/project
sg comments 42 --provider gitlab --repo group/project
sg comment 42 --provider gitlab --repo group/project "Low-risk smoke comment."
sg comment 42 --provider gitlab --repo group/project "Low-risk smoke comment." --apply <PLAN_ID>
sg start 42 --provider gitlab --repo group/project --branch issue/42-smoke
sg start 42 --provider gitlab --repo group/project --branch issue/42-smoke --apply <PLAN_ID>
sg review 42 --provider gitlab --repo group/project
sg review 42 --provider gitlab --repo group/project --apply <PLAN_ID>
sg testing 42 --provider gitlab --repo group/project --qa gitlab-qa-username
sg testing 42 --provider gitlab --repo group/project --qa gitlab-qa-username --apply <PLAN_ID>
sg pr-analyze --url https://gitlab.example.com/group/project/-/merge_requests/123
sg pr-files --url https://gitlab.example.com/group/project/-/merge_requests/123
sg pr-file --url https://gitlab.example.com/group/project/-/merge_requests/123 --path /README.md
sg pr-diff --url https://gitlab.example.com/group/project/-/merge_requests/123 --path /README.md
sg pr-comment --url https://gitlab.example.com/group/project/-/merge_requests/123 "Please clarify this section."
sg pr-inline-comment --url https://gitlab.example.com/group/project/-/merge_requests/123 --path /README.md --line 12 "Inline GitLab note."
sg pr-reply --url https://gitlab.example.com/group/project/-/merge_requests/123 --thread <thread-id> "Thanks, updating this."
sg create-pr 42 --provider gitlab --repo group/project --source test/42-smoke --work-item-title "Disposable smoke issue"
sg create-pr 42 --provider gitlab --repo group/project --source test/42-smoke --work-item-title "Disposable smoke issue" --apply <PLAN_ID>
sg cleanup-artifacts --provider gitlab --repo group/project --issue 42 --mr 123 --branch test/42-smoke
sg cleanup-artifacts --provider gitlab --repo group/project --issue 42 --mr 123 --branch test/42-smoke --apply <PLAN_ID>
```

For branch cleanup, the preview resolves the GitLab project’s numeric identity and each branch’s exact commit SHA. Apply rechecks those branch tips immediately before deletion and rejects a missing branch or changed tip (including a branch recreated at a different commit), so a stale approval cannot delete a different ref state.

Current GitLab scope is intentionally narrower than Azure DevOps, but it is no longer read-only. GitLab issue inspection, start planning, comments, workflow transitions, merge-request creation, top-level review-thread mutations, inline diff comments, and disposable-artifact cleanup are helper-backed when you pass `--provider gitlab` and an explicit GitLab project path in `--repo`. The existing positional `id` acts as the tracking-item or issue id for title and description defaults in `create-pr`. Public merge-request analysis, files, and diffs are still validated without `GITLAB_TOKEN` on public targets, while `pr-comments` and `pr-statuses` can still require `GITLAB_TOKEN` because GitLab may gate those API endpoints even when the merge request itself is public. Build flows remain Azure DevOps-only.

`pr-statuses` is useful when a PR has no discussion comments but still carries build, policy, or coverage signals.

## Operating model

Singularity is intentionally local and approval-aware.

- It can inspect and prepare.
- It can automate repetitive Azure DevOps mutations.
- Every external mutation previews by default and requires `--apply <PLAN_ID>` for the exact current action, target, and payload.
- An agent should show the complete preview and Plan ID and obtain approval for each action; approval for local code work or a prior mutation does not carry forward.
- It should ask for clarification when requirements are incomplete.
- It should not silently guess business intent.
- It should keep human review points before high-impact external actions.

### Known safety boundaries

- A Plan ID is a Git-worktree-identity-bound, one-shot grant for one recomputed plan, not a durable capability. It expires after one hour and cannot be replayed after the first validated provider-dispatch attempt in the normal workflow.
- The local plan store is not a security boundary against another malicious process running as the same OS user. Protect the workstation and account; a same-user process that can alter Singularity's files can also tamper with local approval records.
- Pending and consumed plan records contain hashes and approval metadata, not mutation targets or payload text.
- Because the grant is consumed before provider dispatch, a network failure can leave the outcome unknown. Reconcile Azure DevOps or GitLab state before requesting approval for a retry.
- PR/MR creation APIs accept a source branch rather than an atomic create-if-commit precondition. Singularity rechecks the remote tip immediately before creation and verifies the source SHA returned by the provider; if that final verification differs, the change request already exists and must be inspected instead of retried.
- A review draft applies one selected entry per preview and approval; use `--entry <INDEX>` when a draft contains multiple actions. Artifact cleanup remains sequential, so earlier targets can succeed before a later target fails.
- PR creation and the work-item review transition are deliberately separate commands with separate approvals. Singularity does not offer a composite `prepare-review` mutation.
- Off-origin work-item media is denied unless its exact public HTTPS origin is configured. Allowlisting an origin authorizes anonymous network access to it, so treat that configuration as a trust decision.

## Safety notes

- Review generated PR titles, descriptions, and external comments before posting when possible.
- Treat work-item context and linked screenshots as potentially sensitive.
- Work-item media downloads attach Azure DevOps bearer credentials only to exact trusted HTTPS organization URLs. Every redirect is revalidated; explicitly allowed off-origin URLs are fetched anonymously, resolved addresses must be public, and connections are pinned to the validated address to prevent DNS rebinding.
- GitLab tokens are attached only to the exact HTTPS origin configured by `GITLAB_BASE_URL` and are never forwarded across redirects.
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
- Add resumable journals or idempotency keys for sequential bulk cleanup operations.
- Extract a reusable core service layer from the monolithic script.
- Deepen the GitLab adapter toward parity with the Azure DevOps workflow surface, including build visibility.
- Defer GitHub integration until after the current roadmap; when revisited, prefer a thin approval-aware provider for PR, review, and Actions workflows rather than duplicating the official GitHub MCP server or `gh` CLI.
- Add an MCP wrapper only after the service boundaries are stable.

## Positioning

If you are evaluating this project for open source, the most accurate framing is:

> Singularity is a local, provider-backed delivery workflow CLI for agent-assisted development, not a generalized autonomous developer platform.

Azure DevOps has the broadest workflow coverage today and GitLab covers a practical issue-plus-merge-request loop, but `sg` is the front door for both; provider-specific capabilities live behind explicit adapters rather than one platform being the default entrypoint. Singularity is also not intended to compete head-on with the official Azure DevOps CLI, Azure DevOps MCP, or the GitLab CLI. It is an opinionated workflow layer built around a narrower developer experience.
