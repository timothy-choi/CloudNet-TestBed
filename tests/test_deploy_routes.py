import logging
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from app.db import get_session
from app.main import app
from app.providers.mock_provider import MockProvider
from app.routes import topology as topology_routes
from app.services import access_service
from app.services import deployment_service
from app.services import topology_status_service


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


def create_topology(
    client: TestClient,
    nodes: list[dict[str, str]] | None = None,
    links: list[dict[str, str]] | None = None,
) -> int:
    response = client.post(
        "/topologies",
        json={
            "name": "deploy-test",
            "nodes": nodes or [
                {"name": "client-a", "type": "host"},
                {"name": "client-b", "type": "host"},
            ],
            "links": links or [
                {"from": "client-a", "to": "client-b", "subnet": "10.20.1.0/24"},
            ],
        },
    )

    assert response.status_code == 200
    return response.json()["id"]


def mock_deployment_provider(monkeypatch) -> MockProvider:
    provider = MockProvider()
    monkeypatch.setattr(deployment_service, "get_provider", lambda: provider)
    monkeypatch.setattr(topology_status_service, "get_provider", lambda: provider)
    monkeypatch.setattr(access_service, "get_provider", lambda: provider)
    return provider


def test_deploy_creates_network_and_subnet(client: TestClient, monkeypatch) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    provider = mock_deployment_provider(monkeypatch)

    def create_network(name: str, cidr: str | None = None) -> dict[str, str]:
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

    def create_server(name: str, network_id: str) -> dict[str, Any]:
        server_id = f"server-{name}"
        calls.append(("server", {"name": name, "network_id": network_id}))
        return {
            "id": server_id,
            "name": name,
            "status": "ACTIVE",
            "addresses": {},
        }

    monkeypatch.setattr(provider, "create_network", create_network)
    monkeypatch.setattr(provider, "create_subnet", create_subnet)
    monkeypatch.setattr(provider, "create_server", create_server)

    topology_id = create_topology(client)
    response = client.post(f"/topologies/{topology_id}/deploy")

    assert response.status_code == 200
    assert response.json() == {
        "topology_id": topology_id,
        "status": "ACTIVE",
        "resources": [
            {
                "type": "neutron_network",
                "name": "deploy-test-net-1",
                "id": "net-1",
                "provider_resource_id": "net-1",
            },
            {
                "type": "neutron_subnet",
                "name": "deploy-test-net-1-subnet",
                "id": "subnet-1",
                "provider_resource_id": "subnet-1",
            },
            {
                "type": "nova_server",
                "name": "client-a",
                "id": "server-client-a",
                "provider_resource_id": "server-client-a",
            },
            {
                "type": "nova_server",
                "name": "client-b",
                "id": "server-client-b",
                "provider_resource_id": "server-client-b",
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
        ("server", {"name": "client-a", "network_id": "net-1"}),
        ("server", {"name": "client-b", "network_id": "net-1"}),
    ]


def test_topology_status_becomes_active_on_success(
    client: TestClient,
    monkeypatch,
) -> None:
    provider = mock_deployment_provider(monkeypatch)
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
            "id": f"server-{name}",
            "name": name,
            "status": "ACTIVE",
            "addresses": {},
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
    provider = mock_deployment_provider(monkeypatch)

    def fail_create_network(name: str, cidr: str | None = None) -> dict[str, str]:
        raise RuntimeError("neutron unavailable")

    monkeypatch.setattr(
        provider,
        "create_network",
        fail_create_network,
    )

    topology_id = create_topology(client)
    response = client.post(f"/topologies/{topology_id}/deploy")

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Provider deployment failed: neutron unavailable"
    }

    topology_response = client.get(f"/topologies/{topology_id}")
    assert topology_response.status_code == 200
    assert topology_response.json()["status"] == "FAILED"


def test_resources_endpoint_returns_saved_resources(
    client: TestClient,
    monkeypatch,
) -> None:
    provider = mock_deployment_provider(monkeypatch)
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
            "id": f"server-{name}",
            "name": name,
            "status": "ACTIVE",
            "addresses": {},
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
            "provider_resource_id": resource["provider_resource_id"],
        }
        for resource in body["resources"]
    ] == [
        {
            "type": "neutron_network",
            "name": "deploy-test-net-1",
            "openstack_id": "net-1",
            "provider_resource_id": "net-1",
        },
        {
            "type": "neutron_subnet",
            "name": "deploy-test-net-1-subnet",
            "openstack_id": "subnet-1",
            "provider_resource_id": "subnet-1",
        },
        {
            "type": "nova_server",
            "name": "client-a",
            "openstack_id": "server-client-a",
            "provider_resource_id": "server-client-a",
        },
        {
            "type": "nova_server",
            "name": "client-b",
            "openstack_id": "server-client-b",
            "provider_resource_id": "server-client-b",
        },
    ]


