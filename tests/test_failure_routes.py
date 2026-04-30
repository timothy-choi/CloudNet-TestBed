from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from app.db import get_session
from app.main import app
from app.models import DeploymentResource
from app.providers.mock_provider import MockProvider
from app.services import failure_service


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
            "name": "failure-test",
            "nodes": [
                {"name": "client-a", "type": "host"},
                {"name": "client-b", "type": "host"},
                {"name": "router-a", "type": "router"},
            ],
            "links": [
                {"from": "client-a", "to": "client-b", "subnet": "10.40.1.0/24"},
            ],
        },
    )

    assert response.status_code == 200
    return response.json()["id"]


def seed_server_resource(
    client: TestClient,
    topology_id: int,
    node_name: str,
    resource_type: str = "nova_server",
) -> None:
    session_override = app.dependency_overrides[get_session]
    session_generator = session_override()
    session = next(session_generator)
    try:
        session.add(
            DeploymentResource(
                topology_id=topology_id,
                resource_type=resource_type,
                resource_name=node_name,
                openstack_id=(
                    f"i-{node_name}" if resource_type == "aws_instance"
                    else f"server-{node_name}"
                ),
            )
        )
        session.commit()
    finally:
        session_generator.close()


def mock_failure_provider(monkeypatch) -> MockProvider:
    provider = MockProvider()
    monkeypatch.setattr(failure_service, "get_provider", lambda: provider)
    return provider


def test_node_down_calls_stop_server(client: TestClient, monkeypatch) -> None:
    calls: list[str] = []
    provider = mock_failure_provider(monkeypatch)

    def stop_server(server_id: str) -> dict[str, str]:
        calls.append(server_id)
        return {"id": server_id, "status": "SHUTOFF"}

    monkeypatch.setattr(
        provider,
        "stop_server",
        stop_server,
    )

    topology_id = create_topology(client)
    seed_server_resource(client, topology_id, "client-b")

    response = client.post(
        f"/topologies/{topology_id}/failures/node-down",
        json={"node": "client-b"},
    )

    assert response.status_code == 200
    assert calls == ["server-client-b"]
    assert response.json()["action"] == "node-down"
    assert response.json()["status"] == "SUCCESS"


def test_recover_calls_start_server(client: TestClient, monkeypatch) -> None:
    calls: list[str] = []
    provider = mock_failure_provider(monkeypatch)

    def start_server(server_id: str) -> dict[str, str]:
        calls.append(server_id)
        return {"id": server_id, "status": "ACTIVE"}

    monkeypatch.setattr(
        provider,
        "start_server",
        start_server,
    )

    topology_id = create_topology(client)
    seed_server_resource(client, topology_id, "client-b")

    response = client.post(
        f"/topologies/{topology_id}/recover/node",
        json={"node": "client-b"},
    )

    assert response.status_code == 200
    assert calls == ["server-client-b"]
    assert response.json()["action"] == "recover-node"
    assert response.json()["status"] == "SUCCESS"


def test_aws_node_down_finds_aws_instance_and_calls_stop_server(
    client: TestClient,
    monkeypatch,
) -> None:
    calls: list[str] = []
    provider = mock_failure_provider(monkeypatch)
    provider.name = "aws"

    def stop_server(server_id: str) -> dict[str, str]:
        calls.append(server_id)
        return {"id": server_id, "status": "stopped"}

    monkeypatch.setattr(provider, "stop_server", stop_server)

    topology_id = create_topology(client)
    seed_server_resource(client, topology_id, "client-b", "aws_instance")

    response = client.post(
        f"/topologies/{topology_id}/failures/node-down",
        json={"node": "client-b"},
    )

    assert response.status_code == 200
    assert calls == ["i-client-b"]
    assert response.json()["action"] == "node-down"
    assert response.json()["status"] == "SUCCESS"


def test_aws_recover_finds_aws_instance_and_calls_start_server(
    client: TestClient,
    monkeypatch,
) -> None:
    calls: list[str] = []
    provider = mock_failure_provider(monkeypatch)
    provider.name = "aws"

    def start_server(server_id: str) -> dict[str, str]:
        calls.append(server_id)
        return {"id": server_id, "status": "running"}

    monkeypatch.setattr(provider, "start_server", start_server)

    topology_id = create_topology(client)
    seed_server_resource(client, topology_id, "client-b", "aws_instance")

    response = client.post(
        f"/topologies/{topology_id}/recover/node",
        json={"node": "client-b"},
    )

    assert response.status_code == 200
    assert calls == ["i-client-b"]
    assert response.json()["action"] == "recover-node"
    assert response.json()["status"] == "SUCCESS"


def test_unknown_topology_returns_404(client: TestClient) -> None:
    response = client.post(
        "/topologies/999/failures/node-down",
        json={"node": "client-b"},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "topology not found"}


def test_unknown_node_returns_400(client: TestClient) -> None:
    topology_id = create_topology(client)

    response = client.post(
        f"/topologies/{topology_id}/failures/node-down",
        json={"node": "missing-client"},
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "unknown host node 'missing-client'"}


def test_missing_server_resource_error_lists_available_servers(
    client: TestClient,
) -> None:
    topology_id = create_topology(client)
    seed_server_resource(client, topology_id, "client-a", "aws_instance")

    response = client.post(
        f"/topologies/{topology_id}/failures/node-down",
        json={"node": "client-b"},
    )

    assert response.status_code == 400
    assert response.json() == {
        "detail": (
            "server for node 'client-b' has not been deployed; "
            "available server resources: aws_instance:client-a"
        )
    }


def test_failure_event_is_stored(client: TestClient, monkeypatch) -> None:
    provider = mock_failure_provider(monkeypatch)
    monkeypatch.setattr(
        provider,
        "stop_server",
        lambda server_id: {"id": server_id, "status": "SHUTOFF"},
    )

    topology_id = create_topology(client)
    seed_server_resource(client, topology_id, "client-b")
    failure_response = client.post(
        f"/topologies/{topology_id}/failures/node-down",
        json={"node": "client-b"},
    )
    assert failure_response.status_code == 200

    response = client.get(f"/topologies/{topology_id}/failures")

    assert response.status_code == 200
    body = response.json()
    assert body["topology_id"] == topology_id
    assert len(body["failures"]) == 1
    assert body["failures"][0]["target_type"] == "node"
    assert body["failures"][0]["target_name"] == "client-b"
    assert body["failures"][0]["action"] == "node-down"
    assert body["failures"][0]["status"] == "SUCCESS"
