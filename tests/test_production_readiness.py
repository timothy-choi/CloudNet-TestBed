"""Production-readiness: quotas, cleanup, config validation, structured logs."""

from __future__ import annotations

import json
import logging
from collections.abc import Generator
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from app.db import get_session
from app.main import app
from app.services import deployment_service
from app.services import scenario_service
from app.services.deployment_service import DeploymentError


@pytest.fixture
def client(tmp_path: Path) -> Generator[TestClient, None, None]:
    database_url = f"sqlite:///{tmp_path / 'prod.db'}"
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


_MIN_TWO_HOST_TOPO = {
    "name": "quota-topo",
    "nodes": [
        {"name": "a", "type": "host"},
        {"name": "b", "type": "host"},
    ],
    "links": [
        {"from": "a", "to": "b", "subnet": "10.77.1.0/24"},
    ],
    "firewall_rules": [],
}


def test_quota_violation_fails_before_deploy(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLOUDNET_MAX_HOST_NODES_PER_SCENARIO", "1")

    deploy_calls: list[int] = []

    def must_not_deploy(
        session: Session,
        topology,
        *,
        scenario_run_id: int | None = None,
    ) -> dict:
        deploy_calls.append(topology.id)
        return {"status": "ACTIVE"}

    monkeypatch.setattr(scenario_service, "deploy_topology", must_not_deploy)

    response = client.post(
        "/scenarios/run",
        json={
            "scenario": {"name": "quota-test"},
            "topology": _MIN_TWO_HOST_TOPO,
            "steps": [],
        },
    )
    assert response.status_code == 400
    detail = response.json().get("detail", "")
    assert "quota" in str(detail).lower()
    assert deploy_calls == []


def test_cleanup_on_failure_invokes_cleanup_after_deploy_error(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit deploy step fails → cleanup_topology_deployment runs when flag set."""
    from app.providers.mock_provider import MockProvider
    from app.services import connectivity_service
    from app.services import control_plane_service
    from app.services import drift_service
    from app.services import failure_service

    provider = MockProvider()
    for module in (
        deployment_service,
        failure_service,
        control_plane_service,
        connectivity_service,
        drift_service,
    ):
        monkeypatch.setattr(module, "get_provider", lambda p=provider: p)

    cleanup_calls: list[int] = []

    def deploy_boom(
        session: Session,
        topology,
        *,
        scenario_run_id: int | None = None,
    ) -> dict:
        raise DeploymentError("forced deploy failure")

    def track_cleanup(session: Session, topology) -> dict:
        cleanup_calls.append(topology.id)
        return deployment_service.cleanup_topology_deployment(session, topology)

    monkeypatch.setattr(scenario_service, "deploy_topology", deploy_boom)
    monkeypatch.setattr(scenario_service, "cleanup_topology_deployment", track_cleanup)

    response = client.post(
        "/scenarios/run",
        json={
            "scenario": {"name": "cleanup-test", "cleanup_on_failure": True},
            "topology": {
                "name": "one-host",
                "nodes": [{"name": "solo", "type": "host"}],
                "links": [{"from": "solo", "to": "solo", "subnet": "10.88.1.0/24"}],
                "firewall_rules": [],
            },
            "steps": [{"deploy": True}],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "FAILED"
    assert cleanup_calls, "cleanup_topology_deployment should run after deploy failure"
    deploy_step = body["steps"][0]
    assert deploy_step["action"] == "deploy"
    assert deploy_step["status"] == "FAILED"


def test_config_validate_aws_without_credentials_not_ok(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLOUDNET_PROVIDER", "aws")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)

    import boto3

    fake_sess = MagicMock()
    fake_sess.get_credentials.return_value = None
    monkeypatch.setattr(boto3, "Session", MagicMock(return_value=fake_sess))

    response = client.get("/config/validate")
    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "aws"
    assert body["ok"] is False
    checks = {c["id"]: c for c in body["checks"]}
    assert checks["aws_credentials"]["ok"] is False


def test_structured_logs_include_scenario_run_id(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("CLOUDNET_PROVIDER", "mock")

    from app.providers.mock_provider import MockProvider
    from app.services import connectivity_service
    from app.services import control_plane_service
    from app.services import drift_service
    from app.services import failure_service

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

    caplog.set_level(logging.INFO, logger="cloudnet.trace")

    response = client.post(
        "/scenarios/run",
        json={
            "scenario": {"name": "log-test"},
            "topology": {
                "name": "solo-topo",
                "nodes": [{"name": "solo", "type": "host"}],
                "links": [{"from": "solo", "to": "solo", "subnet": "10.99.1.0/24"}],
                "firewall_rules": [],
            },
            "steps": [{"validate": "all"}],
        },
    )
    assert response.status_code == 200
    run_id = response.json()["scenario_run_id"]

    payloads = []
    for rec in caplog.records:
        if rec.name != "cloudnet.trace":
            continue
        try:
            payloads.append(json.loads(rec.message))
        except json.JSONDecodeError:
            continue

    assert payloads, "expected JSON lines on cloudnet.trace"
    ids_found = {p.get("scenario_run_id") for p in payloads}
    assert run_id in ids_found
    for p in payloads:
        assert "topology_id" in p
        assert p.get("provider") == "mock"
