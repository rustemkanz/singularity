import argparse
import contextlib
import io
import json
import unittest
from unittest import mock

from cli_commands import service_endpoints as service_endpoint_commands
from errors import CliError
from providers.azure_devops.service_endpoint_provider import AzureDevOpsServiceEndpointProvider
import workflow_models


def _make_endpoint(**overrides) -> workflow_models.ServiceEndpointSummary:
    defaults = dict(
        id="endpoint-guid",
        name="sp-example-deploy-dev",
        type="azurerm",
        url="https://management.azure.com/",
        is_ready=True,
        is_shared=False,
        owner="library",
        description=None,
        created_by="Ada Lovelace",
        authorization_scheme="ServicePrincipal",
        service_principal_id="11111111-2222-3333-4444-555555555555",
        tenant_id="66666666-7777-8888-9999-000000000000",
        subscription_id="sub-id",
        subscription_name="Example Subscription",
    )
    defaults.update(overrides)
    return workflow_models.ServiceEndpointSummary(**defaults)


class ServiceEndpointCommandTests(unittest.TestCase):
    def test_cmd_service_endpoints_json_uses_provider(self):
        args = argparse.Namespace(project="Example Project", name=None, type=None, json=True)
        provider = mock.Mock()
        provider.list_service_endpoints.return_value = [_make_endpoint()]

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            service_endpoint_commands.cmd_service_endpoints(
                args,
                token="token",
                build_service_endpoint_provider_func=lambda _token: provider,
            )

        rendered = json.loads(stdout.getvalue())
        self.assertEqual(rendered["project"], "Example Project")
        self.assertEqual(
            rendered["serviceEndpoints"][0]["servicePrincipalId"],
            "11111111-2222-3333-4444-555555555555",
        )
        provider.list_service_endpoints.assert_called_once_with(
            project="Example Project",
            endpoint_names=None,
            endpoint_type=None,
        )

    def test_cmd_service_endpoints_text_lists_names(self):
        args = argparse.Namespace(project="Example Project", name=None, type=None, json=False)
        provider = mock.Mock()
        provider.list_service_endpoints.return_value = [_make_endpoint()]

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            service_endpoint_commands.cmd_service_endpoints(
                args,
                token="token",
                build_service_endpoint_provider_func=lambda _token: provider,
            )

        self.assertIn("sp-example-deploy-dev", stdout.getvalue())

    def test_cmd_service_endpoint_show_json_includes_app_id(self):
        args = argparse.Namespace(project="Example Project", name="sp-example-deploy-dev", id=None, json=True)
        provider = mock.Mock()
        provider.get_service_endpoint.return_value = _make_endpoint()

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            service_endpoint_commands.cmd_service_endpoint_show(
                args,
                token="token",
                build_service_endpoint_provider_func=lambda _token: provider,
            )

        rendered = json.loads(stdout.getvalue())
        self.assertEqual(rendered["tenantId"], "66666666-7777-8888-9999-000000000000")
        provider.get_service_endpoint.assert_called_once_with(
            project="Example Project",
            name="sp-example-deploy-dev",
            endpoint_id=None,
        )

    def test_cmd_service_endpoint_show_text_includes_spn_id(self):
        args = argparse.Namespace(project="Example Project", name="sp-example-deploy-dev", id=None, json=False)
        provider = mock.Mock()
        provider.get_service_endpoint.return_value = _make_endpoint()

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            service_endpoint_commands.cmd_service_endpoint_show(
                args,
                token="token",
                build_service_endpoint_provider_func=lambda _token: provider,
            )

        rendered = stdout.getvalue()
        self.assertIn("App/SPN ID  : 11111111-2222-3333-4444-555555555555", rendered)
        self.assertIn("Tenant ID   : 66666666-7777-8888-9999-000000000000", rendered)


class AzureDevOpsServiceEndpointProviderTests(unittest.TestCase):
    def test_list_service_endpoints_parses_authorization_and_data(self):
        provider = AzureDevOpsServiceEndpointProvider("token")
        payload = {
            "value": [
                {
                    "id": "endpoint-guid",
                    "name": "sp-example-deploy-dev",
                    "type": "azurerm",
                    "url": "https://management.azure.com/",
                    "isReady": True,
                    "isShared": False,
                    "owner": "library",
                    "createdBy": {"displayName": "Ada Lovelace"},
                    "authorization": {
                        "scheme": "ServicePrincipal",
                        "parameters": {
                            "tenantid": "66666666-7777-8888-9999-000000000000",
                            "serviceprincipalid": "11111111-2222-3333-4444-555555555555",
                        },
                    },
                    "data": {
                        "subscriptionId": "sub-id",
                        "subscriptionName": "Example Subscription",
                    },
                }
            ]
        }

        with mock.patch(
            "providers.azure_devops.service_endpoint_provider.api",
            return_value=payload,
        ) as api_mock:
            endpoints = provider.list_service_endpoints(project="Example Project")

        api_mock.assert_called_once()
        token, method, url = api_mock.call_args.args
        self.assertEqual(token, "token")
        self.assertEqual(method, "GET")
        self.assertIn("/Example%20Project/_apis/serviceendpoint/endpoints", url)
        self.assertEqual(len(endpoints), 1)
        endpoint = endpoints[0]
        self.assertEqual(endpoint.service_principal_id, "11111111-2222-3333-4444-555555555555")
        self.assertEqual(endpoint.tenant_id, "66666666-7777-8888-9999-000000000000")
        self.assertEqual(endpoint.subscription_id, "sub-id")
        self.assertEqual(endpoint.created_by, "Ada Lovelace")

    def test_get_service_endpoint_by_name_requires_unique_exact_match(self):
        provider = AzureDevOpsServiceEndpointProvider("token")
        payload = {
            "value": [
                {"id": "1", "name": "deploy-dev", "type": "azurerm"},
                {"id": "2", "name": "deploy-dev-extra", "type": "azurerm"},
            ]
        }

        with mock.patch(
            "providers.azure_devops.service_endpoint_provider.api",
            return_value=payload,
        ):
            endpoint = provider.get_service_endpoint(project="Example Project", name="deploy-dev")

        self.assertEqual(endpoint.id, "1")

    def test_get_service_endpoint_raises_when_not_found(self):
        provider = AzureDevOpsServiceEndpointProvider("token")
        with mock.patch(
            "providers.azure_devops.service_endpoint_provider.api",
            return_value={"value": []},
        ):
            with self.assertRaisesRegex(CliError, "was not found"):
                provider.get_service_endpoint(project="Example Project", name="missing")

    def test_get_service_endpoint_requires_name_or_id(self):
        provider = AzureDevOpsServiceEndpointProvider("token")
        with self.assertRaisesRegex(CliError, "Provide either"):
            provider.get_service_endpoint(project="Example Project")

    def test_get_service_endpoint_by_id_uses_direct_lookup(self):
        provider = AzureDevOpsServiceEndpointProvider("token")
        response = {"id": "endpoint-guid", "name": "deploy-dev", "type": "azurerm"}

        with mock.patch(
            "providers.azure_devops.service_endpoint_provider.api",
            return_value=response,
        ) as api_mock:
            endpoint = provider.get_service_endpoint(project="Example Project", endpoint_id="endpoint-guid")

        api_mock.assert_called_once()
        token, method, url = api_mock.call_args.args
        self.assertIn("/serviceendpoint/endpoints/endpoint-guid", url)
        self.assertEqual(endpoint.name, "deploy-dev")


if __name__ == "__main__":
    unittest.main()
