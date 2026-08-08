# Azure DevOps Workflow Reference

## Overview

This document describes the Azure DevOps-specific workflow reference for Singularity: connecting to Azure DevOps, picking up work items, creating branches, managing PRs, and transitioning states.

For work on this repository, `./sg` wraps the CLI implementation. For delivery work in another repository, install Singularity and run `sg` from the target code checkout so repository and branch context cannot come from the Singularity source checkout.

---

## 1. Authentication

Use the Azure CLI to obtain a bearer token scoped to Azure DevOps:

```bash
TOKEN=$(az account get-access-token \
  --resource 499b84ac-1321-427f-aa17-267ca6975798 \
  --query accessToken -o tsv)

# Bash helper for the protocol examples below. The bearer value is sent to
# curl over a private process-substitution stream instead of appearing in argv.
ado_curl() {
  command curl --config <(printf 'header = "Authorization: Bearer %s"\n' "$TOKEN") "$@"
}
```

> Token is valid for ~1 hour. Re-run the command to refresh it. Prefer `sg` for normal operation. Do not put an expanded bearer token directly in a curl `-H` argument, shell trace, or log.

**Environment variables supported by the CLI:**
```bash
export AZURE_DEVOPS_ORG="your-ado-org"
export AZURE_DEVOPS_PROJECT="your-ado-project"
export AZURE_DEVOPS_TEAM_ID="your-team-id"
export AZURE_DEVOPS_USER="you@example.com"
export AZURE_DEVOPS_QA_USER="qa@example.com"
```

Off-origin work-item media is blocked by default. Trust a required CDN only by exact public HTTPS origin:

```bash
export AZURE_DEVOPS_EXTERNAL_MEDIA_ORIGINS="https://media.example.com"
```

Optional fallback when you are not running inside the target work repo:
```bash
export AZURE_DEVOPS_DEFAULT_REPO="your-repo-name"
```

For project-specific local defaults, you can instead copy `.env.local.example` to `.env.local`. The `./sg` wrapper loads `.env.local` and `.env` automatically from the repo root.

If you do not know the team GUID for `AZURE_DEVOPS_TEAM_ID`, run `./sg teams` and copy the id for your team.

Before repo-aware work, verify the target checkout with `git rev-parse --show-toplevel`, `git remote -v`, and `git branch --show-current`. Pass `--repo` explicitly for mutations and both `--repo` and `--source` for PR commands; do not rely on context inferred from another checkout.

If these variables are not set, `sg.py` falls back to neutral placeholder values and `./sg doctor` reports the missing configuration.

**Useful CLI commands:**
```bash
./sg sprint
./sg ready-items
./sg pick-next
./sg pick-next --start
./sg show <id>
./sg comments <id>
./sg comments <id> --latest 5
./sg context <id>
./sg attachments <id>
./sg attachments <id> --open
./sg introduced-by <id>
./sg triage <id1> <id2> <id3>
./sg start <id>
./sg start <id> --json
./sg start <id> --apply <PLAN_ID>
./sg create-pr <id> --repo <repo-name-or-id> --source <branch>
./sg create-pr <id> --repo <repo-name-or-id> --source <branch> --apply <PLAN_ID>
./sg review <id>
./sg review <id> --apply <PLAN_ID>
./sg pr-analyze --url <ado-pr-url>
./sg pr-analyze --url <ado-pr-url> --json
./sg pr-statuses --url <ado-pr-url>
./sg pr-statuses --url <ado-pr-url> --json
./sg pr-files --url <ado-pr-url>
./sg pr-file --url <ado-pr-url> --path <repo-path> --number-lines --start-line 1 --end-line 80
./sg pr-diff --url <ado-pr-url> --path <repo-path>
./sg pr-comments --url <ado-pr-url> --unresolved-only
./sg pr-comments --url <ado-pr-url> --json
./sg pr-inline-comment --url <ado-pr-url> --path <repo-path> --line <line> "Please clarify this branch logic."
./sg pr-reply --url <ado-pr-url> --thread <thread-id> "Thanks, I will adjust this."
./sg pr-edit-comment --url <ado-pr-url> --thread <thread-id> --comment <comment-id> "Updated reviewer response. AI-assisted via Copilot."
./sg pr-resolve --url <ado-pr-url> --thread <thread-id>
./sg pr-review-draft --url <ado-pr-url> --output review-draft.json
./sg pr-review-apply review-draft.json --entry <index>
./sg builds --definition <pipeline-id> --project <ado-project>
./sg builds --definition <pipeline-id> --project <ado-project> --latest-for-branch main
./sg builds --definition <pipeline-id> --project <ado-project> --commit <sha>
./sg build-status <build-id> --project <ado-project> --watch --verbose
./sg build-status <build-id> --project <ado-project> --stage "Deploy" --only-failed --show-log-ids
./sg build-approvals <build-id> --project <ado-project>
./sg approve-gate <build-id> --project <ado-project> --approval <approval-id>
./sg queue-build --definition <pipeline-id> --project <ado-project> --branch <branch> --commit <commit-ref>
./sg build-logs <build-id> --project <ado-project> --failed
./sg handoff-to-qa <id>
./sg doctor
./sg comment <id> "Need clarification on ..."
./sg comment <id> "Need clarification on ..." --apply <PLAN_ID>
```

