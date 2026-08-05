---
name: azure-devops-devloop
description: 'Azure DevOps developer workflow skill for agent-assisted delivery. Use when working from Azure DevOps work items, user stories, bugs, PR reviews, review threads, attachments, screenshots, and build follow-up with the local ./sg helper.'
argument-hint: 'Describe the work item, PR, or workflow step you want to progress'
user-invocable: true
---

# Azure DevOps Dev Loop

Use this skill when the task is primarily an Azure DevOps workflow task and the local `./sg` helper should drive the next action.

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

## Core Rule

Use the local `./sg` helper before rebuilding raw Azure DevOps REST calls. If the helper lacks a needed action, extend it first when practical.

## Procedure

1. Inspect the work item or PR context before changing code.
2. If screenshots or pasted UI evidence may matter, fetch attachments locally.
3. If requirements are unclear, add a clarification comment instead of making speculative changes.
4. Only move the item to `In Progress` when the request is clear enough to implement.
5. After implementation and local validation, create or prepare the PR.
6. Use the PR analysis and PR comment commands for review follow-up instead of guessing the current thread state.
7. Move the work item forward only when the engineering state actually matches the workflow state.

## Common Command Flow

```bash
./sg sprint
./sg ready-items
./sg show <id>
./sg comments <id> --latest 5
./sg attachments <id>
./sg start <id>
./sg create-pr <id> --repo <repo> --dry-run
./sg pr-analyze --url <ado-pr-url>
./sg pr-comments --url <ado-pr-url> --unresolved-only
./sg review <id>
./sg handoff-to-qa <id>
```

## Guardrails

- Do not start coding from the work-item title alone.
- Do not skip attachment review when UI context is likely relevant.
- Do not move items forward prematurely.
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