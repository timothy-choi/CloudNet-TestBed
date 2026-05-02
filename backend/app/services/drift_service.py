from typing import Any

from sqlmodel import Session

from app.models import DeploymentResource, Topology
from app.providers.base import BaseProvider
from app.providers.factory import get_provider
from app.resource_types import (
    instance_lookup_types,
    primary_instance_type_for_missing_drift,
    subnet_resource_types_for_drift,
)
from app.services.deployment_service import (
    compile_deployment_plan,
    list_topology_resources,
)
from app.services.trace_logging import log_trace


class DriftError(Exception):
    pass


def _find_instance_resource(
    resources: list[DeploymentResource],
    node_name: str,
    provider: BaseProvider,
) -> DeploymentResource | None:
    for resource_type in instance_lookup_types(provider.name):
        for resource in resources:
            if (
                resource.resource_type == resource_type
                and resource.resource_name == node_name
            ):
                return resource
    return None


def detect_topology_drift(
    session: Session,
    topology: Topology,
    provider: BaseProvider | None = None,
) -> dict[str, Any]:
    if topology.id is None:
        raise DriftError("topology must be saved before drift detection")

    log_trace(
        "INFO",
        "drift_check",
        status="STARTED",
        message=f"topology={topology.name}",
        resource_type="topology",
        resource_id=str(topology.id),
    )

    provider = provider or get_provider()
    if provider.name not in {"aws", "mock"}:
        raise DriftError(
            "drift detection is currently supported for AWS and mock topologies"
        )

    resources = list_topology_resources(session, topology.id)
    subnet_types = subnet_resource_types_for_drift(provider.name)
    missing_label = primary_instance_type_for_missing_drift(provider.name)

    items: list[dict[str, str]] = []

    for node in topology.nodes:
        if node.type != "host":
            continue
        resource = _find_instance_resource(resources, node.name, provider)
        if resource is None:
            items.append(
                _drift_item(
                    resource_type=missing_label,
                    name=node.name,
                    expected="running",
                    actual="missing",
                    severity="critical",
                )
            )
            continue

        try:
            status = provider.get_server_status(resource.openstack_id)
        except Exception:
            items.append(
                _drift_item(
                    resource_type=resource.resource_type,
                    name=node.name,
                    expected="running",
                    actual="missing",
                    severity="critical",
                )
            )
            continue

        if status != "running":
            items.append(
                _drift_item(
                    resource_type=resource.resource_type,
                    name=node.name,
                    expected="running",
                    actual=status,
                    severity="warning",
                )
            )

    for resource in resources:
        if resource.resource_type not in subnet_types:
            continue
        if not provider.resource_exists(resource.resource_type, resource.openstack_id):
            items.append(
                _drift_item(
                    resource_type=resource.resource_type,
                    name=resource.resource_name,
                    expected="present",
                    actual="missing",
                    severity="critical",
                )
            )

    if provider.name != "aws":
        return {
            "topology_id": topology.id,
            "drift_detected": bool(items),
            "items": items,
        }

    security_group_resource = _security_group_resource(resources)
    if security_group_resource is None:
        if resources:
            items.append(
                _drift_item(
                    resource_type="aws_security_group",
                    name="cloudnet-sg",
                    expected="present",
                    actual="missing",
                    severity="critical",
                )
            )
    elif not provider.resource_exists(
        security_group_resource.resource_type,
        security_group_resource.openstack_id,
    ):
        items.append(
            _drift_item(
                resource_type="aws_security_group",
                name=security_group_resource.resource_name,
                expected="present",
                actual="missing",
                severity="critical",
            )
        )
    else:
        plan = compile_deployment_plan(topology)
        for firewall_rule in plan["firewall_rules"]:
            if not provider.firewall_rule_exists(
                security_group_id=security_group_resource.openstack_id,
                firewall_rule=firewall_rule,
            ):
                items.append(
                    _drift_item(
                        resource_type="aws_security_group_rule",
                        name=firewall_rule["name"],
                        expected="present",
                        actual="missing",
                        severity="warning",
                    )
                )

    return {
        "topology_id": topology.id,
        "drift_detected": bool(items),
        "items": items,
    }


def _security_group_resource(
    resources: list[DeploymentResource],
) -> DeploymentResource | None:
    for resource in resources:
        if resource.resource_type == "aws_security_group":
            return resource
    return None


def _drift_item(
    resource_type: str,
    name: str,
    expected: str,
    actual: str,
    severity: str,
) -> dict[str, str]:
    return {
        "resource_type": resource_type,
        "name": name,
        "expected": expected,
        "actual": actual,
        "severity": severity,
    }
