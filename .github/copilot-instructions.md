Use the local Singularity helper at `./sg` when working with Azure DevOps items in this project.

Primary workflow reference: `./azure-devops-workflow.md`.

When the user asks Copilot to pick up, inspect, or progress ADO work, follow this process:

1. Use `./sg sprint` to identify the active sprint when needed.
2. Use `./sg ready-items` or `./sg pick-next` to find assigned open items in `New` or `Ready for development`.
3. Use `./sg show <id>` to inspect the work item before starting implementation.
4. If screenshots, attachments, or pasted UI context may matter, use `./sg attachments <id>` so local context includes the work item media. This command downloads image evidence by default into `./.sg-artifacts/work-item-<id>/`.
5. If description, acceptance criteria, or expected behavior are unclear, do not start coding yet.
6. In that unclear case, add a clarification comment with `./sg comment <id> "..."` and explain the blocker to the user.
7. If the work item is clear enough to implement, move it to `In Progress` with `./sg start <id>`.
8. After starting work, create a branch for the item if one does not already exist.
9. Then inspect the repository, implement the requested change, and run the narrowest relevant validation.
10. Once local validation is complete and the change is ready for review, create the PR with `./sg create-pr <id> --repo <repo>` and then move the work item to `In Review` with `./sg review <id>`.
11. When review is complete and the item should be handed to QA, use `./sg handoff-to-qa <id>` or `./sg testing <id> --qa <email>`.

Operational expectations:

- Copilot is the worker. Prefer taking the next concrete action instead of telling the user to run the helper manually.
- Use the local CLI instead of rebuilding raw Azure DevOps REST calls unless the CLI is missing a required feature.
- If a needed ADO action is missing from `sg.py`, extend the script first when practical, then use it.
- When changing user-visible helper behavior or workflow steps, update `CHANGELOG.md` in the same change.
- Before implementation, always inspect the work item context. Do not start coding from title alone.
- For UI-heavy work or bugs, prefer `./sg attachments <id>` in addition to `show` so screenshots are available locally before implementation.
- If the item lacks enough detail, ask through the work item comments before making speculative changes.
- Keep state transitions aligned with actual engineering progress. Do not move items forward prematurely.
- Prefer small, reviewable branches and changes scoped to a single work item.
- When creating or updating a PR, link it clearly to the work item.
- Prefer using `./sg create-pr ... --dry-run` before the first real PR in a repository to confirm repo, branch, and payload are correct.
- Keep the helper portable by preferring environment variables over hard-coded organization, project, assignee, and QA settings.
- When drafting or posting AI-authored text to external systems such as ADO work item comments or PR threads, keep the body natural and concise.
- Use the short trailing disclosure `AI-assisted via Copilot.` instead of bracket-heavy prefixes unless the target system provides its own machine-readable AI attribution field.
- When drafting PR replies for user approval, prefer readable headers based on the reviewer text, for example `Thread "What is the role of this and why do we allow undefined values in the array input?"`, instead of `Thread 336140`.

If this repository is copied to another machine or project, configure `AZURE_DEVOPS_ORG`, `AZURE_DEVOPS_PROJECT`, `AZURE_DEVOPS_TEAM_ID`, `AZURE_DEVOPS_USER`, and `AZURE_DEVOPS_QA_USER` for that environment before using the workflow.