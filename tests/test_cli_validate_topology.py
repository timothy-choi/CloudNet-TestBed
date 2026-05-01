"""CLI validate-topology subcommand."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from cli.cloudnet import cmd_validate_topology


_TWO_NODE = """name: cli-validate-test
nodes:
  - name: a
    type: host
  - name: b
    type: host
links:
  - from: a
    to: b
    subnet: 10.200.1.0/24
firewall_rules: []
"""


def test_validate_topology_cli_ok(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    p = tmp_path / "topo.yaml"
    p.write_text(_TWO_NODE)
    rc = cmd_validate_topology(MagicMock(), argparse.Namespace(file=str(p)))
    assert rc == 0
    out = capsys.readouterr().out
    assert '"ok": true' in out
    assert "subnet_count" in out


def test_validate_topology_cli_bad_yaml(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text("{ not yaml")
    rc = cmd_validate_topology(MagicMock(), argparse.Namespace(file=str(p)))
    assert rc == 1
    assert "invalid YAML" in capsys.readouterr().err
