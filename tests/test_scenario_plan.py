"""Dry-run scenario plan (describe_scenario_plan)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.resource_types import PROVIDER_INSTANCE, PROVIDER_NETWORK, PROVIDER_SUBNET
from app.services.local_state_store import record_deploy_snapshot
from app.services.scenario_plan import describe_scenario_plan


def test_plan_backend_failure_shape() -> None:
    payload = {
        "scenario": {"name": "t"},
        "topology": {
            "name": "three-tier-app",
            "nodes": [
                {"name": "frontend", "type": "host"},
                {"name": "backend", "type": "host"},
                {"name": "db", "type": "host"},
            ],
            "links": [
                {"from": "frontend", "to": "backend", "subnet": "10.100.1.0/24"},
                {"from": "backend", "to": "db", "subnet": "10.100.2.0/24"},
            ],
            "firewall_rules": [],
        },
        "steps": [
            {"deploy": True},
            {"validate": "all"},
            {"fail": {"node": "backend"}},
            {"validate": {"expect": "fail"}},
            {"drift": {"expect": "detected"}},
            {"reconcile": True},
            {"validate": {"expect": "pass"}},
        ],
    }
    lines = describe_scenario_plan(payload)
    assert any("Create 1 VPC" in x for x in lines)
    assert any("Create 2 subnets" in x for x in lines)
    assert any("Create 3 instances" in x for x in lines)
    assert any("Inject failure: node_down (backend)" in x for x in lines)
    assert any("Reconcile actions: restart backend" in x for x in lines)


def test_plan_implicit_deploy_note() -> None:
    payload = {
        "scenario": {"name": "x"},
        "topology": {
            "name": "two",
            "nodes": [
                {"name": "a", "type": "host"},
                {"name": "b", "type": "host"},
            ],
            "links": [{"from": "a", "to": "b", "subnet": "10.1.1.0/24"}],
            "firewall_rules": [],
        },
        "steps": [{"validate": "all"}],
    }
    lines = describe_scenario_plan(payload)
    assert any("implicit deploy" in x.lower() for x in lines)


def test_plan_requirements_line() -> None:
    payload = {
        "scenario": {"name": "x"},
        "topology": {
            "name": "two",
            "nodes": [
                {"name": "a", "type": "host"},
                {"name": "b", "type": "host"},
            ],
            "links": [{"from": "a", "to": "b", "subnet": "10.1.1.0/24"}],
            "firewall_rules": [],
        },
        "steps": [{"validate": "all"}],
        "requirements": {"latency": {"max_avg_ms": 100}},
    }
    lines = describe_scenario_plan(payload)
    assert any("Evaluate requirements:" in x and "latency" in x for x in lines)


def test_plan_idempotency_shows_skip_when_state_matches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLOUDNET_STATE_FILE", str(tmp_path / "plan-state.json"))
    monkeypatch.setenv("CLOUDNET_PROVIDER", "mock")

    class Row:
        def __init__(self, resource_type: str, resource_name: str, openstack_id: str):
            self.resource_type = resource_type
            self.resource_name = resource_name
            self.openstack_id = openstack_id

    record_deploy_snapshot(
        topology_id=1,
        topology_name="two",
        scenario_run_id=None,
        resources=[
            Row(PROVIDER_NETWORK, "two-net-1", "n1"),
            Row(PROVIDER_SUBNET, "two-net-1-subnet", "s1"),
            Row(PROVIDER_INSTANCE, "a", "i1"),
            Row(PROVIDER_INSTANCE, "b", "i2"),
        ],  # type: ignore[arg-type]
        status="ACTIVE",
    )

    payload = {
        "scenario": {"name": "x"},
        "topology": {
            "name": "two",
            "nodes": [
                {"name": "a", "type": "host"},
                {"name": "b", "type": "host"},
            ],
            "links": [{"from": "a", "to": "b", "subnet": "10.1.1.0/24"}],
            "firewall_rules": [],
        },
        "steps": [{"validate": "all"}],
    }
    lines = describe_scenario_plan(payload)
    assert any(
        "SKIP:" in ln and "two-net-1-subnet" in ln for ln in lines
    ), lines
    assert any("SKIP:" in ln for ln in lines)
