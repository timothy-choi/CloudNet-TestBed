from typing import Any

from sqlmodel import Session

from app.core.config import get_cloudnet_provider
from app.models import Topology
from app.providers.factory import get_provider
from app.resource_types import AWS_SECURITY_GROUP, instance_types_filter
from app.services.connectivity_service import (
    ConnectivityTestError,
    validate_topology_links,
)
from app.services.deployment_service import (
    compile_deployment_plan,
    list_topology_resources,
    multi_homed_warnings,
)
from app.services.drift_service import detect_topology_drift
from app.services.local_state_store import resources_from_local_state
from app.services.trace_logging import log_trace


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
            "firewall_rules": compiled["firewall_rules"],
        },
    }
    warnings = multi_homed_warnings(compiled)
    if warnings:
        response["warnings"] = warnings
    return response


def reconcile_topology(session: Session, topology: Topology) -> dict[str, Any]:
    if topology.id is None:
        raise ControlPlaneError("topology must be saved before reconcile")

    log_trace(
        "INFO",
        "reconcile_topology",
        status="STARTED",
        message=f"topology={topology.name}",
        resource_type="topology",
        resource_id=str(topology.id),
    )

    provider = get_provider()
    if provider.name not in {"aws", "mock"}:
        raise ControlPlaneError(
            "reconcile is currently supported for AWS and mock topologies"
        )

    actions: list[dict[str, str]] = []
    started_instances: list[str] = []
    resources = list_topology_resources(session, topology.id)
    if not resources:
        resources = resources_from_local_state(topology.id)
    drift = detect_topology_drift(session=session, topology=topology, provider=provider)

    for resource in _repairable_instance_resources(resources, provider.name):
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

    plan = compile_deployment_plan(topology)
    if provider.name == "aws" and plan["firewall_rules"]:
        security_group_resource = _aws_security_group_resource(resources)
        if security_group_resource is None:
            raise ControlPlaneError("CloudNet security group has not been deployed")
        for result in provider.ensure_firewall_rules(
            security_group_id=security_group_resource.openstack_id,
            firewall_rules=plan["firewall_rules"],
        ):
            if result["result"] == "created":
                actions.append(
                    {
                        "resource": "cloudnet-sg",
                        "action": "restore_firewall_rule",
                        "result": "created",
                    }
                )

    validation_status = _run_default_validation(session=session, topology=topology)
    actions.append({"action": "validate", "result": validation_status})

    return {
        "topology_id": topology.id,
        "status": "RECONCILED",
        "drift": drift,
        "actions": actions,
    }


def _repairable_instance_resources(
    resources: list[Any],
    provider_name: str,
) -> list[Any]:
    allowed = instance_types_filter(provider_name)
    return [
        resource
        for resource in resources
        if getattr(resource, "resource_type", None) in allowed
    ]


def _aws_security_group_resource(resources: list[Any]) -> Any | None:
    for resource in resources:
        if getattr(resource, "resource_type", None) == AWS_SECURITY_GROUP:
            return resource
    return None


def _run_default_validation(session: Session, topology: Topology) -> str:
    try:
        validation = validate_topology_links(session=session, topology=topology)
    except ConnectivityTestError as exc:
        raise ControlPlaneError(str(exc)) from exc

    return str(validation["status"])
