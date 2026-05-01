from collections.abc import Generator
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from app.db import get_session
from app.main import app
from app.providers.mock_provider import MockProvider
from app.services import connectivity_service
from app.services import control_plane_service
from app.services import deployment_service
from app.services import drift_service
from app.services import failure_service


@pytest.fixture
def client(tmp_path: Path) -> Generator[TestClient, None, None]:
    database_url = f"sqlite:///{tmp_path / 'scenario.db'}"
    engine = create_engine(
        database_url,
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)

    def override_get_session() -> Generator[Session, None, None]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def mock_stack(monkeypatch) -> MockProvider:
    provider = MockProvider()
    for module in (
        deployment_service,
        failure_service,
        control_plane_service,
        connectivity_service,
        drift_service,
    ):
        monkeypatch.setattr(module, "get_provider", lambda p=provider: p)
    monkeypatch.setattr(
        provider,
        "create_network",
        lambda name, cidr=None: {"id": "net-1", "name": name, "status": "ACTIVE"},
    )
    monkeypatch.setattr(
        provider,
        "create_subnet",
        lambda network_id, name, cidr: {
            "id": "subnet-1",
            "name": name,
            "cidr": cidr,
            "network_id": network_id,
        },
    )
    monkeypatch.setattr(
        provider,
        "create_server",
        lambda name, network_id, subnet_id=None: {
            "id": f"mock-server-{name}",
            "name": name,
            "status": "running",
            "addresses": {},
        },
    )
    return provider


def test_scenario_run_backend_failure_flow(client: TestClient, monkeypatch) -> None:
    mock_stack(monkeypatch)

    response = client.post(
        "/scenarios/run",
        json={
            "scenario": {"name": "backend_failure_test"},
            "topology": {
                "name": "scenario-two-host",
                "nodes": [
                    {"name": "client-a", "type": "host"},
                    {"name": "client-b", "type": "host"},
                ],
                "links": [
                    {
                        "from": "client-a",
                        "to": "client-b",
                        "subnet": "10.99.1.0/24",
                    },
                ],
                "firewall_rules": [],
            },
            "steps": [
                {"validate": "all"},
                {"fail": {"node": "client-b"}},
                {"validate": {"expect": "fail"}},
                {"drift": {"expect": "detected"}},
                {"reconcile": True},
                {"validate": {"expect": "pass"}},
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["scenario"] == "backend_failure_test"
    assert body["status"] == "PASSED"
    assert body["topology_name"] == "scenario-two-host"
    steps = body["steps"]
    assert [s["step"] for s in steps] == [
        "validate",
        "fail client-b",
        "validate",
        "drift",
        "reconcile",
        "validate",
    ]
    assert steps[0]["result"] == "PASSED"
    assert steps[0]["step_passed"] is True
    assert steps[1]["result"] == "SUCCESS"
    assert steps[2]["result"] == "FAILED"
    assert steps[2]["step_passed"] is True
    assert steps[3]["result"] == "DETECTED"
    assert steps[3]["step_passed"] is True
    assert steps[4]["result"] == "RECONCILED"
    assert steps[5]["result"] == "PASSED"


def test_scenario_run_accepts_yaml_body(client: TestClient, monkeypatch) -> None:
    mock_stack(monkeypatch)

    payload = {
        "scenario": {"name": "yaml_payload"},
        "topology": {
            "name": "scenario-yaml-body",
            "nodes": [{"name": "solo", "type": "host"}],
            "links": [{"from": "solo", "to": "solo", "subnet": "10.98.1.0/24"}],
            "firewall_rules": [],
        },
        "steps": [{"validate": "all"}],
    }
    response = client.post(
        "/scenarios/run",
        content=yaml.dump(payload),
        headers={"Content-Type": "application/x-yaml"},
    )
    assert response.status_code == 200
    assert response.json()["scenario"] == "yaml_payload"


def test_scenario_run_rejects_bad_step(client: TestClient, monkeypatch) -> None:
    mock_stack(monkeypatch)

    response = client.post(
        "/scenarios/run",
        json={
            "scenario": {"name": "bad"},
            "topology": {
                "name": "scenario-bad-step",
                "nodes": [{"name": "a", "type": "host"}],
                "links": [{"from": "a", "to": "a", "subnet": "10.1.1.0/24"}],
                "firewall_rules": [],
            },
            "steps": [{"unknown": True}],
        },
    )

    assert response.status_code == 400
