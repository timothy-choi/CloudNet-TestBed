"""Idempotent deploy: state.json stability and plan SKIP lines."""

from __future__ import annotations

import json
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
from app.services import drift_service
from app.services import failure_service


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    monkeypatch.setenv("CLOUDNET_STATE_FILE", str(tmp_path / "cloudnet-state.json"))
    database_url = f"sqlite:///{tmp_path / 'idem.db'}"
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


def _mock_stack(monkeypatch: pytest.MonkeyPatch) -> MockProvider:
    """Install mock provider across services (same as scenario route tests)."""
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
        lambda name, network_id: {
            "id": f"srv-{name}",
            "name": name,
            "status": "ACTIVE",
            "addresses": {},
        },
    )
    return provider


def test_state_json_stable_across_two_deploys(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "cloudnet-state.json"
    _mock_stack(monkeypatch)

    r = client.post(
        "/topologies",
        json={
            "name": "idem-topo",
            "nodes": [
                {"name": "a", "type": "host"},
                {"name": "b", "type": "host"},
            ],
            "links": [{"from": "a", "to": "b", "subnet": "10.40.1.0/24"}],
            "firewall_rules": [],
        },
    )
    tid = r.json()["id"]
    assert client.post(f"/topologies/{tid}/deploy").status_code == 200

    after_first = json.loads(state_path.read_text())
    assert client.post(f"/topologies/{tid}/deploy").status_code == 200
    after_second = json.loads(state_path.read_text())

    dep1 = after_first["deployments"][str(tid)]
    dep2 = after_second["deployments"][str(tid)]
    assert dep1["provider_resource_ids"] == dep2["provider_resource_ids"]
    assert dep1["resources"] == dep2["resources"]
    assert dep1["topology_name"] == dep2["topology_name"] == "idem-topo"


def test_scenario_second_run_skips_on_implicit_deploy(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLOUDNET_MOCK_PING_LOSS_RATE", "0")
    _mock_stack(monkeypatch)
    payload = {
        "scenario": {"name": "idem_twice"},
        "topology": {
            "name": "scenario-idem",
            "nodes": [
                {"name": "client-a", "type": "host"},
                {"name": "client-b", "type": "host"},
            ],
            "links": [
                {"from": "client-a", "to": "client-b", "subnet": "10.55.1.0/24"},
            ],
            "firewall_rules": [],
        },
        "steps": [{"validate": "all"}],
    }
    assert client.post("/scenarios/run", json=payload).status_code == 200
    body = client.post("/scenarios/run", json=payload).json()
    assert body["status"] == "PASSED"
    deploy_steps = [s for s in body["steps"] if s.get("action") == "deploy"]
    assert len(deploy_steps) == 1
    assert deploy_steps[0].get("idempotent") is True
    assert len(deploy_steps[0].get("skipped") or []) >= 1
