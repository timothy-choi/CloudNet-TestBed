from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.db import get_session
from app.models import Link, Node, Topology
from app.schemas import TopologyInput
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
