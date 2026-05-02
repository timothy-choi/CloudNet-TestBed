"""Built-in scenario templates CLI."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from cli.cloudnet import cmd_templates_list, cmd_templates_run


def test_templates_list_names_include_builtin(capsys: pytest.CaptureFixture[str]) -> None:
    rc = cmd_templates_list(MagicMock(), argparse.Namespace())
    assert rc == 0
    out = capsys.readouterr().out
    assert "backend-failure" in out
    assert "simple-connectivity" in out
    assert "latency-test" in out


def test_templates_run_unknown_returns_one(capsys: pytest.CaptureFixture[str]) -> None:
    rc = cmd_templates_run(
        MagicMock(),
        argparse.Namespace(template="does-not-exist", json=False, cleanup=False),
    )
    assert rc == 1
    assert "unknown template" in capsys.readouterr().err


def test_templates_run_delegates_to_scenario_post() -> None:
    client = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "scenario": "backend_failure_test",
        "status": "PASSED",
        "steps": [],
        "duration_ms": 0,
        "topology_id": 1,
    }
    client.post.return_value = resp
    rc = cmd_templates_run(
        client,
        argparse.Namespace(template="simple-connectivity", json=False, cleanup=False),
    )
    assert rc == 0
    assert client.post.call_count == 1
    args, kwargs = client.post.call_args
    assert args[0] == "/scenarios/run"
    body = kwargs.get("json") or (args[1] if len(args) > 1 else None)
    assert body is not None
    assert body["scenario"]["name"] == "simple_connectivity_test"