def test_deploy_refuses_existing_resources(client: TestClient, monkeypatch) -> None:
    provider = mock_deployment_provider(monkeypatch)
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
            "id": f"server-{name}",
            "name": name,
            "status": "ACTIVE",
            "addresses": {},
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


def test_deploy_skips_router_nodes(client: TestClient, monkeypatch) -> None:
    server_calls: list[dict[str, str]] = []
    provider = mock_deployment_provider(monkeypatch)

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

    def create_server(name: str, network_id: str) -> dict[str, Any]:
        server_calls.append({"name": name, "network_id": network_id})
        return {
            "id": f"server-{name}",
            "name": name,
            "status": "ACTIVE",
            "addresses": {},
        }

    monkeypatch.setattr(
        provider,
        "create_server",
        create_server,
    )

    topology_id = create_topology(
        client,
        nodes=[
            {"name": "client-a", "type": "host"},
            {"name": "router-a", "type": "router"},
        ],
        links=[
            {"from": "client-a", "to": "router-a", "subnet": "10.20.1.0/24"},
        ],
    )

    response = client.post(f"/topologies/{topology_id}/deploy")

    assert response.status_code == 200
    assert server_calls == [{"name": "client-a", "network_id": "net-1"}]
    assert [
        resource
        for resource in response.json()["resources"]
        if resource["type"] == "nova_server"
    ] == [
        {
            "type": "nova_server",
            "name": "client-a",
            "id": "server-client-a",
            "provider_resource_id": "server-client-a",
        },
    ]