`builds` surfaces the Azure DevOps build reason and supports latest-branch or commit filtering for duplicate-run triage. `queue-build` resolves its commit reference to a full SHA before previewing and binds that `sourceVersion` plus the recent matching-build snapshot into the plan. Existing matching runs block queueing unless `--allow-duplicate` is included in the separately reviewed preview and approved apply. `build-status` is read-only, groups stage state, includes concise failure-tail lines, and prints only changed lines while watching unless `--verbose` is set. Use `build-approvals` to inspect pending gates and `approve-gate` for a separately approved gate mutation.

Every external mutator prints an exact plan and full `sha256:` Plan ID by default. Inspect that output, obtain explicit approval for that individual action, and rerun the otherwise unchanged command with `--apply <PLAN_ID>`. If any target, payload, or provider-derived value changes, the ID is rejected and the new plan needs fresh approval. After a timeout or ambiguous response, inspect provider state before previewing a retry. `--dry-run` remains accepted as a compatibility form where it existed previously.

---

## 2. Find the Current Sprint

```bash
ado_curl -s "${BASE_URL}/${TEAM_ID}/_apis/work/teamsettings/iterations?api-version=7.1" | python3 -c "
import json, sys
from datetime import date
today = date.today()
data = json.load(sys.stdin)
for it in data.get('value', []):
    attrs = it.get('attributes', {})
    start = attrs.get('startDate', '')[:10] if attrs.get('startDate') else None
    end = attrs.get('finishDate', '')[:10] if attrs.get('finishDate') else None
    if start and end and date.fromisoformat(start) <= today <= date.fromisoformat(end):
        print(it['name'], it['path'])
"
```

---

## 3. Query Open Work Items (New / Ready for Development)

The CLI form is:

```bash
./sg ready-items
```

The raw REST approach is:

```bash
SPRINT_PATH="your-project\\\\your-team\\\\current-sprint"

ado_curl -s -X POST \
  "${BASE_URL}/_apis/wit/wiql?api-version=7.1" \
  -H "Content-Type: application/json" \
  -d "{
    \"query\": \"SELECT [System.Id],[System.Title],[System.WorkItemType],[System.State] FROM WorkItems WHERE [System.AssignedTo] = '${ME}' AND [System.IterationPath] UNDER '${SPRINT_PATH}' AND [System.State] IN ('New','Ready for development') AND [System.WorkItemType] IN ('Bug','User Story') ORDER BY [System.Id]\"
  }"
```

This returns a list of `workItems` with `id` and `url`. Extract IDs and fetch details:

