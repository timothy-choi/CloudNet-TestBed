"""Dry-run scenario descriptions (no API, no provider calls)."""

from __future__ import annotations

from typing import Any

from app.core.config import get_cloudnet_provider
from app.schemas import TopologyInput
from app.services.local_state_store import find_active_deployment_by_topology_name, load_state
from app.services.scenario_quotas import validate_scenario_topology_quotas
from app.services.scenario_service import (
    ScenarioError,
    _CleanupStep,
    _DeployStep,
    _DriftStep,
    _FailStep,
    _ReconcileStep,
    _ValidateStep,
    parse_scenario_steps,
)
from app.topology_compiler import compile_topology


def _expected_idempotency_plan_rows(
    compiled: dict[str, Any], provider: str
) -> list[tuple[str, str]]:
    """(label, resource_name) in deploy order; names match DB / state.json entries."""
    rows: list[tuple[str, str]] = []
    networks = compiled["networks"]
    hosts = [s["name"] for s in compiled["servers"] if s["type"] == "host"]
    if provider == "aws":
        if networks:
            rows.append(("VPC", networks[0]["name"]))
        for nw in networks:
            sn = f"{nw['name']}-subnet"
            rows.append(("subnet", sn))
            rows.append(("internet_gateway", f"{sn}-igw"))
            rows.append(("route_table", f"{sn}-rt"))
            rows.append(("route_table_association", f"{sn}-rt-assoc"))
        rows.append(("security_group", "cloudnet-sg"))
        for h in hosts:
            rows.append(("instance", h))
    else:
        for nw in networks:
            rows.append(("network", nw["name"]))
            rows.append(("subnet", f"{nw['name']}-subnet"))
        for h in hosts:
            rows.append(("instance", h))
    return rows


def _idempotency_plan_lines(
    compiled: dict[str, Any], topology_name: str, provider: str
) -> list[str]:
    """CREATE / SKIP / optional DELETE lines vs ``state.json`` for this topology name."""
    expected = _expected_idempotency_plan_rows(compiled, provider)
    desired_names = {name for _, name in expected}

    state = load_state()
    active = find_active_deployment_by_topology_name(state, topology_name)
    recorded: set[str] = set()
    if active:
        for item in active.get("resources") or []:
            if isinstance(item, dict) and item.get("resource_name"):
                recorded.add(str(item["resource_name"]))

    lines: list[str] = []
    lines.append("")
    lines.append(
        "Idempotent deploy (repeat runs skip resources already in SQLite/state.json;"
        " AWS may also skip before create via Project=CloudNet + Name tags):"
    )
    if active and recorded:
        for label, res_name in expected:
            if res_name in recorded:
                lines.append(f"SKIP: {label} {res_name} (in local state)")
            else:
                lines.append(f"CREATE: {label} {res_name}")
        orphans = recorded - desired_names
        if orphans:
            lines.append(
                "DELETE (optional): stale in local state vs compiled topology — "
                + ", ".join(sorted(orphans))
            )
    else:
        for label, res_name in expected:
            lines.append(f"CREATE: {label} {res_name}")
    return lines


def describe_scenario_plan(payload: dict[str, Any]) -> list[str]:
    """
    Return human-readable plan bullets for a scenario YAML payload.

    Raises ScenarioError / ValueError / pydantic.ValidationError on invalid input.
    """
    if not isinstance(payload.get("topology"), dict):
        raise ScenarioError("scenario payload must include topology")
    steps_raw = payload.get("steps")
    if steps_raw is None:
        raise ScenarioError("scenario payload must include steps")
    if not isinstance(steps_raw, list):
        raise ScenarioError("steps must be a list")

    topology_input = TopologyInput.model_validate(payload["topology"])
    validate_scenario_topology_quotas(topology_input)

    raw_topo = topology_input.model_dump(by_alias=True)
    compiled = compile_topology(raw_topo)
    provider = get_cloudnet_provider()

    host_count = sum(1 for s in compiled["servers"] if s["type"] == "host")
    subnet_count = len(compiled["networks"])

    lines: list[str] = []

    lines.append(f"Create 1 VPC")
    lines.append(f"Create {subnet_count} subnet{'s' if subnet_count != 1 else ''}")
    lines.append(
        f"Create {host_count} instance{'s' if host_count != 1 else ''}"
    )
    lines.extend(
        _idempotency_plan_lines(compiled, topology_input.name, provider)
    )

    parsed = parse_scenario_steps(steps_raw)
    failed_nodes: list[str] = []

    for step in parsed:
        if isinstance(step, _DeployStep):
            lines.append("Deploy: provision topology resources (explicit deploy step)")
        elif isinstance(step, _ValidateStep):
            exp = "pass" if step.expect == "pass" else "fail"
            lines.append(f"Validate connectivity (expect {exp})")
        elif isinstance(step, _FailStep):
            failed_nodes.append(step.node)
            lines.append(f"Inject failure: node_down ({step.node})")
        elif isinstance(step, _DriftStep):
            want = "drift present" if step.expect == "detected" else "no drift"
            lines.append(f"Check drift (expect {want})")
        elif isinstance(step, _ReconcileStep):
            targets = sorted(set(failed_nodes))
            if len(targets) == 1:
                lines.append(f"Reconcile actions: restart {targets[0]}")
            elif len(targets) > 1:
                lines.append(f"Reconcile actions: restart {', '.join(targets)}")
            else:
                lines.append(
                    "Reconcile actions: restart stopped instances if needed"
                )
        elif isinstance(step, _CleanupStep):
            lines.append("Cleanup: tear down deployment resources")

    if steps_raw and not any(isinstance(s, _DeployStep) for s in parsed):
        lines.insert(
            3,
            "Deploy: provision resources once before steps (implicit deploy)",
        )

    req = payload.get("requirements")
    if isinstance(req, dict) and req:
        parts: list[str] = []
        if "availability" in req:
            parts.append("availability")
        if "latency" in req:
            parts.append("latency")
        if "recovery" in req:
            parts.append("recovery")
        if parts:
            lines.append(
                "Evaluate requirements: " + ", ".join(parts)
            )

    return lines
