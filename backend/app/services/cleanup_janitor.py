"""Best-effort cleanup of provider resources listed in ``state.json`` without DB rows."""

from __future__ import annotations

from typing import Any

from sqlmodel import Session

from app.models import DeploymentResource, Topology
from app.providers.factory import get_provider
from app.services.deployment_service import list_topology_resources, teardown_provider_resources
from app.services.local_state_store import load_state, remove_local_deployment


def deployment_resources_from_snapshot(
    topology_id: int,
    snapshot: list[dict[str, Any]],
) -> list[DeploymentResource]:
    """Build in-memory deployment rows from persisted JSON (no ORM insert)."""
    out: list[DeploymentResource] = []
    for item in snapshot:
        if not isinstance(item, dict):
            continue
        rt = item.get("resource_type")
        name = item.get("resource_name")
        oid = item.get("openstack_id")
        if rt and name is not None and oid:
            out.append(
                DeploymentResource(
                    topology_id=topology_id,
                    resource_type=str(rt),
                    resource_name=str(name),
                    openstack_id=str(oid),
                )
            )
    return out


def run_cleanup_janitor(session: Session) -> dict[str, Any]:
    """
    Remove orphaned cloud resources: ACTIVE entries in ``state.json`` whose resource IDs
    are not backed by ``deploymentresource`` rows (e.g. crash after provider create).
    """
    provider = get_provider()
    state = load_state()
    deps = state.get("deployments") or {}
    actions: list[dict[str, Any]] = []

    for _key, row in list(deps.items()):
        if not isinstance(row, dict):
            continue
        if row.get("status") != "ACTIVE":
            continue
        tid = row.get("topology_id")
        if tid is None:
            continue
        snapshot = row.get("resources") or []
        if not isinstance(snapshot, list) or not snapshot:
            continue

        db_rows = list_topology_resources(session, int(tid))
        if db_rows:
            continue

        handles = deployment_resources_from_snapshot(int(tid), snapshot)
        if not handles:
            continue

        topo = session.get(Topology, int(tid))
        try:
            teardown_provider_resources(provider, handles)
            remove_local_deployment(int(tid))
            if topo is not None:
                topo.status = "CREATED"
                session.add(topo)
                session.commit()
            actions.append({"topology_id": tid, "result": "cleaned_orphan"})
        except Exception as exc:
            actions.append(
                {"topology_id": tid, "result": "failed", "detail": str(exc)}
            )

    return {"status": "ok", "actions": actions}