```bash
IDS="<comma-separated ids>"

ado_curl -s "${BASE_URL}/_apis/wit/workitems?ids=${IDS}&fields=System.Id,System.Title,System.WorkItemType,System.State,System.Description,Microsoft.VSTS.Common.AcceptanceCriteria&api-version=7.1" | python3 -c "
import json, sys
data = json.load(sys.stdin)
for item in data.get('value', []):
    f = item['fields']
    print(f\"[{f['System.Id']}] {f['System.WorkItemType']} | {f['System.State']}\")
    print(f\"  Title: {f['System.Title']}\")
    print(f\"  Description: {(f.get('System.Description') or '').strip()[:200]}\")
    print(f\"  Acceptance Criteria: {(f.get('Microsoft.VSTS.Common.AcceptanceCriteria') or '').strip()[:200]}\")
    print()
"
```

---

## 4. Check Context / Acceptance Criteria

Before starting work, verify the work item has:
- A clear **Description**
- Defined **Acceptance Criteria**
- No blocking questions

**If context is unclear → add a comment** (see Section 8) and wait.

**If context is clear → proceed to Section 5.**

To inspect one work item via CLI:

```bash
./sg show <id>
./sg comments <id>
./sg comments <id> --latest 5
./sg context <id>
```

`show` prints the work item Description, Repro Steps, and Acceptance Criteria, and now also previews any linked screenshot/file context it can detect.

`comments` is the direct comment view. It prints work-item comments without the rest of the context bundle, which is useful when the latest tester or QA feedback is the main thing you need. Use `--latest <count>` when you only want the newest few comments.

`context` is the higher-context variant. It includes recent comments, related work items, and linked PR/commit artifacts when ADO exposes them on the work item.

If screenshots, mockups, or pasted UI context may matter, also fetch the work item media context:

```bash
./sg attachments <id>
```

This downloads image evidence by default into `./.sg-artifacts/work-item-<id>/` so the local implementation context includes the same screenshots visible in Azure DevOps. Azure-hosted media is authenticated only inside the configured organization scope. Off-origin media is denied unless its exact public HTTPS origin appears in `AZURE_DEVOPS_EXTERNAL_MEDIA_ORIGINS`; redirects and DNS destinations are revalidated. Use `--no-download` to inspect references only or `--images-only` to limit output to screenshot-like media.

Use `--download-all` when you also want non-image attachments, and `--open` to reveal the download directory in the local file browser after download.

Use `attachments` even when `show` already previews context refs if you need the full reference list or the downloaded image files.

To let the CLI choose the next best candidate based on state and type priority:

```bash
./sg pick-next
```

When changing the work-item context parsing or command modules, run this smoke test before committing:

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

When the helper itself is failing due to auth, repo selection, or project mismatch, run:

```bash
./sg doctor
```

---

## 5. Start Work: Create a Branch & Move to "In Progress"

The CLI form is:

```bash
./sg start <id>
./sg start <id> --json
./sg pick-next --start
```

`start` is the single source for the provider-generated `StartWorkPlan`, so branch, PR metadata, and the `In Progress` transition cannot diverge between commands. It remains read-only by default, supports `--json`, and moves the item only with its exact approved Plan ID. An explicit `--branch` overrides the rendered branch plan. Bugs default to `fix/<id>-...`; other types use the provider's canonical prefix.

`start` does not create the local branch. After approving and applying the transition, create the exact displayed branch in the verified target checkout. `pick-next --start --apply <PLAN_ID>` uses the same action path for the selected item.

If you want a quick regression-origin signal before archaeology in git, use:

```bash
./sg introduced-by <id>
```

If you are triaging several bugs and want grouping hints for review-friendly PRs, use:

```bash
./sg triage <id1> <id2> <id3>
```

### 5a. Create a Git branch

```bash
ITEM_ID=<work-item-id>
BRANCH_NAME="<exact-branch-from-start-plan>"

git checkout main && git pull
git checkout -b "$BRANCH_NAME"
```

