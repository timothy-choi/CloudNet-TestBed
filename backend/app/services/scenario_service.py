"""Execute declarative test scenarios against deployed topologies."""

import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import selectinload
from sqlmodel import Session, select

from app.models import (
    FirewallRule,
    Link,
    Node,
    ScenarioRun,
    ScenarioStepResult,
    Topology,
    utc_now,
)
from app.schemas import TopologyInput
from app.services.connectivity_service import ConnectivityTestError, validate_topology_links
from app.services.control_plane_service import ControlPlaneError, reconcile_topology
from app.services.deployment_service import (
    DeploymentAlreadyExistsError,
    DeploymentError,
    deploy_topology,
)
from app.services.drift_service import DriftError, detect_topology_drift
from app.services.event_service import emit_event
from app.services.failure_service import FailureError, inject_node_down
from app.topology_compiler import compile_topology


class ScenarioError(Exception):
    pass


@dataclass(frozen=True)
class _ValidateStep:
    expect: str  # "pass" | "fail"


@dataclass(frozen=True)
class _FailStep:
    node: str


@dataclass(frozen=True)
class _DriftStep:
    expect: str  # "detected" | "clean"


@dataclass(frozen=True)
class _ReconcileStep:
    pass


def parse_scenario_steps(raw_steps: list[Any]) -> list[Any]:
    out: list[Any] = []
    for index, raw in enumerate(raw_steps):
        if not isinstance(raw, dict) or len(raw) != 1:
            raise ScenarioError(
                f"steps[{index}] must be a mapping with exactly one action key"
            )
        key = next(iter(raw))
        val = raw[key]
        if key == "validate":
            if val == "all":
                out.append(_ValidateStep(expect="pass"))
            elif isinstance(val, dict):
                exp = val.get("expect", "pass")
                if exp not in ("pass", "fail"):
                    raise ScenarioError(
                        f"steps[{index}] validate.expect must be 'pass' or 'fail'"
                    )
                out.append(_ValidateStep(expect=exp))
            else:
                raise ScenarioError(
                    f"steps[{index}] validate must be 'all' or an object with expect"
                )
        elif key == "fail":
            if not isinstance(val, dict) or "node" not in val:
                raise ScenarioError(f"steps[{index}] fail must include node")
            out.append(_FailStep(node=str(val["node"])))
        elif key == "drift":
            if not isinstance(val, dict):
                raise ScenarioError(f"steps[{index}] drift must be an object")
            exp = val.get("expect")
            if exp not in ("detected", "clean"):
                raise ScenarioError(
                    f"steps[{index}] drift.expect must be 'detected' or 'clean'"
                )
            out.append(_DriftStep(expect=str(exp)))
        elif key == "reconcile":
            if val is True:
                out.append(_ReconcileStep())
            else:
                raise ScenarioError(f"steps[{index}] reconcile must be true")
        else:
            raise ScenarioError(f"unsupported step key {key!r}")
    return out


def _persist_topology(session: Session, topology_input: TopologyInput) -> Topology:
    topology_data = topology_input.model_dump(by_alias=True)
    try:
        compile_topology(topology_data)
    except ValueError as exc:
        raise ScenarioError(str(exc)) from exc

    topology = Topology(name=topology_input.name)
    session.add(topology)
    session.flush()

    for node in topology_data["nodes"]:
        session.add(
            Node(
                topology_id=topology.id,
                name=node["name"],
                type=node["type"],
            )
        )

    for link in topology_data["links"]:
        session.add(
            Link(
                topology_id=topology.id,
                from_node=link["from"],
                to_node=link["to"],
                subnet=link["subnet"],
            )
        )

    for rule in topology_data["firewall_rules"]:
        session.add(
            FirewallRule(
                topology_id=topology.id,
                name=rule["name"],
                protocol=rule["protocol"],
                port=rule.get("port"),
                from_node=rule["from"],
                to_node=rule["to"],
            )
        )

    session.commit()
    return _load_topology(session, topology.id)


def _load_topology(session: Session, topology_id: int) -> Topology:
    stmt = (
        select(Topology)
        .where(Topology.id == topology_id)
        .options(
            selectinload(Topology.nodes),
            selectinload(Topology.links),
            selectinload(Topology.firewall_rules),
        )
    )
    loaded = session.exec(stmt).first()
    if loaded is None:
        raise ScenarioError("topology not found after create")
    return loaded


def _step_matches_validate(actual: str, expect: str) -> bool:
    want = "PASSED" if expect == "pass" else "FAILED"
    return actual == want


