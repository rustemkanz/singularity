import json


def _require_provider(factory):
    if factory is None:
        raise RuntimeError("Missing service endpoint provider factory.")
    return factory


def cmd_service_endpoints(args, token, *, build_service_endpoint_provider_func=None):
    provider = _require_provider(build_service_endpoint_provider_func)(token)
    endpoints = provider.list_service_endpoints(
        project=args.project,
        endpoint_names=[args.name] if getattr(args, "name", None) else None,
        endpoint_type=getattr(args, "type", None),
    )
    if args.json:
        print(json.dumps({
            "project": args.project,
            "serviceEndpoints": [endpoint.to_legacy_dict() for endpoint in endpoints],
        }, indent=2))
        return
    if not endpoints:
        print(f"No service endpoints found in project '{args.project}'.")
        return
    for endpoint in endpoints:
        print(endpoint.to_display_line())


def cmd_service_endpoint_show(args, token, *, build_service_endpoint_provider_func=None):
    provider = _require_provider(build_service_endpoint_provider_func)(token)
    endpoint = provider.get_service_endpoint(
        project=args.project,
        name=args.name,
        endpoint_id=args.id,
    )
    if args.json:
        print(json.dumps(endpoint.to_legacy_dict(), indent=2))
        return
    print(f"Name        : {endpoint.name}")
    print(f"Id          : {endpoint.id}")
    print(f"Type        : {endpoint.type}")
    print(f"Ready       : {endpoint.is_ready}")
    print(f"Shared      : {endpoint.is_shared}")
    if endpoint.url:
        print(f"URL         : {endpoint.url}")
    if endpoint.owner:
        print(f"Owner       : {endpoint.owner}")
    if endpoint.created_by:
        print(f"Created by  : {endpoint.created_by}")
    if endpoint.description:
        print(f"Description : {endpoint.description}")
    if endpoint.authorization_scheme:
        print(f"Auth scheme : {endpoint.authorization_scheme}")
    if endpoint.service_principal_id:
        print(f"App/SPN ID  : {endpoint.service_principal_id}")
    if endpoint.tenant_id:
        print(f"Tenant ID   : {endpoint.tenant_id}")
    if endpoint.subscription_id:
        print(f"Subscription: {endpoint.subscription_id} ({endpoint.subscription_name or 'unnamed'})")


def register_service_endpoint_subcommands(sub):
    p = sub.add_parser("service-endpoints", help="List Azure DevOps service connections/endpoints")
    p.add_argument("--project", required=True, metavar="NAME", help="ADO project name")
    p.add_argument("--name", metavar="NAME", help="Filter to a single endpoint name")
    p.add_argument("--type", metavar="TYPE", help="Filter by endpoint type (e.g. azurerm)")
    p.add_argument("--json", action="store_true", help="Emit structured JSON output for scripting")

    p = sub.add_parser("service-endpoint-show", help="Show one Azure DevOps service connection/endpoint")
    p.add_argument("name", nargs="?", help="Service endpoint name (omit if using --id)")
    p.add_argument("--project", required=True, metavar="NAME", help="ADO project name")
    p.add_argument("--id", metavar="ENDPOINT_ID", help="Service endpoint id (use instead of name)")
    p.add_argument("--json", action="store_true", help="Emit structured JSON output for scripting")
