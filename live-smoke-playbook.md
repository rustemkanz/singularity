# Live Smoke Playbook

This document captures a reusable live-validation scenario for Singularity.

Use it for two purposes:

- validate that a provider integration still works end to end against a real project
- demonstrate the operator workflow Singularity can cover with explicit approval points

Keep the scenario stable even when the concrete provider changes. The workflow contract matters more than the exact project, repository, or field names.

## Why This Exists

This playbook is the canonical reusable live-validation scenario.

It defines one disposable but realistic walkthrough that can be repeated, audited, and later adapted to additional providers such as GitLab.

## Scenario Contract

Every live smoke run should cover the same high-value workflow slices in the same order.

1. Choose or create a disposable work item or equivalent tracking artifact.
2. Inspect the item and confirm the helper can surface the essential context.
3. Post one low-risk mutation such as a comment.
4. Start work and verify the provider-specific workflow state transition.
5. Create a disposable branch in the target repository.
6. Prepare and inspect the change-request payload before creating it.
7. Create the real change request.
8. Read the created change request back through the helper.
9. Create, reply to, edit, and resolve one review thread.
10. Move the tracking artifact to the review-equivalent state.
11. Clean up the disposable artifacts.

If a provider does not yet support one of these mutations, stop at the last supported slice and record that boundary explicitly.

## Output To Record

Every live smoke run should capture the resulting disposable artifact ids so the run can be reviewed and cleaned up safely.

- tracking item id
- tracking comment id
- repository name
- disposable branch name
- change-request id or URL
- review thread id
- reply comment id
- final workflow state names observed in the target project
- cleanup outcome

## Safety Rules

- Use clearly disposable titles such as `Singularity smoke test - do not implement`.
- Use disposable branches that encode the artifact id.
- Keep the changed file small and obviously synthetic.
- Verify the target checkout with `git rev-parse --show-toplevel`, `git remote -v`, and `git branch --show-current`, then run an installed `sg` from that checkout.
- Pass explicit `--repo` and `--source` values for change-request operations.
- Run every plan-first mutation without `--apply`, review the output, and obtain explicit approval before repeating it with `--apply`.
- Prefer projects or repos where comments, branches, PRs, and workflow transitions are safe to exercise.
- Clean up the PR or merge request, branch, and work item after validation unless the reviewer explicitly wants them left in place.
- Record provider-specific workflow state names instead of assuming literals such as `In Progress` or `Done`.

## Provider Configuration

This playbook is provider-neutral at the scenario level, but each reference run uses provider-specific configuration.

- Azure DevOps runs use the `AZURE_DEVOPS_*` settings because the current mutation-capable implementation is still broadest on Azure DevOps.
- GitLab runs use a target repository and merge-request URL, and may also use `GITLAB_TOKEN` when the target project is private or the instance requires authenticated API access. Self-hosted GitLab can also override `GITLAB_BASE_URL` for helper-driven merge-request creation.
- Future providers should add their own reference-run prerequisites under this same playbook instead of forking the scenario contract.

## ADO Reference Run

The current implementation supports this scenario most fully in Azure DevOps.

### Preconditions

- `sg doctor` is clean enough to run mutations from the target checkout
- for the Azure DevOps reference run only, `AZURE_DEVOPS_ORG`, `AZURE_DEVOPS_PROJECT`, `AZURE_DEVOPS_TEAM_ID`, `AZURE_DEVOPS_USER`, and `AZURE_DEVOPS_QA_USER` are set
- for the Azure DevOps reference run only, the target repository and current source branch have been verified and will be passed explicitly
- a disposable work item can be created or an existing safe test item is available

### Suggested Flow

1. Inspect configuration and candidate-item behavior.

```bash
./sg doctor
./sg ready-items --json
./sg pick-next
```

2. Inspect the disposable work item.

```bash
./sg show <item-id>
./sg comments <item-id>
./sg context <item-id>
```

3. Post a smoke-test comment.

```bash
sg comment <item-id> "Singularity smoke test comment. No product work requested. AI-assisted via Copilot."
sg comment <item-id> "Singularity smoke test comment. No product work requested. AI-assisted via Copilot." --apply
```

4. Prepare work metadata and start work.

```bash
sg start-work <item-id>
sg start <item-id> --branch test/<item-id>-singularity-smoke
sg start <item-id> --branch test/<item-id>-singularity-smoke --apply
```

5. Create the disposable branch in the real work repo and add one synthetic file.

```bash
git switch -c test/<item-id>-singularity-smoke
printf 'singularity smoke test\n' > smoke-test-<item-id>.txt
git add smoke-test-<item-id>.txt
git commit -m "test: <item-id> singularity smoke"
git push -u origin test/<item-id>-singularity-smoke
```

6. Preview and create the PR after approval.

```bash
sg create-pr <item-id> --repo <repo> --source test/<item-id>-singularity-smoke
sg create-pr <item-id> --repo <repo> --source test/<item-id>-singularity-smoke --apply
```

7. Read the PR back through the helper.

```bash
./sg pr-analyze --url <ado-pr-url>
./sg pr-files --url <ado-pr-url>
./sg pr-file --url <ado-pr-url> --path /smoke-test-<item-id>.txt
./sg pr-comments --url <ado-pr-url> --json
./sg pr-statuses --url <ado-pr-url>
```

8. Exercise the review-thread mutation path.

```bash
./sg pr-comment --url <ado-pr-url> "Top-level smoke test thread. AI-assisted via Copilot."
./sg pr-reply --url <ado-pr-url> --thread <thread-id> "Reply smoke test. AI-assisted via Copilot."
./sg pr-edit-comment --url <ado-pr-url> --thread <thread-id> --comment <comment-id> "Edited smoke test reply. AI-assisted via Copilot."
./sg pr-resolve --url <ado-pr-url> --thread <thread-id>
```

