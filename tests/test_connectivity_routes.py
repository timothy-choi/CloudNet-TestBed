from collections.abc import Generator
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from app.db import get_session
from app.main import app
from app.models import DeploymentResource
from app.services import connectivity_service


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


def create_topology(client: TestClient) -> int:
    response = client.post(
        "/topologies",
        json={
            "name": "connectivity-test",
            "nodes": [
                {"name": "client-a", "type": "host"},
                {"name": "client-b", "type": "host"},
                {"name": "router-a", "type": "router"},
            ],
            "links": [
                {"from": "client-a", "to": "client-b", "subnet": "10.30.1.0/24"},
            ],
        },
    )

    assert response.status_code == 200
    return response.json()["id"]


def seed_server_resources(client: TestClient, topology_id: int) -> None:
    session_override = app.dependency_overrides[get_session]
    session_generator = session_override()
    session = next(session_generator)
    try:
        session.add(
            DeploymentResource(
                topology_id=topology_id,
                resource_type="nova_server",
                resource_name="client-a",
                openstack_id="server-client-a",
            )
        )
        session.add(
            DeploymentResource(
                topology_id=topology_id,
                resource_type="nova_server",
                resource_name="client-b",
                openstack_id="server-client-b",
            )
        )
        session.commit()
    finally:
        session_generator.close()


def mock_openstack_for_ping(monkeypatch) -> None:
    monkeypatch.setattr(
        connectivity_service.openstack_client,
        "get_server_fixed_ip",
        lambda server_id: "10.30.1.23",
    )
    monkeypatch.setattr(
        connectivity_service.openstack_client,
        "get_server_details",
        lambda server_id: {"addresses": {"private": []}},
    )
    monkeypatch.setattr(
        connectivity_service.openstack_client,
        "create_floating_ip",
        lambda: {"floating_ip_address": "172.24.4.101"},
    )
    monkeypatch.setattr(
        connectivity_service.openstack_client,
        "associate_floating_ip",
        lambda server_id, floating_ip: {"id": server_id},
    )


def mock_paramiko(
    monkeypatch,
    *,
    exit_status: int = 0,
    stdout_output: str = "3 packets transmitted, 3 packets received",
    stderr_output: str = "",
    connect_error: Exception | None = None,
) -> None:
    class FakeChannel:
        def recv_exit_status(self) -> int:
            return exit_status

    class FakeStream:
        channel = FakeChannel()

        def __init__(self, output: str) -> None:
            self.output = output

        def read(self) -> bytes:
            return self.output.encode()

    class FakeSSHClient:
        def set_missing_host_key_policy(self, policy) -> None:
            pass

        def connect(self, **kwargs) -> None:
            if connect_error is not None:
                raise connect_error

        def exec_command(self, command: str, timeout: int):
            return (
                None,
                FakeStream(stdout_output),
                FakeStream(stderr_output),
            )

        def close(self) -> None:
            pass

    fake_paramiko = SimpleNamespace(
        SSHClient=FakeSSHClient,
        AutoAddPolicy=lambda: object(),
    )
    monkeypatch.setitem(sys.modules, "paramiko", fake_paramiko)


def test_ping_unknown_topology_returns_404(client: TestClient) -> None:
    response = client.post(
        "/topologies/999/tests/ping",
        json={"source": "client-a", "target": "client-b"},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "topology not found"}


def test_ping_unknown_source_returns_400(client: TestClient) -> None:
    topology_id = create_topology(client)
    seed_server_resources(client, topology_id)

    response = client.post(
        f"/topologies/{topology_id}/tests/ping",
        json={"source": "missing-client", "target": "client-b"},
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "unknown source host 'missing-client'"}


def test_ping_unknown_target_returns_400(client: TestClient) -> None:
    topology_id = create_topology(client)
    seed_server_resources(client, topology_id)

    response = client.post(
        f"/topologies/{topology_id}/tests/ping",
        json={"source": "client-a", "target": "router-a"},
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "unknown target host 'router-a'"}


def test_successful_ping_creates_passed_test(client: TestClient, monkeypatch) -> None:
    mock_openstack_for_ping(monkeypatch)
    mock_paramiko(monkeypatch)
    topology_id = create_topology(client)
    seed_server_resources(client, topology_id)

    response = client.post(
        f"/topologies/{topology_id}/tests/ping",
        json={"source": "client-a", "target": "client-b"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "topology_id": topology_id,
        "source": "client-a",
        "target": "client-b",
        "status": "PASSED",
        "output": "3 packets transmitted, 3 packets received",
    }


def test_failed_ssh_creates_failed_test(client: TestClient, monkeypatch) -> None:
    mock_openstack_for_ping(monkeypatch)
    mock_paramiko(monkeypatch, connect_error=TimeoutError("ssh timed out"))
    topology_id = create_topology(client)
    seed_server_resources(client, topology_id)

    response = client.post(
        f"/topologies/{topology_id}/tests/ping",
        json={"source": "client-a", "target": "client-b"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "topology_id": topology_id,
        "source": "client-a",
        "target": "client-b",
        "status": "FAILED",
        "output": "ssh timed out",
    }


def test_connectivity_tests_endpoint_returns_saved_tests(
    client: TestClient,
    monkeypatch,
) -> None:
    mock_openstack_for_ping(monkeypatch)
    mock_paramiko(monkeypatch)
    topology_id = create_topology(client)
    seed_server_resources(client, topology_id)
    ping_response = client.post(
        f"/topologies/{topology_id}/tests/ping",
        json={"source": "client-a", "target": "client-b"},
    )
    assert ping_response.status_code == 200

    response = client.get(f"/topologies/{topology_id}/tests")

    assert response.status_code == 200
    body = response.json()
    assert body["topology_id"] == topology_id
    assert len(body["tests"]) == 1
    assert body["tests"][0]["source"] == "client-a"
    assert body["tests"][0]["target"] == "client-b"
    assert body["tests"][0]["test_type"] == "ping"
    assert body["tests"][0]["status"] == "PASSED"


def test_validate_endpoint_runs_default_client_ping(
    client: TestClient,
    monkeypatch,
) -> None:
    mock_openstack_for_ping(monkeypatch)
    mock_paramiko(monkeypatch)
    topology_id = create_topology(client)
    seed_server_resources(client, topology_id)

    response = client.post(f"/topologies/{topology_id}/validate")

    assert response.status_code == 200
    assert response.json() == {
        "topology_id": topology_id,
        "source": "client-a",
        "target": "client-b",
        "status": "PASSED",
        "output": "3 packets transmitted, 3 packets received",
    }
