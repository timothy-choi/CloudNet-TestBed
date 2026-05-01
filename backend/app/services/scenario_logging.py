"""Structured JSON logs for scenario execution (use logger ``cloudnet.scenario``)."""

from __future__ import annotations

import json
import logging
from typing import Any

_logger = logging.getLogger("cloudnet.scenario")


def log_scenario_structured(event: str, **fields: Any) -> None:
    payload: dict[str, Any] = {"event": event, **fields}
    _logger.info(json.dumps(payload, default=str))
