import argparse
import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

from cli_commands import builds as build_commands
from errors import CliError
from mutation_plans import (
    MutationPlan,
    PLAN_STORE_ENVIRONMENT_VARIABLE,
    register_plan_preview,
)
from providers.azure_devops.build_provider import AzureDevOpsBuildProvider
import workflow_models


SOURCE_VERSION = "a" * 40
UPDATED_SOURCE_VERSION = "b" * 40


class BuildCommandTests(unittest.TestCase):
    def setUp(self):
        self._plan_store = tempfile.TemporaryDirectory()
        self.addCleanup(self._plan_store.cleanup)
        self._plan_store_environment = mock.patch.dict(
            os.environ,
            {PLAN_STORE_ENVIRONMENT_VARIABLE: self._plan_store.name},
        )
        self._plan_store_environment.start()
        self.addCleanup(self._plan_store_environment.stop)

    @staticmethod
    def _queue_build_plan(
        *,
        definition: int = 123,
        project: str = "Example Project",
        source_branch: str = "refs/heads/main",
        source_version: str = SOURCE_VERSION,
        parameters: dict | None = None,
        allow_duplicate: bool = False,
        recent_duplicates: list[dict] | None = None,
    ) -> MutationPlan:
        plan = MutationPlan(
            action="build.queue",
            target={
                "provider": "azure-devops",
                "organization": build_commands.ORG,
                "project": project,
                "definitionId": definition,
            },
            payload={
                "sourceBranch": source_branch,
                "sourceVersion": source_version,
                "parameters": parameters,
                "allowDuplicate": allow_duplicate,
                "recentDuplicates": recent_duplicates or [],
            },
        )
        register_plan_preview(plan)
        return plan

    @staticmethod
    def _approve_gate_plan(
        approval: workflow_models.PendingBuildApproval,
        *,
        build_id: int = 99,
        project: str = "Example Project",
        comment: str = "Approved via CLI",
    ) -> MutationPlan:
        plan = MutationPlan(
            action="build.gate.approve",
            target={
                "provider": "azure-devops",
                "organization": build_commands.ORG,
                "project": project,
                "buildId": build_id,
                "approvalId": approval.id,
            },
            payload={
                "status": "approved",
                "comment": comment,
                "pendingApproval": approval.to_legacy_dict(),
            },
        )
        register_plan_preview(plan)
        return plan

    def test_cmd_build_logs_json_uses_build_provider(self):
        args = argparse.Namespace(
            build_id=99,
            project="Example Project",
            stage="Build",
            job=None,
            step="Validate asset names",
            failed=True,
            json=True,
        )
        snapshot = workflow_models.BuildLogsSnapshot(
            build=workflow_models.BuildRun(
                id=99,
                build_number="20260728.7",
                status="completed",
                result="failed",
                source_branch="refs/heads/main",
                queued_at="2026-07-28T10:00:00Z",
            ),
            matches=[
                workflow_models.BuildLogMatch(
                    log_id=321,
                    stage_name="Build",
                    job_name="Linux",
                    record_name="Validate asset names",
                    record_type="Task",
                    state="completed",
                    result="failed",
                    issue="Validation failed",
                    content="##[error] boom",
                )
            ],
        )

        provider = mock.Mock()
        provider.get_build_logs.return_value = snapshot

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            build_commands.cmd_build_logs(
                args,
                token="token",
                build_build_provider_func=lambda _token: provider,
            )

        provider.get_build_logs.assert_called_once_with(
            build_id=99,
            project="Example Project",
            stage_name="Build",
            job_name=None,
            step_name="Validate asset names",
            failed_only=True,
        )
        rendered = json.loads(stdout.getvalue())
        self.assertEqual(rendered["logs"][0]["logId"], 321)
        self.assertEqual(rendered["logs"][0]["recordType"], "Task")

    def test_cmd_builds_json_uses_build_provider(self):
        args = argparse.Namespace(definition=123, project="Example Project", branch="main", latest_for_branch=None, commit=None, limit=5, json=True)
        builds = [
            workflow_models.BuildRun(
                id=99,
                build_number="20260723.1",
                status="completed",
                result="succeeded",
                source_branch="refs/heads/main",
                queued_at="2026-07-23T10:00:00Z",
                reason="manual",
                source_version="abcdef123456",
            )
        ]

        provider = mock.Mock()
        provider.list_recent_builds.return_value = builds

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            build_commands.cmd_builds(
                args,
                token="token",
                build_build_provider_func=lambda _token: provider,
            )

        rendered = json.loads(stdout.getvalue())
        self.assertEqual(rendered["definition"], 123)
        self.assertEqual(rendered["commit"], None)
        self.assertEqual(rendered["builds"][0]["buildNumber"], "20260723.1")
        self.assertEqual(rendered["builds"][0]["reason"], "manual")
        provider.list_recent_builds.assert_called_once_with(
            definition=123,
            project="Example Project",
            branch="main",
            commit=None,
            limit=5,
        )

    def test_cmd_builds_latest_for_branch_limits_to_one(self):
        args = argparse.Namespace(
            definition=123,
            project="Example Project",
            branch=None,
            latest_for_branch="main",
            commit="abc123",
            limit=5,
            json=False,
        )
        provider = mock.Mock()
        provider.list_recent_builds.return_value = [
            workflow_models.BuildRun(
                id=99,
                build_number="20260723.1",
                status="completed",
                result="succeeded",
                source_branch="refs/heads/main",
                queued_at="2026-07-23T10:00:00Z",
                reason="batchedCI",
                source_version="abc12345",
            )
        ]

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            build_commands.cmd_builds(
                args,
                token="token",
                build_build_provider_func=lambda _token: provider,
            )

        provider.list_recent_builds.assert_called_once_with(
            definition=123,
            project="Example Project",
            branch="main",
            commit="abc123",
            limit=1,
        )
        self.assertIn("batchedCI", stdout.getvalue())

    def test_cmd_builds_rejects_branch_and_latest_for_branch_together(self):
        args = argparse.Namespace(
            definition=123,
            project="Example Project",
            branch="main",
            latest_for_branch="release",
            commit=None,
            limit=5,
            json=False,
        )

        with self.assertRaises(CliError) as exc:
            build_commands.cmd_builds(
                args,
                token="token",
                build_build_provider_func=lambda _token: mock.Mock(),
            )

        self.assertIn("Use either --branch or --latest-for-branch", str(exc.exception))

    def test_cmd_build_status_json_uses_build_provider(self):
        args = argparse.Namespace(build_id=99, project="Example Project", limit=3, json=True, watch=False, verbose=False, show_log_ids=False, stage=None, only_failed=False, only_active=False, interval=60)
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
                            log_id=44,
                            failure_details=[],
                        )
                    ],
                )
            ],
            orphan_tasks=[],
        )

        provider = mock.Mock()
        provider.get_build_status_snapshot.return_value = snapshot

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            build_commands.cmd_build_status(
                args,
                token="token",
                build_build_provider_func=lambda _token: provider,
            )

        rendered = json.loads(stdout.getvalue())
        self.assertEqual(rendered["build"]["id"], 99)
        self.assertIn("[Build] state=completed, result=succeeded", rendered["timeline"][0])
        provider.get_build_status_snapshot.assert_called_once_with(
            build_id=99,
            project="Example Project",
            limit=3,
            stage_name=None,
            only_failed=False,
            only_active=False,
        )

    def test_cmd_build_status_json_can_show_log_ids(self):
        args = argparse.Namespace(build_id=99, project="Example Project", limit=3, json=True, watch=False, verbose=False, show_log_ids=True, stage=None, only_failed=False, only_active=False, interval=60)
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
                    name="Build",
                    state="completed",
                    result="failed",
                    approval_required=False,
                    pending_reason=None,
                    tasks=[
                        workflow_models.BuildTaskSummary(
                            name="Compile",
                            state="completed",
                            result="failed",
                            issue="",
                            log_id=44,
                            failure_details=[],
                        )
                    ],
                )
            ],
            orphan_tasks=[],
        )
        provider = mock.Mock()
        provider.get_build_status_snapshot.return_value = snapshot

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            build_commands.cmd_build_status(
                args,
                token="token",
                build_build_provider_func=lambda _token: provider,
            )

        rendered = json.loads(stdout.getvalue())
        self.assertIn("log=44", rendered["timeline"][1])

    def test_cmd_build_status_renders_failure_tail_summary(self):
        args = argparse.Namespace(build_id=99, project="Example Project", limit=3, json=False, watch=False, verbose=False, show_log_ids=False, stage=None, only_failed=False, only_active=False, interval=60)
        snapshot = workflow_models.BuildStatusSnapshot(
            build=workflow_models.BuildRun(
                id=99,
                build_number="20260723.1",
                status="completed",
                result="failed",
                source_branch="refs/heads/main",
                queued_at="2026-07-23T10:00:00Z",
                reason="manual",
            ),
            stages=[
                workflow_models.BuildStageSummary(
                    name="Build",
                    state="completed",
                    result="failed",
                    approval_required=False,
                    pending_reason=None,
                    tasks=[
                        workflow_models.BuildTaskSummary(
                            name="Validate Dagster asset names",
                            state="completed",
                            result="failed",
                            issue="##[error] Validation failed",
                            log_id=101,
                            failure_details=[
                                "##[error] Validation failed",
                                "Traceback (most recent call last):",
                                "ValueError: bad asset name",
                            ],
                        )
                    ],
                )
            ],
            orphan_tasks=[],
        )

        provider = mock.Mock()
        provider.get_build_status_snapshot.return_value = snapshot

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            build_commands.cmd_build_status(
                args,
                token="token",
                build_build_provider_func=lambda _token: provider,
            )

        rendered = stdout.getvalue()
        self.assertIn("reason=manual", rendered)
        self.assertIn("failure: Traceback (most recent call last):", rendered)
        self.assertIn("failure: ValueError: bad asset name", rendered)

    def test_cmd_build_status_watch_is_read_only(self):
        args = argparse.Namespace(build_id=99, project="Example Project", limit=3, json=False, watch=True, verbose=False, show_log_ids=False, stage=None, only_failed=False, only_active=False, interval=5)
        snapshot_waiting = workflow_models.BuildStatusSnapshot(
            build=workflow_models.BuildRun(
                id=99,
                build_number="20260723.1",
                status="inProgress",
                result="",
                source_branch="refs/heads/main",
                queued_at="2026-07-23T10:00:00Z",
            ),
            stages=[
                workflow_models.BuildStageSummary(
                    name="Approval",
                    state="pending",
                    result="pending",
                    approval_required=True,
                    pending_reason="waiting for approval: ManualValidation",
                    tasks=[],
                )
            ],
            orphan_tasks=[],
        )
        snapshot_completed = workflow_models.BuildStatusSnapshot(
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
                    name="Approval",
                    state="completed",
                    result="succeeded",
                    approval_required=False,
                    pending_reason=None,
                    tasks=[],
                )
            ],
            orphan_tasks=[],
        )

        provider = mock.Mock()
        provider.get_build_status_snapshot.side_effect = [snapshot_waiting, snapshot_completed]

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            build_commands.cmd_build_status(
                args,
                token="token",
                build_build_provider_func=lambda _token: provider,
                get_token_func=lambda: "token",
                sleep_func=lambda _seconds: None,
            )

        self.assertEqual(provider.get_build_status_snapshot.call_count, 2)
        provider.list_pending_approvals.assert_not_called()
        provider.approve_pending_approval.assert_not_called()
        rendered = stdout.getvalue()
        self.assertIn("reason: waiting for approval: ManualValidation", rendered)

    def test_cmd_build_approvals_json_is_read_only(self):
        args = argparse.Namespace(build_id=99, project="Example Project", json=True)
        approval = workflow_models.PendingBuildApproval(
            id="42",
            pipeline_id="99",
            status="pending",
        )
        provider = mock.Mock()
        provider.list_pending_approvals.return_value = [approval]

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            build_commands.cmd_build_approvals(
                args,
                token="token",
                build_build_provider_func=lambda _token: provider,
            )

        rendered = json.loads(stdout.getvalue())
        self.assertEqual(rendered["buildId"], 99)
        self.assertEqual(rendered["approvals"], [approval.to_legacy_dict()])
        provider.list_pending_approvals.assert_called_once_with(
            project="Example Project",
            build_id=99,
        )
        provider.approve_pending_approval.assert_not_called()

    def test_cmd_build_approvals_text_reports_no_pending_approvals(self):
        args = argparse.Namespace(build_id=99, project="Example Project", json=False)
        provider = mock.Mock()
        provider.list_pending_approvals.return_value = []

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            build_commands.cmd_build_approvals(
                args,
                token="token",
                build_build_provider_func=lambda _token: provider,
            )

        self.assertIn("No pending approvals for build 99", stdout.getvalue())
        provider.approve_pending_approval.assert_not_called()

    def test_cmd_approve_gate_previews_exact_pending_approval(self):
        args = argparse.Namespace(
            build_id=99,
            project="Example Project",
            approval="42",
            comment="Ship it",
            json=True,
            apply=None,
        )
        approval = workflow_models.PendingBuildApproval(
            id="42",
            pipeline_id="99",
            status="pending",
        )
        provider = mock.Mock()
        provider.list_pending_approvals.return_value = [approval]

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            build_commands.cmd_approve_gate(
                args,
                token="token",
                build_build_provider_func=lambda _token: provider,
            )

        rendered = json.loads(stdout.getvalue())
        self.assertEqual(rendered["plan"]["action"], "build.gate.approve")
        self.assertEqual(rendered["plan"]["target"]["approvalId"], "42")
        self.assertEqual(rendered["plan"]["payload"]["comment"], "Ship it")
        self.assertEqual(rendered["plan"]["payload"]["pendingApproval"], approval.to_legacy_dict())
        self.assertEqual(rendered["applyArgument"], f"--apply {rendered['planId']}")
        provider.approve_pending_approval.assert_not_called()

    def test_cmd_approve_gate_applies_only_exact_plan_id(self):
        approval = workflow_models.PendingBuildApproval(
            id="42",
            pipeline_id="99",
            status="pending",
        )
        plan = self._approve_gate_plan(approval, comment="Ship it")
        args = argparse.Namespace(
            build_id=99,
            project="Example Project",
            approval="42",
            comment="Ship it",
            json=False,
            apply=plan.plan_id,
        )
        provider = mock.Mock()
        provider.list_pending_approvals.return_value = [approval]
        provider.approve_pending_approval.return_value = True

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            build_commands.cmd_approve_gate(
                args,
                token="token",
                build_build_provider_func=lambda _token: provider,
            )

        provider.approve_pending_approval.assert_called_once_with(
            project="Example Project",
            approval_id="42",
            comment="Ship it",
        )
        self.assertIn("Approved 42 for build 99", stdout.getvalue())

    def test_cmd_approve_gate_rejects_changed_plan_without_mutation(self):
        approval = workflow_models.PendingBuildApproval(
            id="42",
            pipeline_id="99",
            status="pending",
        )
        approved_plan = self._approve_gate_plan(approval, comment="Original approval")
        args = argparse.Namespace(
            build_id=99,
            project="Example Project",
            approval="42",
            comment="Changed approval",
            json=False,
            apply=approved_plan.plan_id,
        )
        provider = mock.Mock()
        provider.list_pending_approvals.return_value = [approval]

        with self.assertRaisesRegex(CliError, "does not match"):
            build_commands.cmd_approve_gate(
                args,
                token="token",
                build_build_provider_func=lambda _token: provider,
            )

        provider.list_pending_approvals.assert_called_once_with(
            project="Example Project",
            build_id=99,
        )
        provider.approve_pending_approval.assert_not_called()

    def test_cmd_build_status_watch_prints_only_changed_lines_by_default(self):
        args = argparse.Namespace(build_id=99, project="Example Project", limit=3, json=False, watch=True, verbose=False, show_log_ids=False, stage=None, only_failed=False, only_active=False, interval=5)
        snapshot_waiting = workflow_models.BuildStatusSnapshot(
            build=workflow_models.BuildRun(
                id=99,
                build_number="20260723.1",
                status="inProgress",
                result="",
                source_branch="refs/heads/main",
                queued_at="2026-07-23T10:00:00Z",
            ),
            stages=[
                workflow_models.BuildStageSummary(
                    name="Build",
                    state="inProgress",
                    result="pending",
                    approval_required=False,
                    pending_reason=None,
                    tasks=[
                        workflow_models.BuildTaskSummary(
                            name="Install deps",
                            state="completed",
                            result="succeeded",
                            issue="",
                            log_id=None,
                            failure_details=[],
                        ),
                        workflow_models.BuildTaskSummary(
                            name="Validate Dagster asset names",
                            state="pending",
                            result="pending",
                            issue="",
                            log_id=101,
                            failure_details=[],
                        ),
                    ],
                )
            ],
            orphan_tasks=[],
        )
        snapshot_completed = workflow_models.BuildStatusSnapshot(
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
                    name="Build",
                    state="completed",
                    result="failed",
                    approval_required=False,
                    pending_reason=None,
                    tasks=[
                        workflow_models.BuildTaskSummary(
                            name="Install deps",
                            state="completed",
                            result="succeeded",
                            issue="",
                            log_id=None,
                            failure_details=[],
                        ),
                        workflow_models.BuildTaskSummary(
                            name="Validate Dagster asset names",
                            state="completed",
                            result="failed",
                            issue="##[error] Validation failed",
                            log_id=101,
                            failure_details=["ValueError: bad asset name"],
                        ),
                    ],
                )
            ],
            orphan_tasks=[],
        )

        provider = mock.Mock()
        provider.get_build_status_snapshot.side_effect = [snapshot_waiting, snapshot_completed]

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            build_commands.cmd_build_status(
                args,
                token="token",
                build_build_provider_func=lambda _token: provider,
                get_token_func=lambda: "token",
                sleep_func=lambda _seconds: None,
            )

        rendered = stdout.getvalue()
        self.assertEqual(rendered.count("Install deps"), 1)
        self.assertIn("Build 20260723.1: status=completed, result=failed", rendered)
        self.assertIn("failure: ValueError: bad asset name", rendered)

    def test_cmd_build_status_watch_verbose_repeats_full_snapshots(self):
        args = argparse.Namespace(build_id=99, project="Example Project", limit=3, json=False, watch=True, verbose=True, show_log_ids=False, stage=None, only_failed=False, only_active=False, interval=5)
        snapshot_running = workflow_models.BuildStatusSnapshot(
            build=workflow_models.BuildRun(
                id=99,
                build_number="20260723.1",
                status="inProgress",
                result="",
                source_branch="refs/heads/main",
                queued_at="2026-07-23T10:00:00Z",
            ),
            stages=[
                workflow_models.BuildStageSummary(
                    name="Build",
                    state="inProgress",
                    result="pending",
                    approval_required=False,
                    pending_reason=None,
                    tasks=[
                        workflow_models.BuildTaskSummary(
                            name="Install deps",
                            state="completed",
                            result="succeeded",
                            issue="",
                            log_id=None,
                            failure_details=[],
                        ),
                    ],
                )
            ],
            orphan_tasks=[],
        )
        snapshot_completed = workflow_models.BuildStatusSnapshot(
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
                            name="Install deps",
                            state="completed",
                            result="succeeded",
                            issue="",
                            log_id=None,
                            failure_details=[],
                        ),
                    ],
                )
            ],
            orphan_tasks=[],
        )

        provider = mock.Mock()
        provider.get_build_status_snapshot.side_effect = [snapshot_running, snapshot_completed]

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            build_commands.cmd_build_status(
                args,
                token="token",
                build_build_provider_func=lambda _token: provider,
                get_token_func=lambda: "token",
                sleep_func=lambda _seconds: None,
            )

        rendered = stdout.getvalue()
        self.assertEqual(rendered.count("Install deps"), 2)

    def test_cmd_build_status_can_filter_to_stage_and_failed_tasks(self):
        args = argparse.Namespace(build_id=99, project="Example Project", limit=3, json=False, watch=False, verbose=False, show_log_ids=True, stage="Deploy", only_failed=True, only_active=False, interval=60)
        snapshot = workflow_models.BuildStatusSnapshot(
            build=workflow_models.BuildRun(
                id=99,
                build_number="20260723.1",
                status="completed",
                result="failed",
                source_branch="refs/heads/main",
                queued_at="2026-07-23T10:00:00Z",
                reason="manual",
            ),
            stages=[
                workflow_models.BuildStageSummary(
                    name="Deploy",
                    state="completed",
                    result="failed",
                    approval_required=False,
                    pending_reason="waiting on upstream dependency, agent capacity, or deployment checks",
                    tasks=[
                        workflow_models.BuildTaskSummary(
                            name="Deploy app",
                            state="completed",
                            result="failed",
                            issue="##[error] deploy failed",
                            log_id=222,
                            failure_details=["##[error] deploy failed"],
                        )
                    ],
                )
            ],
            orphan_tasks=[],
        )
        provider = mock.Mock()
        provider.get_build_status_snapshot.return_value = snapshot

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            build_commands.cmd_build_status(
                args,
                token="token",
                build_build_provider_func=lambda _token: provider,
            )

        provider.get_build_status_snapshot.assert_called_once_with(
            build_id=99,
            project="Example Project",
            limit=3,
            stage_name="Deploy",
            only_failed=True,
            only_active=False,
        )
        rendered = stdout.getvalue()
        self.assertIn("log=222", rendered)
        self.assertIn("reason: waiting on upstream dependency", rendered)

    def test_cmd_build_status_can_filter_to_active_items(self):
        args = argparse.Namespace(build_id=99, project="Example Project", limit=3, json=False, watch=False, verbose=False, show_log_ids=False, stage=None, only_failed=False, only_active=True, interval=60)
        snapshot = workflow_models.BuildStatusSnapshot(
            build=workflow_models.BuildRun(
                id=99,
                build_number="20260723.1",
                status="inProgress",
                result="",
                source_branch="refs/heads/main",
                queued_at="2026-07-23T10:00:00Z",
            ),
            stages=[
                workflow_models.BuildStageSummary(
                    name="Build",
                    state="pending",
                    result="pending",
                    approval_required=False,
                    pending_reason="waiting on task execution: Deploy app",
                    tasks=[
                        workflow_models.BuildTaskSummary(
                            name="Deploy app",
                            state="inProgress",
                            result="pending",
                            issue="",
                            log_id=None,
                            failure_details=[],
                        )
                    ],
                )
            ],
            orphan_tasks=[],
        )
        provider = mock.Mock()
        provider.get_build_status_snapshot.return_value = snapshot

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            build_commands.cmd_build_status(
                args,
                token="token",
                build_build_provider_func=lambda _token: provider,
            )

        rendered = stdout.getvalue()
        self.assertIn("reason: waiting on task execution: Deploy app", rendered)

    def test_cmd_queue_build_preview_binds_recent_duplicate_snapshot(self):
        args = argparse.Namespace(
            definition=123,
            project="Example Project",
            branch=None,
            commit=None,
            parameters='{"env":"dev"}',
            json=True,
            apply=None,
            allow_duplicate=False,
        )
        provider = mock.Mock()
        provider.list_recent_builds.return_value = []
        provider_factory = mock.Mock(return_value=provider)
        current_branch = mock.Mock(return_value="main")
        commit_resolver = mock.Mock(return_value=SOURCE_VERSION)

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            build_commands.cmd_queue_build(
                args,
                token="token",
                build_build_provider_func=provider_factory,
                current_git_branch_func=current_branch,
                resolve_git_commit_func=commit_resolver,
            )

        rendered = json.loads(stdout.getvalue())
        self.assertEqual(rendered["plan"]["action"], "build.queue")
        self.assertEqual(rendered["plan"]["target"]["definitionId"], 123)
        self.assertEqual(rendered["plan"]["payload"], {
            "allowDuplicate": False,
            "parameters": {"env": "dev"},
            "recentDuplicates": [],
            "sourceBranch": "refs/heads/main",
            "sourceVersion": SOURCE_VERSION,
        })
        self.assertEqual(rendered["applyArgument"], f"--apply {rendered['planId']}")
        provider_factory.assert_called_once_with("token")
        provider.list_recent_builds.assert_called_once_with(
            definition=123,
            project="Example Project",
            branch="refs/heads/main",
            commit=SOURCE_VERSION,
            limit=5,
        )
        current_branch.assert_called_once_with()
        commit_resolver.assert_called_once_with("HEAD")

    def test_cmd_queue_build_requires_a_resolved_branch_and_object_parameters(self):
        base_args = dict(
            definition=123,
            project="Example Project",
            branch=None,
            commit=None,
            json=False,
            apply=None,
        )
        with self.assertRaisesRegex(CliError, "Pass --branch explicitly"):
            build_commands.cmd_queue_build(
                argparse.Namespace(**base_args, parameters=None),
                token="token",
                build_build_provider_func=mock.Mock(),
                current_git_branch_func=lambda: None,
            )

        with self.assertRaisesRegex(CliError, "JSON object"):
            build_commands.cmd_queue_build(
                argparse.Namespace(**base_args, parameters='["dev"]'),
                token="token",
                build_build_provider_func=mock.Mock(),
                current_git_branch_func=lambda: "main",
            )

    def test_cmd_queue_build_applies_only_exact_plan_id(self):
        plan = self._queue_build_plan(parameters={"env": "dev"})
        args = argparse.Namespace(
            definition=123,
            project="Example Project",
            branch=None,
            commit=None,
            parameters='{"env":"dev"}',
            json=False,
            apply=plan.plan_id,
        )
        provider = mock.Mock()
        provider.list_recent_builds.return_value = []
        provider.queue_build.return_value = workflow_models.QueuedBuild(
            id=77,
            build_number="20260728.3",
            source_branch="refs/heads/main",
        )

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            build_commands.cmd_queue_build(
                args,
                token="token",
                build_build_provider_func=lambda _token: provider,
                current_git_branch_func=lambda: "main",
                resolve_git_commit_func=lambda _ref: SOURCE_VERSION,
            )

        provider.list_recent_builds.assert_called_once_with(
            definition=123,
            project="Example Project",
            branch="refs/heads/main",
            commit=SOURCE_VERSION,
            limit=5,
        )
        provider.queue_build.assert_called_once_with(
            definition=123,
            project="Example Project",
            source_branch="refs/heads/main",
            source_version=SOURCE_VERSION,
            parameters={"env": "dev"},
        )
        self.assertIn("Queued build 77", stdout.getvalue())

    def test_cmd_queue_build_rejects_changed_plan_after_binding_duplicate_snapshot(self):
        approved_plan = self._queue_build_plan(parameters={"env": "dev"})
        args = argparse.Namespace(
            definition=123,
            project="Example Project",
            branch=None,
            commit=None,
            parameters='{"env":"prod"}',
            json=False,
            apply=approved_plan.plan_id,
        )
        provider = mock.Mock()
        provider.list_recent_builds.return_value = []
        provider_factory = mock.Mock(return_value=provider)
        commit_resolver = mock.Mock(return_value=SOURCE_VERSION)

        with self.assertRaisesRegex(CliError, "does not match"):
            build_commands.cmd_queue_build(
                args,
                token="token",
                build_build_provider_func=provider_factory,
                current_git_branch_func=lambda: "main",
                resolve_git_commit_func=commit_resolver,
            )

        provider_factory.assert_called_once_with("token")
        commit_resolver.assert_called_once_with("HEAD")

    def test_cmd_queue_build_rejects_a_commit_changed_since_preview(self):
        approved_plan = self._queue_build_plan(source_version=SOURCE_VERSION)
        args = argparse.Namespace(
            definition=123,
            project="Example Project",
            branch=None,
            commit=None,
            parameters=None,
            json=False,
            apply=approved_plan.plan_id,
        )
        provider = mock.Mock()
        provider.list_recent_builds.return_value = []
        provider_factory = mock.Mock(return_value=provider)

        with self.assertRaisesRegex(CliError, "does not match"):
            build_commands.cmd_queue_build(
                args,
                token="token",
                build_build_provider_func=provider_factory,
                current_git_branch_func=lambda: "main",
                resolve_git_commit_func=lambda _ref: UPDATED_SOURCE_VERSION,
            )

        provider_factory.assert_called_once_with("token")

    def test_cmd_queue_build_rejects_an_unresolvable_explicit_commit(self):
        args = argparse.Namespace(
            definition=123,
            project="Example Project",
            branch="release",
            commit="ambiguous-prefix",
            parameters=None,
            json=False,
            apply=None,
        )
        provider_factory = mock.Mock()
        commit_resolver = mock.Mock(
            side_effect=CliError("ERROR: Could not resolve Git commit reference 'ambiguous-prefix'.")
        )

        with self.assertRaisesRegex(CliError, "Could not resolve Git commit"):
            build_commands.cmd_queue_build(
                args,
                token="token",
                build_build_provider_func=provider_factory,
                current_git_branch_func=mock.Mock(),
                resolve_git_commit_func=commit_resolver,
            )

        commit_resolver.assert_called_once_with("ambiguous-prefix")
        provider_factory.assert_not_called()

    def test_cmd_queue_build_requires_explicitly_approved_duplicate(self):
        duplicate = workflow_models.BuildRun(
            id=88,
            build_number="20260728.2",
            status="completed",
            result="succeeded",
            source_branch="refs/heads/main",
            queued_at="2026-07-28T10:00:00Z",
            reason="batchedCI",
            source_version="abcdef123456",
        )
        duplicate_snapshot = [{
            "id": 88,
            "buildNumber": "20260728.2",
            "status": "completed",
            "result": "succeeded",
            "reason": "batchedCI",
        }]
        args = argparse.Namespace(
            definition=123,
            project="Example Project",
            branch=None,
            commit=None,
            parameters=None,
            json=False,
            apply=None,
            allow_duplicate=False,
        )
        provider = mock.Mock()
        provider.list_recent_builds.return_value = [duplicate]
        provider.queue_build.return_value = workflow_models.QueuedBuild(
            id=89,
            build_number="20260728.3",
            source_branch="refs/heads/main",
        )

        with self.assertRaisesRegex(CliError, "--allow-duplicate"):
            build_commands.cmd_queue_build(
                args,
                token="token",
                build_build_provider_func=lambda _token: provider,
                current_git_branch_func=lambda: "main",
                resolve_git_commit_func=lambda _ref: SOURCE_VERSION,
            )

        provider.queue_build.assert_not_called()

        args.allow_duplicate = True
        args.apply = self._queue_build_plan(
            allow_duplicate=True,
            recent_duplicates=duplicate_snapshot,
        ).plan_id
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            build_commands.cmd_queue_build(
                args,
                token="token",
                build_build_provider_func=lambda _token: provider,
                current_git_branch_func=lambda: "main",
                resolve_git_commit_func=lambda _ref: SOURCE_VERSION,
            )

        rendered = stdout.getvalue()
        self.assertIn("Approved duplicate queue", rendered)
        self.assertIn("88 reason=batchedCI", rendered)

    def test_cmd_queue_build_resolves_and_checks_an_explicit_branch_commit(self):
        args = argparse.Namespace(
            definition=123,
            project="Example Project",
            branch="release",
            commit=None,
            parameters=None,
            json=False,
            apply=self._queue_build_plan(source_branch="refs/heads/release").plan_id,
        )
        provider = mock.Mock()
        provider.list_recent_builds.return_value = []
        provider.queue_build.return_value = workflow_models.QueuedBuild(
            id=77,
            build_number="20260728.3",
            source_branch="refs/heads/release",
        )
        commit_resolver = mock.Mock(return_value=SOURCE_VERSION)

        with contextlib.redirect_stdout(io.StringIO()):
            build_commands.cmd_queue_build(
                args,
                token="token",
                build_build_provider_func=lambda _token: provider,
                current_git_branch_func=lambda: "main",
                resolve_git_commit_func=commit_resolver,
            )

        commit_resolver.assert_called_once_with("release")
        provider.list_recent_builds.assert_called_once_with(
            definition=123,
            project="Example Project",
            branch="refs/heads/release",
            commit=SOURCE_VERSION,
            limit=5,
        )


