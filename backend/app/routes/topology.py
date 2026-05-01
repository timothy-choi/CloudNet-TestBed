from typing import Any
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlmodel import Session, select

from app.db import get_session
from app.models import FirewallRule, Link, Node, Topology
from app.schemas import NodeFailureRequest, PingTestRequest, TopologyInput
from app.services.connectivity_service import (
    ConnectivityTestError,
    connectivity_test_summary,
    create_ping_test,
    list_connectivity_tests,
    serialize_connectivity_test,
    validate_topology_links,
)
from app.services.control_plane_service import (
    ControlPlaneError,
    plan_topology,
    reconcile_topology,
)
from app.services.deployment_service import (
    DeploymentAlreadyExistsError,
    DeploymentError,
    deploy_topology,
    list_topology_resources,
    serialize_deployment_resource,
)
from app.services.event_service import emit_event, list_events, serialize_event
from app.services.drift_service import DriftError, detect_topology_drift
from app.services.failure_service import (
    FailureError,
    failure_event_summary,
    inject_node_down,
    list_failure_events,
    recover_node,
    serialize_failure_event,
)
from app.services.terraform_export_service import export_terraform
from app.services.topology_status_service import build_topology_status
from app.topology_compiler import compile_topology


router = APIRouter(prefix="/topologies", tags=["topologies"])


def serialize_topology(topology: Topology) -> dict[str, Any]:
    return {
        "id": topology.id,
        "name": topology.name,
        "status": topology.status,
        "created_at": topology.created_at,
        "nodes": [
            {"id": node.id, "name": node.name, "type": node.type}
            for node in topology.nodes
        ],
        "links": [
            {
                "id": link.id,
                "from": link.from_node,
                "to": link.to_node,
                "subnet": link.subnet,
            }
            for link in topology.links
        ],
        "firewall_rules": [
            {
                "id": rule.id,
                "name": rule.name,
                "protocol": rule.protocol,
                "port": rule.port,
                "from": rule.from_node,
                "to": rule.to_node,
            }
            for rule in topology.firewall_rules
        ],
    }


