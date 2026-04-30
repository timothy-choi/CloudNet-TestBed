from typing import Any

from sqlmodel import Session, select

from app.models import DeploymentResource, FailureEvent, Node, Topology
from app.services import openstack_client
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
        openstack_action=openstack_client.stop_server,
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
        openstack_action=openstack_client.start_server,
        success_output_prefix="Started server",
    )


def _record_node_action(
    session: Session,
    topology: Topology,
    node_name: str,
    action: str,
    openstack_action,
    success_output_prefix: str,
) -> FailureEvent:
    if topology.id is None:
        raise FailureError("topology must be saved before failure injection")

    node = _host_node_by_name(topology, node_name)
    if node is None:
        raise FailureError(f"unknown host node '{node_name}'")

    server_resource = _server_resource_by_name(
        list_topology_resources(session, topology.id),
        node.name,
    )
    if server_resource is None:
        raise FailureError(f"server for node '{node_name}' has not been deployed")

    try:
        openstack_action(server_resource.openstack_id)
        server_status = openstack_client.get_server_status(server_resource.openstack_id)
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
        if resource.resource_type == "nova_server" and resource.resource_name == name:
            return resource
    return None
