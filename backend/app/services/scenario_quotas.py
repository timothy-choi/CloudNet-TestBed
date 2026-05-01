"""Scenario-level resource and duration quotas (fail-fast before heavy work)."""

from __future__ import annotations

from typing import Any

from app.core.config import get_scenario_quota_settings
from app.schemas import TopologyInput
from app.topology_compiler import compile_topology


def assert_topology_quotas(plan: dict[str, Any], data: dict[str, Any]) -> None:
    """Raise ScenarioError if topology violates configured scenario quotas."""
    from app.services.scenario_service import ScenarioError

    q = get_scenario_quota_settings()
    host_count = sum(1 for n in data.get("nodes") or [] if n.get("type") == "host")
    net_count = len(plan.get("networks") or [])
    cost_units = host_count + net_count

    if host_count > q.max_host_nodes:
        raise ScenarioError(
            f"Scenario quota: topology has {host_count} host nodes, "
            f"max allowed is CLOUDNET_MAX_HOST_NODES_PER_SCENARIO={q.max_host_nodes}"
        )

    if net_count > q.max_networks:
        raise ScenarioError(
            f"Scenario quota: topology implies {net_count} network segments (links), "
            f"max allowed is CLOUDNET_MAX_NETWORKS_PER_SCENARIO={q.max_networks}"
        )

    if net_count > q.max_vpcs_per_run:
        raise ScenarioError(
            f"Scenario quota: {net_count} network segments exceeds "
            f"CLOUDNET_MAX_VPCS_PER_SCENARIO_RUN={q.max_vpcs_per_run}"
        )

    if cost_units > q.max_cost_risk_units:
        raise ScenarioError(
            f"Scenario quota: cost-risk proxy (hosts + network segments) is {cost_units}, "
            f"max allowed is CLOUDNET_MAX_SCENARIO_COST_RISK_UNITS={q.max_cost_risk_units}"
        )


def validate_scenario_topology_quotas(topology_input: TopologyInput) -> None:
    """Raise ScenarioError if topology violates configured scenario quotas."""
    from app.services.scenario_service import ScenarioError

    data = topology_input.model_dump(by_alias=True)
    try:
        plan = compile_topology(data)
    except ValueError as exc:
        raise ScenarioError(str(exc)) from exc

    assert_topology_quotas(plan, data)