def test_aws_deploy_creates_instances_and_aws_resource_types(
    client: TestClient,
    monkeypatch,
    caplog,
) -> None:
    class AWSLikeProvider(MockProvider):
        name = "aws"

        def max_instances_per_deploy(self) -> int:
            return 2

    provider = AWSLikeProvider()
    monkeypatch.setattr(deployment_service, "get_provider", lambda: provider)
    monkeypatch.setattr(
        provider,
        "create_network",
        lambda name, cidr=None: {
            "id": "vpc-1",
            "name": name,
            "cidr": cidr,
            "state": "available",
        },
    )
    monkeypatch.setattr(
        provider,
        "create_subnet",
        lambda network_id, name, cidr: {
            "id": "subnet-1",
            "name": name,
            "cidr": cidr,
            "vpc_id": network_id,
            "internet_gateway_id": "igw-1",
            "route_table_id": "rtb-1",
            "route_table_association_id": "rtbassoc-1",
        },
    )

    def create_server(
        name: str,
        network_id: str,
        subnet_id: str | None = None,
    ) -> dict[str, Any]:
        return {
            "id": f"i-{name}",
            "name": name,
            "status": "pending",
            "private_ip": f"10.20.1.{10 if name == 'client-a' else 11}",
            "public_ip": f"198.51.100.{10 if name == 'client-a' else 11}",
            "security_group_id": "sg-1",
        }

    monkeypatch.setattr(provider, "create_server", create_server)

    topology_id = create_topology(client)
    with caplog.at_level(logging.DEBUG, logger=deployment_service.__name__):
        response = client.post(f"/topologies/{topology_id}/deploy")

    assert response.status_code == 200
    assert response.json() == {
        "topology_id": topology_id,
        "status": "ACTIVE",
        "resources": [
            {
                "type": "aws_vpc",
                "name": "deploy-test-net-1",
                "id": "vpc-1",
                "provider_resource_id": "vpc-1",
            },
            {
                "type": "aws_subnet",
                "name": "deploy-test-net-1-subnet",
                "id": "subnet-1",
                "provider_resource_id": "subnet-1",
            },
            {
                "type": "aws_internet_gateway",
                "name": "deploy-test-net-1-subnet-igw",
                "id": "igw-1",
                "provider_resource_id": "igw-1",
            },
            {
                "type": "aws_route_table",
                "name": "deploy-test-net-1-subnet-rt",
                "id": "rtb-1",
                "provider_resource_id": "rtb-1",
            },
            {
                "type": "aws_route_table_association",
                "name": "deploy-test-net-1-subnet-rt-assoc",
                "id": "rtbassoc-1",
                "provider_resource_id": "rtbassoc-1",
            },
            {
                "type": "aws_security_group",
                "name": "cloudnet-sg",
                "id": "sg-1",
                "provider_resource_id": "sg-1",
            },
            {
                "type": "aws_instance",
                "name": "client-a",
                "id": "i-client-a",
                "provider_resource_id": "i-client-a",
                "private_ip": "10.20.1.10",
                "public_ip": "198.51.100.10",
            },
            {
                "type": "aws_instance",
                "name": "client-b",
                "id": "i-client-b",
                "provider_resource_id": "i-client-b",
                "private_ip": "10.20.1.11",
                "public_ip": "198.51.100.11",
            },
        ],
    }
    assert "Creating EC2 instance for node client-a" in caplog.text
    assert "Creating EC2 instance for node client-b" in caplog.text

    resources_response = client.get(f"/topologies/{topology_id}/resources")
    assert resources_response.status_code == 200
    assert [
        {
            "type": resource["type"],
            "name": resource["name"],
            "openstack_id": resource["openstack_id"],
            "provider_resource_id": resource["provider_resource_id"],
        }
        for resource in resources_response.json()["resources"]
    ] == [
        {
            "type": "aws_vpc",
            "name": "deploy-test-net-1",
            "openstack_id": "vpc-1",
            "provider_resource_id": "vpc-1",
        },
        {
            "type": "aws_subnet",
            "name": "deploy-test-net-1-subnet",
            "openstack_id": "subnet-1",
            "provider_resource_id": "subnet-1",
        },
        {
            "type": "aws_internet_gateway",
            "name": "deploy-test-net-1-subnet-igw",
            "openstack_id": "igw-1",
            "provider_resource_id": "igw-1",
        },
        {
            "type": "aws_route_table",
            "name": "deploy-test-net-1-subnet-rt",
            "openstack_id": "rtb-1",
            "provider_resource_id": "rtb-1",
        },
        {
            "type": "aws_route_table_association",
            "name": "deploy-test-net-1-subnet-rt-assoc",
            "openstack_id": "rtbassoc-1",
            "provider_resource_id": "rtbassoc-1",
        },
        {
            "type": "aws_security_group",
            "name": "cloudnet-sg",
            "openstack_id": "sg-1",
            "provider_resource_id": "sg-1",
        },
        {
            "type": "aws_instance",
            "name": "client-a",
            "openstack_id": "i-client-a",
            "provider_resource_id": "i-client-a",
        },
        {
            "type": "aws_instance",
            "name": "client-b",
            "openstack_id": "i-client-b",
            "provider_resource_id": "i-client-b",
        },
    ]


