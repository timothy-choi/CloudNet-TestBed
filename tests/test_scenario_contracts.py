"""Scenario behavior contracts for the mock provider.

These tests exercise the same command path reviewers use (`cloudnet run`) while
keeping the API in-process with FastAPI's TestClient. The assertions intentionally
focus on stable scenario behavior, not generated ids or timings.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.db import get_session
from app.main import app
from app.providers.mock_provider import MockProvider
from app.services import connectivity_service
from app.services import control_plane_service
from app.services import deployment_service
from app.services import drift_service
from app.services import failure_service
from cli.cloudnet import cmd_run


EXAMPLES = _REPO_ROOT / "examples"
TOPOLOGIES = EXAMPLES / "topologies"


@pytest.fixture
def client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[TestClient, None, None]:
    database_url = f"sqlite:///{tmp_path / 'scenario_contracts.db'}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)

    def override_get_session() -> Generator[Session, None, None]:
        with Session(engine) as session:
            yield session

    provider = MockProvider()
    for module in (
        deployment_service,
        failure_service,
        control_plane_service,
        connectivity_service,
        drift_service,
    ):
        monkeypatch.setattr(module, "get_provider", lambda p=provider: p)
    monkeypatch.setenv("CLOUDNET_PROVIDER", "mock")
    monkeypatch.setenv("CLOUDNET_MOCK_PING_LOSS_RATE", "0")

    app.dependency_overrides[get_session] = override_get_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text())


def _write_scenario(tmp_path: Path, name: str, payload: dict[str, Any]) -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(payload, sort_keys=False))
    return path


def _run_args(path: Path) -> argparse.Namespace:
    return argparse.Namespace(file=str(path), json=False, cleanup=False)


def _normalize(body: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {
        "scenario": body["scenario"],
        "status": body["status"],
        "steps": [
            {
                "action": step.get("action"),
                "name": step.get("name"),
                "status": step.get("status"),
                "expected": step.get("expected"),
                "actual": step.get("actual"),
            }
            for step in body.get("steps", [])
        ],
    }
    if "requirements" in body:
        normalized["requirements"] = {
            category: payload.get("status")
            for category, payload in body["requirements"].items()
        }
    return normalized


def _last_response_json(client: TestClient) -> dict[str, Any]:
    response = client.get("/scenarios/1/results")
    assert response.status_code == 200
    return response.json()


def test_backend_failure_golden_contract(client: TestClient) -> None:
    scenario = _load_yaml(EXAMPLES / "backend-failure.yaml")
    scenario["requirements"] = {
        "availability": {"min_success_rate": 0.5},
        "latency": {"max_avg_ms": 500, "max_p95_ms": 500},
        "recovery": {"max_recovery_seconds": 3600},
    }

    response = client.post("/scenarios/run", json=scenario)

    assert response.status_code == 200
    assert _normalize(response.json()) == {
        "scenario": "backend_failure_test",
        "status": "PASSED",
        "steps": [
            {
                "action": "deploy",
                "name": "deploy",
                "status": "PASSED",
                "expected": "ACTIVE",
                "actual": "ACTIVE",
            },
            {
                "action": "validate",
                "name": "validate",
                "status": "PASSED",
                "expected": "PASSED",
                "actual": "PASSED",
            },
            {
                "action": "fail",
                "name": "fail backend",
                "status": "PASSED",
                "expected": "SUCCESS",
                "actual": "SUCCESS",
            },
            {
                "action": "validate",
                "name": "validate",
                "status": "PASSED",
                "expected": "FAILED",
                "actual": "FAILED",
            },
            {
                "action": "drift",
                "name": "drift",
                "status": "PASSED",
                "expected": "DETECTED",
                "actual": "DETECTED",
            },
            {
                "action": "reconcile",
                "name": "reconcile",
                "status": "PASSED",
                "expected": "RECONCILED",
                "actual": "RECONCILED",
            },
            {
                "action": "validate",
                "name": "validate",
                "status": "PASSED",
                "expected": "PASSED",
                "actual": "PASSED",
            },
        ],
        "requirements": {
            "availability": "PASSED",
            "latency": "PASSED",
            "recovery": "PASSED",
        },
    }


@pytest.mark.parametrize(
    ("scenario_path", "expected"),
    [
        (
            EXAMPLES / "simple-connectivity.yaml",
            {
                "scenario": "simple_connectivity_test",
                "status": "PASSED",
                "steps": [
                    {
                        "action": "deploy",
                        "name": "deploy",
                        "status": "PASSED",
                        "expected": "ACTIVE",
                        "actual": "ACTIVE",
                    },
                    {
                        "action": "validate",
                        "name": "validate",
                        "status": "PASSED",
                        "expected": "PASSED",
                        "actual": "PASSED",
                    },
                ],
            },
        ),
    ],
)
def test_tracked_scenario_golden_contracts(
    client: TestClient,
    scenario_path: Path,
    expected: dict[str, Any],
) -> None:
    response = client.post("/scenarios/run", json=_load_yaml(scenario_path))

    assert response.status_code == 200
    assert _normalize(response.json()) == expected


def test_multi_subnet_topology_scenario_contract(client: TestClient) -> None:
    scenario = {
        "scenario": {"name": "multi_subnet_contract"},
        "topology": _load_yaml(TOPOLOGIES / "valid-multi-subnet-chain.yaml"),
        "steps": [
            {"deploy": True},
            {"validate": "all"},
        ],
    }

    response = client.post("/scenarios/run", json=scenario)

    assert response.status_code == 200
    assert _normalize(response.json()) == {
        "scenario": "multi_subnet_contract",
        "status": "PASSED",
        "steps": [
            {
                "action": "deploy",
                "name": "deploy",
                "status": "PASSED",
                "expected": "ACTIVE",
                "actual": "ACTIVE",
            },
            {
                "action": "validate",
                "name": "validate",
                "status": "PASSED",
                "expected": "PASSED",
                "actual": "PASSED",
            },
        ],
    }


def test_invalid_topology_scenario_fails_early(client: TestClient) -> None:
    scenario = {
        "scenario": {"name": "invalid_topology_contract"},
        "topology": _load_yaml(TOPOLOGIES / "invalid-missing-node.yaml"),
        "steps": [{"validate": "all"}],
    }

    response = client.post("/scenarios/run", json=scenario)

    assert response.status_code == 400
    assert "unknown node" in response.json()["detail"]


def test_cli_contract_success_exit_and_key_steps(
    client: TestClient,
    capsys: pytest.CaptureFixture[str],
) -> None:
    scenario_path = EXAMPLES / "backend-failure.yaml"

    assert cmd_run(client, _run_args(scenario_path)) == 0

    out = capsys.readouterr().out
    assert "Running scenario: backend_failure_test" in out
    assert "Validate ✔ PASSED" in out
    assert "Fail backend ✔" in out
    assert "Drift ✔ detected" in out
    assert "Reconcile ✔ repaired" in out
    assert "Scenario PASSED" in out


def test_cli_expected_pass_but_fails_exits_one(
    client: TestClient,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    scenario = _load_yaml(EXAMPLES / "backend-failure.yaml")
    scenario["scenario"]["name"] = "expected_pass_but_fails"
    scenario["steps"] = [
        {"deploy": True},
        {"validate": "all"},
        {"fail": {"node": "backend"}},
        {"validate": {"expect": "pass"}},
    ]
    path = _write_scenario(tmp_path, "expected-pass-but-fails.yaml", scenario)

    assert cmd_run(client, _run_args(path)) == 1

    out = capsys.readouterr().out
    assert "Validate ✖ FAILED" in out
    assert "Scenario FAILED" in out
    assert _last_response_json(client)["status"] == "FAILED"


def test_cli_expected_fail_but_passes_exits_one(
    client: TestClient,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    scenario = _load_yaml(EXAMPLES / "simple-connectivity.yaml")
    scenario["scenario"]["name"] = "expected_fail_but_passes"
    scenario["steps"] = [
        {"deploy": True},
        {"validate": {"expect": "fail"}},
    ]
    path = _write_scenario(tmp_path, "expected-fail-but-passes.yaml", scenario)

    assert cmd_run(client, _run_args(path)) == 1

    out = capsys.readouterr().out
    assert "Validate ✖ PASSED" in out
    assert "Scenario FAILED" in out
    assert _last_response_json(client)["status"] == "FAILED"


def test_cli_invalid_topology_exits_one(
    client: TestClient,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    scenario = {
        "scenario": {"name": "invalid_topology_cli_contract"},
        "topology": _load_yaml(TOPOLOGIES / "invalid-missing-node.yaml"),
        "steps": [{"validate": "all"}],
    }
    path = _write_scenario(tmp_path, "invalid-topology.yaml", scenario)

    assert cmd_run(client, _run_args(path)) == 1

    err = capsys.readouterr().err
    assert "scenario run failed: 400" in err
    assert "unknown node" in err
