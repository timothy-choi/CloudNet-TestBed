"""Parse ICMP ping output and compute latency statistics."""

from __future__ import annotations

import math
import re
from typing import Any


_TIME_MS_RE = re.compile(r"time[=<]([\d.]+)\s*ms", re.IGNORECASE)
_RTT_TRIPLE_RE = re.compile(
    r"(?:round-trip|rtt)[^\n]*=\s*([\d.]+)/([\d.]+)/([\d.]+)",
    re.IGNORECASE,
)


def extract_icmp_latencies_ms(output: str) -> list[float]:
    """Extract per-reply latencies from ping stdout (Linux / mock)."""
    if not output or not output.strip():
        return []
    times = [float(m.group(1)) for m in _TIME_MS_RE.finditer(output)]
    if times:
        return times
    m = _RTT_TRIPLE_RE.search(output)
    if m:
        return [float(m.group(2))]
    return []


def mean_latency_ms(latencies: list[float]) -> float | None:
    if not latencies:
        return None
    return sum(latencies) / len(latencies)


def p95_latency_ms(latencies: list[float]) -> float | None:
    if not latencies:
        return None
    s = sorted(latencies)
    idx = int(math.ceil(0.95 * len(s))) - 1
    idx = max(0, min(len(s) - 1, idx))
    return s[idx]


def summarize_validate_metrics(reply_latencies_ms: list[float]) -> dict[str, Any]:
    return {
        "avg_latency_ms": mean_latency_ms(reply_latencies_ms),
        "p95_latency_ms": p95_latency_ms(reply_latencies_ms),
    }
