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
    cleanup_topology_deployment,
    deploy_topology,
)
from app.services.drift_service import DriftError, detect_topology_drift
from app.services.event_service import emit_event
from app.services.failure_service import FailureError, inject_node_down
from app.services.ping_metrics import mean_latency_ms, p95_latency_ms
from app.services.requirements_evaluation import (
    ScenarioRequirementsSpec,
    evaluate_requirements,
    parse_requirements_dict,
)
from app.topology_compiler import compile_topology

from app.core.config import get_scenario_quota_settings
from app.providers.factory import get_provider
from app.services.scenario_logging import log_scenario_structured
from app.services.scenario_quotas import validate_scenario_topology_quotas


class ScenarioError(Exception):
    pass


@dataclass(frozen=True)
class _DeployStep:
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


@dataclass(frozen=True)
class _CleanupStep:
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
        if key == "deploy":
            if val is True:
                out.append(_DeployStep())
            else:
                raise ScenarioError(f"steps[{index}] deploy must be true")
        elif key == "validate":
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
            if exp == "none":
                exp = "clean"
            if exp not in ("detected", "clean"):
                raise ScenarioError(
                    f"steps[{index}] drift.expect must be 'detected', 'clean', or 'none'"
                )
            out.append(_DriftStep(expect=str(exp)))
        elif key == "cleanup":
            if val is True:
                out.append(_CleanupStep())
            else:
                raise ScenarioError(f"steps[{index}] cleanup must be true")
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


def _attempt_scenario_cleanup(session: Session, topology: Topology) -> None:
    try:
        cleanup_topology_deployment(session, topology)
    except DeploymentError:
        pass


