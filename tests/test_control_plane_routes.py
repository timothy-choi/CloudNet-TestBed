from collections.abc import Generator
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from app.db import get_session
from app.main import app
from app.models import DeploymentResource
from app.services import control_plane_service


class FakeAWSProvider:
    name = "aws"

    def __init__(self, statuses: dict[str, str | Exception]) -> None:
        self.statuses = statuses
        self.started: list[str] = []
        self.waited: list[str] = []

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
        },
    }


def test_reconcile_starts_stopped_instance_and_validates(
    client: TestClient,
    monkeypatch,
) -> None:
    topology_id = create_topology(client)
    seed_aws_instance_resources(topology_id)
    provider = FakeAWSProvider(
        {"i-client-a": "running", "i-client-b": "stopped"}
    )
    validation_calls: list[tuple[str, str]] = []

    monkeypatch.setattr(control_plane_service, "get_provider", lambda: provider)
    monkeypatch.setattr(
        control_plane_service,
        "create_ping_test",
        lambda session, topology, source, target: validation_calls.append(
            (source, target)
        )
        or SimpleNamespace(status="PASSED"),
    )

    response = client.post(f"/topologies/{topology_id}/reconcile")

    assert response.status_code == 200
    assert response.json() == {
        "topology_id": topology_id,
        "status": "RECONCILED",
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
    provider = FakeAWSProvider(
        {"i-client-a": "running", "i-client-b": "running"}
    )

    monkeypatch.setattr(control_plane_service, "get_provider", lambda: provider)
    monkeypatch.setattr(
        control_plane_service,
        "create_ping_test",
        lambda session, topology, source, target: SimpleNamespace(status="PASSED"),
    )

    response = client.post(f"/topologies/{topology_id}/reconcile")

    assert response.status_code == 200
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
    provider = FakeAWSProvider(
        {
            "i-client-a": "running",
            "i-client-b": RuntimeError("instance not found"),
        }
    )

    monkeypatch.setattr(control_plane_service, "get_provider", lambda: provider)
    monkeypatch.setattr(
        control_plane_service,
        "create_ping_test",
        lambda session, topology, source, target: SimpleNamespace(status="FAILED"),
    )

    response = client.post(f"/topologies/{topology_id}/reconcile")

    assert response.status_code == 200
    assert response.json()["actions"] == [
        {"node": "client-b", "action": "MISSING", "result": "missing"},
        {"action": "validate", "result": "FAILED"},
    ]
    assert provider.started == []
