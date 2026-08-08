import urllib.parse

from app_config import API_VER, DEFAULT_REPO, ORG, PROJECT
from errors import CliError
from git_client import current_git_branch, infer_git_repository_ref
from providers.azure_devops.http import api
from providers.azure_devops.pull_requests import (
    build_inline_thread_payload,
    build_change_request,
    build_pr_thread_payload,
    collect_pr_analysis_bundle,
    collect_pr_change_context,
    collect_pr_review_threads,
    collect_pr_reviewers,
    create_pr_thread,
    default_reply_parent_comment_id,
    fetch_pr_comment,
    fetch_pr_file_content,
    fetch_pr_statuses,
    fetch_pr_thread,
    fetch_pr_threads,
    fetch_pull_request,
    find_pr_change_for_path,
    list_pull_requests,
    parse_azure_devops_pr_url,
    serialize_pr_comment,
    resolve_pull_request,
    serialize_pr_status,
    thread_status_name,
)
from providers.azure_devops.work_items import fetch_work_item_title
from providers.azure_devops.work_item_context import list_repositories
from providers.interfaces import (
    RepositoryRef,
    ReviewAnalysis,
    ReviewContext,
    ReviewMutationPreview,
    ReviewMutationResult,
    ReviewProvider,
)
from workflow_models import ChangeRequest, ExistingReviewComment, ReviewChangeSummary, ReviewFileChange


def _match_repository(repos: list[dict], repo_ref: str | None) -> dict | None:
    if repo_ref is None:
        return None
    for repo in repos:
        if repo_ref in (repo.get("id"), repo.get("name")):
            return repo
    return None


def _repository_resolution_candidates(repo_ref: str | None) -> list[tuple[str, str]]:
    if repo_ref is not None:
        return [(repo_ref, "--repo")]

    candidates: list[tuple[str, str]] = []
    current_repo_ref = infer_git_repository_ref()
    if current_repo_ref:
        candidates.append((current_repo_ref, "current git repo"))
    if DEFAULT_REPO and DEFAULT_REPO not in {candidate for candidate, _source in candidates}:
        candidates.append((DEFAULT_REPO, "AZURE_DEVOPS_DEFAULT_REPO"))
    return candidates


