"""Deploy failure triggers partial cleanup; janitor cleans orphaned state."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select

from app.db import get_session
from app.main import app
from app.models import DeploymentResource, Topology
from app.providers.mock_provider import MockProvider
from app.services import deployment_service
from app.services.cleanup_janitor import run_cleanup_janitor
from app.services.deployment_service import deploy_topology


@pytest.fixture
def client(tmp_path: Path) -> Generator[TestClient, None, None]:
    database_url = f"sqlite:///{tmp_path / 'cleanup.db'}"
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


def test_deploy_crash_invokes_full_cleanup(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simulate crash mid-deploy: DB rows exist → deploy cleans provider + DB."""
    provider = MockProvider()
    monkeypatch.setattr(deployment_service, "get_provider", lambda: provider)

    calls: list[str] = []

    real_teardown = deployment_service.teardown_provider_resources

    def track_teardown(prov, resources: list) -> None:
        calls.append("teardown")
        return real_teardown(prov, resources)

    monkeypatch.setattr(deployment_service, "teardown_provider_resources", track_teardown)

    orig_cn = provider.create_network

    def boom_create_network(name: str, cidr=None):
        calls.append("create_network")
        if calls.count("create_network") >= 2:
            raise RuntimeError("simulated crash during deploy")
        return orig_cn(name, cidr=cidr)

    monkeypatch.setattr(provider, "create_network", boom_create_network)

    r = client.post(
        "/topologies",
        json={
            "name": "partial-fail",
            "nodes": [
                {"name": "a", "type": "host"},
                {"name": "b", "type": "host"},
                {"name": "c", "type": "host"},
            ],
            "links": [
                {"from": "a", "to": "b", "subnet": "10.55.1.0/24"},
                {"from": "b", "to": "c", "subnet": "10.55.2.0/24"},
            ],
            "firewall_rules": [],
        },
    )
    assert r.status_code == 200
    tid = r.json()["id"]

    database_url = f"sqlite:///{tmp_path / 'cleanup.db'}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    with Session(engine) as session:
        topology = session.get(Topology, tid)
        assert topology is not None
        with pytest.raises(deployment_service.DeploymentError):
            deploy_topology(session, topology)
        session.commit()
        remaining = list(
            session.exec(
                select(DeploymentResource).where(DeploymentResource.topology_id == tid)
            ).all()
        )
        assert remaining == []

    assert "teardown" in calls


def test_janitor_cleans_orphan_state_without_db_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLOUDNET_STATE_FILE", str(tmp_path / "st.json"))

    from app.services import local_state_store

    local_state_store.save_state(
        {
            "version": 1,
            "deployments": {
                "99": {
                    "topology_id": 99,
                    "scenario_run_id": None,
                    "status": "ACTIVE",
                    "provider_resource_ids": {
                        "vpc": ["vpc-net"],
                        "subnets": [],
                        "instances": [],
                    },
                    "resources": [
                        {
                            "resource_type": "provider_network",
                            "resource_name": "orphan-net",
                            "openstack_id": "vpc-net",
                        },
                    ],
                    "updated_at": "2026-01-01T00:00:00Z",
                }
            },
        }
    )

    database_url = f"sqlite:///{tmp_path / 'j.db'}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        monkeypatch.setattr(
            "app.services.cleanup_janitor.get_provider",
            lambda: MockProvider(),
        )
        out = run_cleanup_janitor(session)
        assert out["actions"]
        assert out["actions"][0]["result"] == "cleaned_orphan"

    st = local_state_store.load_state()
    assert "99" not in st.get("deployments", {})
