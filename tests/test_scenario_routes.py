from collections.abc import Generator
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from app.db import get_session
from app.main import app
from app.providers.mock_provider import MockProvider
from app.services import connectivity_service
from app.services import control_plane_service
from app.services import deployment_service
from app.services import drift_service
from app.services import failure_service


@pytest.fixture
def client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[TestClient, None, None]:
    monkeypatch.setenv("CLOUDNET_STATE_FILE", str(tmp_path / "cloudnet-state.json"))
    database_url = f"sqlite:///{tmp_path / 'scenario.db'}"
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


def mock_stack(monkeypatch) -> MockProvider:
    provider = MockProvider()
    for module in (
        deployment_service,
        failure_service,
        control_plane_service,
        connectivity_service,
        drift_service,
    ):
        monkeypatch.setattr(module, "get_provider", lambda p=provider: p)
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
        lambda name, network_id, subnet_id=None: {
            "id": f"mock-server-{name}",
            "name": name,
            "status": "running",
            "addresses": {},
        },
    )
    return provider


def test_scenario_run_backend_failure_flow(client: TestClient, monkeypatch) -> None:
    mock_stack(monkeypatch)

    response = client.post(
        "/scenarios/run",
        json={
            "scenario": {"name": "backend_failure_test"},
            "topology": {
                "name": "scenario-two-host",
                "nodes": [
                    {"name": "client-a", "type": "host"},
                    {"name": "client-b", "type": "host"},
                ],
                "links": [
                    {
                        "from": "client-a",
                        "to": "client-b",
                        "subnet": "10.99.1.0/24",
                    },
                ],
                "firewall_rules": [],
            },
            "steps": [
                {"deploy": True},
                {"validate": "all"},
                {"fail": {"node": "client-b"}},
                {"validate": {"expect": "fail"}},
                {"drift": {"expect": "detected"}},
                {"reconcile": True},
                {"validate": {"expect": "pass"}},
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["scenario"] == "backend_failure_test"
    assert body["status"] == "PASSED"
    assert body["topology_name"] == "scenario-two-host"
    assert body["event_timeline_url"] == f"/topologies/{body['topology_id']}/events"
    assert "duration_ms" in body
    assert body["duration_ms"] >= 0
    assert "scenario_run_id" in body
    assert "started_at" in body and "finished_at" in body

    steps = body["steps"]
    assert [s["name"] for s in steps] == [
        "deploy",
        "validate",
        "fail client-b",
        "validate",
        "drift",
        "reconcile",
        "validate",
    ]
    assert [s["action"] for s in steps] == [
        "deploy",
        "validate",
        "fail",
        "validate",
        "drift",
        "reconcile",
        "validate",
    ]
    assert steps[0]["actual"] == "ACTIVE"
    assert steps[0]["status"] == "PASSED"
    assert steps[1]["actual"] == "PASSED"
    assert steps[2]["actual"] == "SUCCESS"
    assert steps[2]["provider_action"] == "stop_server"
    assert steps[3]["actual"] == "FAILED"
    assert steps[3]["status"] == "PASSED"
    assert steps[4]["actual"] == "DETECTED"
    assert steps[5]["actual"] == "RECONCILED"
    assert steps[6]["actual"] == "PASSED"

    for s in steps:
        assert "duration_ms" in s


def test_scenario_implicit_deploy_when_no_deploy_step(client: TestClient, monkeypatch) -> None:
    """Implicit deploy runs once before steps and records a deploy step."""
    mock_stack(monkeypatch)

    response = client.post(
        "/scenarios/run",
        json={
            "scenario": {"name": "implicit_deploy"},
            "topology": {
                "name": "scenario-impl",
                "nodes": [
                    {"name": "client-a", "type": "host"},
                    {"name": "client-b", "type": "host"},
                ],
                "links": [
                    {"from": "client-a", "to": "client-b", "subnet": "10.88.1.0/24"},
                ],
                "firewall_rules": [],
            },
            "steps": [{"validate": "all"}],
        },
    )
    assert response.status_code == 200
    steps = response.json()["steps"]
    assert len(steps) == 2
    assert steps[0]["action"] == "deploy"
    assert steps[0]["actual"] == "ACTIVE"
    assert steps[1]["action"] == "validate"


def test_scenario_get_results_round_trip(client: TestClient, monkeypatch) -> None:
    mock_stack(monkeypatch)

    post = client.post(
        "/scenarios/run",
        json={
            "scenario": {"name": "roundtrip"},
            "topology": {
                "name": "scenario-rt",
                "nodes": [{"name": "solo", "type": "host"}],
                "links": [{"from": "solo", "to": "solo", "subnet": "10.97.1.0/24"}],
                "firewall_rules": [],
            },
            "steps": [{"validate": "all"}],
        },
    )
    assert post.status_code == 200
    body = post.json()
    rid = body["scenario_run_id"]
    tid = body["topology_id"]

    get_r = client.get(f"/scenarios/{rid}/results")
    assert get_r.status_code == 200
    fetched = get_r.json()
    assert fetched["scenario_run_id"] == rid
    assert fetched["event_timeline_url"] == f"/topologies/{tid}/events"
    assert fetched["scenario"] == "roundtrip"
    assert len(fetched["steps"]) == 2
    assert fetched["steps"][0]["action"] == "deploy"
    assert fetched["steps"][1]["action"] == "validate"


def test_scenario_run_accepts_yaml_body(client: TestClient, monkeypatch) -> None:
    mock_stack(monkeypatch)

    payload = {
        "scenario": {"name": "yaml_payload"},
        "topology": {
            "name": "scenario-yaml-body",
            "nodes": [{"name": "solo", "type": "host"}],
            "links": [{"from": "solo", "to": "solo", "subnet": "10.98.1.0/24"}],
            "firewall_rules": [],
        },
        "steps": [{"validate": "all"}],
    }
    response = client.post(
        "/scenarios/run",
        content=yaml.dump(payload),
        headers={"Content-Type": "application/x-yaml"},
    )
    assert response.status_code == 200
    assert response.json()["scenario"] == "yaml_payload"


def test_scenario_run_rejects_bad_step(client: TestClient, monkeypatch) -> None:
    mock_stack(monkeypatch)

    response = client.post(
        "/scenarios/run",
        json={
            "scenario": {"name": "bad"},
            "topology": {
                "name": "scenario-bad-step",
                "nodes": [{"name": "a", "type": "host"}],
                "links": [{"from": "a", "to": "a", "subnet": "10.1.1.0/24"}],
                "firewall_rules": [],
            },
            "steps": [{"unknown": True}],
        },
    )

    assert response.status_code == 400


def test_scenario_results_not_found(client: TestClient, monkeypatch) -> None:
    mock_stack(monkeypatch)
    r = client.get("/scenarios/99999/results")
    assert r.status_code == 404


def test_scenario_fails_when_validate_expectation_mismatch(
    client: TestClient, monkeypatch,
) -> None:
    mock_stack(monkeypatch)
    response = client.post(
        "/scenarios/run",
        json={
            "scenario": {"name": "bad_expect"},
            "topology": {
                "name": "scenario-mismatch",
                "nodes": [
                    {"name": "a", "type": "host"},
                    {"name": "b", "type": "host"},
                ],
                "links": [{"from": "a", "to": "b", "subnet": "10.77.1.0/24"}],
                "firewall_rules": [],
            },
            "steps": [
                {"validate": "all"},
                {"validate": {"expect": "fail"}},
            ],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "FAILED"
    assert body["steps"][2]["status"] == "FAILED"


def test_simple_connectivity_example_passes(client: TestClient, monkeypatch) -> None:
    mock_stack(monkeypatch)
    root = Path(__file__).resolve().parents[1]
    text = (root / "examples" / "simple-connectivity.yaml").read_text()
    data = yaml.safe_load(text)
    response = client.post("/scenarios/run", json=data)
    assert response.status_code == 200
    assert response.json()["status"] == "PASSED"


def test_scenario_drift_expect_none_alias(client: TestClient, monkeypatch) -> None:
    mock_stack(monkeypatch)
    response = client.post(
        "/scenarios/run",
        json={
            "scenario": {"name": "drift_none"},
            "topology": {
                "name": "scenario-drift-none",
                "nodes": [{"name": "solo", "type": "host"}],
                "links": [{"from": "solo", "to": "solo", "subnet": "10.76.1.0/24"}],
                "firewall_rules": [],
            },
            "steps": [
                {"validate": "all"},
                {"drift": {"expect": "none"}},
            ],
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "PASSED"


_NFR_TOPO = {
    "name": "scenario-nfr",
    "nodes": [
        {"name": "client-a", "type": "host"},
        {"name": "client-b", "type": "host"},
    ],
    "links": [{"from": "client-a", "to": "client-b", "subnet": "10.99.1.0/24"}],
    "firewall_rules": [],
}

_NFR_STEPS = [
    {"deploy": True},
    {"validate": "all"},
    {"fail": {"node": "client-b"}},
    {"validate": {"expect": "fail"}},
    {"drift": {"expect": "detected"}},
    {"reconcile": True},
    {"validate": {"expect": "pass"}},
]


def test_scenario_requirements_report_when_declared(
    client: TestClient, monkeypatch,
) -> None:
    mock_stack(monkeypatch)
    monkeypatch.setenv("CLOUDNET_MOCK_PING_LOSS_RATE", "0")
    response = client.post(
        "/scenarios/run",
        json={
            "scenario": {"name": "nfr_declared"},
            "topology": _NFR_TOPO,
            "steps": _NFR_STEPS,
            "requirements": {
                "availability": {"min_success_rate": 0.5},
                "latency": {"max_avg_ms": 500.0, "max_p95_ms": 500.0},
                "recovery": {"max_recovery_seconds": 3600},
            },
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "PASSED"
    req = body["requirements"]
    assert req["availability"]["status"] == "PASSED"
    assert req["latency"]["status"] == "PASSED"
    assert req["recovery"]["status"] == "PASSED"


def test_scenario_requirements_latency_fails(client: TestClient, monkeypatch) -> None:
    from app.providers.mock_provider import MockProvider

    mock_stack(monkeypatch)

    def slow_ping(self, source_server_id: str, target_ip: str) -> str:
        lines = [
            f"64 bytes from {target_ip}: icmp_seq={i + 1} ttl=64 time=800.00 ms"
            for i in range(3)
        ]
        return "\n".join(lines)

    monkeypatch.setattr(MockProvider, "run_ping", slow_ping)
    response = client.post(
        "/scenarios/run",
        json={
            "scenario": {"name": "nfr_lat"},
            "topology": _NFR_TOPO,
            "steps": _NFR_STEPS,
            "requirements": {
                "latency": {"max_avg_ms": 100.0, "max_p95_ms": 900.0},
            },
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["requirements"]["latency"]["status"] == "FAILED"
    assert body["status"] == "FAILED"


def test_scenario_requirements_availability_fails(
    client: TestClient, monkeypatch,
) -> None:
    mock_stack(monkeypatch)
    monkeypatch.setenv("CLOUDNET_MOCK_PING_LOSS_RATE", "1")
    response = client.post(
        "/scenarios/run",
        json={
            "scenario": {"name": "nfr_avail"},
            "topology": _NFR_TOPO,
            "steps": _NFR_STEPS,
            "requirements": {
                "availability": {"min_success_rate": 0.95},
            },
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["requirements"]["availability"]["status"] == "FAILED"
    assert body["status"] == "FAILED"


def test_scenario_requirements_recovery_fails(client: TestClient, monkeypatch) -> None:
    mock_stack(monkeypatch)
    monkeypatch.setenv("CLOUDNET_MOCK_PING_LOSS_RATE", "0")
    response = client.post(
        "/scenarios/run",
        json={
            "scenario": {"name": "nfr_rec"},
            "topology": _NFR_TOPO,
            "steps": _NFR_STEPS,
            "requirements": {
                "recovery": {"max_recovery_seconds": 0},
            },
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["requirements"]["recovery"]["status"] == "FAILED"
    assert body["status"] == "FAILED"


def test_requirement_events_on_timeline(client: TestClient, monkeypatch) -> None:
    mock_stack(monkeypatch)
    monkeypatch.setenv("CLOUDNET_MOCK_PING_LOSS_RATE", "0")
    response = client.post(
        "/scenarios/run",
        json={
            "scenario": {"name": "nfr_events"},
            "topology": _NFR_TOPO,
            "steps": _NFR_STEPS,
            "requirements": {
                "availability": {"min_success_rate": 0.5},
            },
        },
    )
    assert response.status_code == 200
    tid = response.json()["topology_id"]
    ev = client.get(f"/topologies/{tid}/events")
    assert ev.status_code == 200
    types = [e["type"] for e in ev.json()["events"]]
    assert "REQUIREMENT_EVALUATED" in types
