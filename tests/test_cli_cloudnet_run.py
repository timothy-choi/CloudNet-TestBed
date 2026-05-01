"""CLI scenario exit codes (0 pass, 1 fail)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from cli.cloudnet import cmd_run


_MIN_SCENARIO = """scenario:
  name: cli_probe
topology:
  name: cli-probe-topo
  nodes:
    - name: solo
      type: host
  links:
    - from: solo
      to: solo
      subnet: 10.55.1.0/24
  firewall_rules: []
steps: []
"""


@pytest.fixture
def scenario_file(tmp_path: Path) -> Path:
    p = tmp_path / "scenario.yaml"
    p.write_text(_MIN_SCENARIO)
    return p


def test_cmd_run_returns_zero_on_pass(scenario_file: Path) -> None:
    client = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "status": "PASSED",
        "steps": [],
        "duration_ms": 0,
        "topology_id": 1,
    }
    client.post.return_value = resp
    args = argparse.Namespace(file=str(scenario_file), json=False)
    assert cmd_run(client, args) == 0


def test_cmd_run_returns_one_on_failed_scenario(scenario_file: Path) -> None:
    client = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"status": "FAILED", "steps": [], "duration_ms": 1}
    client.post.return_value = resp
    args = argparse.Namespace(file=str(scenario_file), json=False)
    assert cmd_run(client, args) == 1


def test_cmd_run_returns_one_on_http_error(scenario_file: Path) -> None:
    client = MagicMock()
    resp = MagicMock()
    resp.status_code = 400
    resp.text = "bad"
    client.post.return_value = resp
    args = argparse.Namespace(file=str(scenario_file), json=False)
    assert cmd_run(client, args) == 1