def _create_scenario_run_placeholder(
    session: Session,
    *,
    topology_id: int,
    scenario_name: str,
    started_at: Any,
) -> ScenarioRun:
    run = ScenarioRun(
        topology_id=topology_id,
        scenario_name=scenario_name,
        status="RUNNING",
        started_at=started_at,
        finished_at=started_at,
        duration_ms=0,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _finalize_scenario_run(
    session: Session,
    run: ScenarioRun,
    *,
    finished_at: Any,
    duration_ms: int,
    overall_status: str,
    step_dicts: list[dict[str, Any]],
    scenario_name: str,
) -> ScenarioRun:
    run.status = overall_status
    run.finished_at = finished_at
    run.duration_ms = duration_ms
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
        run.topology_id,
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
    requirements: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "scenario": scenario_name,
        "scenario_run_id": scenario_run_id,
        "status": overall_status,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_ms": duration_ms,
        "topology_id": topology_id,
        "topology_name": topology_name,
        "event_timeline_url": f"/topologies/{topology_id}/events",
        "steps": steps,
    }
    if requirements is not None:
        body["requirements"] = requirements
    return body


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
        requirements_spec: ScenarioRequirementsSpec | None = None,
        cleanup_on_failure: bool = False,
        cleanup_after_run: bool = False,
    ) -> dict[str, Any]:
        session = self._session
        validate_scenario_topology_quotas(topology_input)
        started_at = utc_now()
        t_run = time.perf_counter()

        agg_tests_total = 0
        agg_tests_passed = 0
        agg_latencies: list[float] = []
        failure_perf: float | None = None
        reconcile_after_failure = False
        recovery_seconds: float | None = None

        parsed = parse_scenario_steps(raw_steps)
        has_explicit_deploy = any(isinstance(s, _DeployStep) for s in parsed)
        topology = _persist_topology(session, topology_input)
        topology_id = topology.id
        assert topology_id is not None

        run = _create_scenario_run_placeholder(
            session,
            topology_id=topology_id,
            scenario_name=scenario_name,
            started_at=started_at,
        )
        scenario_run_id = run.id
        provider_name = get_provider().name

        topology = _load_topology(session, topology_id)
        topology_name = topology.name

        quota_settings = get_scenario_quota_settings()

        step_records: list[dict[str, Any]] = []
        overall_ok = True
        abort_scenario = False

        def push_step(rec: dict[str, Any]) -> None:
            step_records.append(rec)
            log_scenario_structured(
                "scenario_step",
                scenario_run_id=scenario_run_id,
                topology_id=topology_id,
                provider=provider_name,
                action=rec.get("action"),
                status=rec.get("status"),
                name=rec.get("name"),
                step_index=len(step_records) - 1,
            )

        if not has_explicit_deploy:
            t_impl = time.perf_counter()
            if time.perf_counter() - t_run > quota_settings.max_duration_seconds:
                rec = _step_record(
                    name="duration_quota",
                    action="quota",
                    expected="within_limit",
                    actual="FAILED",
                    step_passed=False,
                    duration_ms=_elapsed_ms(t_impl),
                    message=(
                        "scenario duration quota exceeded before implicit deploy "
                        f"(CLOUDNET_MAX_SCENARIO_DURATION_SECONDS="
                        f"{quota_settings.max_duration_seconds})"
                    ),
                )
                push_step(rec)
                overall_ok = False
                abort_scenario = True
            else:
                try:
                    deploy_topology(session, topology, scenario_run_id=scenario_run_id)
                except DeploymentAlreadyExistsError as exc:
                    session.delete(run)
                    session.commit()
                    raise ScenarioError(str(exc)) from exc
                except DeploymentError as exc:
                    rec = _step_record(
                        name="deploy",
                        action="deploy",
                        expected="ACTIVE",
                        actual="FAILED",
                        step_passed=False,
                        duration_ms=_elapsed_ms(t_impl),
                        message=str(exc),
                    )
                    push_step(rec)
                    overall_ok = False
                    abort_scenario = True
                    if cleanup_on_failure:
                        _attempt_scenario_cleanup(session, topology)
                else:
                    topology = _load_topology(session, topology_id)

        for step in parsed:
            if abort_scenario:
                break
            if time.perf_counter() - t_run > quota_settings.max_duration_seconds:
                t_step = time.perf_counter()
                rec = _step_record(
                    name="duration_quota",
                    action="quota",
                    expected="within_limit",
                    actual="FAILED",
                    step_passed=False,
                    duration_ms=_elapsed_ms(t_step),
                    message=(
                        f"scenario exceeded CLOUDNET_MAX_SCENARIO_DURATION_SECONDS="
                        f"{quota_settings.max_duration_seconds}"
                    ),
                )
                push_step(rec)
                overall_ok = False
                abort_scenario = True
                break
            t_step = time.perf_counter()

            if isinstance(step, _DeployStep):
                try:
                    dep = deploy_topology(session, topology, scenario_run_id=scenario_run_id)
                    actual = str(dep.get("status", ""))
                    ok = actual == "ACTIVE"
                    msg = None
                except DeploymentAlreadyExistsError as exc:
                    actual = "FAILED"
                    ok = False
                    msg = str(exc)
                except DeploymentError as exc:
                    actual = "FAILED"
                    ok = False
                    msg = str(exc)

                topology = _load_topology(session, topology.id)
                push_step(
                    _step_record(
                        name="deploy",
                        action="deploy",
                        expected="ACTIVE",
                        actual=actual,
                        step_passed=ok,
                        duration_ms=_elapsed_ms(t_step),
                        message=msg,
                    )
                )
                if not ok:
                    overall_ok = False
                    if cleanup_on_failure:
                        _attempt_scenario_cleanup(session, topology)
                    abort_scenario = True

            elif isinstance(step, _ValidateStep):
                exp_label = _validate_expected_label(step.expect)
                try:
                    response = validate_topology_links(session=session, topology=topology)
                except ConnectivityTestError as exc:
                    actual = "FAILED"
                    ok = _step_matches_validate(actual, step.expect)
                    push_step(
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

                metrics = response.get("metrics") or {}
                agg_tests_total += int(metrics.get("tests_total") or 0)
                agg_tests_passed += int(metrics.get("tests_passed") or 0)
                agg_latencies.extend(metrics.get("reply_latencies_ms") or [])

                actual = str(response["status"])
                ok = _step_matches_validate(actual, step.expect)
                push_step(
                    _step_record(
                        name="validate",
                        action="validate",
                        expected=exp_label,
                        actual=actual,
                        step_passed=ok,
                        duration_ms=_elapsed_ms(t_step),
                    )
                )
                if (
                    actual == "PASSED"
                    and step.expect == "pass"
                    and failure_perf is not None
                    and reconcile_after_failure
                    and recovery_seconds is None
                ):
                    recovery_seconds = time.perf_counter() - failure_perf
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

                push_step(
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
                if ok:
                    failure_perf = time.perf_counter()
                if not ok:
                    overall_ok = False

            elif isinstance(step, _DriftStep):
                exp_drift = "DETECTED" if step.expect == "detected" else "CLEAN"
                try:
                    drift = detect_topology_drift(session=session, topology=topology)
                    detected = bool(drift.get("drift_detected"))
                except DriftError as exc:
                    push_step(
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
                push_step(
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
                    push_step(
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
                push_step(
                    _step_record(
                        name="reconcile",
                        action="reconcile",
                        expected="RECONCILED",
                        actual=actual,
                        step_passed=ok,
                        duration_ms=_elapsed_ms(t_step),
                    )
                )
                if ok and failure_perf is not None:
                    reconcile_after_failure = True
                if not ok:
                    overall_ok = False

            elif isinstance(step, _CleanupStep):
                try:
                    result = cleanup_topology_deployment(session, topology)
                    actual = str(result.get("status", ""))
                    ok = actual in ("CLEANED", "SKIPPED")
                    msg = result.get("detail") if actual == "SKIPPED" else None
                except DeploymentError as exc:
                    actual = "FAILED"
                    ok = False
                    msg = str(exc)

                topology = _load_topology(session, topology.id)
                push_step(
                    _step_record(
                        name="cleanup",
                        action="cleanup",
                        expected="CLEANED",
                        actual=actual,
                        step_passed=ok,
                        duration_ms=_elapsed_ms(t_step),
                        message=msg,
                    )
                )
                if not ok:
                    overall_ok = False

        avg_latency_ms = mean_latency_ms(agg_latencies) if agg_latencies else None
        p95_latency_ms_computed = p95_latency_ms(agg_latencies) if agg_latencies else None

        req_report, req_ok = evaluate_requirements(
            requirements_spec,
            tests_total=agg_tests_total,
            tests_passed=agg_tests_passed,
            avg_latency_ms=avg_latency_ms,
            p95_latency_ms=p95_latency_ms_computed,
            recovery_seconds=recovery_seconds,
        )
        if req_report is not None:
            overall_ok = overall_ok and req_ok

        finished_at = utc_now()
        duration_ms = int((time.perf_counter() - t_run) * 1000)
        overall_status = "PASSED" if overall_ok else "FAILED"

        saved = _finalize_scenario_run(
            session,
            run,
            finished_at=finished_at,
            duration_ms=duration_ms,
            overall_status=overall_status,
            step_dicts=step_records,
            scenario_name=scenario_name,
        )

        log_scenario_structured(
            "scenario_completed",
            scenario_run_id=saved.id,
            topology_id=topology_id,
            provider=provider_name,
            status=overall_status,
            duration_ms=duration_ms,
        )

        if cleanup_after_run or (overall_status == "FAILED" and cleanup_on_failure):
            _attempt_scenario_cleanup(session, topology)

        if req_report:
            for category, payload in req_report.items():
                status = str(payload.get("status") or "")
                emit_event(
                    session,
                    topology_id,
                    "REQUIREMENT_EVALUATED",
                    status,
                    f"Requirement {category}: {status}",
                    {
                        "category": category,
                        "scenario_run_id": saved.id,
                        "detail": payload,
                    },
                )
                if status != "PASSED":
                    emit_event(
                        session,
                        topology_id,
                        "REQUIREMENT_FAILED",
                        "FAILED",
                        f"Requirement {category} not met",
                        {
                            "category": category,
                            "scenario_run_id": saved.id,
                            "detail": payload,
                        },
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
            requirements=req_report,
        )


def run_scenario(
    session: Session,
    *,
    scenario_name: str,
    topology_input: TopologyInput,
    raw_steps: list[Any],
    requirements: dict[str, Any] | None = None,
    cleanup_on_failure: bool = False,
    cleanup_after_run: bool = False,
) -> dict[str, Any]:
    spec = parse_requirements_dict(requirements)
    return ScenarioRunner(session).run(
        scenario_name=scenario_name,
        topology_input=topology_input,
        raw_steps=raw_steps,
        requirements_spec=spec,
        cleanup_on_failure=cleanup_on_failure,
        cleanup_after_run=cleanup_after_run,
    )