def test_aws_deploy_enforces_max_instances_before_creating_resources(
    client: TestClient,
    monkeypatch,
) -> None:
    class AWSLikeProvider(MockProvider):
        name = "aws"

        def max_instances_per_deploy(self) -> int:
            return 1

    provider = AWSLikeProvider()
    calls: list[str] = []
    monkeypatch.setattr(deployment_service, "get_provider", lambda: provider)
    monkeypatch.setattr(
        provider,
        "create_network",
        lambda name, cidr=None: calls.append("network") or {"id": "vpc-1", "name": name},
    )

    topology_id = create_topology(client)
    response = client.post(f"/topologies/{topology_id}/deploy")

    assert response.status_code == 503
    assert response.json() == {
        "detail": (
            "Provider deployment failed: Topology requests 2 AWS instances, "
            "but AWS_MAX_INSTANCES_PER_DEPLOY is 1"
        )
    }
    assert calls == []


def test_aws_deploy_creates_subnet_per_link_and_uses_first_host_subnet(
    client: TestClient,
    monkeypatch,
) -> None:
    class AWSLikeProvider(MockProvider):
        name = "aws"

        def max_instances_per_deploy(self) -> int:
            return 3

    provider = AWSLikeProvider()
    subnet_calls: list[dict[str, str]] = []
    server_calls: list[dict[str, str | None]] = []
    monkeypatch.setattr(deployment_service, "get_provider", lambda: provider)
    monkeypatch.setattr(
        provider,
        "create_network",
        lambda name, cidr=None: {
            "id": "vpc-1",
            "name": name,
            "cidr": cidr,
            "state": "available",
        },
    )

    def create_subnet(network_id: str, name: str, cidr: str) -> dict[str, str]:
        subnet_id = f"subnet-{len(subnet_calls) + 1}"
        subnet_calls.append({"network_id": network_id, "name": name, "cidr": cidr})
        return {
            "id": subnet_id,
            "name": name,
            "cidr": cidr,
            "vpc_id": network_id,
        }

    def create_server(
        name: str,
        network_id: str,
        subnet_id: str | None = None,
    ) -> dict[str, str | None]:
        server_calls.append(
            {"name": name, "network_id": network_id, "subnet_id": subnet_id}
        )
        return {
            "id": f"i-{name}",
            "name": name,
            "status": "pending",
            "private_ip": None,
            "public_ip": None,
            "security_group_id": "sg-1",
        }

    monkeypatch.setattr(provider, "create_subnet", create_subnet)
    monkeypatch.setattr(provider, "create_server", create_server)

    topology_id = create_topology(
        client,
        nodes=[
            {"name": "frontend", "type": "host"},
            {"name": "backend", "type": "host"},
            {"name": "db", "type": "host"},
        ],
        links=[
            {"from": "frontend", "to": "backend", "subnet": "10.100.1.0/24"},
            {"from": "backend", "to": "db", "subnet": "10.100.2.0/24"},
        ],
    )

    response = client.post(f"/topologies/{topology_id}/deploy")

    assert response.status_code == 200
    assert subnet_calls == [
        {
            "network_id": "vpc-1",
            "name": "deploy-test-net-1-subnet",
            "cidr": "10.100.1.0/24",
        },
        {
            "network_id": "vpc-1",
            "name": "deploy-test-net-2-subnet",
            "cidr": "10.100.2.0/24",
        },
    ]
    assert server_calls == [
        {"name": "backend", "network_id": "vpc-1", "subnet_id": "subnet-1"},
        {"name": "db", "network_id": "vpc-1", "subnet_id": "subnet-2"},
        {"name": "frontend", "network_id": "vpc-1", "subnet_id": "subnet-1"},
    ]
    assert [
        resource
        for resource in response.json()["resources"]
        if resource["type"] == "aws_subnet"
    ] == [
        {
            "type": "aws_subnet",
            "name": "deploy-test-net-1-subnet",
            "id": "subnet-1",
            "provider_resource_id": "subnet-1",
        },
        {
            "type": "aws_subnet",
            "name": "deploy-test-net-2-subnet",
            "id": "subnet-2",
            "provider_resource_id": "subnet-2",
        },
    ]
    assert response.json()["warnings"] == [
        "multi-homed node backend appears in multiple links; "
        "attached to first subnet only"
    ]


