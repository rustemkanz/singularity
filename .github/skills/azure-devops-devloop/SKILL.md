---
name: azure-devops-devloop
description: 'Azure DevOps developer workflow skill for safe agent-assisted delivery with Singularity. Use for work items, bugs, user stories, pull requests, review threads, attachments, screenshots, and build follow-up, including prompts such as "using Singularity fix bug 123".'
argument-hint: 'Describe the work item, PR, or workflow step you want to progress'
user-invocable: true
---

# Azure DevOps Dev Loop

Use this skill when the task is primarily an Azure DevOps workflow task and `sg` should drive provider operations. Singularity manages workflow state; Copilot inspects and edits the target code repository.

## When To Use

- Picking up the next assigned Azure DevOps item.
- Inspecting a user story or bug before coding.
- Checking comments, acceptance criteria, or repro steps.
- Pulling screenshot or attachment context locally.
- Moving an item into progress, review, or testing.
- Preparing or creating a pull request.
- Reading PR files, diffs, comments, or unresolved threads.
- Replying to review comments or resolving review threads.
- Checking build status for a PR-related pipeline.

## Core Rules

- Verify the target checkout with `git rev-parse --show-toplevel`, `git remote -v`, and `git branch --show-current`.
- Run an installed `sg` command from that checkout. Use this repository's `./sg` only when this is the target repository.
- Pass explicit `--repo` and `--source` values to PR commands.
- Treat the initial task as approval for local code work only. Before each external mutation, preview the exact action and ask for explicit approval. Add `--apply` after approval for plan-first commands; do not invoke older immediate mutators before approval.
- Use Singularity before rebuilding raw Azure DevOps REST calls. If it lacks a needed action, extend it first when practical.

## Procedure

1. Inspect the work item or PR context before changing code.
2. If screenshots or pasted UI evidence may matter, fetch attachments locally.
3. If requirements are unclear, preview a clarification comment instead of making speculative changes; post it only after approval.
4. Use plan-only `start-work` or preview `start` to obtain the same canonical branch/state plan. Only `start --apply` changes provider state, and only after approval; neither command creates the local branch.
5. Create the exact planned branch in the target checkout.
6. After implementation and local validation, preview a PR with explicit repo and source values; create it only after approval.
7. Use the PR analysis and PR comment commands for review follow-up instead of guessing the current thread state.
8. Preview the review transition and apply it only after separate approval. Move the work item forward only when the engineering state matches the workflow state.

## Common Command Flow

```bash
sg sprint
sg ready-items
sg show <id>
sg comments <id> --latest 5
sg attachments <id>
sg start-work <id>
sg start <id>
sg start <id> --apply
sg create-pr <id> --repo <repo> --source <branch>
sg create-pr <id> --repo <repo> --source <branch> --apply
sg pr-analyze --url <ado-pr-url>
sg pr-comments --url <ado-pr-url> --unresolved-only
sg review <id>
sg review <id> --apply
```

## Guardrails

- Do not start coding from the work-item title alone.
- Do not skip attachment review when UI context is likely relevant.
- Do not move items forward prematurely.
- Do not infer external-mutation approval from a request to fix or implement an item.
- Do not reuse one approval for a later comment, transition, PR, review action, pipeline action, or QA handoff.
- Prefer concise, natural external comments and PR replies.
- Use `AI-assisted via Copilot.` as the short trailing disclosure when disclosure is needed.

## Expected Inputs

Useful prompts for this skill include:

- `Pick up the next ready ADO item assigned to me.`
- `Inspect work item 123456 and tell me whether it is clear enough to start.`
- `Download attachment context for bug 123456.`
- `Prepare a PR for work item 123456 in repo foo.`
- `List unresolved review comments on this PR and draft replies.`

## Validation

- Prefer the narrowest relevant local validation for repository code changes.
- For helper changes in this repository, run:

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```
