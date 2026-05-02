from collections.abc import Generator
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from app.db import get_session
from app.main import app
from app.models import DeploymentResource
from app.providers.mock_provider import MockProvider
from app.services import control_plane_service, deployment_service, drift_service


class FakeAWSProvider:
    name = "aws"

    def __init__(self, statuses: dict[str, str | Exception]) -> None:
        self.statuses = statuses
        self.started: list[str] = []
        self.waited: list[str] = []
        self.firewall_results: list[dict[str, str]] = []

    def get_server_status(self, instance_id: str) -> str:
        status = self.statuses[instance_id]
        if isinstance(status, Exception):
            raise status
        return status

    def start_server(self, instance_id: str) -> dict[str, str]:
        self.started.append(instance_id)
        self.statuses[instance_id] = "running"
        return {"id": instance_id, "status": "running"}

    def wait_for_server_running(self, instance_id: str) -> None:
        self.waited.append(instance_id)

    def ensure_firewall_rules(
        self,
        security_group_id: str,
        firewall_rules: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        return self.firewall_results

    def resource_exists(self, resource_type: str, resource_id: str) -> bool:
        return "missing" not in resource_id

    def firewall_rule_exists(
        self,
        security_group_id: str,
        firewall_rule: dict[str, str],
    ) -> bool:
        return "missing" not in firewall_rule["name"]


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
            "name": "control-plane-test",
            "nodes": [
                {"name": "client-a", "type": "host"},
                {"name": "client-b", "type": "host"},
                {"name": "router-a", "type": "router"},
            ],
            "links": [
                {"from": "client-a", "to": "client-b", "subnet": "10.91.1.0/24"},
            ],
        },
    )

    assert response.status_code == 200
    return response.json()["id"]


def create_three_tier_topology(client: TestClient) -> int:
    response = client.post(
        "/topologies",
        json={
            "name": "three-tier-app",
            "nodes": [
                {"name": "frontend", "type": "host"},
                {"name": "backend", "type": "host"},
                {"name": "db", "type": "host"},
            ],
            "links": [
                {"from": "frontend", "to": "backend", "subnet": "10.100.1.0/24"},
                {"from": "backend", "to": "db", "subnet": "10.100.2.0/24"},
            ],
        },
    )

    assert response.status_code == 200
    return response.json()["id"]


def create_secure_three_tier_topology(client: TestClient) -> int:
    response = client.post(
        "/topologies",
        json={
            "name": "secure-three-tier-app",
            "nodes": [
                {"name": "frontend", "type": "host"},
                {"name": "backend", "type": "host"},
                {"name": "db", "type": "host"},
            ],
            "links": [
                {"from": "frontend", "to": "backend", "subnet": "10.120.1.0/24"},
                {"from": "backend", "to": "db", "subnet": "10.120.2.0/24"},
            ],
            "firewall_rules": [
                {
                    "name": "allow-frontend-backend-ping",
                    "protocol": "icmp",
                    "from": "frontend",
                    "to": "backend",
                },
                {
                    "name": "allow-backend-db-ping",
                    "protocol": "icmp",
                    "from": "backend",
                    "to": "db",
                },
            ],
        },
    )

    assert response.status_code == 200
    return response.json()["id"]


def seed_aws_instance_resources(topology_id: int) -> None:
    session_override = app.dependency_overrides[get_session]
    session_generator = session_override()
    session = next(session_generator)
    try:
        session.add(
            DeploymentResource(
                topology_id=topology_id,
                resource_type="aws_instance",
                resource_name="client-a",
                openstack_id="i-client-a",
            )
        )
        session.add(
            DeploymentResource(
                topology_id=topology_id,
                resource_type="aws_instance",
                resource_name="client-b",
                openstack_id="i-client-b",
            )
        )
        session.commit()
    finally:
        session_generator.close()


def seed_aws_security_group_resource(topology_id: int) -> None:
    session_override = app.dependency_overrides[get_session]
    session_generator = session_override()
    session = next(session_generator)
    try:
        session.add(
            DeploymentResource(
                topology_id=topology_id,
                resource_type="aws_security_group",
                resource_name="cloudnet-sg",
                openstack_id="sg-1",
            )
        )
        session.commit()
    finally:
        session_generator.close()


def seed_aws_subnet_resource(topology_id: int) -> None:
    session_override = app.dependency_overrides[get_session]
    session_generator = session_override()
    session = next(session_generator)
    try:
        session.add(
            DeploymentResource(
                topology_id=topology_id,
                resource_type="aws_subnet",
                resource_name="control-plane-test-net-1-subnet",
                openstack_id="subnet-1",
            )
        )
        session.commit()
    finally:
        session_generator.close()


