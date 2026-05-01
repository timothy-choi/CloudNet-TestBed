"""Golden topology examples: compile counts + warnings (no AWS)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.services.topology_validate_service import (
    summarize_compiled_plan,
    validate_topology_yaml_dict,
)
from app.topology_compiler import compile_topology

_REPO_ROOT = Path(__file__).resolve().parents[1]
TOPOLOGIES = _REPO_ROOT / "examples" / "topologies"


def _load(name: str) -> dict:
    return yaml.safe_load((TOPOLOGIES / name).read_text())


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        (
            "valid-two-node.yaml",
            {
                "vpc_count": 1,
                "subnet_count": 1,
                "instance_count": 2,
                "firewall_rule_count": 0,
                "warnings": [],
            },
        ),
        (
            "valid-three-tier.yaml",
            {
                "vpc_count": 1,
                "subnet_count": 2,
                "instance_count": 3,
                "firewall_rule_count": 2,
                "warnings": [
                    "multi-homed node backend appears in multiple links; "
                    "attached to first subnet only"
                ],
            },
        ),
        (
            "valid-multi-subnet-chain.yaml",
            {
                "vpc_count": 1,
                "subnet_count": 3,
                "instance_count": 4,
                "firewall_rule_count": 0,
                "warnings": [
                    "multi-homed node api appears in multiple links; "
                    "attached to first subnet only",
                    "multi-homed node worker appears in multiple links; "
                    "attached to first subnet only",
                ],
            },
        ),
        (
            "valid-firewall-icmp.yaml",
            {
                "vpc_count": 1,
                "subnet_count": 1,
                "instance_count": 2,
                "firewall_rule_count": 1,
                "warnings": [],
            },
        ),
        (
            "partial-multihomed-warning.yaml",
            {
                "vpc_count": 1,
                "subnet_count": 2,
                "instance_count": 3,
                "firewall_rule_count": 0,
                "warnings": [
                    "multi-homed node fe appears in multiple links; "
                    "attached to first subnet only"
                ],
            },
        ),
    ],
)
def test_golden_topology_plan_counts(filename: str, expected: dict) -> None:
    data = _load(filename)
    plan = compile_topology(data)
    summary = summarize_compiled_plan(plan)
    assert summary == expected

    validated = validate_topology_yaml_dict(data)
    assert validated["ok"] is True
    assert validated["vpc_count"] == expected["vpc_count"]
    assert validated["subnet_count"] == expected["subnet_count"]
    assert validated["instance_count"] == expected["instance_count"]
    assert validated["firewall_rule_count"] == expected["firewall_rule_count"]
    assert validated["warnings"] == expected["warnings"]
