import unittest
from unittest import mock

from errors import CliError
from providers.azure_devops import work_tracking_provider as provider_module
from providers.azure_devops.work_tracking_provider import AzureDevOpsWorkTrackingProvider


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
