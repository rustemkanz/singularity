---
name: azure-devops-devloop
description: Safely progress Azure DevOps work items, bugs, user stories, pull requests, review threads, attachments, and pipeline follow-up with the Singularity `sg` CLI. Use when Codex is asked to inspect, implement, fix, start, review, or hand off an Azure DevOps item, especially prompts such as "using Singularity fix bug 123". Enforce target-checkout verification and explicit approval before external mutations.
---

# Azure DevOps Dev Loop

Use `sg` for Azure DevOps workflow operations and use Codex for repository inspection, code changes, and validation. Singularity does not implement the code fix itself.

## Establish the target checkout

1. Run `git rev-parse --show-toplevel`, `git remote -v`, and `git branch --show-current` before repo-aware work.
2. Confirm the checkout is the repository that should receive the fix. Do not infer a target repository from the Singularity source checkout.
3. Run `sg` from the target code checkout. Use an installed `sg` entrypoint or an absolute path to the Singularity launcher.
4. Pass explicit `--repo` and `--source` values to PR commands. Do not rely on repository or branch inference for external mutations.

## Approval boundary

Treat the initial request as approval for local inspection, code edits, and tests only. Before every external mutation:

1. Show the exact action and payload to the user.
2. Ask for explicit approval for that action.
3. For a plan-first command, add `--apply` only after approval is granted. For an older immediate mutator, do not invoke it at all until its exact action has been approved.

Never infer approval from "fix bug 123" alone. Approval for one action does not authorize later comments, state transitions, PR creation, review-state changes, pipeline runs, or QA handoff.

## Workflow

### 1. Inspect before coding

Run the narrow read path for an explicit item:

```text
sg show <id>
sg comments <id> --latest 5
```

Use `sg context <id>` when linked work items, PRs, or commits matter. Use `sg attachments <id>` when screenshot evidence matters; inspect listed destinations and never weaken TLS or credential-scope checks.

Decide whether the description, repro steps, acceptance criteria, comments, and evidence make the work implementable. The CLI does not make this decision.

### 2. Clarify or start

If requirements are unclear, prepare a concise clarification:

```text
sg comment <id> "<question>"
```

The command previews by default. Show the text to the user, obtain approval, then run the same command with `--apply`. Stop implementation until the blocker is resolved.

If requirements are clear, obtain the canonical branch and PR plan:

```text
sg start-work <id>
sg start <id>
```

Both commands must show the same `StartWorkPlan` by default.

- Treat `start-work` as plan-only: it supports `--json`, has no apply mode, and never changes provider state.
- Treat `start` as the action boundary: it previews by default and changes state only with `--apply`.
- Do not claim either command creates the local branch; create the displayed branch in the verified target checkout.

Show the proposed state transition and branch to the user. After approval, run:

```text
sg start <id> --apply
```

Create the exact planned branch in the target checkout. If branch creation or setup fails after the transition, report the partial state; do not hide or guess a rollback.

### 3. Implement and validate

Inspect the target repository, implement the smallest complete fix, and run the narrowest relevant tests. Singularity does not edit code, commit, push, or validate acceptance criteria.

Do not create a PR until local validation passes. Commit and push only when the user request and repository policy authorize those Git operations.

### 4. Prepare and create the PR

Always pass the verified repository and current source branch:

```text
sg create-pr <id> --repo <repo> --source <branch>
```

PR creation previews by default. Show the repository, source, target, title, description, and linked work item. After explicit approval, run:

```text
sg create-pr <id> --repo <repo> --source <branch> --apply
```

Then preview the work-item review transition with `sg review <id>`. Apply it only after separate approval:

```text
sg review <id> --apply
```

Do not merge, approve a pipeline gate, resolve review threads, or hand off to QA unless the user explicitly requests and approves that next action.

## Failure handling

- Stop on missing authentication, configuration, permissions, repository mismatch, or unclear requirements.
- Report external changes that succeeded before a later failure; composite actions are not transactional.
- Do not move an item forward when code, tests, branch push, or PR creation failed.
- Never place tokens in commands, logs, comments, or generated files.

## Validation for Singularity changes

When the target item changes this repository, run:

```text
python -m unittest discover -s tests -p "test_*.py"
```