def _validate_expected_label(expect: str) -> str:
    return "PASSED" if expect == "pass" else "FAILED"


def _elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _step_record(
    *,
    name: str,
    action: str,
    expected: str | None,
    actual: str | None,
    step_passed: bool,
    duration_ms: int,
    message: str | None = None,
    provider_action: str | None = None,
) -> dict[str, Any]:
    rec: dict[str, Any] = {
        "name": name,
        "action": action,
        "expected": expected,
        "actual": actual,
        "status": "PASSED" if step_passed else "FAILED",
        "duration_ms": duration_ms,
        "message": message,
    }
    if provider_action is not None:
        rec["provider_action"] = provider_action
    return rec


def _persist_scenario_report(
    session: Session,
    *,
    topology_id: int,
    scenario_name: str,
    overall_status: str,
    started_at: Any,
    finished_at: Any,
    duration_ms: int,
    step_dicts: list[dict[str, Any]],
) -> ScenarioRun:
    run = ScenarioRun(
        topology_id=topology_id,
        scenario_name=scenario_name,
        status=overall_status,
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=duration_ms,
    )
    session.add(run)
    session.flush()
    for i, s in enumerate(step_dicts):
        session.add(
            ScenarioStepResult(
                scenario_run_id=run.id,
                step_index=i,
                name=s["name"],
                action=s["action"],
                expected=s.get("expected"),
                actual=s.get("actual"),
                status=s["status"],
                duration_ms=s["duration_ms"],
                message=s.get("message"),
                provider_action=s.get("provider_action"),
            )
        )
    session.commit()
    session.refresh(run)
    emit_event(
        session,
        topology_id,
        "SCENARIO_RUN",
        overall_status,
        f"Scenario {scenario_name} finished with status {overall_status}",
        {
            "scenario_run_id": run.id,
            "scenario_name": scenario_name,
            "duration_ms": duration_ms,
        },
    )
    return run


def scenario_result_response(
    *,
    scenario_name: str,
    scenario_run_id: int,
    topology_id: int,
    topology_name: str,
    overall_status: str,
    started_at: Any,
    finished_at: Any,
    duration_ms: int,
    steps: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "scenario": scenario_name,
        "scenario_run_id": scenario_run_id,
        "status": overall_status,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_ms": duration_ms,
        "topology_id": topology_id,
        "topology_name": topology_name,
        "steps": steps,
    }


def get_scenario_run_results(session: Session, scenario_run_id: int) -> dict[str, Any] | None:
    stmt = (
        select(ScenarioRun)
        .where(ScenarioRun.id == scenario_run_id)
        .options(selectinload(ScenarioRun.steps))
    )
    run = session.exec(stmt).first()
    if run is None:
        return None
    topo = session.get(Topology, run.topology_id)
    topology_name = topo.name if topo else ""
    ordered = sorted(run.steps, key=lambda s: s.step_index)
    steps_out = [_step_row_to_api(s) for s in ordered]
    return scenario_result_response(
        scenario_name=run.scenario_name,
        scenario_run_id=run.id,
        topology_id=run.topology_id,
        topology_name=topology_name,
        overall_status=run.status,
        started_at=run.started_at,
        finished_at=run.finished_at,
        duration_ms=run.duration_ms,
        steps=steps_out,
    )


def _step_row_to_api(row: ScenarioStepResult) -> dict[str, Any]:
    d: dict[str, Any] = {
        "name": row.name,
        "action": row.action,
        "expected": row.expected,
        "actual": row.actual,
        "status": row.status,
        "duration_ms": row.duration_ms,
        "message": row.message,
    }
    if row.provider_action:
        d["provider_action"] = row.provider_action
    return d