def test_plan_endpoint_compiles_without_calling_provider(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setenv("CLOUDNET_PROVIDER", "aws")
    topology_id = create_topology(client)

    response = client.get(f"/topologies/{topology_id}/plan")

    assert response.status_code == 200
    assert response.json() == {
        "topology_id": topology_id,
        "provider": "aws",
        "plan": {
            "vpc": {"cidr": "10.0.0.0/16"},
            "subnets": [{"cidr": "10.91.1.0/24"}],
            "instances": [
                {"name": "client-a"},
                {"name": "client-b"},
            ],
            "security_groups": [{"name": "cloudnet-sg"}],
            "firewall_rules": [],
        },
    }


def test_plan_endpoint_creates_multiple_subnets_and_warning(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setenv("CLOUDNET_PROVIDER", "aws")
    topology_id = create_three_tier_topology(client)

    response = client.get(f"/topologies/{topology_id}/plan")

    assert response.status_code == 200
    assert response.json()["plan"]["subnets"] == [
        {"cidr": "10.100.1.0/24"},
        {"cidr": "10.100.2.0/24"},
    ]
    assert response.json()["plan"]["instances"] == [
        {"name": "frontend"},
        {"name": "backend"},
        {"name": "db"},
    ]
    assert response.json()["warnings"] == [
        "multi-homed node backend appears in multiple links; "
        "attached to first subnet only"
    ]


def test_plan_endpoint_includes_firewall_rules(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setenv("CLOUDNET_PROVIDER", "aws")
    response = client.post(
        "/topologies",
        json={
            "name": "secure-three-tier-app",
            "nodes": [
                {"name": "frontend", "type": "host"},
                {"name": "backend", "type": "host"},
                {"name": "db", "type": "host"},
            ],
            "links": [
                {"from": "frontend", "to": "backend", "subnet": "10.120.1.0/24"},
                {"from": "backend", "to": "db", "subnet": "10.120.2.0/24"},
            ],
            "firewall_rules": [
                {
                    "name": "allow-frontend-backend-ping",
                    "protocol": "icmp",
                    "from": "frontend",
                    "to": "backend",
                }
            ],
        },
    )
    topology_id = response.json()["id"]

    response = client.get(f"/topologies/{topology_id}/plan")

    assert response.status_code == 200
    assert response.json()["plan"]["firewall_rules"] == [
        {
            "name": "allow-frontend-backend-ping",
            "protocol": "icmp",
            "from": "frontend",
            "to": "backend",
        }
    ]


def test_terraform_export_includes_vpc_subnets_instances_and_firewall_rules(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
    topology_id = create_secure_three_tier_topology(client)

    response = client.get(f"/topologies/{topology_id}/terraform")

    assert response.status_code == 200
    body = response.json()
    assert body["topology_id"] == topology_id
    assert body["provider"] == "aws"
    assert set(body["files"]) == {"main.tf", "variables.tf", "outputs.tf"}
    main_tf = body["files"]["main.tf"]
    assert 'resource "aws_vpc" "cloudnet"' in main_tf
    assert main_tf.count('resource "aws_subnet"') == 2
    assert 'cidr_block              = "10.120.1.0/24"' in main_tf
    assert 'cidr_block              = "10.120.2.0/24"' in main_tf
    assert main_tf.count('resource "aws_instance"') == 3
    assert 'resource "aws_instance" "frontend"' in main_tf
    assert 'resource "aws_instance" "backend"' in main_tf
    assert 'resource "aws_instance" "db"' in main_tf
    assert 'resource "aws_security_group_rule" "allow_frontend_backend_ping"' in main_tf
    assert 'resource "aws_security_group_rule" "allow_backend_db_ping"' in main_tf
    assert 'Project   = "CloudNet"' in main_tf
    assert 'ManagedBy = "CloudNet"' in main_tf


def test_terraform_export_zip_contains_files(client: TestClient) -> None:
    topology_id = create_secure_three_tier_topology(client)

    response = client.get(f"/topologies/{topology_id}/terraform.zip")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    with ZipFile(BytesIO(response.content)) as archive:
        assert sorted(archive.namelist()) == ["main.tf", "outputs.tf", "variables.tf"]
        assert 'resource "aws_vpc" "cloudnet"' in archive.read("main.tf").decode()


def test_reconcile_starts_stopped_instance_and_validates(
    client: TestClient,
    monkeypatch,
) -> None:
    topology_id = create_topology(client)
    seed_aws_instance_resources(topology_id)
    seed_aws_security_group_resource(topology_id)
    provider = FakeAWSProvider(
        {"i-client-a": "running", "i-client-b": "stopped"}
    )
    validation_calls: list[tuple[str, str]] = []

    monkeypatch.setattr(control_plane_service, "get_provider", lambda: provider)
    monkeypatch.setattr(
        control_plane_service,
        "validate_topology_links",
        lambda session, topology: validation_calls.append(("client-a", "client-b"))
        or {"topology_id": topology.id, "status": "PASSED", "results": []},
    )

    response = client.post(f"/topologies/{topology_id}/reconcile")

    assert response.status_code == 200
    assert response.json() == {
        "topology_id": topology_id,
        "status": "RECONCILED",
        "drift": {
            "topology_id": topology_id,
            "drift_detected": True,
            "items": [
                {
                    "resource_type": "aws_instance",
                    "name": "client-b",
                    "expected": "running",
                    "actual": "stopped",
                    "severity": "warning",
                }
            ],
        },
        "actions": [
            {"node": "client-b", "action": "start", "result": "started"},
            {"action": "validate", "result": "PASSED"},
        ],
    }
    assert provider.started == ["i-client-b"]
    assert provider.waited == ["i-client-b"]
    assert validation_calls == [("client-a", "client-b")]


def test_reconcile_running_instance_takes_no_repair_action(
    client: TestClient,
    monkeypatch,
) -> None:
    topology_id = create_topology(client)
    seed_aws_instance_resources(topology_id)
    seed_aws_security_group_resource(topology_id)
    provider = FakeAWSProvider(
        {"i-client-a": "running", "i-client-b": "running"}
    )

    monkeypatch.setattr(control_plane_service, "get_provider", lambda: provider)
    monkeypatch.setattr(
        control_plane_service,
        "validate_topology_links",
        lambda session, topology: {
            "topology_id": topology.id,
            "status": "PASSED",
            "results": [],
        },
    )

    response = client.post(f"/topologies/{topology_id}/reconcile")

    assert response.status_code == 200
    assert response.json()["drift"] == {
        "topology_id": topology_id,
        "drift_detected": False,
        "items": [],
    }
    assert response.json()["actions"] == [
        {"action": "validate", "result": "PASSED"},
    ]
    assert provider.started == []
    assert provider.waited == []


def test_reconcile_missing_instance_records_missing_action(
    client: TestClient,
    monkeypatch,
) -> None:
    topology_id = create_topology(client)
    seed_aws_instance_resources(topology_id)
    seed_aws_security_group_resource(topology_id)
    provider = FakeAWSProvider(
        {
            "i-client-a": "running",
            "i-client-b": RuntimeError("instance not found"),
        }
    )

    monkeypatch.setattr(control_plane_service, "get_provider", lambda: provider)
    monkeypatch.setattr(
        control_plane_service,
        "validate_topology_links",
        lambda session, topology: {
            "topology_id": topology.id,
            "status": "FAILED",
            "results": [],
        },
    )

    response = client.post(f"/topologies/{topology_id}/reconcile")

    assert response.status_code == 200
    assert response.json()["drift"] == {
        "topology_id": topology_id,
        "drift_detected": True,
        "items": [
            {
                "resource_type": "aws_instance",
                "name": "client-b",
                "expected": "running",
                "actual": "missing",
                "severity": "critical",
            }
        ],
    }
    assert response.json()["actions"] == [
        {"node": "client-b", "action": "MISSING", "result": "missing"},
        {"action": "validate", "result": "FAILED"},
    ]
    assert provider.started == []


def test_reconcile_restores_missing_firewall_rule(
    client: TestClient,
    monkeypatch,
) -> None:
    response = client.post(
        "/topologies",
        json={
            "name": "secure-test",
            "nodes": [
                {"name": "client-a", "type": "host"},
                {"name": "client-b", "type": "host"},
            ],
            "links": [
                {"from": "client-a", "to": "client-b", "subnet": "10.91.1.0/24"},
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
    seed_aws_instance_resources(topology_id)
    seed_aws_security_group_resource(topology_id)
    provider = FakeAWSProvider(
        {"i-client-a": "running", "i-client-b": "running"}
    )
    provider.firewall_results = [
        {"name": "allow-client-ping", "protocol": "icmp", "result": "created"}
    ]

    monkeypatch.setattr(control_plane_service, "get_provider", lambda: provider)
    monkeypatch.setattr(
        control_plane_service,
        "validate_topology_links",
        lambda session, topology: {
            "topology_id": topology.id,
            "status": "PASSED",
            "results": [],
        },
    )

    response = client.post(f"/topologies/{topology_id}/reconcile")

    assert response.status_code == 200
    assert response.json()["drift"]["drift_detected"] is False
    assert {
        "resource": "cloudnet-sg",
        "action": "restore_firewall_rule",
        "result": "created",
    } in response.json()["actions"]


def test_drift_endpoint_returns_no_drift(
    client: TestClient,
    monkeypatch,
) -> None:
    topology_id = create_topology(client)
    seed_aws_instance_resources(topology_id)
    seed_aws_security_group_resource(topology_id)
    provider = FakeAWSProvider(
        {"i-client-a": "running", "i-client-b": "running"}
    )
    monkeypatch.setattr(drift_service, "get_provider", lambda: provider)

    response = client.get(f"/topologies/{topology_id}/drift")

    assert response.status_code == 200
    assert response.json() == {
        "topology_id": topology_id,
        "drift_detected": False,
        "items": [],
    }


def test_drift_endpoint_reports_stopped_instance(
    client: TestClient,
    monkeypatch,
) -> None:
    topology_id = create_topology(client)
    seed_aws_instance_resources(topology_id)
    seed_aws_security_group_resource(topology_id)
    provider = FakeAWSProvider(
        {"i-client-a": "running", "i-client-b": "stopped"}
    )
    monkeypatch.setattr(drift_service, "get_provider", lambda: provider)

    response = client.get(f"/topologies/{topology_id}/drift")

    assert response.status_code == 200
    assert response.json()["drift_detected"] is True
    assert response.json()["items"] == [
        {
            "resource_type": "aws_instance",
            "name": "client-b",
            "expected": "running",
            "actual": "stopped",
            "severity": "warning",
        }
    ]


def test_drift_endpoint_reports_missing_instance(
    client: TestClient,
    monkeypatch,
) -> None:
    topology_id = create_topology(client)
    seed_aws_instance_resources(topology_id)
    seed_aws_security_group_resource(topology_id)
    provider = FakeAWSProvider(
        {
            "i-client-a": "running",
            "i-client-b": RuntimeError("instance not found"),
        }
    )
    monkeypatch.setattr(drift_service, "get_provider", lambda: provider)

    response = client.get(f"/topologies/{topology_id}/drift")

    assert response.status_code == 200
    assert response.json()["items"] == [
        {
            "resource_type": "aws_instance",
            "name": "client-b",
            "expected": "running",
            "actual": "missing",
            "severity": "critical",
        }
    ]


def test_events_are_created_on_deploy(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(deployment_service, "get_provider", lambda: MockProvider())
    topology_id = create_topology(client)

    response = client.post(f"/topologies/{topology_id}/deploy")

    assert response.status_code == 200
    events_response = client.get(f"/topologies/{topology_id}/events")
    assert events_response.status_code == 200
    events = events_response.json()["events"]
    assert [event["type"] for event in events] == [
        "DEPLOY_START",
        "DEPLOY_COMPLETE",
    ]
    assert events[0]["status"] == "STARTED"
    assert events[1]["status"] == "SUCCESS"
    assert events[1]["metadata"]["instance_count"] == 2


def test_event_is_created_on_validation(
    client: TestClient,
    monkeypatch,
) -> None:
    from app.models import DeploymentResource
    from app.services import connectivity_service

    monkeypatch.setenv("CLOUDNET_PROVIDER", "mock")
    topology_id = create_topology(client)

    session_override = app.dependency_overrides[get_session]
    gen = session_override()
    session = next(gen)
    try:
        session.add(
            DeploymentResource(
                topology_id=topology_id,
                resource_type="provider_instance",
                resource_name="client-a",
                openstack_id="srv-a",
            )
        )
        session.add(
            DeploymentResource(
                topology_id=topology_id,
                resource_type="provider_instance",
                resource_name="client-b",
                openstack_id="srv-b",
            )
        )
        session.commit()
    finally:
        gen.close()

    monkeypatch.setattr(connectivity_service, "get_provider", lambda: MockProvider())

    response = client.post(f"/topologies/{topology_id}/validate")

    assert response.status_code == 200
    events = client.get(f"/topologies/{topology_id}/events").json()["events"]
    assert [event["type"] for event in events] == [
        "VALIDATION_STARTED",
        "VALIDATION_COMPLETE",
        "VALIDATION",
    ]
    assert events[-1]["type"] == "VALIDATION"
    assert events[-1]["status"] == "SUCCESS"
    assert events[-1]["metadata"]["results"][0]["status"] == "PASSED"
    assert events[1]["metadata"].get("duration_ms") is not None


def test_events_are_created_on_reconcile(
    client: TestClient,
    monkeypatch,
) -> None:
    topology_id = create_topology(client)
    seed_aws_instance_resources(topology_id)
    seed_aws_security_group_resource(topology_id)
    provider = FakeAWSProvider(
        {"i-client-a": "running", "i-client-b": "stopped"}
    )
    monkeypatch.setattr(control_plane_service, "get_provider", lambda: provider)
    monkeypatch.setattr(
        control_plane_service,
        "validate_topology_links",
        lambda session, topology: {
            "topology_id": topology.id,
            "status": "PASSED",
            "results": [],
        },
    )

    response = client.post(f"/topologies/{topology_id}/reconcile")

    assert response.status_code == 200
    events = client.get(f"/topologies/{topology_id}/events").json()["events"]
    assert [event["type"] for event in events] == [
        "RECONCILE",
        "DRIFT_DETECTED",
        "RECONCILE",
        "RECONCILE",
    ]
    assert events[0]["status"] == "STARTED"
    assert events[1]["metadata"]["items"] == [
        {
            "resource_type": "aws_instance",
            "name": "client-b",
            "expected": "running",
            "actual": "stopped",
            "severity": "warning",
        }
    ]
    assert {k: events[2]["metadata"][k] for k in ("node", "action", "result")} == {
        "node": "client-b",
        "action": "start",
        "result": "started",
    }
    assert events[2]["metadata"].get("topology_id") == topology_id
    assert events[3]["message"] == "Reconcile complete"


def test_events_are_returned_in_order_with_reverse_and_limit(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setenv("CLOUDNET_PROVIDER", "aws")
    topology_id = create_topology(client)

    response = client.get(f"/topologies/{topology_id}/plan")

    assert response.status_code == 200
    events = client.get(f"/topologies/{topology_id}/events").json()["events"]
    assert [event["status"] for event in events] == ["STARTED", "SUCCESS"]

    latest = client.get(
        f"/topologies/{topology_id}/events",
        params={"reverse": True, "limit": 1},
    ).json()["events"]
    assert len(latest) == 1
    assert latest[0]["status"] == "SUCCESS"


def test_mock_control_plane_flow_deploy_validate_drift_reconcile_events(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setenv("CLOUDNET_PROVIDER", "mock")
    topology_id = create_secure_three_tier_topology(client)

    deploy_response = client.post(f"/topologies/{topology_id}/deploy")

    assert deploy_response.status_code == 200
    assert deploy_response.json()["status"] == "ACTIVE"
    resources = client.get(f"/topologies/{topology_id}/resources").json()["resources"]
    assert [resource["type"] for resource in resources].count("provider_subnet") == 2
    assert [resource["type"] for resource in resources].count("provider_instance") == 3

    validation_response = client.post(f"/topologies/{topology_id}/validate")
    assert validation_response.status_code == 200
    assert validation_response.json()["status"] == "PASSED"

    failure_response = client.post(
        f"/topologies/{topology_id}/failures/node-down",
        json={"node": "backend"},
    )
    assert failure_response.status_code == 200
    assert failure_response.json()["status"] == "SUCCESS"

    failed_validation = client.post(f"/topologies/{topology_id}/validate")
    assert failed_validation.status_code == 200
    assert failed_validation.json()["status"] == "FAILED"

    drift_response = client.get(f"/topologies/{topology_id}/drift")
    assert drift_response.status_code == 200
    assert drift_response.json()["drift_detected"] is True
    assert {
        "resource_type": "provider_instance",
        "name": "backend",
        "expected": "running",
        "actual": "stopped",
        "severity": "warning",
    } in drift_response.json()["items"]

    reconcile_response = client.post(f"/topologies/{topology_id}/reconcile")
    assert reconcile_response.status_code == 200
    assert reconcile_response.json()["status"] == "RECONCILED"
    assert reconcile_response.json()["drift"]["drift_detected"] is True
    assert {
        "node": "backend",
        "action": "start",
        "result": "started",
    } in reconcile_response.json()["actions"]

    recovered_validation = client.post(f"/topologies/{topology_id}/validate")
    assert recovered_validation.status_code == 200
    assert recovered_validation.json()["status"] == "PASSED"

    events = client.get(f"/topologies/{topology_id}/events").json()["events"]
    event_types = [event["type"] for event in events]
    assert "DEPLOY_COMPLETE" in event_types
    assert "VALIDATION" in event_types
    assert "FAILURE_INJECTED" in event_types
    assert "DRIFT_DETECTED" in event_types
    assert any(event["message"] == "Reconcile complete" for event in events)
