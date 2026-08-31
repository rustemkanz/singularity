import unittest
from unittest import mock

from errors import CliError
from providers.azure_devops import work_tracking_provider as provider_module
from providers.azure_devops.work_tracking_provider import AzureDevOpsWorkTrackingProvider
from work_item_authoring import ItemSpec


class AzureDevOpsWorkTrackingProviderTests(unittest.TestCase):
    def test_prepared_transition_binds_revision_and_applies_exact_json_patch(self):
        provider = AzureDevOpsWorkTrackingProvider("token")
        work_item = {
            "rev": 9,
            "fields": {
                "System.WorkItemType": "Bug",
                "System.State": "New",
            },
        }

        with mock.patch.object(provider_module, "fetch_work_item", return_value=work_item):
            with mock.patch.object(
                provider_module,
                "resolve_transition_state_name",
                return_value="Active",
            ) as resolve_state:
                preview = provider.prepare_work_item_transition(
                    item_id=42,
                    state="In Progress",
                    assignee="alice@example.com",
                )

        self.assertEqual(preview.concrete_state, "Active")
        self.assertEqual(preview.current_snapshot, {"state": "New", "revision": 9})
        self.assertEqual(
            preview.request,
            {
                "method": "PATCH",
                "operations": [
                    {"op": "test", "path": "/rev", "value": 9},
                    {
                        "op": "replace",
                        "path": "/fields/System.State",
                        "value": "Active",
                    },
                    {
                        "op": "replace",
                        "path": "/fields/System.AssignedTo",
                        "value": "alice@example.com",
                    },
                ],
            },
        )
        resolve_state.assert_called_once_with(
            "token",
            item_id=42,
            desired_state="In Progress",
            work_item=work_item,
        )

        with mock.patch.object(provider_module, "patch_item") as patch_item:
            actual_state = provider.apply_prepared_work_item_transition(preview)

        self.assertEqual(actual_state, "Active")
        patch_item.assert_called_once_with("token", 42, preview.request["operations"])

    def test_prepare_work_item_tree_inherits_parent_paths_and_links_children(self):
        provider = AzureDevOpsWorkTrackingProvider("token")
        parent = {
            "fields": {
                "System.Id": 100,
                "System.Title": "Feature X",
                "System.WorkItemType": "Feature",
                "System.AreaPath": "Proj\\Area",
                "System.IterationPath": "Proj\\Sprint 1",
            },
        }
        specs = [
            ItemSpec(type="User Story", title="Story one", description_html="<p>Body</p>"),
            ItemSpec(
                type="Bug",
                title="Bug two",
                description_html="",
                tags=("regression",),
                assigned_to="qa@example.com",
            ),
        ]

        with mock.patch.object(provider_module, "fetch_work_item", return_value=parent):
            preview = provider.prepare_work_item_tree(
                parent_id=100,
                items=specs,
                tags=["v0.0.1"],
                assignee="dev@example.com",
            )

        self.assertEqual(preview.parent_snapshot["areaPath"], "Proj\\Area")
        self.assertEqual(len(preview.requests), 2)

        story_ops = preview.requests[0]["operations"]
        self.assertIn({"op": "add", "path": "/fields/System.Title", "value": "Story one"}, story_ops)
        self.assertIn({"op": "add", "path": "/fields/System.Tags", "value": "v0.0.1"}, story_ops)
        self.assertIn({"op": "add", "path": "/fields/System.AssignedTo", "value": "dev@example.com"}, story_ops)
        self.assertIn({"op": "add", "path": "/fields/System.IterationPath", "value": "Proj\\Sprint 1"}, story_ops)
        self.assertEqual(
            story_ops[-1],
            {
                "op": "add",
                "path": "/relations/-",
                "value": {
                    "rel": "System.LinkTypes.Hierarchy-Reverse",
                    "url": provider_module.BASE_URL + "/_apis/wit/workItems/100",
                },
            },
        )

        bug_ops = preview.requests[1]["operations"]
        self.assertIn({"op": "add", "path": "/fields/System.Tags", "value": "regression"}, bug_ops)
        self.assertIn({"op": "add", "path": "/fields/System.AssignedTo", "value": "qa@example.com"}, bug_ops)
        self.assertNotIn("/fields/System.Description", [op["path"] for op in bug_ops])

    def test_apply_work_item_tree_reports_partial_failure_per_row(self):
        provider = AzureDevOpsWorkTrackingProvider("token")
        preview = provider_module.WorkItemTreePreview(
            provider="azure-devops",
            parent_id=100,
            parent_snapshot={},
            requests=[
                {"type": "User Story", "title": "ok one", "operations": [{"op": "add", "path": "/fields/System.Title", "value": "ok one"}]},
                {"type": "Bug", "title": "bad two", "operations": [{"op": "add", "path": "/fields/System.Title", "value": "bad two"}]},
            ],
        )

        def fake_api(token, method, url, body=None, *, content_type=None):
            self.assertEqual(method, "POST")
            self.assertEqual(content_type, "application/json-patch+json")
            if "Bug" in url:
                raise CliError("HTTP 400 — bad type")
            return {"id": 501}

        seen = []
        with mock.patch.object(provider_module, "api", side_effect=fake_api):
            results = provider.apply_prepared_work_item_tree(preview, on_result=seen.append)

        self.assertEqual(results[0], {"title": "ok one", "ok": True, "id": 501})
        self.assertFalse(results[1]["ok"])
        self.assertIn("bad type", results[1]["error"])
        self.assertEqual(len(seen), 2)

    def test_list_team_members_maps_identity_fields_and_sorts(self):
        provider = AzureDevOpsWorkTrackingProvider("token")
        payload = {
            "value": [
                {"identity": {"id": "b", "displayName": "Zoe Q", "uniqueName": "zoe@example.com"}},
                {"identity": {"id": "a", "displayName": "Amy R", "uniqueName": "amy@example.com"}, "isTeamAdmin": True},
                {"identity": {}},
            ]
        }
        with mock.patch.object(provider_module, "api", return_value=payload):
            members = provider.list_team_members(team_id="team-guid")

        self.assertEqual([m.display_name for m in members], ["Amy R", "Zoe Q"])
        self.assertTrue(members[0].is_admin)
        self.assertEqual(members[0].unique_name, "amy@example.com")

    def test_prepare_transition_rejects_missing_revision(self):
        provider = AzureDevOpsWorkTrackingProvider("token")
        work_item = {
            "fields": {
                "System.WorkItemType": "Bug",
                "System.State": "New",
            },
        }

        with mock.patch.object(provider_module, "fetch_work_item", return_value=work_item):
            with self.assertRaisesRegex(CliError, "valid revision"):
                provider.prepare_work_item_transition(item_id=42, state="In Progress")


if __name__ == "__main__":
    unittest.main()
