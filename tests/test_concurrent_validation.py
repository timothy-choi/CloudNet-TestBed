"""Concurrent topology link validation (limits, ordering, timeout)."""

from __future__ import annotations

import threading
import time
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select

from app.db import get_session
from app.main import app
from app.models import ConnectivityTest, DeploymentResource, Link, Node, Topology
from app.providers.mock_provider import MockProvider
from app.services import connectivity_service
from app.services.connectivity_service import validate_topology_links


@pytest.fixture
def client(tmp_path: Path) -> Generator[TestClient, None, None]:
    database_url = f"sqlite:///{tmp_path / 'test.db'}"
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


def _seed_instances(client: TestClient, topology_id: int, names: list[str]) -> None:
    session_override = app.dependency_overrides[get_session]
    gen = session_override()
    session = next(gen)
    try:
        for name in names:
            session.add(
                DeploymentResource(
                    topology_id=topology_id,
                    resource_type="provider_instance",
                    resource_name=name,
                    openstack_id=f"srv-{name}",
                )
            )
        session.commit()
    finally:
        gen.close()


def test_validate_topology_orders_results_by_link_order_not_completion_order(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Slow first link finishes last; API results still follow topology link order."""
    monkeypatch.setenv("CLOUDNET_PROVIDER", "mock")
    monkeypatch.setenv("MAX_PARALLEL_VALIDATIONS", "5")

    r = client.post(
        "/topologies",
        json={
            "name": "chain-order",
            "nodes": [
                {"name": "a", "type": "host"},
                {"name": "b", "type": "host"},
                {"name": "c", "type": "host"},
                {"name": "d", "type": "host"},
            ],
            "links": [
                {"from": "a", "to": "b", "subnet": "10.60.1.0/24"},
                {"from": "b", "to": "c", "subnet": "10.60.2.0/24"},
                {"from": "c", "to": "d", "subnet": "10.60.3.0/24"},
            ],
            "firewall_rules": [],
        },
    )
    assert r.status_code == 200
    tid = r.json()["id"]
    _seed_instances(client, tid, ["a", "b", "c", "d"])

    delay_by_source = {"srv-a": 0.12, "srv-b": 0.02, "srv-c": 0.06}

    class DelayMock(MockProvider):
        def run_ping(self, source_server_id: str, target_ip: str) -> str:
            time.sleep(delay_by_source.get(source_server_id, 0.01))
            return "3 packets transmitted, 3 received, 0% packet loss"

    monkeypatch.setattr(connectivity_service, "get_provider", lambda: DelayMock())

    resp = client.post(f"/topologies/{tid}/validate")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "PASSED"
    ordered = [(x["source"], x["target"]) for x in body["results"]]
    assert ordered == [("a", "b"), ("b", "c"), ("c", "d")]


def test_max_parallel_validations_limits_peak_concurrency(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLOUDNET_PROVIDER", "mock")
    monkeypatch.setenv("MAX_PARALLEL_VALIDATIONS", "2")

    r = client.post(
        "/topologies",
        json={
            "name": "cap-test",
            "nodes": [
                {"name": "n0", "type": "host"},
                {"name": "n1", "type": "host"},
                {"name": "n2", "type": "host"},
                {"name": "n3", "type": "host"},
                {"name": "n4", "type": "host"},
            ],
            "links": [
                {"from": "n0", "to": "n1", "subnet": "10.61.1.0/24"},
                {"from": "n1", "to": "n2", "subnet": "10.61.2.0/24"},
                {"from": "n2", "to": "n3", "subnet": "10.61.3.0/24"},
                {"from": "n3", "to": "n4", "subnet": "10.61.4.0/24"},
            ],
            "firewall_rules": [],
        },
    )
    assert r.status_code == 200
    tid = r.json()["id"]
    _seed_instances(client, tid, ["n0", "n1", "n2", "n3", "n4"])

    lock = threading.Lock()
    current = 0
    peak = 0

    class CountingMock(MockProvider):
        def run_ping(self, source_server_id: str, target_ip: str) -> str:
            nonlocal current, peak
            with lock:
                current += 1
                peak = max(peak, current)
            time.sleep(0.08)
            with lock:
                current -= 1
            return "ok"

    monkeypatch.setattr(connectivity_service, "get_provider", lambda: CountingMock())

    resp = client.post(f"/topologies/{tid}/validate")
    assert resp.status_code == 200
    assert resp.json()["status"] == "PASSED"
    assert peak <= 2
    assert peak >= 2


def test_validation_timeout_marks_link_failed(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLOUDNET_PROVIDER", "mock")
    monkeypatch.setenv("VALIDATION_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("MAX_PARALLEL_VALIDATIONS", "2")

    r = client.post(
        "/topologies",
        json={
            "name": "timeout-one",
            "nodes": [
                {"name": "x", "type": "host"},
                {"name": "y", "type": "host"},
            ],
            "links": [
                {"from": "x", "to": "y", "subnet": "10.62.1.0/24"},
            ],
            "firewall_rules": [],
        },
    )
    tid = r.json()["id"]
    _seed_instances(client, tid, ["x", "y"])

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

    session_override = app.dependency_overrides[get_session]
    gen = session_override()
    s = next(gen)
    try:
        rows = list(s.exec(select(ConnectivityTest).where(ConnectivityTest.topology_id == tid)).all())
        assert any(t.output == "validation timed out" for t in rows)
    finally:
        gen.close()


def test_one_failed_link_collects_all_results_and_overall_failed(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLOUDNET_PROVIDER", "mock")
    monkeypatch.setenv("MAX_PARALLEL_VALIDATIONS", "5")

    r = client.post(
        "/topologies",
        json={
            "name": "partial-fail",
            "nodes": [
                {"name": "p", "type": "host"},
                {"name": "q", "type": "host"},
                {"name": "r", "type": "host"},
            ],
            "links": [
                {"from": "p", "to": "q", "subnet": "10.63.1.0/24"},
                {"from": "q", "to": "r", "subnet": "10.63.2.0/24"},
            ],
            "firewall_rules": [],
        },
    )
    tid = r.json()["id"]
    _seed_instances(client, tid, ["p", "q", "r"])

    class FlakyMock(MockProvider):
        def run_ping(self, source_server_id: str, target_ip: str) -> str:
            if source_server_id == "srv-q":
                raise RuntimeError("simulated unreachable")
            return "3 packets transmitted, 3 received"

    monkeypatch.setattr(connectivity_service, "get_provider", lambda: FlakyMock())

    resp = client.post(f"/topologies/{tid}/validate")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "FAILED"
    assert len(body["results"]) == 2
    assert body["results"][0]["status"] == "PASSED"
    assert body["results"][1]["status"] == "FAILED"


def test_validate_topology_links_service_respects_emit_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLOUDNET_PROVIDER", "mock")
    database_url = f"sqlite:///{tmp_path / 'solo.db'}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        t = Topology(name="solo", status="ACTIVE")
        session.add(t)
        session.commit()
        session.refresh(t)
        for nm in ("u", "v"):
            session.add(Node(topology_id=t.id, name=nm, type="host"))
        session.add(
            Link(
                topology_id=t.id,
                from_node="u",
                to_node="v",
                subnet="10.64.1.0/24",
            )
        )
        session.add(
            DeploymentResource(
                topology_id=t.id,
                resource_type="provider_instance",
                resource_name="u",
                openstack_id="srv-u",
            )
        )
        session.add(
            DeploymentResource(
                topology_id=t.id,
                resource_type="provider_instance",
                resource_name="v",
                openstack_id="srv-v",
            )
        )
        session.commit()

        emitted: list[str] = []

        def capture_emit(sess, topology_id, event_type, status, message, metadata=None):
            emitted.append(event_type)
            from app.models import Event

            ev = Event(
                topology_id=topology_id,
                type=event_type,
                status=status,
                message=message,
                event_metadata=metadata or {},
            )
            sess.add(ev)
            sess.commit()
            return ev

        monkeypatch.setattr(
            connectivity_service,
            "emit_event",
            capture_emit,
        )

        topology = session.get(Topology, t.id)
        assert topology is not None
        validate_topology_links(session, topology, emit_validation_events=False)
        assert emitted == []
