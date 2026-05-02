"""Negative paths: retries, partial deploy cleanup, scenario validation errors."""

from __future__ import annotations

import logging
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select

from app.db import get_session
from app.main import app
from app.models import DeploymentResource
from app.providers.mock_provider import MockProvider
from app.services import connectivity_service
from app.services import deployment_service
from app.services import scenario_service


@pytest.fixture
def fast_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLOUDNET_TEST_FAST_RETRY", "1")


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    monkeypatch.setenv("CLOUDNET_STATE_FILE", str(tmp_path / "cloudnet-state.json"))
    database_url = f"sqlite:///{tmp_path / 't.db'}"
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


def _topology_body(name: str = "retry-test") -> dict:
    return {
        "name": name,
        "nodes": [
            {"name": "client-a", "type": "host"},
            {"name": "client-b", "type": "host"},
        ],
        "links": [{"from": "client-a", "to": "client-b", "subnet": "10.20.1.0/24"}],
        "firewall_rules": [],
    }


def test_deploy_retry_succeeds_after_one_rate_limit(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    fast_retry: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("CLOUDNET_PROVIDER", "mock")
    monkeypatch.setenv("CLOUDNET_MOCK_CREATE_NETWORK_FAILS", "1")
    monkeypatch.delenv("CLOUDNET_MOCK_CREATE_SUBNET_FAILS", raising=False)
    monkeypatch.delenv("CLOUDNET_MOCK_CREATE_SERVER_FAILS", raising=False)

    provider = MockProvider()
    provider.refresh_simulation_env()
    monkeypatch.setattr(deployment_service, "get_provider", lambda: provider)

    r = client.post("/topologies", json=_topology_body())
    tid = r.json()["id"]

    with caplog.at_level(logging.INFO, logger="cloudnet.retry"):
        dr = client.post(f"/topologies/{tid}/deploy")
    assert dr.status_code == 200
    assert dr.json()["status"] == "ACTIVE"
    assert any("Retrying (1/3):" in rec.message for rec in caplog.records)
    assert any("✔ VPC creation succeeded after retry" in rec.message for rec in caplog.records)


def test_deploy_fails_cleanly_after_retries_exhausted(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    fast_retry: None,
) -> None:
    monkeypatch.setenv("CLOUDNET_PROVIDER", "mock")
    monkeypatch.setenv("CLOUDNET_MOCK_CREATE_NETWORK_FAILS", "4")
    monkeypatch.setenv("CLOUDNET_PROVIDER_MAX_RETRIES", "3")

    provider = MockProvider()
    provider.refresh_simulation_env()
    monkeypatch.setattr(deployment_service, "get_provider", lambda: provider)

    r = client.post("/topologies", json=_topology_body("exhaust"))
    tid = r.json()["id"]
    dr = client.post(f"/topologies/{tid}/deploy")
    assert dr.status_code == 503
    assert "RateLimitExceeded" in dr.json()["detail"]


def test_partial_deploy_cleanup_calls_delete_for_mock_resources(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    fast_retry: None,
) -> None:
    monkeypatch.setenv("CLOUDNET_PROVIDER", "mock")
    monkeypatch.delenv("CLOUDNET_MOCK_CREATE_NETWORK_FAILS", raising=False)
    monkeypatch.delenv("CLOUDNET_MOCK_CREATE_SUBNET_FAILS", raising=False)
    monkeypatch.delenv("CLOUDNET_MOCK_CREATE_SERVER_FAILS", raising=False)

    deleted: list[tuple[str, str]] = []

    class TrackingMock(MockProvider):
        def create_server(self, name: str, network_id: str, subnet_id: str | None = None):
            raise RuntimeError("InvalidParameter: cannot create instance (non-retryable)")

        def delete_resource(self, resource_type: str, resource_id: str):
            deleted.append((resource_type, resource_id))
            return {"id": resource_id, "deleted": True}

    provider = TrackingMock()
    monkeypatch.setattr(deployment_service, "get_provider", lambda: provider)

    r = client.post("/topologies", json=_topology_body("partial"))
    tid = r.json()["id"]
    dr = client.post(f"/topologies/{tid}/deploy")
    assert dr.status_code == 503

    session_gen = app.dependency_overrides[get_session]()
    session = next(session_gen)
    try:
        rows = session.exec(
            select(DeploymentResource).where(DeploymentResource.topology_id == tid)
        ).all()
        assert rows == []
    finally:
        session_gen.close()

    assert deleted, "cleanup should delete partial mock resources"


def test_scenario_validate_internal_error_returns_failed_scenario(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    fast_retry: None,
) -> None:
    monkeypatch.setenv("CLOUDNET_PROVIDER", "mock")

    def boom(*a, **k):
        raise RuntimeError("simulated internal validator crash")

    monkeypatch.setattr(scenario_service, "validate_topology_links", boom)

    resp = client.post(
        "/scenarios/run",
        json={
            "scenario": {"name": "internal_err"},
            "topology": _topology_body("ie-topo"),
            "steps": [{"validate": "all"}],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "FAILED"
    assert any(
        "internal error" in (s.get("message") or "").lower()
        for s in body["steps"]
        if s.get("action") == "validate"
    )


def test_validation_timeout_marks_overall_failed(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    fast_retry: None,
) -> None:
    import time

    monkeypatch.setenv("CLOUDNET_PROVIDER", "mock")
    monkeypatch.setenv("VALIDATION_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("MAX_PARALLEL_VALIDATIONS", "2")

    r = client.post(
        "/topologies",
        json={
            "name": "to-val",
            "nodes": [
                {"name": "x", "type": "host"},
                {"name": "y", "type": "host"},
            ],
            "links": [{"from": "x", "to": "y", "subnet": "10.62.9.0/24"}],
            "firewall_rules": [],
        },
    )
    tid = r.json()["id"]
    session_gen = app.dependency_overrides[get_session]()
    session = next(session_gen)
    try:
        for name in ("x", "y"):
            session.add(
                DeploymentResource(
                    topology_id=tid,
                    resource_type="provider_instance",
                    resource_name=name,
                    openstack_id=f"srv-{name}",
                )
        )
        session.commit()
    finally:
        session_gen.close()

    class SlowMock(MockProvider):
        def run_ping(self, source_server_id: str, target_ip: str) -> str:
            time.sleep(3)
            return "ok"

    monkeypatch.setattr(connectivity_service, "get_provider", lambda: SlowMock())

    resp = client.post(f"/topologies/{tid}/validate")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "FAILED"
    assert body["results"][0]["status"] == "FAILED"


def test_provider_error_classification() -> None:
    from app.services.provider_errors import is_retryable

    assert is_retryable(RuntimeError("RateLimitExceeded: slow down")) is True
    assert is_retryable(RuntimeError("InternalServerError: aws")) is True
    assert is_retryable(RuntimeError("InvalidParameter: bad ami")) is False
