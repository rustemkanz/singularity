import urllib.parse

from app_config import ORG
from errors import CliError
from providers.azure_devops.http import api
from workflow_models import ServiceEndpointSummary


SERVICE_ENDPOINT_API_VER = "7.1"


class AzureDevOpsServiceEndpointProvider:
    def __init__(self, token: str):
        self.token = token

    def list_service_endpoints(
        self,
        *,
        project: str,
        endpoint_names: list[str] | None = None,
        endpoint_type: str | None = None,
    ) -> list[ServiceEndpointSummary]:
        project_name = urllib.parse.quote(project)
        url = (
            f"https://dev.azure.com/{ORG}/{project_name}/_apis/serviceendpoint/endpoints"
            f"?api-version={SERVICE_ENDPOINT_API_VER}"
        )
        if endpoint_names:
            url += f"&endpointNames={urllib.parse.quote(','.join(endpoint_names))}"
        if endpoint_type:
            url += f"&type={urllib.parse.quote(endpoint_type)}"
        data = api(self.token, "GET", url)
        return [self._to_summary(endpoint) for endpoint in data.get("value", [])]

    def get_service_endpoint(
        self,
        *,
        project: str,
        name: str | None = None,
        endpoint_id: str | None = None,
    ) -> ServiceEndpointSummary:
        if not name and not endpoint_id:
            raise CliError("ERROR: Provide either an endpoint name or --id.")
        if endpoint_id:
            project_name = urllib.parse.quote(project)
            url = (
                f"https://dev.azure.com/{ORG}/{project_name}/_apis/serviceendpoint/endpoints/"
                f"{urllib.parse.quote(endpoint_id)}?api-version={SERVICE_ENDPOINT_API_VER}"
            )
            endpoint = api(self.token, "GET", url)
            if not endpoint:
                raise CliError(f"ERROR: Service endpoint '{endpoint_id}' was not found.")
            return self._to_summary(endpoint)

        matches = self.list_service_endpoints(project=project, endpoint_names=[name])
        exact_matches = [endpoint for endpoint in matches if endpoint.name == name]
        if not exact_matches:
            raise CliError(f"ERROR: Service endpoint '{name}' was not found in project '{project}'.")
        if len(exact_matches) > 1:
            ids = ", ".join(endpoint.id for endpoint in exact_matches)
            raise CliError(
                f"ERROR: Multiple service endpoints named '{name}' were found ({ids}); use --id to disambiguate."
            )
        return exact_matches[0]

    def _to_summary(self, endpoint: dict) -> ServiceEndpointSummary:
        authorization = endpoint.get("authorization") or {}
        parameters = authorization.get("parameters") or {}
        data = endpoint.get("data") or {}
        created_by = endpoint.get("createdBy") or {}
        return ServiceEndpointSummary(
            id=str(endpoint.get("id") or ""),
            name=endpoint.get("name") or "",
            type=endpoint.get("type") or "",
            url=endpoint.get("url"),
            is_ready=bool(endpoint.get("isReady")),
            is_shared=bool(endpoint.get("isShared")),
            owner=endpoint.get("owner"),
            description=endpoint.get("description"),
            created_by=created_by.get("displayName"),
            authorization_scheme=authorization.get("scheme"),
            service_principal_id=parameters.get("serviceprincipalid"),
            tenant_id=parameters.get("tenantid"),
            subscription_id=data.get("subscriptionId"),
            subscription_name=data.get("subscriptionName"),
        )
