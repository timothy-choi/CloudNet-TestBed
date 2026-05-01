"""Evaluate optional scenario NFR thresholds against observed metrics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ScenarioRequirementsSpec:
    min_success_rate: float | None = None
    max_avg_ms: float | None = None
    max_p95_ms: float | None = None
    max_recovery_seconds: float | None = None


def parse_requirements_dict(raw: dict[str, Any] | None) -> ScenarioRequirementsSpec | None:
    if not raw:
        return None
    avail = raw.get("availability") or {}
    lat = raw.get("latency") or {}
    rec = raw.get("recovery") or {}
    spec = ScenarioRequirementsSpec(
        min_success_rate=avail.get("min_success_rate"),
        max_avg_ms=lat.get("max_avg_ms"),
        max_p95_ms=lat.get("max_p95_ms"),
        max_recovery_seconds=rec.get("max_recovery_seconds"),
    )
    if (
        spec.min_success_rate is None
        and spec.max_avg_ms is None
        and spec.max_p95_ms is None
        and spec.max_recovery_seconds is None
    ):
        return None
    return spec


def evaluate_requirements(
    spec: ScenarioRequirementsSpec | None,
    *,
    tests_total: int,
    tests_passed: int,
    avg_latency_ms: float | None,
    p95_latency_ms: float | None,
    recovery_seconds: float | None,
) -> tuple[dict[str, Any] | None, bool]:
    """Return API-shaped ``requirements`` mapping and whether all gates passed."""
    if spec is None:
        return None, True

    requirements: dict[str, Any] = {}
    all_ok = True

    if spec.min_success_rate is not None:
        if tests_total <= 0:
            success_rate = 1.0
        else:
            success_rate = tests_passed / tests_total
        ok = success_rate >= spec.min_success_rate
        requirements["availability"] = {
            "status": "PASSED" if ok else "FAILED",
            "success_rate": round(success_rate, 6),
            "threshold": spec.min_success_rate,
        }
        if not ok:
            all_ok = False

    if spec.max_avg_ms is not None or spec.max_p95_ms is not None:
        lat_block: dict[str, Any] = {"status": "PASSED"}
        if avg_latency_ms is not None:
            lat_block["avg_ms"] = round(float(avg_latency_ms), 2)
        if p95_latency_ms is not None:
            lat_block["p95_ms"] = round(float(p95_latency_ms), 2)
        ok = True
        if spec.max_avg_ms is not None:
            lat_block["max_avg_ms"] = spec.max_avg_ms
            if avg_latency_ms is None or avg_latency_ms > spec.max_avg_ms:
                ok = False
        if spec.max_p95_ms is not None:
            lat_block["max_p95_ms"] = spec.max_p95_ms
            if p95_latency_ms is None or p95_latency_ms > spec.max_p95_ms:
                ok = False
        lat_block["status"] = "PASSED" if ok else "FAILED"
        requirements["latency"] = lat_block
        if not ok:
            all_ok = False

    if spec.max_recovery_seconds is not None:
        if recovery_seconds is None:
            ok = False
            block = {
                "status": "FAILED",
                "recovery_seconds": None,
                "max_recovery_seconds": spec.max_recovery_seconds,
            }
        else:
            ok = recovery_seconds <= spec.max_recovery_seconds
            block = {
                "status": "PASSED" if ok else "FAILED",
                "recovery_seconds": round(recovery_seconds, 3),
                "max_recovery_seconds": spec.max_recovery_seconds,
            }
        requirements["recovery"] = block
        if not ok:
            all_ok = False

    return requirements if requirements else None, all_ok
