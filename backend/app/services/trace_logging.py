"""Structured JSON logs for control-plane actions (logger ``cloudnet.trace``)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.services.trace_context import current_trace_metadata

_logger = logging.getLogger("cloudnet.trace")

_LEVEL = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "WARN": logging.WARNING,
    "ERROR": logging.ERROR,
}


def log_trace(
    level: str,
    action: str,
    *,
    status: str,
    message: str = "",
    resource_type: str | None = None,
    resource_id: str | None = None,
    **extra: Any,
) -> None:
    """Emit one JSON object per line with correlation fields when bound."""
    payload: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "level": level.upper(),
        "action": action,
        "status": status,
        "message": message,
    }
    if resource_type is not None:
        payload["resource_type"] = resource_type
    if resource_id is not None:
        payload["resource_id"] = resource_id
    payload.update(current_trace_metadata())
    for k, v in extra.items():
        if v is not None:
            payload[k] = v
    msg = json.dumps(payload, default=str)
    _logger.log(_LEVEL.get(level.upper(), logging.INFO), msg)


def log_scenario_line(event: str, **fields: Any) -> None:
    """Backward-compatible wrapper: maps legacy ``event`` to ``action``."""
    fields = dict(fields)
    status = str(fields.pop("status", "INFO"))
    message = str(fields.pop("message", ""))
    if "action" in fields:
        fields["step_action"] = fields.pop("action")
    log_trace("INFO", event, status=status, message=message, **fields)
