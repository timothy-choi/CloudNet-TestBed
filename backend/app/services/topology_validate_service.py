"""Compile and validate topology YAML without deploying (CLI / operators)."""

from __future__ import annotations

from typing import Any

from app.schemas import TopologyInput
from app.services.deployment_service import multi_homed_warnings
from app.services.scenario_quotas import assert_topology_quotas
from app.topology_compiler import compile_topology


def summarize_compiled_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Counts aligned with GET /topologies/{id}/plan (single VPC lab model)."""
    host_instances = sum(1 for s in plan["servers"] if s["type"] == "host")
    return {
        "vpc_count": 1,
        "subnet_count": len(plan["networks"]),
        "instance_count": host_instances,
        "firewall_rule_count": len(plan["firewall_rules"]),
        "warnings": multi_homed_warnings(plan),
    }


def validate_topology_yaml_dict(data: dict[str, Any]) -> dict[str, Any]:
    """
    Schema + compile + subnet semantics + scenario quotas + multi-home warnings.

    Raises ValueError from the compiler, ScenarioError from quotas, or pydantic
    ValidationError from TopologyInput.
    """
    body = TopologyInput.model_validate(data)
    raw = body.model_dump(by_alias=True)
    plan = compile_topology(raw)
    assert_topology_quotas(plan, raw)
    summary = summarize_compiled_plan(plan)
    return {
        "ok": True,
        "topology_name": plan["topology_name"],
        **summary,
    }
