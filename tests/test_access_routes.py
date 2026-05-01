from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from app.db import get_session
from app.main import app
from app.providers.mock_provider import MockProvider
from app.services import access_service
from app.services import deployment_service
from app.services.access_service import command_is_forbidden


@pytest.fixture
def client(tmp_path: Path) -> Generator[TestClient, None, None]:
    database_url = f"sqlite:///{tmp_path / 'access.db'}"
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


def mock_provider(monkeypatch) -> MockProvider:
    provider = MockProvider()
    monkeypatch.setattr(deployment_service, "get_provider", lambda: provider)
    monkeypatch.setattr(access_service, "get_provider", lambda: provider)
    return provider


def create_two_node_topology(client: TestClient) -> int:
    response = client.post(
        "/topologies",
        json={
            "name": "access-test",
            "nodes": [
                {"name": "client-a", "type": "host"},
                {"name": "client-b", "type": "host"},
            ],
            "links": [
                {"from": "client-a", "to": "client-b", "subnet": "10.40.1.0/24"},
            ],
        },
    )
    assert response.status_code == 200
    return response.json()["id"]


def test_access_endpoint_returns_deployed_nodes(client: TestClient, monkeypatch) -> None:
    mock_provider(monkeypatch)
    monkeypatch.setattr(
        MockProvider,
        "create_network",
        lambda self, name, cidr=None: {"id": "net-1", "name": name, "status": "ACTIVE"},
    )
    monkeypatch.setattr(
        MockProvider,
        "create_subnet",
        lambda self, network_id, name, cidr: {
            "id": "subnet-1",
            "name": name,
            "cidr": cidr,
            "network_id": network_id,
        },
    )
    monkeypatch.setattr(
        MockProvider,
        "create_server",
        lambda self, name, network_id, subnet_id=None: {
            "id": f"mock-server-{name}",
            "name": name,
            "status": "running",
            "addresses": {},
        },
    )

    topology_id = create_two_node_topology(client)
    assert client.post(f"/topologies/{topology_id}/deploy").status_code == 200

    response = client.get(f"/topologies/{topology_id}/access")
    assert response.status_code == 200
    body = response.json()
    assert body["topology_id"] == topology_id
    assert body["provider"] == "mock"
    names = {n["name"] for n in body["nodes"]}
    assert names == {"client-a", "client-b"}
    for node in body["nodes"]:
        assert node["instance_id"].startswith("mock-server-")
        assert node["private_ip"] == "10.0.0.10"
        assert node["ssm_available"] is True
        assert node["access_methods"] == ["ssm_exec"]


def test_exec_runs_command_via_mock_provider(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setenv("CLOUDNET_ALLOW_EXEC", "true")
    mock_provider(monkeypatch)
    monkeypatch.setattr(
        MockProvider,
        "create_network",
        lambda self, name, cidr=None: {"id": "net-1", "name": name, "status": "ACTIVE"},
    )
    monkeypatch.setattr(
        MockProvider,
        "create_subnet",
        lambda self, network_id, name, cidr: {
            "id": "subnet-1",
            "name": name,
            "cidr": cidr,
            "network_id": network_id,
        },
    )
    monkeypatch.setattr(
        MockProvider,
        "create_server",
        lambda self, name, network_id, subnet_id=None: {
            "id": f"mock-server-{name}",
            "name": name,
            "status": "running",
            "addresses": {},
        },
    )

    topology_id = create_two_node_topology(client)
    assert client.post(f"/topologies/{topology_id}/deploy").status_code == 200

    response = client.post(
        f"/topologies/{topology_id}/nodes/client-a/exec",
        json={"command": "hostname"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "SUCCESS"
    assert "mock:hostname" in payload["stdout"]
    assert payload["node"] == "client-a"


def test_exec_blocked_without_allow_exec_env(client: TestClient, monkeypatch) -> None:
    monkeypatch.delenv("CLOUDNET_ALLOW_EXEC", raising=False)
    mock_provider(monkeypatch)
    monkeypatch.setattr(
        MockProvider,
        "create_network",
        lambda self, name, cidr=None: {"id": "net-1", "name": name, "status": "ACTIVE"},
    )
    monkeypatch.setattr(
        MockProvider,
        "create_subnet",
        lambda self, network_id, name, cidr: {
            "id": "subnet-1",
            "name": name,
            "cidr": cidr,
            "network_id": network_id,
        },
    )
    monkeypatch.setattr(
        MockProvider,
        "create_server",
        lambda self, name, network_id, subnet_id=None: {
            "id": f"mock-server-{name}",
            "name": name,
            "status": "running",
            "addresses": {},
        },
    )

    topology_id = create_two_node_topology(client)
    assert client.post(f"/topologies/{topology_id}/deploy").status_code == 200

    response = client.post(
        f"/topologies/{topology_id}/nodes/client-a/exec",
        json={"command": "echo hi"},
    )
    assert response.status_code == 403


def test_exec_blocks_dangerous_command(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("CLOUDNET_ALLOW_EXEC", "true")
    mock_provider(monkeypatch)
    monkeypatch.setattr(
        MockProvider,
        "create_network",
        lambda self, name, cidr=None: {"id": "net-1", "name": name, "status": "ACTIVE"},
    )
    monkeypatch.setattr(
        MockProvider,
        "create_subnet",
        lambda self, network_id, name, cidr: {
            "id": "subnet-1",
            "name": name,
            "cidr": cidr,
            "network_id": network_id,
        },
    )
    monkeypatch.setattr(
        MockProvider,
        "create_server",
        lambda self, name, network_id, subnet_id=None: {
            "id": f"mock-server-{name}",
            "name": name,
            "status": "running",
            "addresses": {},
        },
    )

    topology_id = create_two_node_topology(client)
    assert client.post(f"/topologies/{topology_id}/deploy").status_code == 200

    response = client.post(
        f"/topologies/{topology_id}/nodes/client-a/exec",
        json={"command": "shutdown now"},
    )
    assert response.status_code == 400


@pytest.mark.parametrize(
    ("cmd", "expected"),
    [
        ("echo ok", False),
        ("sudo reboot", True),
        ("mkfs.ext4 /dev/xvda", True),
        ("rm -rf /", True),
        (":(){ :|:& };:", True),
    ],
)
def test_command_forbidden_detection(cmd: str, expected: bool) -> None:
    assert command_is_forbidden(cmd) is expected


def test_http_demo_workload_endpoint(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("CLOUDNET_ALLOW_EXEC", "true")
    mock_provider(monkeypatch)
    monkeypatch.setattr(
        MockProvider,
        "create_network",
        lambda self, name, cidr=None: {"id": "net-1", "name": name, "status": "ACTIVE"},
    )
    monkeypatch.setattr(
        MockProvider,
        "create_subnet",
        lambda self, network_id, name, cidr: {
            "id": "subnet-1",
            "name": name,
            "cidr": cidr,
            "network_id": network_id,
        },
    )
    monkeypatch.setattr(
        MockProvider,
        "create_server",
        lambda self, name, network_id, subnet_id=None: {
            "id": f"mock-server-{name}",
            "name": name,
            "status": "running",
            "addresses": {},
        },
    )

    topology_id = create_two_node_topology(client)
    assert client.post(f"/topologies/{topology_id}/deploy").status_code == 200

    response = client.post(
        f"/topologies/{topology_id}/workloads/http-demo",
        json={"node": "client-a"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "status": "STARTED",
        "node": "client-a",
        "port": 8080,
    }
