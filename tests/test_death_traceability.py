"""Partial-failure scenarios: cleanup, trace IDs, structured logs, and events."""

from __future__ import annotations

import json
import logging
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select

from app.db import get_session
from app.main import app
from app.models import DeploymentResource, Topology
from app.providers import factory as provider_factory
from app.services import deployment_service
from app.services import scenario_service


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


def _two_host_topo(name: str = "death-topo") -> dict:
    return {
        "name": name,
        "nodes": [
            {"name": "client-a", "type": "host"},
            {"name": "client-b", "type": "host"},
        ],
        "links": [{"from": "client-a", "to": "client-b", "subnet": "10.88.1.0/24"}],
        "firewall_rules": [],
    }


def test_structured_trace_logs_include_scenario_run_id(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("CLOUDNET_PROVIDER", "mock")
    provider_factory._mock_provider.refresh_simulation_env()

    with caplog.at_level(logging.INFO, logger="cloudnet.trace"):
        resp = client.post(
            "/scenarios/run",
            json={
                "scenario": {"name": "trace_logs"},
                "topology": _two_host_topo("trace-logs"),
                "steps": [{"deploy": True}],
            },
        )
    assert resp.status_code == 200
    body = resp.json()
    rid = body["scenario_run_id"]
    assert rid is not None
    seen = False
    for rec in caplog.records:
        try:
            payload = json.loads(rec.message)
        except json.JSONDecodeError:
            continue
        if payload.get("scenario_run_id") == rid:
            seen = True
            break
    assert seen, "expected JSON log line with matching scenario_run_id"


def test_subnet_failure_cleans_up_network_rows(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLOUDNET_PROVIDER", "mock")
    monkeypatch.setenv("CLOUDNET_PROVIDER_MAX_RETRIES", "0")
    monkeypatch.setenv("CLOUDNET_MOCK_CREATE_SUBNET_FAILS", "2")
    provider_factory._mock_provider.refresh_simulation_env()

    resp = client.post(
        "/scenarios/run",
        json={
            "scenario": {"name": "subnet_death"},
            "topology": _two_host_topo("subnet-death"),
            "steps": [{"deploy": True}],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "FAILED"
    assert body.get("failed_step") == "deploy"
    tid = body["topology_id"]

    session_gen = app.dependency_overrides[get_session]()
    session = next(session_gen)
    try:
        rows = session.exec(
            select(DeploymentResource).where(DeploymentResource.topology_id == tid)
        ).all()
        assert rows == []
    finally:
        session_gen.close()


def test_instance_failure_cleans_up_subnet_and_network(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLOUDNET_PROVIDER", "mock")
    monkeypatch.setenv("CLOUDNET_PROVIDER_MAX_RETRIES", "0")
    monkeypatch.setenv("CLOUDNET_MOCK_CREATE_SERVER_FAILS", "2")
    provider_factory._mock_provider.refresh_simulation_env()

    resp = client.post(
        "/scenarios/run",
        json={
            "scenario": {"name": "inst_death"},
            "topology": _two_host_topo("inst-death"),
            "steps": [{"deploy": True}],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "FAILED"
    tid = body["topology_id"]

    session_gen = app.dependency_overrides[get_session]()
    session = next(session_gen)
    try:
        rows = session.exec(
            select(DeploymentResource).where(DeploymentResource.topology_id == tid)
        ).all()
        assert rows == []
    finally:
        session_gen.close()


def test_failed_scenario_emits_scenario_failed_event(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLOUDNET_PROVIDER", "mock")
    monkeypatch.setenv("CLOUDNET_PROVIDER_MAX_RETRIES", "0")
    monkeypatch.setenv("CLOUDNET_MOCK_CREATE_NETWORK_FAILS", "2")
    provider_factory._mock_provider.refresh_simulation_env()

    resp = client.post(
        "/scenarios/run",
        json={
            "scenario": {"name": "evt_death"},
            "topology": _two_host_topo("evt-death"),
            "steps": [{"deploy": True}],
        },
    )
    assert resp.status_code == 200
    tid = resp.json()["topology_id"]

    ev_resp = client.get(f"/topologies/{tid}/events")
    assert ev_resp.status_code == 200
    types = {e["type"] for e in ev_resp.json()["events"]}
    assert "SCENARIO_FAILED" in types


def test_cleanup_retry_logged_on_second_attempt(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("CLOUDNET_PROVIDER", "mock")
    provider_factory._mock_provider.refresh_simulation_env()
    calls = {"n": 0}
    real = deployment_service.cleanup_topology_deployment

    def flaky_cleanup(session: Session, topology: Topology) -> dict:
        calls["n"] += 1
        if calls["n"] == 1:
            raise deployment_service.DeploymentError("simulated cleanup failure")
        return real(session, topology)

    monkeypatch.setattr(scenario_service, "cleanup_topology_deployment", flaky_cleanup)

    with caplog.at_level(logging.WARNING, logger="cloudnet.trace"):
        resp = client.post(
            "/scenarios/run",
            json={
                "scenario": {"name": "retry_clean"},
                "topology": _two_host_topo("retry-clean"),
                "steps": [
                    {"deploy": True},
                    {"validate": "all"},
                ],
                "cleanup": True,
            },
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == "PASSED"
    assert calls["n"] >= 2
    retry_msgs = [r.message for r in caplog.records if "RETRYING" in r.message]
    assert retry_msgs, "expected scenario_cleanup RETRYING log on cloudnet.trace"


def test_validation_failure_failed_step_and_cleanup(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLOUDNET_PROVIDER", "mock")
    monkeypatch.setenv("CLOUDNET_MOCK_PING_LOSS_RATE", "1")
    provider_factory._mock_provider.refresh_simulation_env()

    resp = client.post(
        "/scenarios/run",
        json={
            "scenario": {"name": "val_death"},
            "topology": _two_host_topo("val-death"),
            "steps": [{"deploy": True}, {"validate": "all"}],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "FAILED"
    assert body.get("failed_step") == "validate"
    c = body.get("cleanup") or {}
    assert c.get("outcome") == "SUCCESS"


def test_reconcile_failure_after_inject(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.control_plane_service import ControlPlaneError

    monkeypatch.setenv("CLOUDNET_PROVIDER", "mock")
    provider_factory._mock_provider.refresh_simulation_env()

    def boom(session: Session, topology: Topology) -> dict:
        raise ControlPlaneError("simulated reconcile failure")

    monkeypatch.setattr(scenario_service, "reconcile_topology", boom)

    resp = client.post(
        "/scenarios/run",
        json={
            "scenario": {"name": "rec_death"},
            "topology": _two_host_topo("rec-death"),
            "steps": [
                {"deploy": True},
                {"validate": "all"},
                {"fail": {"node": "client-a"}},
                {"reconcile": True},
            ],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "FAILED"
    assert body.get("failed_step") == "reconcile"
