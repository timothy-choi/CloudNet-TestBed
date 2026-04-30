from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from app.db import get_session
from app.main import app
from app.services import deployment_service


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
            "name": "deploy-test",
            "nodes": [
                {"name": "client-a", "type": "host"},
                {"name": "client-b", "type": "host"},
            ],
            "links": [
                {"from": "client-a", "to": "client-b", "subnet": "10.20.1.0/24"},
            ],
        },
    )

    assert response.status_code == 200
    return response.json()["id"]


def test_deploy_creates_network_and_subnet(client: TestClient, monkeypatch) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def create_network(name: str) -> dict[str, str]:
        calls.append(("network", {"name": name}))
        return {"id": "net-1", "name": name, "status": "ACTIVE"}

    def create_subnet(network_id: str, name: str, cidr: str) -> dict[str, str]:
        calls.append(
            (
                "subnet",
                {"network_id": network_id, "name": name, "cidr": cidr},
            )
        )
        return {
            "id": "subnet-1",
            "name": name,
            "cidr": cidr,
            "network_id": network_id,
        }

    monkeypatch.setattr(deployment_service.openstack_client, "create_network", create_network)
    monkeypatch.setattr(deployment_service.openstack_client, "create_subnet", create_subnet)

    topology_id = create_topology(client)
    response = client.post(f"/topologies/{topology_id}/deploy")

    assert response.status_code == 200
    assert response.json() == {
        "topology_id": topology_id,
        "status": "ACTIVE",
        "resources": [
            {"type": "neutron_network", "name": "deploy-test-net-1", "id": "net-1"},
            {
                "type": "neutron_subnet",
                "name": "deploy-test-net-1-subnet",
                "id": "subnet-1",
            },
        ],
    }
    assert calls == [
        ("network", {"name": "deploy-test-net-1"}),
        (
            "subnet",
            {
                "network_id": "net-1",
                "name": "deploy-test-net-1-subnet",
                "cidr": "10.20.1.0/24",
            },
        ),
    ]


def test_topology_status_becomes_active_on_success(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        deployment_service.openstack_client,
        "create_network",
        lambda name: {"id": "net-1", "name": name, "status": "ACTIVE"},
    )
    monkeypatch.setattr(
        deployment_service.openstack_client,
        "create_subnet",
        lambda network_id, name, cidr: {
            "id": "subnet-1",
            "name": name,
            "cidr": cidr,
            "network_id": network_id,
        },
    )

    topology_id = create_topology(client)

    response = client.post(f"/topologies/{topology_id}/deploy")
    assert response.status_code == 200

    topology_response = client.get(f"/topologies/{topology_id}")
    assert topology_response.status_code == 200
    assert topology_response.json()["status"] == "ACTIVE"


def test_topology_status_becomes_failed_on_client_exception(
    client: TestClient,
    monkeypatch,
) -> None:
    def fail_create_network(name: str) -> dict[str, str]:
        raise RuntimeError("neutron unavailable")

    monkeypatch.setattr(
        deployment_service.openstack_client,
        "create_network",
        fail_create_network,
    )

    topology_id = create_topology(client)
    response = client.post(f"/topologies/{topology_id}/deploy")

    assert response.status_code == 503
    assert response.json() == {
        "detail": "OpenStack deployment failed: neutron unavailable"
    }

    topology_response = client.get(f"/topologies/{topology_id}")
    assert topology_response.status_code == 200
    assert topology_response.json()["status"] == "FAILED"


def test_resources_endpoint_returns_saved_resources(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        deployment_service.openstack_client,
        "create_network",
        lambda name: {"id": "net-1", "name": name, "status": "ACTIVE"},
    )
    monkeypatch.setattr(
        deployment_service.openstack_client,
        "create_subnet",
        lambda network_id, name, cidr: {
            "id": "subnet-1",
            "name": name,
            "cidr": cidr,
            "network_id": network_id,
        },
    )

    topology_id = create_topology(client)
    deploy_response = client.post(f"/topologies/{topology_id}/deploy")
    assert deploy_response.status_code == 200

    response = client.get(f"/topologies/{topology_id}/resources")

    assert response.status_code == 200
    body = response.json()
    assert body["topology_id"] == topology_id
    assert [
        {
            "type": resource["type"],
            "name": resource["name"],
            "openstack_id": resource["openstack_id"],
        }
        for resource in body["resources"]
    ] == [
        {
            "type": "neutron_network",
            "name": "deploy-test-net-1",
            "openstack_id": "net-1",
        },
        {
            "type": "neutron_subnet",
            "name": "deploy-test-net-1-subnet",
            "openstack_id": "subnet-1",
        },
    ]


def test_deploy_refuses_existing_resources(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        deployment_service.openstack_client,
        "create_network",
        lambda name: {"id": "net-1", "name": name, "status": "ACTIVE"},
    )
    monkeypatch.setattr(
        deployment_service.openstack_client,
        "create_subnet",
        lambda network_id, name, cidr: {
            "id": "subnet-1",
            "name": name,
            "cidr": cidr,
            "network_id": network_id,
        },
    )

    topology_id = create_topology(client)
    assert client.post(f"/topologies/{topology_id}/deploy").status_code == 200

    response = client.post(f"/topologies/{topology_id}/deploy")

    assert response.status_code == 409
    assert response.json() == {
        "detail": (
            "topology is already deployed; delete existing resources before redeploying"
        )
    }