9. Move the work item into review.

```bash
sg review <item-id>
sg review <item-id> --apply
```

10. Clean up the disposable artifacts.

Expected cleanup today:

- abandon the PR
- delete the remote branch
- switch the local repo away from the disposable branch and delete it locally
- move the work item to its real terminal state for that project

### ADO-Specific Lessons Already Confirmed

- Some projects have no active sprint, so backlog fallback matters for candidate-item discovery.
- State names vary across projects. Record the actual resolved state names seen in the target project.
- Cleanup is still partly manual or provider-internal because the helper does not yet expose a first-class teardown command.

## GitLab Reference Run

The current GitLab slice is merge-request-centered, with a public read path and a narrower authenticated issue-plus-merge-request mutation path.

### Preconditions

- a public GitLab merge-request URL is available, or `GITLAB_TOKEN` is set for private or restricted targets
- no `ADO_*` configuration is required for GitLab review commands
- provide a disposable GitLab issue or other tracking id if you want `create-pr --provider gitlab` to carry an issue reference in its default title and description

### Suggested Flow

1. Run the public read-only inspection commands.

```bash
./sg pr-analyze --url <gitlab-mr-url>
./sg pr-files --url <gitlab-mr-url>
./sg pr-file --url <gitlab-mr-url> --path <repo-path>
./sg pr-diff --url <gitlab-mr-url> --path <repo-path>
```

2. If `GITLAB_TOKEN` is configured and the target instance allows it, also run:

```bash
./sg show <issue-id> --provider gitlab --repo <group/project>
./sg comments <issue-id> --provider gitlab --repo <group/project>
sg comment <issue-id> --provider gitlab --repo <group/project> "GitLab issue smoke comment. AI-assisted via Copilot."
sg comment <issue-id> --provider gitlab --repo <group/project> "GitLab issue smoke comment. AI-assisted via Copilot." --apply
sg start-work <issue-id> --provider gitlab --repo <group/project>
sg start <issue-id> --provider gitlab --repo <group/project> --branch issue/<issue-id>-smoke
sg start <issue-id> --provider gitlab --repo <group/project> --branch issue/<issue-id>-smoke --apply
sg create-pr <issue-id> --provider gitlab --repo <group/project> --source <disposable-branch> --work-item-title "Disposable smoke issue"
sg create-pr <issue-id> --provider gitlab --repo <group/project> --source <disposable-branch> --work-item-title "Disposable smoke issue" --apply
./sg pr-comments --url <gitlab-mr-url>
./sg pr-statuses --url <gitlab-mr-url>
./sg pr-comment --url <gitlab-mr-url> "Top-level GitLab smoke thread. AI-assisted via Copilot."
./sg pr-inline-comment --url <gitlab-mr-url> --path /smoke-test-<issue-id>.txt --line 1 "Inline GitLab smoke thread. AI-assisted via Copilot."
./sg pr-reply --url <gitlab-mr-url> --thread <thread-id> "Reply smoke test. AI-assisted via Copilot."
./sg pr-edit-comment --url <gitlab-mr-url> --thread <thread-id> --comment <comment-id> "Edited smoke test reply. AI-assisted via Copilot."
./sg pr-resolve --url <gitlab-mr-url> --thread <thread-id>
sg review <issue-id> --provider gitlab --repo <group/project>
sg review <issue-id> --provider gitlab --repo <group/project> --apply
./sg testing <issue-id> --provider gitlab --repo <group/project> --qa <gitlab-username>
./sg cleanup-artifacts --provider gitlab --repo <group/project> --issue <issue-id> --mr <mr-id> --branch <disposable-branch>
```

### GitLab-Specific Lessons Already Confirmed

- GitLab-specific runs do not use the `AZURE_DEVOPS_*` configuration used by the ADO reference run.
- Public merge requests can now be inspected live without `GITLAB_TOKEN` for `pr-analyze`, `pr-files`, `pr-file`, and `pr-diff`.
- GitLab can still return `401 Unauthorized` for discussion-thread and commit-status APIs on otherwise public merge requests, so `pr-comments` and `pr-statuses` may still need `GITLAB_TOKEN`.
- With access to the project, `show`, `comments`, and `start-work` are read-only; `start-work` prints the canonical issue branch/PR plan. `comment` previews by default and posts only with `--apply` when you pass `--provider gitlab --repo <group/project>`.
- With authenticated access to a writable project, `start`, `review`, and `testing` can now move GitLab issues through helper-owned workflow labels, and `testing` can also assign a GitLab username.
- With authenticated access to a writable project and source branch, `create-pr --provider gitlab --repo <group/project>` can now open a merge request.
- With authenticated access to a writable merge request, top-level discussion creation, inline diff comments, reply, note edit, and thread resolution are now viable.
- `cleanup-artifacts --provider gitlab` can now close disposable issues and merge requests and delete disposable branches in one helper-driven step.
- GitLab cleanup can leave helper workflow labels such as `sg:state:in-testing` on a now-closed issue, so issue rendering must let the provider's terminal `closed` state override those labels.
- GitLab's issue APIs did not immediately surface a newly created related merge request during live validation, so `context` may still show no linked PRs even when the MR description contains `Closes #<issue-id>`.

## Recommended Future Enhancements

1. Add a provider-neutral cleanup command so Azure DevOps and future providers can match GitLab's current `cleanup-artifacts` coverage.
2. Add a scripted live-smoke recorder that emits a JSON bundle of created ids and final states.
3. Add a provider-neutral live-smoke command that runs only the slices supported by the selected provider and prints any unsupported boundaries explicitly.
