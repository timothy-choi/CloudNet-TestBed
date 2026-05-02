"""Tests for ``state.json`` local deployment snapshot."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.resource_types import PROVIDER_INSTANCE, PROVIDER_NETWORK, PROVIDER_SUBNET
from app.services.local_state_store import (
    ResourceHandle,
    clear_all_local_state,
    load_state,
    record_deploy_failed,
    record_deploy_snapshot,
    remove_local_deployment,
    resources_from_local_state,
)


@pytest.fixture
def isolated_state_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    p = tmp_path / "state.json"
    monkeypatch.setenv("CLOUDNET_STATE_FILE", str(p))
    yield p
    monkeypatch.delenv("CLOUDNET_STATE_FILE", raising=False)


def test_record_deploy_and_remove_roundtrip(
    isolated_state_file: Path,
) -> None:
    class FakeRow:
        def __init__(self, resource_type: str, resource_name: str, openstack_id: str):
            self.resource_type = resource_type
            self.resource_name = resource_name
            self.openstack_id = openstack_id

    rows = [
        FakeRow(PROVIDER_NETWORK, "net-a", "vpc-1"),
        FakeRow(PROVIDER_SUBNET, "sub-a", "subnet-1"),
        FakeRow(PROVIDER_INSTANCE, "host-a", "i-1"),
    ]
    record_deploy_snapshot(
        topology_id=7,
        scenario_run_id=99,
        resources=rows,  # type: ignore[arg-type]
        status="ACTIVE",
    )

    data = json.loads(isolated_state_file.read_text())
    dep = data["deployments"]["7"]
    assert dep["topology_id"] == 7
    assert dep["scenario_run_id"] == 99
    assert dep["status"] == "ACTIVE"
    assert dep["provider_resource_ids"]["vpc"] == ["vpc-1"]
    assert "subnet-1" in dep["provider_resource_ids"]["subnets"]
    assert dep["provider_resource_ids"]["instances"] == ["i-1"]

    handles = resources_from_local_state(7)
    assert len(handles) == 3
    assert isinstance(handles[0], ResourceHandle)

    remove_local_deployment(7)
    assert load_state()["deployments"] == {}


def test_record_deploy_failed(isolated_state_file: Path) -> None:
    record_deploy_failed(topology_id=3, scenario_run_id=None)
    dep = load_state()["deployments"]["3"]
    assert dep["status"] == "FAILED"
    assert dep["resources"] == []


def test_clear_all(isolated_state_file: Path) -> None:
    record_deploy_failed(topology_id=1, scenario_run_id=None)
    clear_all_local_state()
    assert load_state()["deployments"] == {}
