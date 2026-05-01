"""Mock lifecycle tests for every supported topology class."""

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
TOPOLOGIES = _REPO_ROOT / "examples" / "topologies"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    database_url = f"sqlite:///{tmp_path / 'topo_scenario.db'}"
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

    app.dependency_overrides[get_session] = override_get_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _load(name: str) -> dict:
    return yaml.safe_load((TOPOLOGIES / name).read_text())


@pytest.mark.parametrize(
    ("filename", "fail_node", "expected"),
    [
        (
            "valid-two-node.yaml",
            "client-b",
            {
                "subnets": 1,
                "instances": 2,
                "firewall_rules": 0,
                "warnings": [],
            },
        ),
        (
            "valid-three-tier.yaml",
            "backend",
            {
                "subnets": 2,
                "instances": 3,
                "firewall_rules": 2,
                "warnings": [
                    "multi-homed node backend appears in multiple links; "
                    "attached to first subnet only"
                ],
            },
        ),
        (
            "valid-multi-subnet-chain.yaml",
            "api",
            {
                "subnets": 3,
                "instances": 4,
                "firewall_rules": 0,
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
            "dst",
            {
                "subnets": 1,
                "instances": 2,
                "firewall_rules": 1,
                "warnings": [],
            },
        ),
    ],
)
def test_supported_topology_mock_lifecycle(
    client: TestClient,
    filename: str,
    fail_node: str,
    expected: dict,
) -> None:
    topology = _load(filename)

    created = client.post("/topologies", json=topology)
    assert created.status_code == 200
    topology_id = created.json()["id"]

    planned = client.get(f"/topologies/{topology_id}/plan")
    assert planned.status_code == 200
    plan_body = planned.json()
    assert len(plan_body["plan"]["subnets"]) == expected["subnets"]
    assert len(plan_body["plan"]["instances"]) == expected["instances"]
    assert len(plan_body["plan"]["firewall_rules"]) == expected["firewall_rules"]
    assert plan_body.get("warnings", []) == expected["warnings"]

    deployed = client.post(f"/topologies/{topology_id}/deploy")
    assert deployed.status_code == 200
    deploy_body = deployed.json()
    assert deploy_body["status"] == "ACTIVE"
    assert deploy_body.get("warnings", []) == expected["warnings"]

    first_validation = client.post(f"/topologies/{topology_id}/validate")
    assert first_validation.status_code == 200
    assert first_validation.json()["status"] == "PASSED"

    failed = client.post(
        f"/topologies/{topology_id}/failures/node-down",
        json={"node": fail_node},
    )
    assert failed.status_code == 200

    failed_validation = client.post(f"/topologies/{topology_id}/validate")
    assert failed_validation.status_code == 200
    assert failed_validation.json()["status"] == "FAILED"

    drifted = client.get(f"/topologies/{topology_id}/drift")
    assert drifted.status_code == 200
    assert drifted.json()["drift_detected"] is True

    reconciled = client.post(f"/topologies/{topology_id}/reconcile")
    assert reconciled.status_code == 200
    assert reconciled.json()["status"] == "RECONCILED"

    final_validation = client.post(f"/topologies/{topology_id}/validate")
    assert final_validation.status_code == 200
    assert final_validation.json()["status"] == "PASSED"
