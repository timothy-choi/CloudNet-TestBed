from typing import Any

from sqlmodel import Session

from app.core.config import get_cloudnet_provider
from app.models import DeploymentResource, Topology
from app.providers.factory import get_provider
from app.services.connectivity_service import (
    ConnectivityTestError,
    validate_topology_links,
)
from app.services.deployment_service import (
    compile_deployment_plan,
    list_topology_resources,
    multi_homed_warnings,
)


class ControlPlaneError(Exception):
    pass


def plan_topology(topology: Topology) -> dict[str, Any]:
    if topology.id is None:
        raise ControlPlaneError("topology must be saved before planning")

    provider_name = get_cloudnet_provider()
    compiled = compile_deployment_plan(topology)
    host_instances = [
        {"name": server["name"]}
        for server in compiled["servers"]
        if server["type"] == "host"
    ]

    response = {
        "topology_id": topology.id,
        "provider": provider_name,
        "plan": {
            "vpc": {
                "cidr": "10.0.0.0/16",
            },
            "subnets": [
                {"cidr": network["subnet"]}
                for network in compiled["networks"]
            ],
            "instances": host_instances,
            "security_groups": [
                {"name": "cloudnet-sg"},
            ],
        },
    }
    warnings = multi_homed_warnings(compiled)
    if warnings:
        response["warnings"] = warnings
    return response


def reconcile_topology(session: Session, topology: Topology) -> dict[str, Any]:
    if topology.id is None:
        raise ControlPlaneError("topology must be saved before reconcile")

    provider = get_provider()
    if provider.name != "aws":
        raise ControlPlaneError("reconcile is currently supported for AWS topologies")

    actions: list[dict[str, str]] = []
    started_instances: list[str] = []

    for resource in _aws_instance_resources(
        list_topology_resources(session, topology.id)
    ):
        try:
            status = provider.get_server_status(resource.openstack_id)
        except Exception:
            actions.append(
                {
                    "node": resource.resource_name,
                    "action": "MISSING",
                    "result": "missing",
                }
            )
            continue

        if status == "stopped":
            provider.start_server(resource.openstack_id)
            started_instances.append(resource.openstack_id)
            actions.append(
                {
                    "node": resource.resource_name,
                    "action": "start",
                    "result": "started",
                }
            )
        elif status in {"terminated", "shutting-down"}:
            actions.append(
                {
                    "node": resource.resource_name,
                    "action": "MISSING",
                    "result": status,
                }
            )

    for instance_id in started_instances:
        provider.wait_for_server_running(instance_id)

    validation_status = _run_default_validation(session=session, topology=topology)
    actions.append({"action": "validate", "result": validation_status})

    return {
        "topology_id": topology.id,
        "status": "RECONCILED",
        "actions": actions,
    }


def _aws_instance_resources(
    resources: list[DeploymentResource],
) -> list[DeploymentResource]:
    return [
        resource
        for resource in resources
        if resource.resource_type == "aws_instance"
    ]


def _run_default_validation(session: Session, topology: Topology) -> str:
    try:
        validation = validate_topology_links(session=session, topology=topology)
    except ConnectivityTestError as exc:
        raise ControlPlaneError(str(exc)) from exc

    return str(validation["status"])
