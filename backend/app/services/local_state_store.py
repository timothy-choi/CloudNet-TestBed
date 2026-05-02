"""Persistent deployment snapshot in ``state.json`` (local file, complements SQLite rows)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import REPO_ROOT
from app.models import DeploymentResource
from app.resource_types import (
    AWS_SUBNET,
    AWS_VPC,
    INSTANCE_RESOURCE_TYPES,
    NETWORK_RESOURCE_TYPES,
    SUBNET_RESOURCE_TYPES,
)

STATE_VERSION = 1

DEFAULT_STATE_FILENAME = "state.json"


def get_state_path() -> Path:
    """Path to JSON state file (override with ``CLOUDNET_STATE_FILE``)."""
    override = os.getenv("CLOUDNET_STATE_FILE")
    if override:
        return Path(override).expanduser().resolve()
    return (REPO_ROOT / DEFAULT_STATE_FILENAME).resolve()


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def load_state() -> dict[str, Any]:
    """Load persisted state; return empty skeleton if missing or invalid."""
    path = get_state_path()
    if not path.is_file():
        return {"version": STATE_VERSION, "deployments": {}}
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return {"version": STATE_VERSION, "deployments": {}}
    if not isinstance(data, dict):
        return {"version": STATE_VERSION, "deployments": {}}
    data.setdefault("version", STATE_VERSION)
    deps = data.get("deployments")
    if not isinstance(deps, dict):
        data["deployments"] = {}
    return data


def save_state(state: dict[str, Any]) -> None:
    path = get_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = json.dumps(state, indent=2, sort_keys=True) + "\n"
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)


def _classify_ids(resources: list[dict[str, Any]]) -> dict[str, list[str]]:
    vpc: list[str] = []
    subnets: list[str] = []
    instances: list[str] = []
    for row in resources:
        rt = row.get("resource_type") or row.get("type") or ""
        rid = row.get("openstack_id") or row.get("provider_resource_id") or row.get("id")
        if not rid:
            continue
        if rt == AWS_VPC or rt in NETWORK_RESOURCE_TYPES:
            vpc.append(str(rid))
        elif rt in SUBNET_RESOURCE_TYPES or rt == AWS_SUBNET:
            subnets.append(str(rid))
        elif rt in INSTANCE_RESOURCE_TYPES:
            instances.append(str(rid))
    return {"vpc": vpc, "subnets": subnets, "instances": instances}


def _rows_to_serializable(rows: list[DeploymentResource]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for r in rows:
        out.append(
            {
                "resource_type": r.resource_type,
                "resource_name": r.resource_name,
                "openstack_id": r.openstack_id,
            }
        )
    return out


def record_deploy_snapshot(
    *,
    topology_id: int,
    topology_name: str | None,
    scenario_run_id: int | None,
    resources: list[DeploymentResource],
    status: str,
) -> None:
    """Merge deployment record into ``state.json`` after successful deploy or status update."""
    state = load_state()
    deps: dict[str, Any] = state.setdefault("deployments", {})
    serial = _rows_to_serializable(resources)
    grouped = _classify_ids(
        [
            {"resource_type": r["resource_type"], "openstack_id": r["openstack_id"]}
            for r in serial
        ]
    )
    key = str(topology_id)
    deps[key] = {
        "topology_id": topology_id,
        "topology_name": topology_name or "",
        "scenario_run_id": scenario_run_id,
        "status": status,
        "provider_resource_ids": grouped,
        "resources": serial,
        "updated_at": _iso_now(),
    }
    save_state(state)


def record_deploy_failed(
    *,
    topology_id: int,
    scenario_run_id: int | None,
    topology_name: str | None = None,
    partial_resources: list[DeploymentResource] | None = None,
) -> None:
    state = load_state()
    deps: dict[str, Any] = state.setdefault("deployments", {})
    key = str(topology_id)
    prev_sr = None
    prev_name = topology_name
    prev = deps.get(key)
    if isinstance(prev, dict):
        p = prev.get("scenario_run_id")
        if isinstance(p, int):
            prev_sr = p
        if prev_name is None or prev_name == "":
            pn = prev.get("topology_name")
            if isinstance(pn, str) and pn:
                prev_name = pn
    sr = scenario_run_id if scenario_run_id is not None else prev_sr
    if partial_resources:
        serial = _rows_to_serializable(partial_resources)
        grouped = _classify_ids(
            [
                {"resource_type": r["resource_type"], "openstack_id": r["openstack_id"]}
                for r in serial
            ]
        )
    else:
        serial = []
        grouped = {"vpc": [], "subnets": [], "instances": []}
    deps[key] = {
        "topology_id": topology_id,
        "topology_name": prev_name or "",
        "scenario_run_id": sr,
        "status": "FAILED",
        "provider_resource_ids": grouped,
        "resources": serial,
        "updated_at": _iso_now(),
    }
    save_state(state)


def remove_local_deployment(topology_id: int) -> None:
    """Drop topology entry from local state (after cleanup)."""
    state = load_state()
    deps = state.get("deployments")
    if isinstance(deps, dict) and str(topology_id) in deps:
        del deps[str(topology_id)]
        save_state(state)


def clear_all_local_state() -> None:
    save_state({"version": STATE_VERSION, "deployments": {}})


def find_active_deployment_by_topology_name(state: dict[str, Any], name: str) -> dict[str, Any] | None:
    """Return the newest deployment row with matching ``topology_name`` and ACTIVE status."""
    deps = state.get("deployments")
    if not isinstance(deps, dict):
        return None
    matches: list[tuple[int, dict[str, Any]]] = []
    for _k, row in deps.items():
        if not isinstance(row, dict):
            continue
        if row.get("topology_name") != name:
            continue
        if row.get("status") != "ACTIVE":
            continue
        tid = row.get("topology_id")
        if isinstance(tid, int):
            matches.append((tid, row))
    if not matches:
        return None
    matches.sort(key=lambda x: x[0], reverse=True)
    return matches[0][1]


def deployment_has_resource_name(row: dict[str, Any], resource_name: str) -> bool:
    resources = row.get("resources")
    if not isinstance(resources, list):
        return False
    for item in resources:
        if isinstance(item, dict) and item.get("resource_name") == resource_name:
            return True
    return False


def load_local_state_on_startup() -> dict[str, Any]:
    """Load ``state.json`` when the API starts (validates file is readable)."""
    return load_state()


@dataclass
class ResourceHandle:
    """Minimal deployment row for reconcile when SQLite rows are missing."""

    resource_type: str
    resource_name: str
    openstack_id: str


def resources_from_local_state(topology_id: int) -> list[ResourceHandle]:
    """Hydrate resource handles from JSON for reconcile fallback."""
    state = load_state()
    deps = state.get("deployments") or {}
    row = deps.get(str(topology_id))
    if not isinstance(row, dict):
        return []
    if row.get("status") != "ACTIVE":
        return []
    raw_res = row.get("resources") or []
    if not isinstance(raw_res, list):
        return []
    handles: list[ResourceHandle] = []
    for item in raw_res:
        if not isinstance(item, dict):
            continue
        rt = item.get("resource_type")
        name = item.get("resource_name")
        oid = item.get("openstack_id")
        if rt and name is not None and oid:
            handles.append(
                ResourceHandle(
                    resource_type=str(rt),
                    resource_name=str(name),
                    openstack_id=str(oid),
                )
            )
    return handles
