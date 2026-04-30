from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.db import get_session
from app.models import Link, Node, Topology
from app.schemas import PingTestRequest, TopologyInput
from app.services.connectivity_service import (
    ConnectivityTestError,
    connectivity_test_summary,
    create_ping_test,
    list_connectivity_tests,
    serialize_connectivity_test,
)
from app.services.deployment_service import (
    DeploymentAlreadyExistsError,
    DeploymentError,
    deploy_topology,
    list_topology_resources,
    serialize_deployment_resource,
)
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


@router.post("/{topology_id}/deploy")
def deploy_topology_endpoint(
    topology_id: int,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    topology = session.get(Topology, topology_id)
    if topology is None:
        raise HTTPException(status_code=404, detail="topology not found")

    try:
        return deploy_topology(session, topology)
    except DeploymentAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DeploymentError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


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
