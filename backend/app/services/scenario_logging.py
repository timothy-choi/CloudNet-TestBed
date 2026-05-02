"""Structured JSON logs for scenario execution (use logger ``cloudnet.scenario``)."""

from __future__ import annotations

from typing import Any

from app.services.trace_logging import log_scenario_line

# Retain old name: logs to cloudnet.trace with action=event name
log_scenario_structured = log_scenario_line
