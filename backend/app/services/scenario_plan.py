"""Dry-run scenario descriptions (no API, no provider calls)."""

from __future__ import annotations

from typing import Any

from app.schemas import TopologyInput
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

    host_count = sum(1 for s in compiled["servers"] if s["type"] == "host")
    subnet_count = len(compiled["networks"])

    lines: list[str] = []

    lines.append(f"Create 1 VPC")
    lines.append(f"Create {subnet_count} subnet{'s' if subnet_count != 1 else ''}")
    lines.append(
        f"Create {host_count} instance{'s' if host_count != 1 else ''}"
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
