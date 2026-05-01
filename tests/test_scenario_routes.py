from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from app.db import get_session
from app.main import app
from app.providers.mock_provider import MockProvider
from app.services import connectivity_service
from app.services import control_plane_service
from app.services import deployment_service
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
    steps = [s["step"] for s in body["steps"]]
    assert steps == [
        "validate",
        "fail client-b",
        "validate",
        "reconcile",
        "validate",
    ]
    assert body["steps"][0]["result"] == "PASSED"
    assert body["steps"][1]["result"] == "SUCCESS"
    assert body["steps"][2]["result"] == "FAILED"
    assert body["steps"][3]["result"] == "RECONCILED"
    assert body["steps"][4]["result"] == "PASSED"


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