class AzureDevOpsBuildProviderTests(unittest.TestCase):
    def test_queue_build_posts_the_pinned_source_version(self):
        provider = AzureDevOpsBuildProvider("token")
        response = {
            "id": 77,
            "buildNumber": "20260808.1",
            "sourceBranch": "refs/heads/main",
        }

        with mock.patch(
            "providers.azure_devops.build_provider.api",
            return_value=response,
        ) as api_mock:
            queued = provider.queue_build(
                definition=4832,
                project="Example Project",
                source_branch="refs/heads/main",
                source_version=SOURCE_VERSION,
                parameters={"environment": "staging"},
            )

        api_mock.assert_called_once()
        token, method, url, payload = api_mock.call_args.args
        self.assertEqual(token, "token")
        self.assertEqual(method, "POST")
        self.assertIn("/Example%20Project/_apis/build/builds", url)
        self.assertEqual(payload, {
            "definition": {"id": 4832},
            "sourceBranch": "refs/heads/main",
            "sourceVersion": SOURCE_VERSION,
            "templateParameters": {"environment": "staging"},
        })
        self.assertEqual(queued.id, 77)

    def test_list_recent_builds_filters_branch_and_commit_and_preserves_reason(self):
        provider = AzureDevOpsBuildProvider("token")
        payload = {
            "value": [
                {
                    "id": 1,
                    "status": "completed",
                    "result": "succeeded",
                    "reason": "batchedCI",
                    "buildNumber": "20260728.1",
                    "queueTime": "2026-07-28T10:00:00Z",
                    "sourceBranch": "refs/heads/main",
                    "sourceVersion": "abcdef1234567890",
                },
                {
                    "id": 2,
                    "status": "completed",
                    "result": "failed",
                    "reason": "manual",
                    "buildNumber": "20260728.2",
                    "queueTime": "2026-07-28T11:00:00Z",
                    "sourceBranch": "refs/heads/main",
                    "sourceVersion": "fff999",
                },
            ]
        }

        with mock.patch("providers.azure_devops.build_provider.api", return_value=payload) as api_mock:
            builds = provider.list_recent_builds(
                definition=4832,
                project="Example Project",
                branch="main",
                commit="abcdef",
                limit=5,
            )

        api_mock.assert_called_once()
        self.assertEqual(len(builds), 1)
        self.assertEqual(builds[0].id, 1)
        self.assertEqual(builds[0].reason, "batchedCI")
        self.assertEqual(builds[0].source_version, "abcdef1234567890")

    def test_get_build_logs_filters_to_failed_task_matches(self):
        timeline = {
            "records": [
                {"id": "stage-1", "type": "Stage", "name": "Orchestration", "order": 1},
                {"id": "job-1", "type": "Job", "name": "Linux", "parentId": "stage-1", "order": 2},
                {
                    "id": "task-1",
                    "type": "Task",
                    "name": "Install deps",
                    "parentId": "job-1",
                    "order": 3,
                    "state": "completed",
                    "result": "succeeded",
                    "log": {"id": 100},
                },
                {
                    "id": "task-2",
                    "type": "Task",
                    "name": "Validate Dagster asset names",
                    "parentId": "job-1",
                    "order": 4,
                    "state": "completed",
                    "result": "failed",
                    "issues": [{"message": "##[error] bad asset"}],
                    "log": {"id": 101},
                },
            ]
        }
        build_payload = {
            "id": 77,
            "buildNumber": "20260728.4",
            "status": "completed",
            "result": "failed",
            "reason": "manual",
            "sourceBranch": "refs/heads/main",
            "sourceVersion": "abcdef1234567890",
            "queueTime": "2026-07-28T10:00:00Z",
        }
        provider = AzureDevOpsBuildProvider("token")

        with mock.patch.object(provider, "_fetch_build", return_value=build_payload), mock.patch.object(
            provider,
            "_fetch_timeline",
            return_value=timeline,
        ), mock.patch.object(provider, "_fetch_log_text", return_value="##[error] bad asset\n") as fetch_log_text:
            snapshot = provider.get_build_logs(
                build_id=77,
                project="Example Project",
                stage_name="orchestr",
                job_name="lin",
                step_name="dagster",
                failed_only=True,
            )

        fetch_log_text.assert_called_once_with(77, 101, "Example Project")
        self.assertEqual(snapshot.build.id, 77)
        self.assertEqual(len(snapshot.matches), 1)
        self.assertEqual(snapshot.matches[0].log_id, 101)
        self.assertEqual(snapshot.matches[0].stage_name, "Orchestration")
        self.assertEqual(snapshot.matches[0].job_name, "Linux")
        self.assertEqual(snapshot.matches[0].record_name, "Validate Dagster asset names")

    def test_get_build_logs_raises_when_filters_match_nothing(self):
        provider = AzureDevOpsBuildProvider("token")
        build_payload = {
            "id": 77,
            "buildNumber": "20260728.4",
            "status": "completed",
            "result": "failed",
            "reason": "manual",
            "sourceBranch": "refs/heads/main",
            "sourceVersion": "abcdef1234567890",
            "queueTime": "2026-07-28T10:00:00Z",
        }
        timeline = {
            "records": [
                {"id": "stage-1", "type": "Stage", "name": "Orchestration", "order": 1},
            ]
        }

        with mock.patch.object(provider, "_fetch_build", return_value=build_payload), mock.patch.object(
            provider,
            "_fetch_timeline",
            return_value=timeline,
        ):
            with self.assertRaises(CliError) as exc:
                provider.get_build_logs(
                    build_id=77,
                    project="Example Project",
                    stage_name="missing",
                    job_name=None,
                    step_name=None,
                    failed_only=False,
                )

        self.assertIn("No build logs found", str(exc.exception))

    def test_get_build_status_snapshot_includes_failed_task_tail(self):
        timeline = {
            "records": [
                {"id": "stage-1", "type": "Stage", "name": "Orchestration", "order": 1},
                {"id": "job-1", "type": "Job", "name": "Linux", "parentId": "stage-1", "order": 2},
                {
                    "id": "task-1",
                    "type": "Task",
                    "name": "Validate Dagster asset names",
                    "parentId": "job-1",
                    "order": 3,
                    "state": "completed",
                    "result": "failed",
                    "issues": [{"message": "##[error] Validation failed"}],
                    "log": {"id": 101},
                    "lastModified": "2026-07-28T10:00:00Z",
                },
            ]
        }
        build_payload = {
            "id": 77,
            "buildNumber": "20260728.4",
            "status": "completed",
            "result": "failed",
            "reason": "manual",
            "sourceBranch": "refs/heads/main",
            "sourceVersion": "abcdef1234567890",
            "queueTime": "2026-07-28T10:00:00Z",
        }
        provider = AzureDevOpsBuildProvider("token")
        log_text = "header\n##[error] Validation failed\nTraceback (most recent call last):\n  File 'main.py', line 1\nValueError: bad asset name\n"

        with mock.patch.object(provider, "_fetch_build", return_value=build_payload), mock.patch.object(
            provider,
            "_fetch_timeline",
            return_value=timeline,
        ), mock.patch.object(provider, "_fetch_log_text", return_value=log_text) as fetch_log_text:
            snapshot = provider.get_build_status_snapshot(
                build_id=77,
                project="Example Project",
                limit=3,
                stage_name=None,
                only_failed=False,
                only_active=False,
            )

        fetch_log_text.assert_called_once_with(77, 101, "Example Project")
        task = snapshot.stages[0].tasks[0]
        self.assertEqual(task.issue, "##[error] Validation failed")
        self.assertEqual(task.log_id, 101)
        self.assertIn("Traceback (most recent call last):", task.failure_details)
        self.assertIn("ValueError: bad asset name", task.failure_details)

    def test_get_build_status_snapshot_can_filter_stage_and_active_items(self):
        provider = AzureDevOpsBuildProvider("token")
        build_payload = {
            "id": 77,
            "buildNumber": "20260728.4",
            "status": "inProgress",
            "result": "",
            "reason": "batchedCI",
            "sourceBranch": "refs/heads/main",
            "sourceVersion": "abcdef1234567890",
            "queueTime": "2026-07-28T10:00:00Z",
        }
        timeline = {
            "records": [
                {"id": "stage-1", "type": "Stage", "name": "Build", "state": "completed", "result": "succeeded", "order": 1},
                {"id": "stage-2", "type": "Stage", "name": "Deploy", "state": "pending", "result": "pending", "order": 2},
                {"id": "job-2", "type": "Job", "name": "Deploy Linux", "parentId": "stage-2", "state": "pending", "result": "pending", "order": 3},
                {"id": "task-1", "type": "Task", "name": "Compile", "parentId": "job-2", "state": "completed", "result": "succeeded", "order": 4, "lastModified": "2026-07-28T10:00:00Z"},
                {"id": "task-2", "type": "Task", "name": "Deploy app", "parentId": "job-2", "state": "inProgress", "result": "pending", "order": 5, "lastModified": "2026-07-28T10:01:00Z", "log": {"id": 222}},
            ]
        }

        with mock.patch.object(provider, "_fetch_build", return_value=build_payload), mock.patch.object(
            provider,
            "_fetch_timeline",
            return_value=timeline,
        ), mock.patch.object(provider, "_fetch_log_text", return_value=""):
            snapshot = provider.get_build_status_snapshot(
                build_id=77,
                project="Example Project",
                limit=3,
                stage_name="Deploy",
                only_failed=False,
                only_active=True,
            )

        self.assertEqual(len(snapshot.stages), 1)
        self.assertEqual(snapshot.stages[0].name, "Deploy")
        self.assertEqual(snapshot.stages[0].pending_reason, "waiting on task execution: Deploy app")
        self.assertEqual(len(snapshot.stages[0].tasks), 1)
        self.assertEqual(snapshot.stages[0].tasks[0].name, "Deploy app")

    def test_get_build_status_snapshot_can_filter_failed_items(self):
        provider = AzureDevOpsBuildProvider("token")
        build_payload = {
            "id": 77,
            "buildNumber": "20260728.4",
            "status": "completed",
            "result": "failed",
            "reason": "manual",
            "sourceBranch": "refs/heads/main",
            "sourceVersion": "abcdef1234567890",
            "queueTime": "2026-07-28T10:00:00Z",
        }
        timeline = {
            "records": [
                {"id": "stage-1", "type": "Stage", "name": "Build", "state": "completed", "result": "succeeded", "order": 1},
                {"id": "stage-2", "type": "Stage", "name": "Deploy", "state": "completed", "result": "failed", "order": 2},
                {"id": "job-2", "type": "Job", "name": "Deploy Linux", "parentId": "stage-2", "state": "completed", "result": "failed", "order": 3},
                {"id": "task-1", "type": "Task", "name": "Compile", "parentId": "job-2", "state": "completed", "result": "succeeded", "order": 4, "lastModified": "2026-07-28T10:00:00Z"},
                {"id": "task-2", "type": "Task", "name": "Deploy app", "parentId": "job-2", "state": "completed", "result": "failed", "order": 5, "lastModified": "2026-07-28T10:01:00Z", "issues": [{"message": "##[error] deploy failed"}], "log": {"id": 222}},
            ]
        }

        with mock.patch.object(provider, "_fetch_build", return_value=build_payload), mock.patch.object(
            provider,
            "_fetch_timeline",
            return_value=timeline,
        ), mock.patch.object(provider, "_fetch_log_text", return_value="##[error] deploy failed"):
            snapshot = provider.get_build_status_snapshot(
                build_id=77,
                project="Example Project",
                limit=3,
                stage_name=None,
                only_failed=True,
                only_active=False,
            )

        self.assertEqual(len(snapshot.stages), 1)
        self.assertEqual(snapshot.stages[0].name, "Deploy")
        self.assertEqual(len(snapshot.stages[0].tasks), 1)
        self.assertEqual(snapshot.stages[0].tasks[0].name, "Deploy app")


if __name__ == "__main__":
    unittest.main()