### 5b. Move work item to "In Progress"

```bash
ado_curl -s -X PATCH \
  "https://dev.azure.com/${ORG}/${PROJECT}/_apis/wit/workitems/${ITEM_ID}?api-version=7.1" \
  -H "Content-Type: application/json-patch+json" \
  -d '[{"op":"replace","path":"/fields/System.State","value":"In Progress"}]'
```

---

## 6. Create a PR & Move to "In Review"

The CLI form is:

```bash
./sg create-pr <id> --repo <repo-name-or-id> --source <branch>
./sg create-pr <id> --repo <repo-name-or-id> --source <branch> --apply <PLAN_ID>
./sg review <id>
./sg review <id> --apply <PLAN_ID>
```

The first `create-pr` invocation previews the exact PR payload. Create it only with that plan's approved ID. Preview and approve the separate work-item review transition independently; PR creation approval does not authorize `review`. There is deliberately no composite PR-plus-transition mutation.

### 6a. Push branch and open a PR

```bash
git push -u origin "$BRANCH_NAME"
```

Then create the PR via REST (or via the ADO UI):

```bash
REPO_ID="<your-repo-id>"   # Get from: GET ${BASE_URL}/_apis/git/repositories?api-version=7.1

ado_curl -s -X POST \
  "https://dev.azure.com/${ORG}/${PROJECT}/_apis/git/repositories/${REPO_ID}/pullrequests?api-version=7.1" \
  -H "Content-Type: application/json" \
  -d "{
    \"title\": \"[${ITEM_ID}] <short description>\",
    \"description\": \"Closes #${ITEM_ID}\",
    \"sourceRefName\": \"refs/heads/${BRANCH_NAME}\",
    \"targetRefName\": \"refs/heads/main\",
    \"workItemRefs\": [{\"id\": \"${ITEM_ID}\"}]
  }"
```

### 6b. Move work item to "In Review"

```bash
ado_curl -s -X PATCH \
  "https://dev.azure.com/${ORG}/${PROJECT}/_apis/wit/workitems/${ITEM_ID}?api-version=7.1" \
  -H "Content-Type: application/json-patch+json" \
  -d '[{"op":"replace","path":"/fields/System.State","value":"In Review"}]'
```

---

## 6c. Inspect or Draft PR Review Comments Natively

When you want to inspect a PR or prepare comments before posting them, stay inside the Singularity CLI instead of dropping to ad hoc REST calls.

List changed files and inspect the diff:

```bash
./sg pr-files --url <ado-pr-url>
./sg pr-file --url <ado-pr-url> --path <repo-path> --number-lines --start-line 1 --end-line 120
./sg pr-diff --url <ado-pr-url> --path <repo-path>
```

Review existing comments and prepare new ones:

```bash
./sg pr-comments --url <ado-pr-url> --unresolved-only
./sg pr-inline-comment --url <ado-pr-url> --path <repo-path> --line <line> "Please clarify this branch logic."
./sg pr-comment --url <ado-pr-url> "High-level feedback before approval."
```

Draft a review bundle for later per-entry approval and application:

```bash
./sg pr-review-draft --url <ado-pr-url> --output review-draft.json
./sg pr-review-apply review-draft.json --entry <index>
```

Each mutation command above previews by default. Apply an individually approved review action by rerunning the same command with its displayed `--apply <PLAN_ID>`. A review draft with multiple entries requires `--entry <INDEX>` so every posted comment, edit, reply, or resolution receives its own preview and approval; a single-entry draft may omit `--entry`.

When drafting reviewer replies for user approval in chat, label each section with a quoted excerpt of the reviewer comment instead of only the numeric thread id. Example:

```text
Draft replies for approval:

Thread "What is the role of this and why do we allow undefined values in the array input?"
```

Edit `review-draft.json` by filling `draftComments` entries with one of these types:

