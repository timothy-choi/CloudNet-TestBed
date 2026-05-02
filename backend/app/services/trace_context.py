"""Optional ambient trace fields for correlating logs and events with scenario runs."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Generator

scenario_run_id_ctx: ContextVar[int | None] = ContextVar(
    "scenario_run_id", default=None
)
topology_id_ctx: ContextVar[int | None] = ContextVar("topology_id", default=None)
provider_ctx: ContextVar[str | None] = ContextVar("provider", default=None)


def current_trace_metadata() -> dict[str, Any]:
    """Fields suitable for merging into event metadata and structured logs."""
    out: dict[str, Any] = {}
    sid = scenario_run_id_ctx.get()
    tid = topology_id_ctx.get()
    prov = provider_ctx.get()
    if sid is not None:
        out["scenario_run_id"] = sid
        out["scenario_run_ref"] = f"run-{sid}"
    if tid is not None:
        out["topology_id"] = tid
    if prov:
        out["provider"] = prov
    return out


@contextmanager
def bind_scenario_trace(
    *,
    scenario_run_id: int | None,
    topology_id: int | None,
    provider: str | None,
) -> Generator[None, None, None]:
    """Bind scenario correlation IDs for the duration of a scenario execution."""
    tokens: list[tuple[ContextVar[Any], Any]] = []
    try:
        if scenario_run_id is not None:
            tokens.append((scenario_run_id_ctx, scenario_run_id_ctx.set(scenario_run_id)))
        if topology_id is not None:
            tokens.append((topology_id_ctx, topology_id_ctx.set(topology_id)))
        if provider is not None:
            tokens.append((provider_ctx, provider_ctx.set(provider)))
        yield
    finally:
        for var, tok in reversed(tokens):
            var.reset(tok)


def reset_trace() -> None:
    scenario_run_id_ctx.set(None)
    topology_id_ctx.set(None)
    provider_ctx.set(None)
