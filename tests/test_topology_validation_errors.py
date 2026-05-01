"""Invalid topologies loaded from examples/topologies (no AWS)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.services.scenario_quotas import assert_topology_quotas
from app.services.scenario_service import ScenarioError
from app.services.topology_validate_service import validate_topology_yaml_dict
from app.topology_compiler import compile_topology

_REPO_ROOT = Path(__file__).resolve().parents[1]
TOPOLOGIES = _REPO_ROOT / "examples" / "topologies"


def _load(name: str) -> dict:
    return yaml.safe_load((TOPOLOGIES / name).read_text())


def test_invalid_missing_node() -> None:
    with pytest.raises(ValueError, match="unknown node"):
        compile_topology(_load("invalid-missing-node.yaml"))


def test_invalid_overlapping_subnets() -> None:
    with pytest.raises(ValueError, match="overlap"):
        compile_topology(_load("invalid-overlapping-subnets.yaml"))


def test_invalid_cidr() -> None:
    with pytest.raises(ValueError, match="invalid subnet CIDR"):
        compile_topology(_load("invalid-cidr.yaml"))


def test_unsupported_load_balancer_type() -> None:
    with pytest.raises(ValueError, match="unsupported type"):
        compile_topology(_load("unsupported-load-balancer.yaml"))


def test_duplicate_node_names() -> None:
    topo = {
        "name": "dup",
        "nodes": [
            {"name": "x", "type": "host"},
            {"name": "x", "type": "host"},
        ],
        "links": [],
        "firewall_rules": [],
    }
    with pytest.raises(ValueError, match="duplicate node name"):
        compile_topology(topo)


def test_quota_too_many_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLOUDNET_MAX_HOST_NODES_PER_SCENARIO", "2")
    topo = {
        "name": "too-many",
        "nodes": [
            {"name": "a", "type": "host"},
            {"name": "b", "type": "host"},
            {"name": "c", "type": "host"},
        ],
        "links": [
            {"from": "a", "to": "b", "subnet": "10.90.1.0/24"},
            {"from": "b", "to": "c", "subnet": "10.90.2.0/24"},
        ],
        "firewall_rules": [],
    }
    plan = compile_topology(topo)
    with pytest.raises(ScenarioError, match="Scenario quota"):
        assert_topology_quotas(plan, topo)


def test_validate_topology_yaml_dict_wraps_quota(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLOUDNET_MAX_HOST_NODES_PER_SCENARIO", "1")
    data = _load("two-node.yaml")
    with pytest.raises(ScenarioError, match="Scenario quota"):
        validate_topology_yaml_dict(data)


def test_validate_topology_yaml_dict_ok_after_quota_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLOUDNET_MAX_HOST_NODES_PER_SCENARIO", "16")
    out = validate_topology_yaml_dict(_load("two-node.yaml"))
    assert out["ok"] is True
