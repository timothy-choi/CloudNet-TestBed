"""Pytest defaults for the CloudNet test suite."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_cloudnet_state_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test gets its own empty ``state.json`` so repo-local snapshots cannot leak."""
    monkeypatch.setenv("CLOUDNET_STATE_FILE", str(tmp_path / "cloudnet-state.json"))
