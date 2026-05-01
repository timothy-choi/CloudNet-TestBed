"""Golden topology examples: compile counts + warnings (no AWS)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.services.topology_validate_service import summarize_compiled_plan, validate_topology_yaml_dict
from app.topology_compiler import compile_topology

_REPO_ROOT = Path(__file__).resolve().parents[1]
TOPOLOGIES = _REPO_ROOT / "examples" / "topologies"


def _load(name: str) -> dict:
    return yaml.safe_load((TOPOLOGIES / name).read_text())


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        (
            "two-node.yaml",
            {
                "vpc_count": 1,
                "subnet_count": 1,
                "instance_count": 2,
                "firewall_rule_count": 0,
                "warning_min": 0,
            },
        ),
        (
            "three-tier.yaml",
            {
                "vpc_count": 1,
                "subnet_count": 2,
                "instance_count": 3,
                "firewall_rule_count": 2,
                "warning_min": 1,
            },
        ),
        (
            "multi-subnet-warning.yaml",
            {
                "vpc_count": 1,
                "subnet_count": 2,
                "instance_count": 3,
                "firewall_rule_count": 0,
                "warning_min": 1,
            },
        ),
        (
            "firewall-icmp.yaml",
            {
                "vpc_count": 1,
                "subnet_count": 1,
                "instance_count": 2,
                "firewall_rule_count": 1,
                "warning_min": 0,
            },
        ),
    ],
)
def test_golden_topology_plan_counts(filename: str, expected: dict) -> None:
    data = _load(filename)
    plan = compile_topology(data)
    summary = summarize_compiled_plan(plan)
    assert summary["vpc_count"] == expected["vpc_count"]
    assert summary["subnet_count"] == expected["subnet_count"]
    assert summary["instance_count"] == expected["instance_count"]
    assert summary["firewall_rule_count"] == expected["firewall_rule_count"]
    assert len(summary["warnings"]) >= expected["warning_min"]

    validated = validate_topology_yaml_dict(data)
    assert validated["ok"] is True
    assert validated["subnet_count"] == expected["subnet_count"]
    assert validated["warnings"] == summary["warnings"]
