import unittest

import workflow_models


class WorkflowModelTests(unittest.TestCase):
    def test_provider_defaults_are_neutral_metadata(self):
        sprint = workflow_models.Sprint(
            id="sprint-1",
            name="Sprint 1",
            path="Example\\Sprint 1",
            start_date="2026-07-01",
            finish_date="2026-07-15",
        )
        change_request = workflow_models.ChangeRequest(
            id=42,
            title="Improve mapping",
            status="active",
            source_branch="fix/42",
            target_branch="main",
            author="Alice",
            repo_name="example-repo",
            repo_id="repo-1",
            api_url="https://example.test/pr/42",
            browser_url="https://example.test/browser/42",
        )

        self.assertEqual(sprint.provider, workflow_models.DEFAULT_PROVIDER)
        self.assertEqual(change_request.provider, workflow_models.DEFAULT_PROVIDER)

    def test_sprint_model_preserves_legacy_shape(self):
        sprint = workflow_models.Sprint(
            id="sprint-1",
            name="Sprint 1",
            path="Example\\Sprint 1",
            start_date="2026-07-01",
            finish_date="2026-07-15",
        )

        self.assertEqual(
            sprint.to_legacy_dict(),
            {
                "id": "sprint-1",
                "name": "Sprint 1",
                "path": "Example\\Sprint 1",
                "attributes": {
                    "startDate": "2026-07-01",
                    "finishDate": "2026-07-15",
                },
            },
        )

    def test_triage_report_preserves_legacy_shape(self):
        report = workflow_models.TriageReport(
            items=[
                workflow_models.TriageItem(
                    id=1,
                    title="Fix global filters",
                    concise_title="Fix global filters",
                    kind="Bug",
                    state="New",
                    area="Example",
                    owner="Example",
                    scope="AMI",
                    severity="Non-Critical",
                    product="MM",
                    tags=["rpp"],
                    keywords=["filters"],
                )
            ],
            pairings=[workflow_models.TriagePairing(left_id=1, right_id=2, suggest_same_group=True, reasons=["same area"] )],
            groups=[workflow_models.TriageGroup(ids=[1, 2], reason_summary=["same area"])],
        )

        rendered = report.to_legacy_dict()
        self.assertEqual(rendered["items"][0]["workItemType"], "Bug")
        self.assertEqual(rendered["pairings"][0]["leftId"], 1)
        self.assertEqual(rendered["groups"][0]["ids"], [1, 2])

    def test_change_request_model_preserves_legacy_summary_shape(self):
        change_request = workflow_models.ChangeRequest(
            id=42,
            title="Improve mapping",
            status="active",
            source_branch="fix/42",
            target_branch="main",
            author="Alice",
            repo_name="example-repo",
            repo_id="repo-1",
            api_url="https://example.test/pr/42",
            browser_url="https://example.test/browser/42",
        )

        self.assertEqual(
            change_request.to_summary_dict(),
            {
                "repoId": "repo-1",
                "repoName": "example-repo",
                "pullRequestId": 42,
                "title": "Improve mapping",
                "status": "active",
                "source": "fix/42",
                "target": "main",
                "url": "https://example.test/pr/42",
            },
        )

    def test_change_request_status_display_state_uses_friendlier_labels(self):
        self.assertEqual(
            workflow_models.ChangeRequestStatus(
                id=1,
                state="unknown",
                description="",
                context_name="CI",
                context_kind="build",
                target_url="",
                created_by="",
                creation_date=None,
                updated_date=None,
            ).display_state(),
            "queued",
        )
        self.assertEqual(
            workflow_models.ChangeRequestStatus(
                id=2,
                state="pending",
                description="",
                context_name="CI",
                context_kind="build",
                target_url="",
                created_by="",
                creation_date=None,
                updated_date=None,
            ).display_state(),
            "running",
        )
        self.assertEqual(
            workflow_models.ChangeRequestStatus(
                id=2,
                state="success",
                description="",
                context_name="CI",
                context_kind="build",
                target_url="",
                created_by="",
                creation_date=None,
                updated_date=None,
            ).display_state(),
            "passed",
        )
        self.assertEqual(
            workflow_models.ChangeRequestStatus(
                id=3,
                state="notApplicable",
                description="",
                context_name="CI",
                context_kind="build",
                target_url="",
                created_by="",
                creation_date=None,
                updated_date=None,
            ).display_state(),
            "skipped",
        )

    def test_review_thread_model_preserves_legacy_shape(self):
        thread = workflow_models.ReviewThread(
            thread_id=7,
            status="active",
            is_deleted=False,
            file_path="/src/example.ts",
            line=14,
            location="/src/example.ts:14",
            comments=[
                workflow_models.ReviewComment(
                    comment_id=3,
                    parent_comment_id=0,
                    author="Alex",
                    content="Please clarify this.",
                    published_date="2026-07-23T10:00:00Z",
                    last_updated_date=None,
                )
            ],
        )

        self.assertEqual(
            thread.to_legacy_dict(),
            {
                "threadId": 7,
                "status": "active",
                "isDeleted": False,
                "filePath": "/src/example.ts",
                "line": 14,
                "location": "/src/example.ts:14",
                "comments": [
                    {
                        "commentId": 3,
                        "parentCommentId": 0,
                        "author": "Alex",
                        "content": "Please clarify this.",
                        "publishedDate": "2026-07-23T10:00:00Z",
                        "lastUpdatedDate": None,
                    }
                ],
            },
        )

    def test_build_status_snapshot_model_preserves_legacy_shape(self):
        snapshot = workflow_models.BuildStatusSnapshot(
            build=workflow_models.BuildRun(
                id=99,
                build_number="20260723.1",
                status="completed",
                result="succeeded",
                source_branch="refs/heads/main",
                queued_at="2026-07-23T10:00:00Z",
            ),
            stages=[
                workflow_models.BuildStageSummary(
                    name="Build",
                    state="completed",
                    result="succeeded",
                    approval_required=False,
                    pending_reason=None,
                    tasks=[
                        workflow_models.BuildTaskSummary(
                            name="Compile",
                            state="completed",
                            result="succeeded",
                            issue="",
                            log_id=42,
                            failure_details=[],
                        )
                    ],
                )
            ],
            orphan_tasks=[],
        )

        rendered = snapshot.to_legacy_dict()
        self.assertEqual(rendered["build"]["id"], 99)
        self.assertEqual(rendered["build"]["buildNumber"], "20260723.1")
        self.assertIn("[Build] state=completed, result=succeeded", rendered["timeline"][0])

    def test_build_status_snapshot_model_can_show_log_ids(self):
        snapshot = workflow_models.BuildStatusSnapshot(
            build=workflow_models.BuildRun(
                id=99,
                build_number="20260723.1",
                status="completed",
                result="failed",
                source_branch="refs/heads/main",
                queued_at="2026-07-23T10:00:00Z",
            ),
            stages=[
                workflow_models.BuildStageSummary(
                    name="Deploy",
                    state="pending",
                    result="pending",
                    approval_required=True,
                    pending_reason="waiting for approval: ManualValidation",
                    tasks=[
                        workflow_models.BuildTaskSummary(
                            name="Deploy app",
                            state="pending",
                            result="pending",
                            issue="",
                            log_id=88,
                            failure_details=[],
                        )
                    ],
                )
            ],
            orphan_tasks=[],
        )

        rendered = snapshot.to_legacy_dict(show_log_ids=True)
        self.assertIn("reason: waiting for approval: ManualValidation", rendered["timeline"][1])
        self.assertIn("log=88", rendered["timeline"][2])


if __name__ == "__main__":
    unittest.main()
