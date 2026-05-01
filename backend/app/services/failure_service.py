from typing import Any

from sqlmodel import Session, select

from app.models import DeploymentResource, FailureEvent, Node, Topology
from app.providers.factory import get_provider
from app.resource_types import INSTANCE_RESOURCE_TYPES
from app.services.deployment_service import list_topology_resources


class FailureError(Exception):
    pass


def serialize_failure_event(event: FailureEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "topology_id": event.topology_id,
        "target_type": event.target_type,
        "target_name": event.target_name,
        "action": event.action,
        "status": event.status,
        "output": event.output,
        "created_at": event.created_at,
    }


def failure_event_summary(event: FailureEvent) -> dict[str, Any]:
    return {
        "topology_id": event.topology_id,
        "target_type": event.target_type,
        "target_name": event.target_name,
        "action": event.action,
        "status": event.status,
        "output": event.output,
    }


def list_failure_events(session: Session, topology_id: int) -> list[FailureEvent]:
    statement = select(FailureEvent).where(
        FailureEvent.topology_id == topology_id
    ).order_by(FailureEvent.id)
    return list(session.exec(statement).all())


def inject_node_down(
    session: Session,
    topology: Topology,
    node_name: str,
) -> FailureEvent:
    return _record_node_action(
        session=session,
        topology=topology,
        node_name=node_name,
        action="node-down",
        provider_action="stop_server",
        success_output_prefix="Stopped server",
    )


def recover_node(
    session: Session,
    topology: Topology,
    node_name: str,
) -> FailureEvent:
    return _record_node_action(
        session=session,
        topology=topology,
        node_name=node_name,
        action="recover-node",
        provider_action="start_server",
        success_output_prefix="Started server",
    )


def _record_node_action(
    session: Session,
    topology: Topology,
    node_name: str,
    action: str,
    provider_action: str,
    success_output_prefix: str,
) -> FailureEvent:
    if topology.id is None:
        raise FailureError("topology must be saved before failure injection")

    node = _host_node_by_name(topology, node_name)
    if node is None:
        raise FailureError(f"unknown host node '{node_name}'")

    resources = list_topology_resources(session, topology.id)
    server_resource = _server_resource_by_name(resources, node.name)
    if server_resource is None:
        available_resources = _available_server_resources(resources)
        raise FailureError(
            f"server for node '{node_name}' has not been deployed; "
            f"available server resources: {available_resources}"
        )

    try:
        provider = get_provider()
        action_result = getattr(provider, provider_action)(server_resource.openstack_id)
        server_status = action_result.get("status")
        if server_status is None:
            server_status = provider.get_server_status(server_resource.openstack_id)
        status = "SUCCESS"
        output = (
            f"{success_output_prefix} {server_resource.openstack_id}; "
            f"current status: {server_status}"
        )
    except Exception as exc:
        status = "FAILED"
        output = str(exc)

    event = FailureEvent(
        topology_id=topology.id,
        target_type="node",
        target_name=node.name,
        action=action,
        status=status,
        output=output,
    )
    session.add(event)
    session.commit()
    session.refresh(event)
    return event


def _host_node_by_name(topology: Topology, name: str) -> Node | None:
    for node in topology.nodes:
        if node.name == name and node.type == "host":
            return node
    return None


def _server_resource_by_name(
    resources: list[DeploymentResource],
    name: str,
) -> DeploymentResource | None:
    for resource in resources:
        if (
            resource.resource_type in INSTANCE_RESOURCE_TYPES
            and resource.resource_name == name
        ):
            return resource
    return None


def _available_server_resources(resources: list[DeploymentResource]) -> str:
    server_resources = [
        f"{resource.resource_type}:{resource.resource_name}"
        for resource in resources
        if resource.resource_type in INSTANCE_RESOURCE_TYPES
    ]
    if not server_resources:
        return "none"
    return ", ".join(server_resources)