- `general`: new top-level PR thread with `text`
- `inline`: new file/line comment with `path`, `line`, and `text`
- `reply`: reply to an existing thread with `threadId` and `text`
- `edit`: update an existing comment with `threadId`, `commentId`, and `text`
- `resolve`: resolve a thread with `threadId`

---

## 7. After Review: Move to "In Testing" & Assign to QA

Once the PR has no blocking comments and is approved:

```bash
./sg handoff-to-qa <id>
./sg handoff-to-qa <id> --apply <PLAN_ID>
```

The first invocation previews the transition and assignee. The approved invocation uses the configured `AZURE_DEVOPS_QA_USER` value unless overridden with `--qa`.

The raw REST form is:

```bash
ado_curl -s -X PATCH \
  "https://dev.azure.com/${ORG}/${PROJECT}/_apis/wit/workitems/${ITEM_ID}?api-version=7.1" \
  -H "Content-Type: application/json-patch+json" \
  -d "[
    {\"op\":\"replace\",\"path\":\"/fields/System.State\",\"value\":\"In Testing\"},
    {\"op\":\"replace\",\"path\":\"/fields/System.AssignedTo\",\"value\":\"${QA_ASSIGNEE}\"}
  ]"
```

---

## 8. Add a Comment to a Work Item

Use this when context is unclear to ask questions before starting:

```bash
ado_curl -s -X POST \
  "https://dev.azure.com/${ORG}/${PROJECT}/_apis/wit/workitems/${ITEM_ID}/comments?api-version=7.1-preview.3" \
  -H "Content-Type: application/json" \
  -d "{\"text\": \"<your question or clarification request here>\"}"
```

---

## 9. Check PR Comments (Poll for Review Feedback)

```bash
PR_ID=<pull-request-id>

ado_curl -s \
  "https://dev.azure.com/${ORG}/${PROJECT}/_apis/git/repositories/${REPO_ID}/pullrequests/${PR_ID}/threads?api-version=7.1" | python3 -c "
import json, sys
data = json.load(sys.stdin)
for thread in data.get('value', []):
    if thread.get('isDeleted'):
        continue
    status = thread.get('status', '')
    for comment in thread.get('comments', []):
        if comment.get('commentType') == 'system':
            continue
        author = comment.get('author', {}).get('displayName', '')
        text = comment.get('content', '').strip()[:300]
        print(f'[{status}] {author}: {text}')
        print()
"
```

---

## 10. Full State Transition Summary

```
New / Ready for development
        │  (context clear)
        ▼
   In Progress   ← branch created, work started
        │  (tested locally, PR created)
        ▼
   In Review     ← PR open, waiting for reviewers
        │  (approved / no blocking comments)
        ▼
   In Testing    ← assigned to QA
        │  (QA passes)
        ▼
     Done
```

If at any point context is **unclear**:
- Add a comment (Section 8)
- Leave the item in its current state
- Re-check after a response is received

For Copilot-driven work, the agent should comment on the item and wait instead of starting speculative implementation.

---

## Useful API Reference

| Purpose | Method | Endpoint |
|---|---|---|
| List iterations | GET | `/{project}/{team}/_apis/work/teamsettings/iterations` |
| WIQL query | POST | `/{project}/_apis/wit/wiql` |
| Get work item(s) | GET | `/{project}/_apis/wit/workitems?ids=...` |
| Update work item | PATCH | `/{project}/_apis/wit/workitems/{id}` |
| Add comment | POST | `/{project}/_apis/wit/workitems/{id}/comments` |
| List repos | GET | `/{project}/_apis/git/repositories` |
| Create PR | POST | `/{project}/_apis/git/repositories/{repoId}/pullrequests` |
| Get PR threads | GET | `/{project}/_apis/git/repositories/{repoId}/pullrequests/{prId}/threads` |

All endpoints use base URL: `https://dev.azure.com/<your-org>/`  
All requests require an Azure DevOps bearer header; the `ado_curl` helper above supplies it without expanding the token into curl's process arguments.
API version: `7.1`
