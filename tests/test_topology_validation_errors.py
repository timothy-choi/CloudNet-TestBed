"""Topology validator coverage for supported and unsupported classes."""

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


def test_node_names_unique() -> None:
    with pytest.raises(ValueError, match="duplicate node name"):
        compile_topology(_load("invalid-duplicate-node.yaml"))


def test_links_reference_existing_nodes() -> None:
    with pytest.raises(ValueError, match="unknown node"):
        compile_topology(_load("invalid-missing-node.yaml"))


def test_cidrs_valid() -> None:
    with pytest.raises(ValueError, match="invalid subnet CIDR"):
        compile_topology(_load("invalid-cidr.yaml"))


def test_cidrs_do_not_overlap() -> None:
    with pytest.raises(ValueError, match="overlap"):
        compile_topology(_load("invalid-overlapping-cidr.yaml"))


def test_unsupported_node_types_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported type"):
        compile_topology(_load("invalid-unsupported-node-type.yaml"))


def test_cycles_rejected_unless_explicitly_supported() -> None:
    with pytest.raises(ValueError, match="cycle topology is unsupported"):
        compile_topology(_load("invalid-cycle.yaml"))


def test_arbitrary_mesh_requiring_multiple_enis_rejected() -> None:
    topo = {
        "name": "invalid-mesh",
        "nodes": [
            {"name": "hub", "type": "host"},
            {"name": "a", "type": "host"},
            {"name": "b", "type": "host"},
            {"name": "c", "type": "host"},
        ],
        "links": [
            {"from": "hub", "to": "a", "subnet": "10.73.1.0/24"},
            {"from": "hub", "to": "b", "subnet": "10.73.2.0/24"},
            {"from": "hub", "to": "c", "subnet": "10.73.3.0/24"},
        ],
        "firewall_rules": [],
    }
    with pytest.raises(ValueError, match="arbitrary mesh topology is unsupported"):
        compile_topology(topo)


def test_host_count_limit_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLOUDNET_MAX_HOST_NODES_PER_SCENARIO", "2")
    plan = compile_topology(_load("valid-three-tier.yaml"))
    with pytest.raises(ScenarioError, match="Scenario quota"):
        assert_topology_quotas(plan, _load("valid-three-tier.yaml"))


def test_validate_topology_yaml_dict_wraps_quota(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLOUDNET_MAX_HOST_NODES_PER_SCENARIO", "1")
    with pytest.raises(ScenarioError, match="Scenario quota"):
        validate_topology_yaml_dict(_load("valid-two-node.yaml"))


def test_validate_topology_yaml_dict_ok_after_quota_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLOUDNET_MAX_HOST_NODES_PER_SCENARIO", "16")
    out = validate_topology_yaml_dict(_load("valid-two-node.yaml"))
    assert out["ok"] is True


def test_multi_homed_warning_emitted() -> None:
    out = validate_topology_yaml_dict(_load("partial-multihomed-warning.yaml"))
    assert out["warnings"] == [
        "multi-homed node fe appears in multiple links; attached to first subnet only"
    ]