class AzureDevOpsReviewProvider(ReviewProvider):
    def __init__(self, token: str):
        self.token = token

    def list_repositories(self) -> list[RepositoryRef]:
        return [
            RepositoryRef(
                id=repo.get("id"),
                name=repo.get("name") or "?",
                project=(repo.get("project") or {}).get("name"),
            )
            for repo in list_repositories(self.token)
        ]

    def _resolve_repository(
        self,
        repo_ref: str | None,
        *,
        project_name: str | None = None,
        org_name: str | None = None,
    ) -> dict:
        repos = list_repositories(self.token, project_name=project_name, org_name=org_name)
        if not repos:
            raise CliError("No repositories found in the configured ADO project.")

        candidates = _repository_resolution_candidates(repo_ref)
        for candidate, _source in candidates:
            repo = _match_repository(repos, candidate)
            if repo:
                return repo

        if repo_ref is not None:
            raise CliError(f"ERROR: Repository '{repo_ref}' not found. Use './sg repos' to list options.")

        if len(repos) == 1:
            return repos[0]

        if candidates:
            attempted = ", ".join(f"{source} '{candidate}'" for candidate, source in candidates)
            raise CliError(
                f"ERROR: Could not resolve a repository from {attempted}. "
                "Pass --repo with repo name or repo id, or set AZURE_DEVOPS_DEFAULT_REPO."
            )

        raise CliError(
            "ERROR: Multiple repositories found. Pass --repo with repo name or repo id, "
            "or set AZURE_DEVOPS_DEFAULT_REPO."
        )

    def _fetch_source_ref_tip(self, repository: RepositoryRef, source_ref_name: str) -> str:
        if repository.id is None:
            raise CliError("ERROR: Repository id is required to resolve a source branch.")
        filter_value = source_ref_name[len("refs/"):] if source_ref_name.startswith("refs/") else source_ref_name
        query = urllib.parse.urlencode({"filter": filter_value, "api-version": API_VER})
        url = (
            f"https://dev.azure.com/{ORG}/{urllib.parse.quote(PROJECT)}/_apis/git/repositories/"
            f"{urllib.parse.quote(repository.id, safe='')}/refs?{query}"
        )
        refs = api(self.token, "GET", url).get("value") or []
        matching_refs = [ref for ref in refs if ref.get("name") == source_ref_name]
        if len(matching_refs) != 1 or not matching_refs[0].get("objectId"):
            raise CliError(
                f"ERROR: Azure DevOps source branch '{source_ref_name}' was not found in "
                f"repository '{repository.name}'."
            )
        return matching_refs[0]["objectId"]

    def resolve_review_context(
        self,
        *,
        repo_ref: str | None,
        change_request_id: int | None,
        source_branch: str | None,
        url: str | None,
        project_name: str | None = None,
        org_name: str | None = None,
    ) -> ReviewContext:
        if url and any(value is not None for value in (repo_ref, change_request_id, source_branch)):
            raise CliError("ERROR: Use either --url or --repo/--pr/--source, not both.")

        if url:
            pr_ref = parse_azure_devops_pr_url(url)
            repo = self._resolve_repository(
                pr_ref["repo"],
                project_name=pr_ref["project"],
                org_name=pr_ref["org"],
            )
            pr = fetch_pull_request(
                self.token,
                repo["id"],
                pr_ref["pr_id"],
                project_name=pr_ref["project"],
                org_name=pr_ref["org"],
            )
            return ReviewContext(
                organization=pr_ref["org"],
                project=pr_ref["project"],
                repository=RepositoryRef(id=repo.get("id"), name=repo.get("name") or "?"),
                change_request=build_change_request(
                    repo,
                    pr,
                    project_name=pr_ref["project"],
                    org_name=pr_ref["org"],
                ),
            )

        resolved_project = project_name or PROJECT
        resolved_org = org_name or ORG
        repo = self._resolve_repository(repo_ref, project_name=resolved_project, org_name=resolved_org)
        pr = resolve_pull_request(
            self.token,
            repo,
            change_request_id,
            source_branch,
            project_name=resolved_project,
            org_name=resolved_org,
        )
        return ReviewContext(
            organization=resolved_org,
            project=resolved_project,
            repository=RepositoryRef(id=repo.get("id"), name=repo.get("name") or "?"),
            change_request=build_change_request(repo, pr, project_name=resolved_project, org_name=resolved_org),
        )

    def _fetch_pull_request(self, context: ReviewContext) -> dict:
        if context.repository.id is None or context.change_request.id is None:
            raise CliError("ERROR: Review context is missing repository or pull request identity.")
        return fetch_pull_request(
            self.token,
            context.repository.id,
            context.change_request.id,
            project_name=context.project,
            org_name=context.organization,
        )

    def _context_repo(self, context: ReviewContext) -> dict:
        return {
            "id": context.repository.id,
            "name": context.repository.name,
        }

    def _require_context_ids(self, context: ReviewContext) -> tuple[str, int]:
        if context.repository.id is None or context.change_request.id is None:
            raise CliError("ERROR: Review context is missing repository or pull request identity.")
        return context.repository.id, context.change_request.id

    def analyze_change_request(self, context: ReviewContext) -> ReviewAnalysis:
        repo = {"id": context.repository.id, "name": context.repository.name}
        pr = self._fetch_pull_request(context)
        analysis = collect_pr_analysis_bundle(self.token, context.organization, context.project, repo, pr)
        return ReviewAnalysis(
            context=context,
            reviewers=collect_pr_reviewers(pr),
            change_summary=ReviewChangeSummary(
                iteration=analysis["changeSummary"].get("iteration"),
                count=analysis["changeSummary"].get("count") or 0,
                by_type=dict(analysis["changeSummary"].get("byType") or {}),
            ),
            files=[
                ReviewFileChange(
                    path=file_entry.get("path") or "",
                    change_type=file_entry.get("changeType") or "",
                )
                for file_entry in analysis.get("files") or []
            ],
            existing_comments=[
                ExistingReviewComment(
                    thread_id=comment.get("threadId"),
                    comment_id=comment.get("commentId"),
                    thread_status=comment.get("threadStatus") or "",
                    file_path=comment.get("file"),
                    line=comment.get("line"),
                    author=comment.get("author") or "",
                    content=comment.get("content") or "",
                )
                for comment in analysis.get("existingComments") or []
            ],
        )

    def list_review_threads(self, context: ReviewContext, *, unresolved_only: bool = False):
        if context.repository.id is None or context.change_request.id is None:
            return []
        return collect_pr_review_threads(
            fetch_pr_threads(
                self.token,
                context.repository.id,
                context.change_request.id,
                project_name=context.project,
                org_name=context.organization,
            ),
            unresolved_only=unresolved_only,
        )

    def list_statuses(self, context: ReviewContext):
        if context.repository.id is None or context.change_request.id is None:
            return []
        return [
            serialize_pr_status(status)
            for status in fetch_pr_statuses(
                self.token,
                context.repository.id,
                context.change_request.id,
                project_name=context.project,
                org_name=context.organization,
            )
        ]

    def get_change_request_file_content(
        self,
        context: ReviewContext,
        *,
        file_path: str,
        version: str,
    ) -> str | None:
        pr = self._fetch_pull_request(context)
        return fetch_pr_file_content(
            self.token,
            self._context_repo(context),
            pr,
            file_path,
            version=version,
            project_name=context.project,
            org_name=context.organization,
        )

    def prepare_change_request(
        self,
        *,
        work_item_id: int,
        repo_ref: str | None,
        source_branch: str | None,
        target_branch: str | None,
        title: str | None,
        description: str | None,
        work_item_title: str | None,
    ) -> tuple[RepositoryRef, dict]:
        repo = self._resolve_repository(repo_ref)
        source = source_branch or current_git_branch() or ""
        if source.startswith("refs/heads/"):
            source = source[len("refs/heads/"):]
        if not source or source == "HEAD":
            raise CliError("ERROR: Could not resolve a source branch for Azure DevOps pull-request creation.")
        target = target_branch or "main"
        resolved_title = title
        if not resolved_title:
            resolved_work_item_title = work_item_title or fetch_work_item_title(self.token, work_item_id)
            resolved_title = f"[{work_item_id}] {resolved_work_item_title}"
        request_body = {
            "title": resolved_title,
            "description": description or f"Closes #{work_item_id}",
            "sourceRefName": f"refs/heads/{source}",
            "targetRefName": f"refs/heads/{target}",
            "workItemRefs": [{"id": str(work_item_id)}],
        }
        repository = RepositoryRef(id=repo.get("id"), name=repo.get("name") or "?", project=PROJECT)
        source_ref_name = request_body["sourceRefName"]
        source_commit_id = self._fetch_source_ref_tip(repository, source_ref_name)
        return repository, {
            "request": {
                "method": "POST",
                "body": request_body,
            },
            "sourceRef": {
                "name": source_ref_name,
                "commitId": source_commit_id,
            },
        }

    def create_change_request(
        self,
        *,
        work_item_id: int,
        repo_ref: str | None,
        source_branch: str | None,
        target_branch: str | None,
        title: str | None,
        description: str | None,
        work_item_title: str | None,
    ) -> ChangeRequest:
        repository, payload = self.prepare_change_request(
            work_item_id=work_item_id,
            repo_ref=repo_ref,
            source_branch=source_branch,
            target_branch=target_branch,
            title=title,
            description=description,
            work_item_title=work_item_title,
        )
        return self.create_prepared_change_request(repository, payload)

    def create_prepared_change_request(
        self,
        repository: RepositoryRef,
        payload: dict,
    ) -> ChangeRequest:
        if repository.id is None:
            raise CliError("ERROR: Repository id is required to create a change request.")
        request = payload.get("request") or {}
        source_ref = payload.get("sourceRef") or {}
        request_body = request.get("body")
        source_ref_name = source_ref.get("name")
        expected_commit_id = source_ref.get("commitId")
        if request.get("method") != "POST" or not isinstance(request_body, dict):
            raise CliError("ERROR: Prepared Azure DevOps change request does not contain an exact POST body.")
        if (
            not isinstance(source_ref_name, str)
            or not isinstance(expected_commit_id, str)
            or request_body.get("sourceRefName") != source_ref_name
        ):
            raise CliError("ERROR: Prepared Azure DevOps change request has an invalid source-ref precondition.")
        current_commit_id = self._fetch_source_ref_tip(repository, source_ref_name)
        if current_commit_id != expected_commit_id:
            raise CliError(
                f"ERROR: Azure DevOps source branch '{source_ref_name}' moved after preview; "
                "generate and approve a fresh plan."
            )
        url = (
            f"https://dev.azure.com/{ORG}/{urllib.parse.quote(PROJECT)}/_apis/git/repositories/{repository.id}/pullrequests"
            f"?api-version={API_VER}"
        )
        result = api(self.token, "POST", url, request_body)
        created_commit_id = (result.get("lastMergeSourceCommit") or {}).get("commitId")
        if created_commit_id != expected_commit_id:
            pull_request_id = result.get("pullRequestId", "?")
            observed = created_commit_id or "not returned by Azure DevOps"
            raise CliError(
                f"ERROR: Azure DevOps created pull request {pull_request_id}, but its source commit "
                f"is {observed} instead of approved commit {expected_commit_id}. Do not retry; "
                "inspect the created pull request and source branch first."
            )
        return build_change_request(
            {"id": repository.id, "name": repository.name},
            result,
            project_name=PROJECT,
            org_name=ORG,
        )

    def prepare_review_comment(
        self,
        context: ReviewContext,
        *,
        text: str,
    ) -> ReviewMutationPreview:
        return ReviewMutationPreview(payload=build_pr_thread_payload(text))

    def create_review_comment(
        self,
        context: ReviewContext,
        *,
        text: str,
    ) -> ReviewMutationResult:
        preview = self.prepare_review_comment(context, text=text)
        return self.create_prepared_review_comment(context, preview)

    def create_prepared_review_comment(
        self,
        context: ReviewContext,
        preview: ReviewMutationPreview,
    ) -> ReviewMutationResult:
        repo_id, pr_id = self._require_context_ids(context)
        result = create_pr_thread(
            self.token,
            {"id": repo_id, "name": context.repository.name},
            {"pullRequestId": pr_id},
            preview.payload,
            project_name=context.project,
            org_name=context.organization,
        )
        return ReviewMutationResult(thread_id=result.get("id"))

    def prepare_inline_review_comment(
        self,
        context: ReviewContext,
        *,
        text: str,
        file_path: str,
        line: int,
        end_line: int | None,
        start_offset: int,
        end_offset: int | None,
    ) -> ReviewMutationPreview:
        repo_id, pr_id = self._require_context_ids(context)
        change_context = collect_pr_change_context(
            self.token,
            {"id": repo_id, "name": context.repository.name},
            {"pullRequestId": pr_id},
            project_name=context.project,
            org_name=context.organization,
        )
        change = find_pr_change_for_path(change_context["changes"], file_path)
        if not change:
            raise CliError(f"ERROR: File '{file_path if file_path.startswith('/') else '/' + file_path}' is not part of the PR diff.")
        change_tracking_id = change.get("changeTrackingId")
        if change_tracking_id is None:
            raise CliError(f"ERROR: No changeTrackingId found for '{file_path if file_path.startswith('/') else '/' + file_path}'.")
        payload = build_inline_thread_payload(
            text,
            file_path,
            line,
            end_line=end_line,
            start_offset=start_offset,
            end_offset=end_offset,
            iteration_id=change_context["iterationId"],
            change_tracking_id=change_tracking_id,
        )
        return ReviewMutationPreview(payload=payload)

    def create_inline_review_comment(
        self,
        context: ReviewContext,
        *,
        text: str,
        file_path: str,
        line: int,
        end_line: int | None,
        start_offset: int,
        end_offset: int | None,
    ) -> ReviewMutationResult:
        preview = self.prepare_inline_review_comment(
            context,
            text=text,
            file_path=file_path,
            line=line,
            end_line=end_line,
            start_offset=start_offset,
            end_offset=end_offset,
        )
        return self.create_prepared_inline_review_comment(context, preview)

    def create_prepared_inline_review_comment(
        self,
        context: ReviewContext,
        preview: ReviewMutationPreview,
    ) -> ReviewMutationResult:
        repo_id, pr_id = self._require_context_ids(context)
        result = create_pr_thread(
            self.token,
            {"id": repo_id, "name": context.repository.name},
            {"pullRequestId": pr_id},
            preview.payload,
            project_name=context.project,
            org_name=context.organization,
        )
        return ReviewMutationResult(thread_id=result.get("id"))

    def prepare_review_reply(
        self,
        context: ReviewContext,
        *,
        thread_id: int,
        text: str,
        parent_comment_id: int | None,
    ) -> ReviewMutationPreview:
        repo_id, pr_id = self._require_context_ids(context)
        thread = fetch_pr_thread(
            self.token,
            repo_id,
            pr_id,
            thread_id,
            project_name=context.project,
            org_name=context.organization,
        )
        resolved_parent = parent_comment_id or default_reply_parent_comment_id(thread)
        payload = {
            "parentCommentId": resolved_parent,
            "content": text,
            "commentType": "text",
        }
        return ReviewMutationPreview(payload=payload, thread_id=thread.get("id"))

    def create_review_reply(
        self,
        context: ReviewContext,
        *,
        thread_id: int,
        text: str,
        parent_comment_id: int | None,
    ) -> ReviewMutationResult:
        preview = self.prepare_review_reply(
            context,
            thread_id=thread_id,
            text=text,
            parent_comment_id=parent_comment_id,
        )
        return self.create_prepared_review_reply(context, preview)

    def create_prepared_review_reply(
        self,
        context: ReviewContext,
        preview: ReviewMutationPreview,
    ) -> ReviewMutationResult:
        repo_id, pr_id = self._require_context_ids(context)
        if preview.thread_id is None:
            raise CliError("ERROR: Prepared review reply is missing a thread id.")
        result = api(
            self.token,
            "POST",
            (
                f"https://dev.azure.com/{context.organization}/{urllib.parse.quote(context.project)}/_apis/git/repositories/"
                f"{repo_id}/pullrequests/{pr_id}/threads/{preview.thread_id}/comments?api-version={API_VER}"
            ),
            preview.payload,
        )
        return ReviewMutationResult(thread_id=preview.thread_id, comment_id=result.get("id"))

    def prepare_review_comment_edit(
        self,
        context: ReviewContext,
        *,
        thread_id: int,
        comment_id: int,
        text: str,
    ) -> ReviewMutationPreview:
        repo_id, pr_id = self._require_context_ids(context)
        thread = fetch_pr_thread(
            self.token,
            repo_id,
            pr_id,
            thread_id,
            project_name=context.project,
            org_name=context.organization,
        )
        comment = fetch_pr_comment(
            self.token,
            repo_id,
            pr_id,
            thread_id,
            comment_id,
            project_name=context.project,
            org_name=context.organization,
        )
        if comment.get("commentType") == "system":
            raise CliError("ERROR: System comments cannot be edited with this command.")
        payload = {
            "parentCommentId": comment.get("parentCommentId", 0),
            "content": text,
            "commentType": comment.get("commentType") or "text",
        }
        return ReviewMutationPreview(
            payload=payload,
            thread_id=thread.get("id"),
            comment_id=comment.get("id"),
            current_content=serialize_pr_comment(comment).content,
        )

    def edit_review_comment(
        self,
        context: ReviewContext,
        *,
        thread_id: int,
        comment_id: int,
        text: str,
    ) -> ReviewMutationResult:
        preview = self.prepare_review_comment_edit(
            context,
            thread_id=thread_id,
            comment_id=comment_id,
            text=text,
        )
        return self.edit_prepared_review_comment(context, preview)

    def edit_prepared_review_comment(
        self,
        context: ReviewContext,
        preview: ReviewMutationPreview,
    ) -> ReviewMutationResult:
        repo_id, pr_id = self._require_context_ids(context)
        if preview.thread_id is None or preview.comment_id is None:
            raise CliError("ERROR: Prepared comment edit is missing thread or comment identity.")
        result = api(
            self.token,
            "PATCH",
            (
                f"https://dev.azure.com/{context.organization}/{urllib.parse.quote(context.project)}/_apis/git/repositories/"
                f"{repo_id}/pullrequests/{pr_id}/threads/{preview.thread_id}/comments/{preview.comment_id}"
                f"?api-version={API_VER}"
            ),
            preview.payload,
            content_type="application/json",
        )
        return ReviewMutationResult(thread_id=preview.thread_id, comment_id=result.get("id"))

    def prepare_review_thread_resolution(
        self,
        context: ReviewContext,
        *,
        thread_id: int,
        status: str,
    ) -> ReviewMutationPreview:
        repo_id, pr_id = self._require_context_ids(context)
        thread = fetch_pr_thread(
            self.token,
            repo_id,
            pr_id,
            thread_id,
            project_name=context.project,
            org_name=context.organization,
        )
        return ReviewMutationPreview(
            payload={"status": status},
            thread_id=thread.get("id"),
            current_status=thread_status_name(thread),
        )

    def resolve_review_thread(
        self,
        context: ReviewContext,
        *,
        thread_id: int,
        status: str,
    ) -> ReviewMutationResult:
        preview = self.prepare_review_thread_resolution(
            context,
            thread_id=thread_id,
            status=status,
        )
        return self.resolve_prepared_review_thread(context, preview)

    def resolve_prepared_review_thread(
        self,
        context: ReviewContext,
        preview: ReviewMutationPreview,
    ) -> ReviewMutationResult:
        repo_id, pr_id = self._require_context_ids(context)
        if preview.thread_id is None:
            raise CliError("ERROR: Prepared thread resolution is missing a thread id.")
        result = api(
            self.token,
            "PATCH",
            (
                f"https://dev.azure.com/{context.organization}/{urllib.parse.quote(context.project)}/_apis/git/repositories/"
                f"{repo_id}/pullrequests/{pr_id}/threads/{preview.thread_id}?api-version={API_VER}"
            ),
            preview.payload,
            content_type="application/json",
        )
        return ReviewMutationResult(thread_id=preview.thread_id, status=thread_status_name(result))
