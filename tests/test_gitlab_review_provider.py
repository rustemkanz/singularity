import unittest
from unittest import mock
import ssl
import urllib.request
import urllib.error

from errors import CliError
from providers.gitlab.review_provider import GitLabReviewProvider, parse_gitlab_merge_request_url
from providers.interfaces import RepositoryRef, ReviewContext
import workflow_models


class GitLabReviewProviderTests(unittest.TestCase):
    def test_parse_gitlab_merge_request_url_supports_standard_path(self):
        parsed = parse_gitlab_merge_request_url(
            "https://gitlab.example.com/group/subgroup/project/-/merge_requests/123"
        )

        self.assertEqual(parsed["base_url"], "https://gitlab.example.com")
        self.assertEqual(parsed["project_path"], "group/subgroup/project")
        self.assertEqual(parsed["merge_request_iid"], 123)

    def test_parse_gitlab_merge_request_url_requires_https(self):
        with self.assertRaisesRegex(CliError, "must use HTTPS"):
            parse_gitlab_merge_request_url(
                "http://gitlab.example.com/group/project/-/merge_requests/123"
            )

    def test_parse_gitlab_merge_request_url_rejects_userinfo(self):
        with self.assertRaisesRegex(CliError, "must not contain user information"):
            parse_gitlab_merge_request_url(
                "https://gitlab.example.com@evil.example/group/project/-/merge_requests/123"
            )

    def test_resolve_review_context_from_url_builds_gitlab_context(self):
        provider = GitLabReviewProvider("token")
        merge_request = {
            "iid": 7,
            "project_id": 99,
            "title": "Improve pipeline rules",
            "state": "opened",
            "source_branch": "feature/rules",
            "target_branch": "main",
            "author": {"name": "Alice"},
            "web_url": "https://gitlab.example.com/group/project/-/merge_requests/7",
        }

        with mock.patch.object(provider, "_request_json", return_value=merge_request):
            context = provider.resolve_review_context(
                repo_ref=None,
                change_request_id=None,
                source_branch=None,
                url="https://gitlab.example.com/group/project/-/merge_requests/7",
            )

        self.assertEqual(context.organization, "https://gitlab.example.com")
        self.assertEqual(context.project, "group/project")
        self.assertEqual(context.repository.id, "99")
        self.assertEqual(context.change_request.provider, "gitlab")
        self.assertEqual(context.change_request.id, 7)

    def test_analyze_change_request_maps_gitlab_review_shapes(self):
        provider = GitLabReviewProvider("token")
        context = ReviewContext(
            organization="https://gitlab.example.com",
            project="group/project",
            repository=RepositoryRef(id="99", name="project", project="group/project"),
            change_request=workflow_models.ChangeRequest(
                id=7,
                title="Improve pipeline rules",
                status="opened",
                source_branch="feature/rules",
                target_branch="main",
                author="Alice",
                repo_name="project",
                repo_id="99",
                api_url="https://gitlab.example.com/api/v4/projects/group%2Fproject/merge_requests/7",
                browser_url="https://gitlab.example.com/group/project/-/merge_requests/7",
                provider="gitlab",
            ),
        )
        merge_request = {
            "iid": 7,
            "project_id": 99,
            "title": "Improve pipeline rules",
            "state": "opened",
            "source_branch": "feature/rules",
            "target_branch": "main",
            "author": {"name": "Alice"},
            "sha": "abc123",
            "web_url": "https://gitlab.example.com/group/project/-/merge_requests/7",
        }
        reviewers = [{"user": {"name": "Alex"}, "state": "reviewed"}]
        versions = [{"id": 11}, {"id": 10}]
        diffs = [
            {"new_path": "src/app.py", "old_path": "src/app.py", "new_file": False, "deleted_file": False, "renamed_file": False},
            {"new_path": "README.md", "old_path": "README.md", "new_file": True, "deleted_file": False, "renamed_file": False},
        ]
        discussions = [
            {
                "id": "thread-1",
                "notes": [
                    {
                        "id": 5,
                        "body": "Please revisit this.",
                        "author": {"name": "Sam"},
                        "system": False,
                        "resolvable": True,
                        "resolved": False,
                        "position": {"new_path": "src/app.py", "new_line": 12},
                    }
                ],
            }
        ]

        with mock.patch.object(provider, "_request_json", side_effect=[merge_request, reviewers, versions, diffs, discussions]):
            analysis = provider.analyze_change_request(context)

        self.assertEqual(analysis.context.change_request.provider, "gitlab")
        self.assertEqual(analysis.reviewers[0].vote_label, "reviewed")
        self.assertEqual(analysis.change_summary.iteration, 2)
        self.assertEqual(analysis.change_summary.by_type, {"edit": 1, "add": 1})
        self.assertEqual(analysis.files[0].path, "/src/app.py")
        self.assertEqual(analysis.existing_comments[0].thread_id, "thread-1")
        self.assertEqual(analysis.existing_comments[0].line, 12)

    def test_list_review_threads_filters_resolved_threads(self):
        provider = GitLabReviewProvider("token")
        context = ReviewContext(
            organization="https://gitlab.example.com",
            project="group/project",
            repository=RepositoryRef(id="99", name="project", project="group/project"),
            change_request=workflow_models.ChangeRequest(
                id=7,
                title="Improve pipeline rules",
                status="opened",
                source_branch="feature/rules",
                target_branch="main",
                author="Alice",
                repo_name="project",
                repo_id="99",
                api_url="https://gitlab.example.com/api/v4/projects/group%2Fproject/merge_requests/7",
                browser_url="https://gitlab.example.com/group/project/-/merge_requests/7",
                provider="gitlab",
            ),
        )
        discussions = [
            {
                "id": "resolved-thread",
                "notes": [
                    {
                        "id": 1,
                        "body": "done",
                        "author": {"name": "Alex"},
                        "system": False,
                        "resolvable": True,
                        "resolved": True,
                    }
                ],
            },
            {
                "id": "active-thread",
                "notes": [
                    {
                        "id": 2,
                        "body": "still open",
                        "author": {"name": "Sam"},
                        "system": False,
                        "resolvable": True,
                        "resolved": False,
                    }
                ],
            },
        ]

        with mock.patch.object(provider, "_request_json", return_value=discussions):
            threads = provider.list_review_threads(context, unresolved_only=True)

        self.assertEqual(len(threads), 1)
        self.assertEqual(threads[0].thread_id, "active-thread")

    def test_get_change_request_file_content_returns_none_for_missing_file(self):
        provider = GitLabReviewProvider("token")
        context = ReviewContext(
            organization="https://gitlab.example.com",
            project="group/project",
            repository=RepositoryRef(id="99", name="project", project="group/project"),
            change_request=workflow_models.ChangeRequest(
                id=7,
                title="Improve pipeline rules",
                status="opened",
                source_branch="feature/rules",
                target_branch="main",
                author="Alice",
                repo_name="project",
                repo_id="99",
                api_url="https://gitlab.example.com/api/v4/projects/group%2Fproject/merge_requests/7",
                browser_url="https://gitlab.example.com/group/project/-/merge_requests/7",
                provider="gitlab",
            ),
        )

        with mock.patch.object(provider, "_request_text", return_value=None):
            content = provider.get_change_request_file_content(context, file_path="src/missing.py", version="source")

        self.assertIsNone(content)

    def test_request_headers_omit_private_token_when_not_configured(self):
        provider = GitLabReviewProvider("")

        headers = provider._request_headers(accept="application/json")

        self.assertEqual(headers, {"Accept": "application/json"})

    def test_request_headers_do_not_expose_private_token_as_redirectable_header(self):
        provider = GitLabReviewProvider("token-123")

        headers = provider._request_headers(accept="application/json")

        self.assertEqual(headers, {"Accept": "application/json"})

    def test_authenticated_request_accepts_exact_normalized_self_hosted_origin(self):
        with mock.patch(
            "providers.gitlab.review_provider.GITLAB_BASE_URL",
            "https://GitLab.Example.COM:8443/",
        ):
            provider = GitLabReviewProvider("token-123")

        with mock.patch.object(provider, "_read_response_body", return_value="{}") as read_body:
            provider._request_json("https://gitlab.example.com:8443", "/projects/1")

        request = read_body.call_args.args[0]
        self.assertEqual(request.full_url, "https://gitlab.example.com:8443/api/v4/projects/1")
        self.assertEqual(request.headers, {"Accept": "application/json"})
        self.assertEqual(request.unredirected_hdrs["Private-token"], "token-123")

    def test_authenticated_request_rejects_urls_outside_configured_origin_before_network(self):
        with mock.patch(
            "providers.gitlab.review_provider.GITLAB_BASE_URL",
            "https://gitlab.example.com",
        ):
            provider = GitLabReviewProvider("token-123")

        hostile_origins = (
            "https://evil.example",
            "https://gitlab.example.com.evil.example",
            "https://gitlab.example.com:8443",
            "http://gitlab.example.com",
            "https://gitlab.example.com@evil.example",
        )
        with mock.patch("providers.gitlab.review_provider.OPENER") as opener:
            for origin in hostile_origins:
                with self.subTest(origin=origin):
                    with self.assertRaises(CliError):
                        provider._request_json(origin, "/projects/1")

        opener.open.assert_not_called()

    def test_anonymous_request_allows_another_public_https_gitlab_origin(self):
        provider = GitLabReviewProvider("")

        with mock.patch.object(provider, "_read_response_body", return_value="{}") as read_body:
            provider._request_json("https://gitlab.other.example", "/projects/1")

        self.assertEqual(
            read_body.call_args.args[0].full_url,
            "https://gitlab.other.example/api/v4/projects/1",
        )

    def test_authenticated_token_is_not_forwarded_by_redirect_handler(self):
        provider = GitLabReviewProvider("token-123")

        with mock.patch.object(provider, "_read_response_body", return_value="{}") as read_body:
            provider._request_json("https://gitlab.com", "/projects/1")

        request = read_body.call_args.args[0]
        redirected = urllib.request.HTTPRedirectHandler().redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "https://attacker.example/redirected",
        )
        self.assertNotIn("Private-token", redirected.headers)
        self.assertNotIn("Private-token", redirected.unredirected_hdrs)

    def test_prepare_change_request_builds_gitlab_merge_request_payload(self):
        provider = GitLabReviewProvider("token")
        project = {
            "id": 99,
            "path": "project",
            "path_with_namespace": "group/project",
            "default_branch": "main",
        }

        with mock.patch.object(provider, "_request_json", return_value=project) as request_json:
            with mock.patch("providers.gitlab.review_provider.current_git_branch", return_value="feature/rules"):
                repository, payload = provider.prepare_change_request(
                    work_item_id=17,
                    repo_ref="group/project",
                    source_branch=None,
                    target_branch=None,
                    title=None,
                    description=None,
                    work_item_title="Improve pipeline rules",
                )

        self.assertEqual(repository.id, "99")
        self.assertEqual(repository.project, "group/project")
        self.assertEqual(payload, {
            "title": "[17] Improve pipeline rules",
            "description": "Closes #17",
            "source_branch": "feature/rules",
            "target_branch": "main",
            "remove_source_branch": True,
        })
        request_json.assert_called_once_with(
            "https://gitlab.com",
            "/projects/group%2Fproject",
            allow_not_found=True,
        )

    def test_create_change_request_posts_gitlab_merge_request(self):
        provider = GitLabReviewProvider("token")
        project = {
            "id": 99,
            "path": "project",
            "path_with_namespace": "group/project",
            "default_branch": "main",
        }
        merge_request = {
            "iid": 7,
            "project_id": 99,
            "title": "[17] Improve pipeline rules",
            "state": "opened",
            "source_branch": "feature/rules",
            "target_branch": "main",
            "author": {"username": "alice"},
            "web_url": "https://gitlab.com/group/project/-/merge_requests/7",
        }

        with mock.patch.object(provider, "_request_json", side_effect=[project, merge_request]) as request_json:
            with mock.patch("providers.gitlab.review_provider.current_git_branch", return_value="feature/rules"):
                change_request = provider.create_change_request(
                    work_item_id=17,
                    repo_ref="group/project",
                    source_branch=None,
                    target_branch=None,
                    title=None,
                    description=None,
                    work_item_title="Improve pipeline rules",
                )

        self.assertEqual(change_request.id, 7)
        self.assertEqual(change_request.repo_id, "99")
        self.assertEqual(change_request.provider, "gitlab")
        self.assertEqual(request_json.call_args_list[1].args[:2], (
            "https://gitlab.com",
            "/projects/99/merge_requests",
        ))
        self.assertEqual(request_json.call_args_list[1].kwargs["method"], "POST")
        self.assertEqual(request_json.call_args_list[1].kwargs["form_data"], {
            "title": "[17] Improve pipeline rules",
            "description": "Closes #17",
            "source_branch": "feature/rules",
            "target_branch": "main",
            "remove_source_branch": True,
        })

    def test_prepare_review_comment_builds_gitlab_payload(self):
        provider = GitLabReviewProvider("token")
        context = ReviewContext(
            organization="https://gitlab.example.com",
            project="group/project",
            repository=RepositoryRef(id="99", name="project", project="group/project"),
            change_request=workflow_models.ChangeRequest(
                id=7,
                title="Improve pipeline rules",
                status="opened",
                source_branch="feature/rules",
                target_branch="main",
                author="Alice",
                repo_name="project",
                repo_id="99",
                api_url="https://gitlab.example.com/api/v4/projects/group%2Fproject/merge_requests/7",
                browser_url="https://gitlab.example.com/group/project/-/merge_requests/7",
                provider="gitlab",
            ),
        )

        preview = provider.prepare_review_comment(context, text="Please check this.")

        self.assertEqual(preview.payload, {"body": "Please check this."})

    def test_create_review_comment_posts_discussion(self):
        provider = GitLabReviewProvider("token")
        context = ReviewContext(
            organization="https://gitlab.example.com",
            project="group/project",
            repository=RepositoryRef(id="99", name="project", project="group/project"),
            change_request=workflow_models.ChangeRequest(
                id=7,
                title="Improve pipeline rules",
                status="opened",
                source_branch="feature/rules",
                target_branch="main",
                author="Alice",
                repo_name="project",
                repo_id="99",
                api_url="https://gitlab.example.com/api/v4/projects/group%2Fproject/merge_requests/7",
                browser_url="https://gitlab.example.com/group/project/-/merge_requests/7",
                provider="gitlab",
            ),
        )

        with mock.patch.object(provider, "_request_json", return_value={"id": "thread-1", "notes": [{"id": 5}]}) as request_json:
            result = provider.create_review_comment(context, text="Please check this.")

        self.assertEqual(result.thread_id, "thread-1")
        self.assertEqual(result.comment_id, 5)
        request_json.assert_called_once_with(
            "https://gitlab.example.com",
            "/projects/99/merge_requests/7/discussions",
            method="POST",
            form_data={"body": "Please check this."},
        )

    def test_prepare_inline_review_comment_builds_gitlab_diff_payload(self):
        provider = GitLabReviewProvider("token")
        context = ReviewContext(
            organization="https://gitlab.example.com",
            project="group/project",
            repository=RepositoryRef(id="99", name="project", project="group/project"),
            change_request=workflow_models.ChangeRequest(
                id=7,
                title="Improve pipeline rules",
                status="opened",
                source_branch="feature/rules",
                target_branch="main",
                author="Alice",
                repo_name="project",
                repo_id="99",
                api_url="https://gitlab.example.com/api/v4/projects/group%2Fproject/merge_requests/7",
                browser_url="https://gitlab.example.com/group/project/-/merge_requests/7",
                provider="gitlab",
            ),
        )
        diffs = [{
            "new_path": "src/app.py",
            "old_path": "src/app.py",
            "diff": "@@ -1,2 +1,3 @@\n alpha\n+beta\n+gamma\n",
        }]
        versions = [{
            "base_commit_sha": "base123",
            "start_commit_sha": "start123",
            "head_commit_sha": "head123",
        }]

        with mock.patch.object(provider, "_request_json", side_effect=[diffs, versions]):
            preview = provider.prepare_inline_review_comment(
                context,
                text="Inline note",
                file_path="src/app.py",
                line=2,
                end_line=3,
                start_offset=1,
                end_offset=None,
            )

        self.assertEqual(preview.payload["body"], "Inline note")
        self.assertEqual(preview.payload["position[base_sha]"], "base123")
        self.assertEqual(preview.payload["position[start_sha]"], "start123")
        self.assertEqual(preview.payload["position[head_sha]"], "head123")
        self.assertEqual(preview.payload["position[new_path]"], "src/app.py")
        self.assertEqual(preview.payload["position[new_line]"], 2)
        self.assertEqual(preview.payload["position[line_range][start][type]"], "new")
        self.assertEqual(preview.payload["position[line_range][start][new_line]"], 2)
        self.assertEqual(preview.payload["position[line_range][end][new_line]"], 3)
        self.assertEqual(
            preview.payload["position[line_range][start][line_code]"],
            "ac95095330a4a20b1f198c92db70024a198bb660__2",
        )
        self.assertEqual(
            preview.payload["position[line_range][end][line_code]"],
            "ac95095330a4a20b1f198c92db70024a198bb660__3",
        )

    def test_prepare_inline_review_comment_rejects_file_outside_diff(self):
        provider = GitLabReviewProvider("token")
        context = ReviewContext(
            organization="https://gitlab.example.com",
            project="group/project",
            repository=RepositoryRef(id="99", name="project", project="group/project"),
            change_request=workflow_models.ChangeRequest(
                id=7,
                title="Improve pipeline rules",
                status="opened",
                source_branch="feature/rules",
                target_branch="main",
                author="Alice",
                repo_name="project",
                repo_id="99",
                api_url="https://gitlab.example.com/api/v4/projects/group%2Fproject/merge_requests/7",
                browser_url="https://gitlab.example.com/group/project/-/merge_requests/7",
                provider="gitlab",
            ),
        )

        with mock.patch.object(provider, "_request_json", return_value=[]):
            with self.assertRaisesRegex(CliError, "is not part of the MR diff"):
                provider.prepare_inline_review_comment(
                    context,
                    text="Inline note",
                    file_path="src/missing.py",
                    line=2,
                    end_line=None,
                    start_offset=1,
                    end_offset=None,
                )

    def test_create_inline_review_comment_posts_discussion(self):
        provider = GitLabReviewProvider("token")
        context = ReviewContext(
            organization="https://gitlab.example.com",
            project="group/project",
            repository=RepositoryRef(id="99", name="project", project="group/project"),
            change_request=workflow_models.ChangeRequest(
                id=7,
                title="Improve pipeline rules",
                status="opened",
                source_branch="feature/rules",
                target_branch="main",
                author="Alice",
                repo_name="project",
                repo_id="99",
                api_url="https://gitlab.example.com/api/v4/projects/group%2Fproject/merge_requests/7",
                browser_url="https://gitlab.example.com/group/project/-/merge_requests/7",
                provider="gitlab",
            ),
        )
        diffs = [{
            "new_path": "src/app.py",
            "old_path": "src/app.py",
            "diff": "@@ -1 +1,2 @@\n alpha\n+beta\n",
        }]
        versions = [{
            "base_commit_sha": "base123",
            "start_commit_sha": "start123",
            "head_commit_sha": "head123",
        }]
        created = {"id": "thread-2", "notes": [{"id": 21}]}

        with mock.patch.object(provider, "_request_json", side_effect=[diffs, versions, created]) as request_json:
            result = provider.create_inline_review_comment(
                context,
                text="Inline note",
                file_path="/src/app.py",
                line=2,
                end_line=None,
                start_offset=1,
                end_offset=None,
            )

        self.assertEqual(result.thread_id, "thread-2")
        self.assertEqual(result.comment_id, 21)
        self.assertEqual(request_json.call_args_list[2].args[:2], (
            "https://gitlab.example.com",
            "/projects/99/merge_requests/7/discussions",
        ))
        self.assertEqual(request_json.call_args_list[2].kwargs["method"], "POST")
        self.assertEqual(request_json.call_args_list[2].kwargs["form_data"]["position[new_line]"], 2)

    def test_prepare_review_reply_builds_gitlab_payload(self):
        provider = GitLabReviewProvider("token")

        preview = provider.prepare_review_reply(mock.Mock(), thread_id="thread-1", text="Thanks", parent_comment_id=None)

        self.assertEqual(preview.payload, {"body": "Thanks"})
        self.assertEqual(preview.thread_id, "thread-1")

    def test_edit_review_comment_uses_discussion_note_endpoint(self):
        provider = GitLabReviewProvider("token")
        context = ReviewContext(
            organization="https://gitlab.example.com",
            project="group/project",
            repository=RepositoryRef(id="99", name="project", project="group/project"),
            change_request=workflow_models.ChangeRequest(
                id=7,
                title="Improve pipeline rules",
                status="opened",
                source_branch="feature/rules",
                target_branch="main",
                author="Alice",
                repo_name="project",
                repo_id="99",
                api_url="https://gitlab.example.com/api/v4/projects/group%2Fproject/merge_requests/7",
                browser_url="https://gitlab.example.com/group/project/-/merge_requests/7",
                provider="gitlab",
            ),
        )

        with mock.patch.object(provider, "_request_json", return_value={}) as request_json:
            result = provider.edit_review_comment(context, thread_id="thread-1", comment_id=5, text="Updated")

        self.assertEqual(result.thread_id, "thread-1")
        self.assertEqual(result.comment_id, 5)
        request_json.assert_called_once_with(
            "https://gitlab.example.com",
            "/projects/99/merge_requests/7/discussions/thread-1/notes/5",
            method="PUT",
            form_data={"body": "Updated"},
        )

    def test_prepare_review_comment_edit_includes_current_content(self):
        provider = GitLabReviewProvider("token")
        context = ReviewContext(
            organization="https://gitlab.example.com",
            project="group/project",
            repository=RepositoryRef(id="99", name="project", project="group/project"),
            change_request=workflow_models.ChangeRequest(
                id=7,
                title="Improve pipeline rules",
                status="opened",
                source_branch="feature/rules",
                target_branch="main",
                author="Alice",
                repo_name="project",
                repo_id="99",
                api_url="https://gitlab.example.com/api/v4/projects/group%2Fproject/merge_requests/7",
                browser_url="https://gitlab.example.com/group/project/-/merge_requests/7",
                provider="gitlab",
            ),
        )

        with mock.patch.object(provider, "_fetch_discussion", return_value={"id": "thread-1", "notes": [{"id": 5, "body": "Old"}] }):
            preview = provider.prepare_review_comment_edit(context, thread_id="thread-1", comment_id=5, text="Updated")

        self.assertEqual(preview.current_content, "Old")
        self.assertEqual(preview.payload, {"body": "Updated"})

    def test_resolve_review_thread_uses_discussion_endpoint(self):
        provider = GitLabReviewProvider("token")
        context = ReviewContext(
            organization="https://gitlab.example.com",
            project="group/project",
            repository=RepositoryRef(id="99", name="project", project="group/project"),
            change_request=workflow_models.ChangeRequest(
                id=7,
                title="Improve pipeline rules",
                status="opened",
                source_branch="feature/rules",
                target_branch="main",
                author="Alice",
                repo_name="project",
                repo_id="99",
                api_url="https://gitlab.example.com/api/v4/projects/group%2Fproject/merge_requests/7",
                browser_url="https://gitlab.example.com/group/project/-/merge_requests/7",
                provider="gitlab",
            ),
        )

        with mock.patch.object(provider, "_request_json", return_value={"resolved": True}) as request_json:
            result = provider.resolve_review_thread(context, thread_id="thread-1", status="fixed")

        self.assertEqual(result.thread_id, "thread-1")
        self.assertEqual(result.status, "resolved")
        request_json.assert_called_once_with(
            "https://gitlab.example.com",
            "/projects/99/merge_requests/7/discussions/thread-1",
            method="PUT",
            form_data={"resolved": True},
        )

    def test_prepare_review_thread_resolution_includes_current_status(self):
        provider = GitLabReviewProvider("token")
        context = ReviewContext(
            organization="https://gitlab.example.com",
            project="group/project",
            repository=RepositoryRef(id="99", name="project", project="group/project"),
            change_request=workflow_models.ChangeRequest(
                id=7,
                title="Improve pipeline rules",
                status="opened",
                source_branch="feature/rules",
                target_branch="main",
                author="Alice",
                repo_name="project",
                repo_id="99",
                api_url="https://gitlab.example.com/api/v4/projects/group%2Fproject/merge_requests/7",
                browser_url="https://gitlab.example.com/group/project/-/merge_requests/7",
                provider="gitlab",
            ),
        )

        with mock.patch.object(provider, "_fetch_discussion", return_value={"id": "thread-1", "resolved": False}):
            preview = provider.prepare_review_thread_resolution(context, thread_id="thread-1", status="fixed")

        self.assertEqual(preview.current_status, "active")
        self.assertEqual(preview.payload, {"resolved": True})

    def test_request_json_falls_back_to_curl_on_ssl_verification_failure(self):
        provider = GitLabReviewProvider("")
        ssl_error = urllib.error.URLError(ssl.SSLCertVerificationError(1, "bad cert"))
        curl_result = mock.Mock(returncode=0, stdout='{"iid": 7}\n__SG_HTTP_CODE__:200', stderr='')

        with mock.patch("providers.gitlab.review_provider.OPENER") as opener:
            opener.open.side_effect = ssl_error
            with mock.patch("providers.gitlab.review_provider.shutil.which", return_value="/usr/bin/curl"):
                with mock.patch("providers.gitlab.review_provider.subprocess.run", return_value=curl_result) as run:
                    payload = provider._request_json("https://gitlab.com", "/projects/example%2Fproject/merge_requests/7")

        self.assertEqual(payload["iid"], 7)
        curl_argv = run.call_args.args[0]
        self.assertFalse(any("PRIVATE-TOKEN" in argument for argument in curl_argv))

    def test_authenticated_request_does_not_fall_back_to_curl_on_ssl_verification_failure(self):
        provider = GitLabReviewProvider("secret-token")
        ssl_error = urllib.error.URLError(ssl.SSLCertVerificationError(1, "bad cert"))

        with mock.patch("providers.gitlab.review_provider.OPENER") as opener:
            opener.open.side_effect = ssl_error
            with mock.patch("providers.gitlab.review_provider.subprocess.run") as run:
                with self.assertRaisesRegex(CliError, "curl fallback is disabled"):
                    provider._request_json(
                        "https://gitlab.com",
                        "/projects/example%2Fproject/merge_requests/7",
                    )

        run.assert_not_called()

    def test_authenticated_curl_fallback_cannot_place_token_in_process_arguments(self):
        provider = GitLabReviewProvider("secret-token")

        with mock.patch("providers.gitlab.review_provider.subprocess.run") as run:
            with self.assertRaisesRegex(CliError, "do not use the curl TLS fallback"):
                provider._request_with_curl(
                    "https://gitlab.com",
                    "/projects/1",
                    accept="application/json",
                )

        run.assert_not_called()

    def test_request_json_accepts_empty_success_response(self):
        provider = GitLabReviewProvider(None)
        with mock.patch.object(provider, "_read_response_body", return_value=""):
            result = provider._request_json(
                "https://gitlab.com",
                "/projects/1/repository/branches/fix%2F123",
                method="DELETE",
            )

        self.assertIsNone(result)

    def test_request_json_allows_expected_status_codes(self):
        provider = GitLabReviewProvider("")
        http_error = urllib.error.HTTPError(
            url="https://gitlab.com/api/v4/example",
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=None,
        )
        http_error.read = lambda: b'{"message":"401 Unauthorized"}'

        with mock.patch("providers.gitlab.review_provider.OPENER") as opener:
            opener.open.side_effect = http_error
            payload = provider._request_json(
                "https://gitlab.com",
                "/projects/example%2Fproject/merge_requests/7/versions",
                allowed_status_codes={401},
            )

        self.assertIsNone(payload)

    def test_curl_binary_falls_back_to_common_absolute_path(self):
        provider = GitLabReviewProvider("")

        with mock.patch("providers.gitlab.review_provider.shutil.which", return_value=None):
            with mock.patch("providers.gitlab.review_provider.os.path.exists", side_effect=lambda path: path == "/usr/bin/curl"):
                self.assertEqual(provider._curl_binary(), "/usr/bin/curl")

    def test_list_review_threads_requires_token_when_gitlab_hides_discussions(self):
        provider = GitLabReviewProvider("")
        context = ReviewContext(
            organization="https://gitlab.example.com",
            project="group/project",
            repository=RepositoryRef(id="99", name="project", project="group/project"),
            change_request=workflow_models.ChangeRequest(
                id=7,
                title="Improve pipeline rules",
                status="opened",
                source_branch="feature/rules",
                target_branch="main",
                author="Alice",
                repo_name="project",
                repo_id="99",
                api_url="https://gitlab.example.com/api/v4/projects/group%2Fproject/merge_requests/7",
                browser_url="https://gitlab.example.com/group/project/-/merge_requests/7",
                provider="gitlab",
            ),
        )

        with mock.patch.object(provider, "_request_json", return_value=None):
            with self.assertRaisesRegex(CliError, "requires authenticated API access"):
                provider.list_review_threads(context)

    def test_list_statuses_requires_token_when_gitlab_hides_statuses(self):
        provider = GitLabReviewProvider("")
        context = ReviewContext(
            organization="https://gitlab.example.com",
            project="group/project",
            repository=RepositoryRef(id="99", name="project", project="group/project"),
            change_request=workflow_models.ChangeRequest(
                id=7,
                title="Improve pipeline rules",
                status="opened",
                source_branch="feature/rules",
                target_branch="main",
                author="Alice",
                repo_name="project",
                repo_id="99",
                api_url="https://gitlab.example.com/api/v4/projects/group%2Fproject/merge_requests/7",
                browser_url="https://gitlab.example.com/group/project/-/merge_requests/7",
                provider="gitlab",
            ),
        )

        with mock.patch.object(provider, "_merge_request_details", return_value={"sha": "abc123"}):
            with mock.patch.object(provider, "_request_json", return_value=None):
                with self.assertRaisesRegex(CliError, "requires authenticated API access"):
                    provider.list_statuses(context)

    def test_request_json_raises_cli_error_on_non_ssl_transport_failure(self):
        provider = GitLabReviewProvider("")
        transport_error = urllib.error.URLError("network down")

        with mock.patch("providers.gitlab.review_provider.OPENER") as opener:
            opener.open.side_effect = transport_error
            with self.assertRaisesRegex(Exception, "GitLab API request failed: network down"):
                provider._request_json("https://gitlab.com", "/projects/example%2Fproject/merge_requests/7")


if __name__ == "__main__":
    unittest.main()
