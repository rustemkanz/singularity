Use Singularity when working with Azure DevOps items. For delivery work in another repository, run an installed `sg` command from that target checkout; use this repository's `./sg` wrapper only when this is the target repository.

Primary workflow reference: `./azure-devops-workflow.md`.

When the user asks Copilot to pick up, inspect, or progress ADO work, follow this process:

1. Verify the target checkout with `git rev-parse --show-toplevel`, `git remote -v`, and `git branch --show-current`.
2. Use `sg sprint` to identify the active sprint when needed.
3. Use `sg ready-items` or `sg pick-next` to find assigned open items in `New` or `Ready for development`.
4. Use `sg show <id>` to inspect the work item before starting implementation.
5. If screenshots, attachments, or pasted UI context may matter, use `sg attachments <id>` so local context includes the work item media.
6. If description, acceptance criteria, or expected behavior are unclear, do not start coding yet.
7. In that unclear case, preview a clarification with `sg comment <id> "..."`; show the exact text, get explicit approval, and only then repeat it with `--apply`.
8. If the work item is clear enough to implement, use plan-only `sg start-work <id>` or preview `sg start <id>` to inspect the same canonical branch/state plan. `start-work` never changes state; run `sg start <id> --apply` only after explicit approval.
9. Create the exact planned branch in the verified target checkout, then implement and run the narrowest relevant validation.
10. Once the change is ready for review, preview the PR with `sg create-pr <id> --repo <repo> --source <branch>`. Show the full payload and create it with `--apply` only after explicit approval.
11. Preview `sg review <id>` and apply the review transition only after separate approval with `sg review <id> --apply`.
12. Hand off to QA only after the user separately requests and approves that external action.

Operational expectations:

- Copilot is the worker. Prefer taking the next safe action instead of telling the user to run the helper manually.
- Treat the user's implementation request as approval for local inspection, edits, and tests, not for external mutations.
- Before every external mutation, show the exact action and payload and obtain explicit approval for that action. Approval does not carry over to the next mutation.
- Use the CLI instead of rebuilding raw Azure DevOps REST calls unless the CLI is missing a required feature.
- If a needed ADO action is missing from `sg.py`, extend the script first when practical, then use it.
- When changing user-visible helper behavior or workflow steps, update `CHANGELOG.md` in the same change.
- Before implementation, always inspect the work item context. Do not start coding from title alone.
- For UI-heavy work or bugs, prefer `sg attachments <id>` in addition to `show` so screenshots are available locally before implementation.
- If the item lacks enough detail, ask through the work item comments before making speculative changes.
- Keep state transitions aligned with actual engineering progress. Do not move items forward prematurely.
- Prefer small, reviewable branches and changes scoped to a single work item.
- When creating or updating a PR, link it clearly to the work item.
- Always pass explicit `--repo` and `--source` values to PR commands; do not infer them from the Singularity source checkout.
- `comment`, `start`, `create-pr`, `prepare-review`, and `review` preview by default. Add `--apply` only after approval; `--dry-run` remains a compatibility alias for PR previews. Treat older mutators without `--apply` as immediate and do not invoke them before approval.
- Keep the helper portable by preferring environment variables over hard-coded organization, project, assignee, and QA settings.
- When drafting or posting AI-authored text to external systems such as ADO work item comments or PR threads, keep the body natural and concise.
- Use the short trailing disclosure `AI-assisted via Copilot.` instead of bracket-heavy prefixes unless the target system provides its own machine-readable AI attribution field.
- When drafting PR replies for user approval, prefer readable headers based on the reviewer text, for example `Thread "What is the role of this and why do we allow undefined values in the array input?"`, instead of `Thread 336140`.

If this repository is copied to another machine or project, configure `AZURE_DEVOPS_ORG`, `AZURE_DEVOPS_PROJECT`, `AZURE_DEVOPS_TEAM_ID`, `AZURE_DEVOPS_USER`, and `AZURE_DEVOPS_QA_USER` for that environment before using the workflow.
