from typing import Any

from sqlmodel import Session, select

from app.models import DeploymentResource, Event, Topology
from app.providers.factory import get_provider
from app.resource_types import INSTANCE_RESOURCE_TYPES, SUBNET_RESOURCE_TYPES
from app.services.deployment_service import list_topology_resources
from app.services.drift_service import DriftError, detect_topology_drift

_SECURITY_GROUP_TYPES = frozenset({"aws_security_group"})


def resources_summary(resources: list[DeploymentResource]) -> dict[str, int]:
    return {
        "instances": sum(
            1 for r in resources if r.resource_type in INSTANCE_RESOURCE_TYPES
        ),
        "subnets": sum(1 for r in resources if r.resource_type in SUBNET_RESOURCE_TYPES),
        "security_groups": sum(
            1 for r in resources if r.resource_type in _SECURITY_GROUP_TYPES
        ),
    }


def latest_validation_label(session: Session, topology_id: int) -> str | None:
    statement = (
        select(Event)
        .where(Event.topology_id == topology_id)
        .where(Event.type == "VALIDATION")
        .order_by(Event.timestamp.desc(), Event.id.desc())
        .limit(1)
    )
    event = session.exec(statement).first()
    if event is None:
        return None
    if event.status == "SUCCESS":
        return "PASSED"
    if event.status == "FAILED":
        return "FAILED"
    return event.status


def build_topology_status(session: Session, topology: Topology) -> dict[str, Any]:
    if topology.id is None:
        raise ValueError("topology must be persisted")

    provider = get_provider()
    resources = list_topology_resources(session, topology.id)
    summary = resources_summary(resources)

    drift_detected = False
    try:
        drift = detect_topology_drift(
            session=session,
            topology=topology,
            provider=provider,
        )
        drift_detected = bool(drift.get("drift_detected"))
    except DriftError:
        drift_detected = False

    return {
        "topology_id": topology.id,
        "status": topology.status,
        "provider": provider.name,
        "resources_summary": summary,
        "last_validation": latest_validation_label(session, topology.id),
        "drift_detected": drift_detected,
    }