class ScenarioRunner:
    """Thin orchestration over deploy, validate, failure injection, drift, reconcile."""

    def __init__(self, session: Session):
        self._session = session

    def run(
        self,
        *,
        scenario_name: str,
        topology_input: TopologyInput,
        raw_steps: list[Any],
    ) -> dict[str, Any]:
        session = self._session
        started_at = utc_now()
        t_run = time.perf_counter()

        parsed = parse_scenario_steps(raw_steps)
        topology = _persist_topology(session, topology_input)

        try:
            deploy_topology(session, topology)
        except DeploymentAlreadyExistsError as exc:
            raise ScenarioError(str(exc)) from exc
        except DeploymentError as exc:
            raise ScenarioError(str(exc)) from exc

        topology = _load_topology(session, topology.id)
        topology_name = topology.name
        topology_id = topology.id

        step_records: list[dict[str, Any]] = []
        overall_ok = True

        for step in parsed:
            t_step = time.perf_counter()

            if isinstance(step, _ValidateStep):
                exp_label = _validate_expected_label(step.expect)
                try:
                    response = validate_topology_links(session=session, topology=topology)
                except ConnectivityTestError as exc:
                    actual = "FAILED"
                    ok = _step_matches_validate(actual, step.expect)
                    step_records.append(
                        _step_record(
                            name="validate",
                            action="validate",
                            expected=exp_label,
                            actual=actual,
                            step_passed=ok,
                            duration_ms=_elapsed_ms(t_step),
                            message=str(exc),
                        )
                    )
                    if not ok:
                        overall_ok = False
                    continue

                actual = str(response["status"])
                ok = _step_matches_validate(actual, step.expect)
                step_records.append(
                    _step_record(
                        name="validate",
                        action="validate",
                        expected=exp_label,
                        actual=actual,
                        step_passed=ok,
                        duration_ms=_elapsed_ms(t_step),
                    )
                )
                if not ok:
                    overall_ok = False

            elif isinstance(step, _FailStep):
                name = f"fail {step.node}"
                try:
                    inject_node_down(
                        session=session,
                        topology=topology,
                        node_name=step.node,
                    )
                    actual = "SUCCESS"
                    ok = True
                    msg = None
                except FailureError as exc:
                    actual = "FAILED"
                    ok = False
                    msg = str(exc)

                step_records.append(
                    _step_record(
                        name=name,
                        action="fail",
                        expected="SUCCESS",
                        actual=actual,
                        step_passed=ok,
                        duration_ms=_elapsed_ms(t_step),
                        message=msg,
                        provider_action="stop_server",
                    )
                )
                if not ok:
                    overall_ok = False

            elif isinstance(step, _DriftStep):
                exp_drift = "DETECTED" if step.expect == "detected" else "CLEAN"
                try:
                    drift = detect_topology_drift(session=session, topology=topology)
                    detected = bool(drift.get("drift_detected"))
                except DriftError as exc:
                    step_records.append(
                        _step_record(
                            name="drift",
                            action="drift",
                            expected=exp_drift,
                            actual=None,
                            step_passed=False,
                            duration_ms=_elapsed_ms(t_step),
                            message=str(exc),
                        )
                    )
                    overall_ok = False
                    continue

                obs = "DETECTED" if detected else "CLEAN"
                ok = obs == exp_drift
                step_records.append(
                    _step_record(
                        name="drift",
                        action="drift",
                        expected=exp_drift,
                        actual=obs,
                        step_passed=ok,
                        duration_ms=_elapsed_ms(t_step),
                        message=None
                        if ok
                        else (
                            f"expected {exp_drift}, observed provider state indicates {obs}"
                        ),
                    )
                )
                if not ok:
                    overall_ok = False

            elif isinstance(step, _ReconcileStep):
                try:
                    response = reconcile_topology(session=session, topology=topology)
                    actual = str(response["status"])
                except ControlPlaneError as exc:
                    step_records.append(
                        _step_record(
                            name="reconcile",
                            action="reconcile",
                            expected="RECONCILED",
                            actual=None,
                            step_passed=False,
                            duration_ms=_elapsed_ms(t_step),
                            message=str(exc),
                        )
                    )
                    overall_ok = False
                    continue

                ok = actual == "RECONCILED"
                step_records.append(
                    _step_record(
                        name="reconcile",
                        action="reconcile",
                        expected="RECONCILED",
                        actual=actual,
                        step_passed=ok,
                        duration_ms=_elapsed_ms(t_step),
                    )
                )
                if not ok:
                    overall_ok = False

        finished_at = utc_now()
        duration_ms = int((time.perf_counter() - t_run) * 1000)
        overall_status = "PASSED" if overall_ok else "FAILED"

        saved = _persist_scenario_report(
            session,
            topology_id=topology_id,
            scenario_name=scenario_name,
            overall_status=overall_status,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            step_dicts=step_records,
        )

        return scenario_result_response(
            scenario_name=scenario_name,
            scenario_run_id=saved.id,
            topology_id=topology_id,
            topology_name=topology_name,
            overall_status=overall_status,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            steps=step_records,
        )


def run_scenario(
    session: Session,
    *,
    scenario_name: str,
    topology_input: TopologyInput,
    raw_steps: list[Any],
) -> dict[str, Any]:
    return ScenarioRunner(session).run(
        scenario_name=scenario_name,
        topology_input=topology_input,
        raw_steps=raw_steps,
    )
