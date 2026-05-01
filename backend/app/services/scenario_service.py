"""Execute declarative test scenarios against deployed topologies."""

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import selectinload
from sqlmodel import Session, select

from app.models import FirewallRule, Link, Node, Topology
from app.schemas import TopologyInput
from app.services.connectivity_service import ConnectivityTestError, validate_topology_links
from app.services.control_plane_service import ControlPlaneError, reconcile_topology
from app.services.deployment_service import (
    DeploymentAlreadyExistsError,
    DeploymentError,
    deploy_topology,
)
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


def run_scenario(
    session: Session,
    *,
    scenario_name: str,
    topology_input: TopologyInput,
    raw_steps: list[Any],
) -> dict[str, Any]:
    parsed = parse_scenario_steps(raw_steps)
    topology = _persist_topology(session, topology_input)

    try:
        deploy_topology(session, topology)
    except DeploymentAlreadyExistsError as exc:
        raise ScenarioError(str(exc)) from exc
    except DeploymentError as exc:
        raise ScenarioError(str(exc)) from exc

    topology = _load_topology(session, topology.id)

    step_records: list[dict[str, Any]] = []
    overall_ok = True

    for step in parsed:
        if isinstance(step, _ValidateStep):
            label = "validate"
            try:
                response = validate_topology_links(session=session, topology=topology)
            except ConnectivityTestError as exc:
                actual = "FAILED"
                ok = _step_matches_validate(actual, step.expect)
                step_records.append({"step": label, "result": actual, "detail": str(exc)})
                if not ok:
                    overall_ok = False
                continue

            actual = str(response["status"])
            ok = _step_matches_validate(actual, step.expect)
            step_records.append({"step": label, "result": actual})
            if not ok:
                overall_ok = False

        elif isinstance(step, _FailStep):
            label = f"fail {step.node}"
            try:
                event = inject_node_down(
                    session=session,
                    topology=topology,
                    node_name=step.node,
                )
                result = event.status
            except FailureError as exc:
                step_records.append({"step": label, "result": "FAILED", "detail": str(exc)})
                overall_ok = False
                continue

            step_records.append({"step": label, "result": result})

        elif isinstance(step, _ReconcileStep):
            label = "reconcile"
            try:
                response = reconcile_topology(session=session, topology=topology)
                actual = str(response["status"])
            except ControlPlaneError as exc:
                step_records.append({"step": label, "result": "FAILED", "detail": str(exc)})
                overall_ok = False
                continue

            ok = actual == "RECONCILED"
            step_records.append({"step": label, "result": actual})
            if not ok:
                overall_ok = False

    return {
        "scenario": scenario_name,
        "topology_id": topology.id,
        "topology_name": topology.name,
        "status": "PASSED" if overall_ok else "FAILED",
        "steps": step_records,
    }