def test_aws_deploy_applies_firewall_rules_to_security_group(
    client: TestClient,
    monkeypatch,
) -> None:
    class AWSLikeProvider(MockProvider):
        name = "aws"

        def max_instances_per_deploy(self) -> int:
            return 2

    provider = AWSLikeProvider()
    firewall_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(deployment_service, "get_provider", lambda: provider)
    monkeypatch.setattr(
        provider,
        "create_network",
        lambda name, cidr=None: {"id": "vpc-1", "name": name, "cidr": cidr},
    )
    monkeypatch.setattr(
        provider,
        "create_subnet",
        lambda network_id, name, cidr: {
            "id": "subnet-1",
            "name": name,
            "cidr": cidr,
            "vpc_id": network_id,
        },
    )
    monkeypatch.setattr(
        provider,
        "create_server",
        lambda name, network_id, subnet_id=None: {
            "id": f"i-{name}",
            "name": name,
            "status": "pending",
            "security_group_id": "sg-1",
        },
    )

    def ensure_firewall_rules(security_group_id, firewall_rules):
        firewall_calls.append(
            {
                "security_group_id": security_group_id,
                "firewall_rules": firewall_rules,
            }
        )
        return [
            {
                "name": firewall_rules[0]["name"],
                "protocol": firewall_rules[0]["protocol"],
                "result": "created",
            }
        ]

    monkeypatch.setattr(provider, "ensure_firewall_rules", ensure_firewall_rules)

    response = client.post(
        "/topologies",
        json={
            "name": "deploy-test",
            "nodes": [
                {"name": "client-a", "type": "host"},
                {"name": "client-b", "type": "host"},
            ],
            "links": [
                {"from": "client-a", "to": "client-b", "subnet": "10.120.1.0/24"},
            ],
            "firewall_rules": [
                {
                    "name": "allow-client-ping",
                    "protocol": "icmp",
                    "from": "client-a",
                    "to": "client-b",
                }
            ],
        },
    )
    topology_id = response.json()["id"]

    response = client.post(f"/topologies/{topology_id}/deploy")

    assert response.status_code == 200
    assert firewall_calls == [
        {
            "security_group_id": "sg-1",
            "firewall_rules": [
                {
                    "name": "allow-client-ping",
                    "protocol": "icmp",
                    "from": "client-a",
                    "to": "client-b",
                }
            ],
        }
    ]


def test_topology_status_endpoint_summarizes_resources_and_validation(
    client: TestClient,
    monkeypatch,
) -> None:
    provider = mock_deployment_provider(monkeypatch)

    def fake_validate(session, topology):
        return {
            "topology_id": topology.id,
            "status": "PASSED",
            "results": [],
        }

    monkeypatch.setattr(
        topology_routes,
        "validate_topology_links",
        fake_validate,
    )
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
            "id": f"server-{name}",
            "name": name,
            "status": "ACTIVE",
            "addresses": {},
        },
    )

    topology_id = create_topology(client)
    empty = client.get(f"/topologies/{topology_id}/status").json()
    assert empty["topology_id"] == topology_id
    assert empty["status"] == "CREATED"
    assert empty["resources_summary"] == {
        "instances": 0,
        "subnets": 0,
        "security_groups": 0,
    }
    assert empty["last_validation"] is None
    assert empty["drift_detected"] is True

    client.post(f"/topologies/{topology_id}/deploy")
    deployed = client.get(f"/topologies/{topology_id}/status").json()
    assert deployed["status"] == "ACTIVE"
    assert deployed["resources_summary"] == {
        "instances": 2,
        "subnets": 1,
        "security_groups": 0,
    }
    assert deployed["drift_detected"] is False

    client.post(f"/topologies/{topology_id}/validate")
    validated = client.get(f"/topologies/{topology_id}/status").json()
    assert validated["last_validation"] == "PASSED"


def test_topology_status_endpoint_returns_404_for_unknown_topology(
    client: TestClient,
) -> None:
    response = client.get("/topologies/999999/status")
    assert response.status_code == 404
