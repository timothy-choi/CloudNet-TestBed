"""End-to-end scenario on a golden topology shape (mock provider, no AWS)."""

from __future__ import annotations

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

_REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def client(tmp_path: Path) -> Generator[TestClient, None, None]:
    database_url = f"sqlite:///{tmp_path / 'topo_scenario.db'}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)

    def override_get_session() -> Generator[Session, None, None]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_supported_flow_on_two_node_topology(client: TestClient, monkeypatch) -> None:
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
            "id": f"srv-{name}",
            "name": name,
            "status": "running",
            "addresses": {},
        },
    )

    topo_path = _REPO_ROOT / "examples" / "topologies" / "two-node.yaml"
    topology = yaml.safe_load(topo_path.read_text())

    response = client.post(
        "/scenarios/run",
        json={
            "scenario": {"name": "golden_two_node_flow"},
            "topology": topology,
            "steps": [
                {"deploy": True},
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
    assert body["status"] == "PASSED"
    actions = [s["action"] for s in body["steps"]]
    assert actions == [
        "deploy",
        "validate",
        "fail",
        "validate",
        "drift",
        "reconcile",
        "validate",
    ]
    assert body["steps"][1]["actual"] == "PASSED"
    assert body["steps"][3]["actual"] == "FAILED"
    assert body["steps"][4]["actual"] == "DETECTED"
    assert body["steps"][5]["actual"] == "RECONCILED"
    assert body["steps"][6]["actual"] == "PASSED"
