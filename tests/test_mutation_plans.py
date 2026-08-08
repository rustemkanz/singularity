import hashlib
import json
import math
import os
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from errors import CliError
from mutation_plans import (
    MutationPlan,
    MutationPlanError,
    PlanApprovalError,
    render_human_preview,
    render_json_preview,
    render_plan_preview,
    resolve_checkout_identity,
    require_approved_plan,
)


class MutationPlanTests(unittest.TestCase):
    def setUp(self):
        self.plan_store = tempfile.TemporaryDirectory()
        self.environment = mock.patch.dict(
            os.environ,
            {"SG_MUTATION_PLAN_STORE": self.plan_store.name},
        )
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.plan_store.cleanup()

    def make_plan(self) -> MutationPlan:
        return MutationPlan(
            action="work-item.comment",
            target={"provider": "azure-devops", "project": "Example", "id": 123},
            payload={"text": "Ready for review", "notify": False},
        )

    def test_plan_has_canonical_envelope_and_sha256_id(self):
        checkout = os.path.realpath(os.getcwd())
        plan = MutationPlan(
            action="work-item.comment",
            target={"provider": "azure-devops", "project": "Example", "id": 123},
            payload={"text": "Ready for review", "notify": False},
            nonce="0" * 32,
            issued_at=100,
            checkout=checkout,
            ttl_seconds=60,
        )
        expected_envelope = {
            "version": 1,
            "action": "work-item.comment",
            "target": {"provider": "azure-devops", "project": "Example", "id": 123},
            "payload": {"text": "Ready for review", "notify": False},
            "approval": {
                "nonce": "0" * 32,
                "issuedAtUnix": 100,
                "expiresAtUnix": 160,
                "checkout": checkout,
                "checkoutIdentity": {"kind": "directory", "path": checkout},
            },
        }
        expected_json = json.dumps(
            expected_envelope,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )

        self.assertEqual(plan.canonical_json, expected_json)
        self.assertEqual(
            plan.plan_id,
            "sha256:" + hashlib.sha256(expected_json.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(plan.envelope, expected_envelope)

    def test_equivalent_key_orders_have_the_same_plan_id_with_same_approval_metadata(self):
        approval = {
            "nonce": "1" * 32,
            "issued_at": 100,
            "checkout": os.getcwd(),
        }
        left = MutationPlan(
            "pr.create",
            {"repo": "widgets", "provider": "gitlab"},
            {"source": "fix/123", "target": "main"},
            **approval,
        )
        right = MutationPlan(
            "pr.create",
            {"provider": "gitlab", "repo": "widgets"},
            {"target": "main", "source": "fix/123"},
            **approval,
        )

        self.assertEqual(left.canonical_json, right.canonical_json)
        self.assertEqual(left.plan_id, right.plan_id)

    def test_fresh_equivalent_plans_get_distinct_one_shot_ids(self):
        left = self.make_plan()
        right = self.make_plan()

        self.assertEqual(left.semantic_canonical_json, right.semantic_canonical_json)
        self.assertNotEqual(left.plan_id, right.plan_id)

    def test_any_material_plan_change_changes_the_id(self):
        approval = {
            "nonce": "2" * 32,
            "issued_at": 100,
            "checkout": os.getcwd(),
        }
        baseline = MutationPlan("comment", {"id": 123}, {"text": "hello"}, **approval)
        variants = (
            MutationPlan("edit-comment", {"id": 123}, {"text": "hello"}, **approval),
            MutationPlan("comment", {"id": 124}, {"text": "hello"}, **approval),
            MutationPlan("comment", {"id": 123}, {"text": "goodbye"}, **approval),
        )

        for variant in variants:
            with self.subTest(plan=variant.envelope):
                self.assertNotEqual(baseline.plan_id, variant.plan_id)

    def test_plan_snapshots_mutable_inputs_and_returns_detached_copies(self):
        target = {"id": 123, "labels": ["bug"]}
        payload = {"body": {"text": "before"}}
        plan = MutationPlan("comment", target, payload)
        original_id = plan.plan_id

        target["labels"].append("changed")
        payload["body"]["text"] = "after"
        exposed = plan.envelope
        exposed["target"]["id"] = 999

        self.assertEqual(plan.target, {"id": 123, "labels": ["bug"]})
        self.assertEqual(plan.payload, {"body": {"text": "before"}})
        self.assertEqual(plan.plan_id, original_id)

    def test_invalid_plan_values_are_rejected(self):
        cases = (
            ("", {"id": 1}, {}, "action"),
            ("comment", ["id", 1], {}, "target"),
            ("comment", {"id": 1}, "text", "payload"),
            ("comment", {1: "numeric key"}, {}, "non-string"),
            ("comment", {"id": 1}, {"value": math.nan}, "non-finite"),
            ("comment", {"id": 1}, {"value": object()}, "non-JSON"),
        )

        for action, target, payload, expected in cases:
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(MutationPlanError, expected):
                    MutationPlan(action, target, payload)

    def test_preview_mode_does_not_require_approval(self):
        self.assertFalse(require_approved_plan(self.make_plan(), None))

    def test_exact_pending_plan_id_allows_apply(self):
        approved = self.make_plan()
        render_human_preview(approved)

        self.assertTrue(require_approved_plan(self.make_plan(), approved.plan_id))

    def test_plan_id_is_one_shot_and_fresh_preview_gets_new_id(self):
        approved = self.make_plan()
        render_human_preview(approved)
        pending_path = next(
            os.path.join(directory, filename)
            for directory, _subdirectories, filenames in os.walk(self.plan_store.name)
            for filename in filenames
            if filename.endswith(".pending.json")
        )
        with open(pending_path, "rb") as handle:
            saved_pending_record = handle.read()
        self.assertTrue(require_approved_plan(self.make_plan(), approved.plan_id))

        with self.assertRaisesRegex(PlanApprovalError, "already been used"):
            require_approved_plan(self.make_plan(), approved.plan_id)

        with open(pending_path, "wb") as handle:
            handle.write(saved_pending_record)
        with self.assertRaisesRegex(PlanApprovalError, "already been used"):
            require_approved_plan(self.make_plan(), approved.plan_id)

        replacement = self.make_plan()
        render_human_preview(replacement)
        self.assertNotEqual(replacement.plan_id, approved.plan_id)
        self.assertTrue(require_approved_plan(self.make_plan(), replacement.plan_id))

    def test_pending_record_contains_hashes_but_no_target_or_payload_content(self):
        approved = self.make_plan()
        render_human_preview(approved)
        pending_path = next(
            os.path.join(directory, filename)
            for directory, _subdirectories, filenames in os.walk(self.plan_store.name)
            for filename in filenames
            if filename.endswith(".pending.json")
        )

        with open(pending_path, encoding="utf-8") as handle:
            record_text = handle.read()
            record = json.loads(record_text)

        self.assertIn("semanticSha256", record)
        self.assertNotIn("canonicalJson", record)
        self.assertNotIn("semanticCanonicalJson", record)
        self.assertNotIn("Ready for review", record_text)
        self.assertNotIn("Example", record_text)

    def test_concurrent_apply_claim_allows_only_one_process(self):
        approved = self.make_plan()
        render_human_preview(approved)

        def attempt_apply():
            try:
                return require_approved_plan(self.make_plan(), approved.plan_id)
            except PlanApprovalError as exc:
                return str(exc)

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _index: attempt_apply(), range(2)))

        self.assertEqual(results.count(True), 1)
        failures = [result for result in results if result is not True]
        self.assertEqual(len(failures), 1)
        self.assertRegex(failures[0], "already been used|already consumed")

    def test_mismatched_or_malformed_plan_id_rejects_apply(self):
        approved = self.make_plan()
        render_human_preview(approved)
        for supplied in (
            "",
            "sha256:deadbeef",
            approved.plan_id.upper(),
            " " + approved.plan_id,
            "sha256:" + "é" * 64,
        ):
            with self.subTest(supplied=supplied):
                with self.assertRaises(PlanApprovalError):
                    require_approved_plan(self.make_plan(), supplied)

        with self.assertRaisesRegex(PlanApprovalError, "does not match"):
            require_approved_plan(
                MutationPlan(
                    "work-item.comment",
                    {"provider": "azure-devops", "project": "Example", "id": 999},
                    {"text": "changed", "notify": False},
                ),
                approved.plan_id,
            )

    def test_expired_plan_is_rejected(self):
        approved = MutationPlan(
            "comment",
            {"id": 1},
            {"text": "hello"},
            issued_at=int(time.time()) - 10,
            ttl_seconds=1,
        )
        render_human_preview(approved)
        expired_path = next(
            os.path.join(directory, filename)
            for directory, _subdirectories, filenames in os.walk(self.plan_store.name)
            for filename in filenames
            if filename.endswith(".pending.json")
        )

        with self.assertRaisesRegex(PlanApprovalError, "expired"):
            require_approved_plan(
                MutationPlan("comment", {"id": 1}, {"text": "hello"}),
                approved.plan_id,
            )

        render_human_preview(MutationPlan("comment", {"id": 2}, {"text": "fresh"}))
        self.assertFalse(os.path.exists(expired_path))

    def test_plan_id_is_bound_to_the_preview_checkout(self):
        original_checkout = os.getcwd()
        preview_checkout = os.path.join(self.plan_store.name, "preview-checkout")
        other_checkout = os.path.join(self.plan_store.name, "other-checkout")
        os.makedirs(preview_checkout)
        os.makedirs(other_checkout)
        try:
            os.chdir(preview_checkout)
            approved = MutationPlan("comment", {"id": 1}, {"text": "hello"})
            render_human_preview(approved)

            os.chdir(other_checkout)
            with self.assertRaisesRegex(PlanApprovalError, "current checkout"):
                require_approved_plan(
                    MutationPlan("comment", {"id": 1}, {"text": "hello"}),
                    approved.plan_id,
                )
        finally:
            os.chdir(original_checkout)

    def test_git_checkout_binding_normalizes_subdirectory_to_worktree_root(self):
        worktree_root = os.getcwd()
        if resolve_checkout_identity(worktree_root).get("kind") != "git":
            self.skipTest("Test must run inside a Git worktree")
        subdirectory = os.path.join(worktree_root, "tests")
        try:
            os.chdir(subdirectory)
            approved = self.make_plan()
            render_human_preview(approved)

            os.chdir(worktree_root)
            self.assertTrue(require_approved_plan(self.make_plan(), approved.plan_id))
        finally:
            os.chdir(worktree_root)

    def test_symbolic_link_plan_store_is_rejected(self):
        target = os.path.join(self.plan_store.name, "actual-store")
        link = os.path.join(self.plan_store.name, "linked-store")
        os.makedirs(target)
        try:
            os.symlink(target, link, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"Symbolic links unavailable: {exc}")

        with mock.patch.dict(os.environ, {"SG_MUTATION_PLAN_STORE": link}):
            with self.assertRaisesRegex(PlanApprovalError, "safe directory"):
                render_human_preview(self.make_plan())

    def test_plan_errors_are_cli_errors(self):
        with self.assertRaises(CliError):
            require_approved_plan(self.make_plan(), "wrong")

    def test_human_preview_contains_complete_approval_context(self):
        plan = self.make_plan()

        rendered = render_human_preview(plan)

        self.assertIn(f"Plan ID : {plan.plan_id}", rendered)
        self.assertIn("Action  : work-item.comment", rendered)
        self.assertIn("Checkout:", rendered)
        self.assertIn("Expires :", rendered)
        self.assertIn('"project": "Example"', rendered)
        self.assertIn('"text": "Ready for review"', rendered)
        self.assertIn("Preview only: no external state was changed.", rendered)
        self.assertIn(f"--apply {plan.plan_id}", rendered)
        self.assertEqual(render_plan_preview(plan), rendered)

    def test_json_preview_contains_plan_id_apply_argument_and_envelope(self):
        plan = self.make_plan()

        rendered = render_json_preview(plan)
        preview = json.loads(rendered)

        self.assertEqual(preview["planId"], plan.plan_id)
        self.assertEqual(preview["applyArgument"], f"--apply {plan.plan_id}")
        self.assertEqual(preview["expiresAtUnix"], plan.approval["expiresAtUnix"])
        self.assertEqual(preview["plan"], plan.envelope)
        self.assertEqual(json.loads(render_plan_preview(plan, json_output=True)), preview)


if __name__ == "__main__":
    unittest.main()
