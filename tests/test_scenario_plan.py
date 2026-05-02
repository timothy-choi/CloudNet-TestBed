"""Dry-run scenario plan (describe_scenario_plan)."""

from __future__ import annotations

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