@router.post("")
def create_topology(
    topology_input: TopologyInput,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    topology_data = topology_input.model_dump(by_alias=True)

    try:
        compile_topology(topology_data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    topology = Topology(name=topology_input.name)
    session.add(topology)
    session.flush()

    for node in topology_data["nodes"]:
        session.add(
            Node(
                topology_id=topology.id,
                name=node["name"],
                type=node["type"],
            )
        )

    for link in topology_data["links"]:
        session.add(
            Link(
                topology_id=topology.id,
                from_node=link["from"],
                to_node=link["to"],
                subnet=link["subnet"],
            )
        )

    for rule in topology_data["firewall_rules"]:
        session.add(
            FirewallRule(
                topology_id=topology.id,
                name=rule["name"],
                protocol=rule["protocol"],
                port=rule.get("port"),
                from_node=rule["from"],
                to_node=rule["to"],
            )
        )

    session.commit()
    session.refresh(topology)
    return serialize_topology(topology)


@router.get("")
def list_topologies(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    topologies = session.exec(select(Topology)).all()
    return [serialize_topology(topology) for topology in topologies]


@router.get("/{topology_id}")
def get_topology(
    topology_id: int,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    topology = session.get(Topology, topology_id)
    if topology is None:
        raise HTTPException(status_code=404, detail="topology not found")

    return serialize_topology(topology)


@router.get("/{topology_id}/status")
def get_topology_status(
    topology_id: int,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    topology = session.get(Topology, topology_id)
    if topology is None:
        raise HTTPException(status_code=404, detail="topology not found")

    return build_topology_status(session, topology)


@router.post("/{topology_id}/deploy")
def deploy_topology_endpoint(
    topology_id: int,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    topology = session.get(Topology, topology_id)
    if topology is None:
        raise HTTPException(status_code=404, detail="topology not found")

    emit_event(
        session=session,
        topology_id=topology_id,
        event_type="DEPLOY_START",
        status="STARTED",
        message="Deployment started",
    )
    try:
        response = deploy_topology(session, topology)
        instance_count = len(
            [
                resource
                for resource in response["resources"]
                if resource["type"] in {"aws_instance", "nova_server"}
            ]
        )
        emit_event(
            session=session,
            topology_id=topology_id,
            event_type="DEPLOY_COMPLETE",
            status="SUCCESS",
            message=f"Deployed {instance_count} instances",
            metadata={"instance_count": instance_count},
        )
        return response
    except DeploymentAlreadyExistsError as exc:
        emit_event(
            session=session,
            topology_id=topology_id,
            event_type="DEPLOY_COMPLETE",
            status="FAILED",
            message=str(exc),
        )
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DeploymentError as exc:
        emit_event(
            session=session,
            topology_id=topology_id,
            event_type="DEPLOY_COMPLETE",
            status="FAILED",
            message=str(exc),
        )
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/{topology_id}/plan")
def plan_topology_endpoint(
    topology_id: int,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    topology = session.get(Topology, topology_id)
    if topology is None:
        raise HTTPException(status_code=404, detail="topology not found")

    emit_event(
        session=session,
        topology_id=topology_id,
        event_type="PLAN",
        status="STARTED",
        message="Plan compilation started",
    )
    try:
        response = plan_topology(topology)
        emit_event(
            session=session,
            topology_id=topology_id,
            event_type="PLAN",
            status="SUCCESS",
            message="Plan compilation succeeded",
            metadata={
                "subnet_count": len(response["plan"]["subnets"]),
                "instance_count": len(response["plan"]["instances"]),
            },
        )
        return response
    except ControlPlaneError as exc:
        emit_event(
            session=session,
            topology_id=topology_id,
            event_type="PLAN",
            status="FAILED",
            message=str(exc),
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{topology_id}/terraform")
def terraform_export_endpoint(
    topology_id: int,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    topology = session.get(Topology, topology_id)
    if topology is None:
        raise HTTPException(status_code=404, detail="topology not found")

    try:
        return export_terraform(topology)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{topology_id}/terraform.zip")
def terraform_export_zip_endpoint(
    topology_id: int,
    session: Session = Depends(get_session),
) -> Response:
    topology = session.get(Topology, topology_id)
    if topology is None:
        raise HTTPException(status_code=404, detail="topology not found")

    try:
        export = export_terraform(topology)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    buffer = BytesIO()
    with ZipFile(buffer, mode="w", compression=ZIP_DEFLATED) as archive:
        for filename, content in export["files"].items():
            archive.writestr(filename, content)

    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f'attachment; filename="cloudnet-topology-{topology_id}-terraform.zip"'
            )
        },
    )


@router.get("/{topology_id}/drift")
def drift_detection_endpoint(
    topology_id: int,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    topology = session.get(Topology, topology_id)
    if topology is None:
        raise HTTPException(status_code=404, detail="topology not found")

    try:
        return detect_topology_drift(session=session, topology=topology)
    except DriftError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{topology_id}/reconcile")
def reconcile_topology_endpoint(
    topology_id: int,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    topology = session.get(Topology, topology_id)
    if topology is None:
        raise HTTPException(status_code=404, detail="topology not found")

    emit_event(
        session=session,
        topology_id=topology_id,
        event_type="RECONCILE",
        status="STARTED",
        message="Reconcile started",
    )
    try:
        response = reconcile_topology(session, topology)
        if response["drift"]["drift_detected"]:
            emit_event(
                session=session,
                topology_id=topology_id,
                event_type="DRIFT_DETECTED",
                status="SUCCESS",
                message="Drift detected before reconcile",
                metadata={"items": response["drift"]["items"]},
            )
        for action in response["actions"]:
            if action.get("action") == "validate":
                continue
            emit_event(
                session=session,
                topology_id=topology_id,
                event_type="RECONCILE",
                status="SUCCESS",
                message=f"Reconcile action: {action.get('action')}",
                metadata=action,
            )
        emit_event(
            session=session,
            topology_id=topology_id,
            event_type="RECONCILE",
            status="SUCCESS",
            message="Reconcile complete",
            metadata={"action_count": len(response["actions"])},
        )
        return response
    except ControlPlaneError as exc:
        emit_event(
            session=session,
            topology_id=topology_id,
            event_type="RECONCILE",
            status="FAILED",
            message=str(exc),
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{topology_id}/resources")
def get_topology_resources(
    topology_id: int,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    topology = session.get(Topology, topology_id)
    if topology is None:
        raise HTTPException(status_code=404, detail="topology not found")

    resources = list_topology_resources(session, topology_id)
    return {
        "topology_id": topology_id,
        "resources": [
            serialize_deployment_resource(resource)
            for resource in resources
        ],
    }


@router.get("/{topology_id}/events")
def get_topology_events(
    topology_id: int,
    limit: int | None = Query(default=None, ge=1),
    reverse: bool = False,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    topology = session.get(Topology, topology_id)
    if topology is None:
        raise HTTPException(status_code=404, detail="topology not found")

    events = list_events(
        session=session,
        topology_id=topology_id,
        limit=limit,
        reverse=reverse,
    )
    return {
        "topology_id": topology_id,
        "events": [serialize_event(event) for event in events],
    }


@router.post("/{topology_id}/tests/ping")
def create_ping_test_endpoint(
    topology_id: int,
    ping_request: PingTestRequest,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    topology = session.get(Topology, topology_id)
    if topology is None:
        raise HTTPException(status_code=404, detail="topology not found")

    try:
        test = create_ping_test(
            session=session,
            topology=topology,
            source=ping_request.source,
            target=ping_request.target,
        )
    except ConnectivityTestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    emit_event(
        session=session,
        topology_id=topology_id,
        event_type="VALIDATION",
        status="SUCCESS" if test.status == "PASSED" else "FAILED",
        message=f"Ping {ping_request.source} -> {ping_request.target}: {test.status}",
        metadata={
            "source": ping_request.source,
            "target": ping_request.target,
            "test_type": "ping",
        },
    )
    return connectivity_test_summary(test)


@router.get("/{topology_id}/tests")
def get_connectivity_tests(
    topology_id: int,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    topology = session.get(Topology, topology_id)
    if topology is None:
        raise HTTPException(status_code=404, detail="topology not found")

    tests = list_connectivity_tests(session, topology_id)
    return {
        "topology_id": topology_id,
        "tests": [
            serialize_connectivity_test(test)
            for test in tests
        ],
    }


@router.post("/{topology_id}/validate")
def validate_topology_endpoint(
    topology_id: int,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    topology = session.get(Topology, topology_id)
    if topology is None:
        raise HTTPException(status_code=404, detail="topology not found")

    try:
        response = validate_topology_links(session=session, topology=topology)
    except ConnectivityTestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    emit_event(
        session=session,
        topology_id=topology_id,
        event_type="VALIDATION",
        status="SUCCESS" if response["status"] == "PASSED" else "FAILED",
        message=f"Topology validation {response['status']}",
        metadata={"results": response["results"]},
    )
    return response


@router.post("/{topology_id}/failures/node-down")
def inject_node_down_endpoint(
    topology_id: int,
    failure_request: NodeFailureRequest,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    topology = session.get(Topology, topology_id)
    if topology is None:
        raise HTTPException(status_code=404, detail="topology not found")

    try:
        event = inject_node_down(
            session=session,
            topology=topology,
            node_name=failure_request.node,
        )
    except FailureError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    emit_event(
        session=session,
        topology_id=topology_id,
        event_type="FAILURE_INJECTED",
        status="SUCCESS" if event.status == "SUCCESS" else "FAILED",
        message=f"Injected node-down on {failure_request.node}",
        metadata={"node": failure_request.node, "action": "stop_instance"},
    )
    return failure_event_summary(event)


@router.post("/{topology_id}/recover/node")
def recover_node_endpoint(
    topology_id: int,
    recovery_request: NodeFailureRequest,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    topology = session.get(Topology, topology_id)
    if topology is None:
        raise HTTPException(status_code=404, detail="topology not found")

    try:
        event = recover_node(
            session=session,
            topology=topology,
            node_name=recovery_request.node,
        )
    except FailureError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return failure_event_summary(event)


@router.get("/{topology_id}/failures")
def get_failure_events(
    topology_id: int,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    topology = session.get(Topology, topology_id)
    if topology is None:
        raise HTTPException(status_code=404, detail="topology not found")

    events = list_failure_events(session, topology_id)
    return {
        "topology_id": topology_id,
        "failures": [
            serialize_failure_event(event)
            for event in events
        ],
    }


@router.delete("/{topology_id}")
def delete_topology(
    topology_id: int,
    session: Session = Depends(get_session),
) -> dict[str, str]:
    topology = session.get(Topology, topology_id)
    if topology is None:
        raise HTTPException(status_code=404, detail="topology not found")

    session.delete(topology)
    session.commit()
    return {"status": "deleted"}
