import unittest
from unittest import mock

from errors import CliError
from providers.gitlab.work_tracking_provider import GitLabWorkTrackingProvider


class GitLabWorkTrackingProviderTests(unittest.TestCase):
    def test_get_work_item_context_maps_issue_and_related_merge_requests(self):
        provider = GitLabWorkTrackingProvider("token", "group/project")
        issue = {
            "iid": 17,
            "title": "Improve pipeline rules",
            "state": "opened",
            "description": "Issue description",
            "issue_type": "issue",
            "author": {"username": "alice"},
            "labels": ["smoke"],
            "references": {"full": "group/project#17"},
        }
        notes = [{"id": 5, "body": "Please check", "created_at": "2026-08-03T10:00:00Z", "author": {"username": "alex"}, "system": False}]
        merge_requests = [{"iid": 3, "title": "[17] Improve pipeline rules", "state": "opened", "web_url": "https://gitlab.example.com/group/project/-/merge_requests/3", "references": {"full": "group/project!3"}}]

        with mock.patch.object(provider, "_request_json", side_effect=[issue, notes, merge_requests]):
            snapshot = provider.get_work_item_context(item_id=17)

        self.assertEqual(snapshot.work_item.id, 17)
        self.assertEqual(snapshot.work_item.provider, "gitlab")
        self.assertEqual(snapshot.comment_count, 1)
        self.assertEqual(snapshot.development_artifacts["pullRequests"][0]["pullRequestId"], 3)

    def test_add_work_item_comment_posts_issue_note(self):
        provider = GitLabWorkTrackingProvider("token", "group/project")

        with mock.patch.object(provider, "_request_json", return_value={"id": 11}) as request_json:
            comment_id = provider.add_work_item_comment(item_id=17, text="Thanks")

        self.assertEqual(comment_id, 11)
        request_json.assert_called_once_with(
            "https://gitlab.com",
            "/projects/group%2Fproject/issues/17/notes",
            method="POST",
            form_data={"body": "Thanks"},
        )

    def test_get_start_work_plan_builds_gitlab_commands(self):
        provider = GitLabWorkTrackingProvider("token", "group/project")
        issue = {
            "iid": 17,
            "title": "Improve pipeline rules",
            "state": "opened",
            "description": "Issue description",
            "issue_type": "issue",
            "author": {"username": "alice"},
            "labels": ["smoke"],
            "references": {"full": "group/project#17"},
            "path_with_namespace": "group/project",
        }
        project = {"path_with_namespace": "group/project"}

        with mock.patch.object(provider, "_fetch_issue", return_value=issue):
            with mock.patch.object(provider, "_resolve_repository", return_value=project):
                plan = provider.get_start_work_plan(item_id=17)

        self.assertIn("sg create-pr 17 --provider gitlab --repo group/project", plan.commands[1])
        self.assertEqual(plan.branch_name, "issue/17-improve-pipeline-rules")

    def test_transition_work_item_updates_workflow_label_and_assignee(self):
        provider = GitLabWorkTrackingProvider("token", "group/project")
        issue = {
            "iid": 17,
            "state": "opened",
            "labels": ["bug", "sg:state:in-progress"],
            "updated_at": "2026-08-08T10:00:00Z",
        }
        updated_issue = {
            "iid": 17,
            "state": "opened",
            "labels": ["bug", "sg:state:in-testing"],
        }

        with mock.patch.object(provider, "_fetch_issue", return_value=issue):
            preview = provider.prepare_work_item_transition(
                item_id=17,
                state="In Testing",
                assignee="qa-user",
            )
            with mock.patch.object(provider, "_request_json", return_value=updated_issue) as request_json:
                actual_state = provider.apply_prepared_work_item_transition(preview)

        self.assertEqual(actual_state, "In Testing")
        self.assertEqual(
            preview.current_snapshot,
            {
                "state": "In Progress",
                "labels": ["bug", "sg:state:in-progress"],
                "updatedAt": "2026-08-08T10:00:00Z",
            },
        )
        self.assertEqual(
            preview.request["formData"],
            {
                "labels": "bug,sg:state:in-testing",
                "assignee_username": "qa-user",
            },
        )
        request_json.assert_called_once_with(
            "https://gitlab.com",
            "/projects/group%2Fproject/issues/17",
            method="PUT",
            form_data={
                "labels": "bug,sg:state:in-testing",
                "assignee_username": "qa-user",
            },
        )

    def test_apply_prepared_transition_rejects_changed_issue_snapshot(self):
        provider = GitLabWorkTrackingProvider("token", "group/project")
        original_issue = {
            "iid": 17,
            "state": "opened",
            "labels": ["bug", "sg:state:in-progress"],
            "updated_at": "2026-08-08T10:00:00Z",
        }
        changed_issue = {
            **original_issue,
            "labels": ["bug", "urgent", "sg:state:in-progress"],
            "updated_at": "2026-08-08T10:01:00Z",
        }

        with mock.patch.object(provider, "_fetch_issue", side_effect=[original_issue, changed_issue]):
            preview = provider.prepare_work_item_transition(item_id=17, state="In Review")
            with mock.patch.object(provider, "_request_json") as request_json:
                with self.assertRaisesRegex(CliError, "changed since the transition preview"):
                    provider.apply_prepared_work_item_transition(preview)

        request_json.assert_not_called()

    def test_transition_work_item_rejects_unsupported_state(self):
        provider = GitLabWorkTrackingProvider("token", "group/project")

        with mock.patch.object(provider, "_fetch_issue", return_value={"iid": 17, "labels": []}):
            with self.assertRaises(CliError):
                provider.prepare_work_item_transition(item_id=17, state="Done")

    def test_get_work_item_context_prefers_helper_workflow_label_for_state(self):
        provider = GitLabWorkTrackingProvider("token", "group/project")
        issue = {
            "iid": 17,
            "title": "Improve pipeline rules",
            "state": "opened",
            "description": "Issue description",
            "issue_type": "issue",
            "author": {"username": "alice"},
            "labels": ["smoke", "sg:state:in-review"],
            "references": {"full": "group/project#17"},
        }

        with mock.patch.object(provider, "_request_json", side_effect=[issue, [], []]):
            snapshot = provider.get_work_item_context(item_id=17)

        self.assertEqual(snapshot.work_item.state, "In Review")

    def test_get_work_item_context_prefers_closed_issue_state_over_helper_workflow_label(self):
        provider = GitLabWorkTrackingProvider("token", "group/project")
        issue = {
            "iid": 17,
            "title": "Improve pipeline rules",
            "state": "closed",
            "description": "Issue description",
            "issue_type": "issue",
            "author": {"username": "alice"},
            "labels": ["smoke", "sg:state:in-testing"],
            "references": {"full": "group/project#17"},
        }

        with mock.patch.object(provider, "_request_json", side_effect=[issue, [], []]):
            snapshot = provider.get_work_item_context(item_id=17)

        self.assertEqual(snapshot.work_item.state, "closed")


if __name__ == "__main__":
    unittest.main()
