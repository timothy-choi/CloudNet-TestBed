from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Any

from sqlmodel import Session, select

from app.core.config import ValidationConcurrencySettings, get_validation_concurrency_settings
from app.models import ConnectivityTest, DeploymentResource, Node, Topology
from app.providers.factory import get_provider
from app.resource_types import INSTANCE_RESOURCE_TYPES
from app.services.event_service import emit_event
from app.services.deployment_service import list_topology_resources
from app.services.ping_metrics import (
    extract_icmp_latencies_ms,
    mean_latency_ms,
    p95_latency_ms,
)
from app.services.trace_logging import log_trace

logger = logging.getLogger(__name__)

CIRROS_USERNAME = "cirros"
CIRROS_PASSWORD = "gocubsgo"
SSH_TIMEOUT_SECONDS = 10
PING_TIMEOUT_SECONDS = 10


class ConnectivityTestError(Exception):
    pass


def serialize_connectivity_test(test: ConnectivityTest) -> dict[str, Any]:
    return {
        "id": test.id,
        "topology_id": test.topology_id,
        "source": test.source_node,
        "target": test.target_node,
        "test_type": test.test_type,
        "status": test.status,
        "output": test.output,
        "created_at": test.created_at,
    }


def connectivity_test_summary(test: ConnectivityTest) -> dict[str, Any]:
    return {
        "topology_id": test.topology_id,
        "source": test.source_node,
        "target": test.target_node,
        "status": test.status,
        "output": test.output,
    }


def list_connectivity_tests(
    session: Session,
    topology_id: int,
) -> list[ConnectivityTest]:
    statement = select(ConnectivityTest).where(
        ConnectivityTest.topology_id == topology_id
    ).order_by(ConnectivityTest.id)
    return list(session.exec(statement).all())


def _execute_ping_core(
    session: Session,
    topology: Topology,
    source: str,
    target: str,
) -> tuple[str, str]:
    source_node = _host_node_by_name(topology, source)
    target_node = _host_node_by_name(topology, target)
    if source_node is None:
        raise ConnectivityTestError(f"unknown source host '{source}'")
    if target_node is None:
        raise ConnectivityTestError(f"unknown target host '{target}'")

    server_resources = _server_resources_by_name(
        list_topology_resources(session, topology.id)  # type: ignore[arg-type]
    )
    if source not in server_resources:
        raise ConnectivityTestError(f"source server '{source}' has not been deployed")
    if target not in server_resources:
        raise ConnectivityTestError(f"target server '{target}' has not been deployed")

    source_server_id = server_resources[source].openstack_id
    target_server_id = server_resources[target].openstack_id

    try:
        provider = get_provider()
        target_fixed_ip = provider.get_server_fixed_ip(target_server_id)
        if provider.name == "mock":
            target_status = provider.get_server_status(target_server_id)
            if target_status != "running":
                raise RuntimeError(
                    f"mock ping failed: target {target_server_id} is {target_status}"
                )
            output = provider.run_ping(source_server_id, target_fixed_ip)
        elif provider.name == "aws":
            output = provider.run_ping(source_server_id, target_fixed_ip)
        else:
            source_floating_ip = provider.get_or_create_floating_ip_for_server(
                source_server_id
            )
            output = _run_ping_over_ssh(
                source_floating_ip=source_floating_ip,
                target_fixed_ip=target_fixed_ip,
            )
        status = "PASSED"
    except Exception as exc:
        output = str(exc)
        status = "FAILED"
    return status, output


def _ping_validate_compute(
    topology_id: int,
    source: str,
    target: str,
    bind: Any,
) -> tuple[str, str, str, str]:
    """Run ping in a worker thread; returns (status, output, source, target). No DB writes."""
    with Session(bind) as session:
        topology = session.get(Topology, topology_id)
        if topology is None:
            return "FAILED", "topology not found", source, target
        try:
            st, out = _execute_ping_core(session, topology, source, target)
        except ConnectivityTestError as exc:
            return "FAILED", str(exc), source, target
        return st, out, source, target


def create_ping_test(
    session: Session,
    topology: Topology,
    source: str,
    target: str,
) -> ConnectivityTest:
    if topology.id is None:
        raise ConnectivityTestError("topology must be saved before testing")

    try:
        status, output = _execute_ping_core(session, topology, source, target)
    except ConnectivityTestError:
        raise

    test = ConnectivityTest(
        topology_id=topology.id,
        source_node=source,
        target_node=target,
        status=status,
        output=output,
    )
    session.add(test)
    session.commit()
    session.refresh(test)
    return test


def _record_connectivity_result(
    session: Session,
    topology_id: int,
    source: str,
    target: str,
    status: str,
    output: str,
) -> None:
    test = ConnectivityTest(
        topology_id=topology_id,
        source_node=source,
        target_node=target,
        status=status,
        output=output,
    )
    session.add(test)
    session.commit()


def _result_dict_for_status(
    source: str,
    target: str,
    status: str,
    output: str,
) -> dict[str, Any]:
    reply_latencies_ms = (
        extract_icmp_latencies_ms(output) if status == "PASSED" else []
    )
    return {
        "source": source,
        "target": target,
        "status": status,
        "reply_latencies_ms": reply_latencies_ms,
    }


def validate_topology_links(
    session: Session,
    topology: Topology,
    *,
    emit_validation_events: bool = True,
) -> dict[str, Any]:
    if topology.id is None:
        raise ConnectivityTestError("topology must be saved before validation")

    log_trace(
        "INFO",
        "validate_topology_links",
        status="STARTED",
        message=f"topology={topology.name}",
        resource_type="topology",
        resource_id=str(topology.id),
    )

    settings = get_validation_concurrency_settings()
    started = time.perf_counter()

    if topology.firewall_rules:
        return _validate_topology_links_firewall_rules(
            session=session,
            topology=topology,
            settings=settings,
            started=started,
            emit_validation_events=emit_validation_events,
        )

    return _validate_topology_links_plain_links(
        session=session,
        topology=topology,
        settings=settings,
        started=started,
        emit_validation_events=emit_validation_events,
    )


def _validate_topology_links_plain_links(
    session: Session,
    topology: Topology,
    *,
    settings: ValidationConcurrencySettings,
    started: float,
    emit_validation_events: bool,
) -> dict[str, Any]:
    slots: list[dict[str, Any] | None] = [None] * len(topology.links)
    ping_jobs = [
        (i, link.from_node, link.to_node) for i, link in enumerate(topology.links)
    ]
    link_count = len(ping_jobs)

    if emit_validation_events:
        emit_event(
            session=session,
            topology_id=topology.id,  # type: ignore[arg-type]
            event_type="VALIDATION_STARTED",
            status="STARTED",
            message=f"Topology validation started ({link_count} links)",
            metadata={"link_count": link_count},
        )

    _run_ping_jobs_parallel(
        session=session,
        topology_id=topology.id,  # type: ignore[arg-type]
        ping_jobs=ping_jobs,
        slots=slots,
        settings=settings,
    )

    results = [slots[i] for i in range(len(slots))]
    duration_ms = int((time.perf_counter() - started) * 1000)

    overall_status = (
        "PASSED"
        if results and all(r["status"] == "PASSED" for r in results)
        else "FAILED"
    )

    payload = _validation_response_with_metrics(
        topology.id,
        overall_status,
        results,
        validation_duration_ms=duration_ms,
    )

    if emit_validation_events:
        metrics = payload.get("metrics") or {}
        emit_event(
            session=session,
            topology_id=topology.id,  # type: ignore[arg-type]
            event_type="VALIDATION_COMPLETE",
            status="SUCCESS" if overall_status == "PASSED" else "FAILED",
            message=f"Topology validation complete ({metrics.get('tests_passed')}/{metrics.get('tests_total')} passed)",
            metadata={
                "tests_passed": metrics.get("tests_passed"),
                "tests_failed": metrics.get("tests_failed"),
                "duration_ms": duration_ms,
            },
        )
        emit_event(
            session=session,
            topology_id=topology.id,  # type: ignore[arg-type]
            event_type="VALIDATION",
            status="SUCCESS" if overall_status == "PASSED" else "FAILED",
            message=f"Topology validation {overall_status}",
            metadata={"results": payload["results"]},
        )

    return payload


def _validate_topology_links_firewall_rules(
    session: Session,
    topology: Topology,
    *,
    settings: ValidationConcurrencySettings,
    started: float,
    emit_validation_events: bool,
) -> dict[str, Any]:
    slots: list[dict[str, Any] | None] = [None] * len(topology.firewall_rules)
    ping_jobs: list[tuple[int, str, str]] = []

    for i, rule in enumerate(topology.firewall_rules):
        if rule.protocol != "icmp":
            slots[i] = {
                "source": rule.from_node,
                "target": rule.to_node,
                "status": "SKIPPED",
            }
        else:
            ping_jobs.append((i, rule.from_node, rule.to_node))

    link_count = len(ping_jobs)

    if emit_validation_events:
        emit_event(
            session=session,
            topology_id=topology.id,  # type: ignore[arg-type]
            event_type="VALIDATION_STARTED",
            status="STARTED",
            message=f"Topology validation started ({link_count} ICMP checks)",
            metadata={"link_count": link_count},
        )

    _run_ping_jobs_parallel(
        session=session,
        topology_id=topology.id,  # type: ignore[arg-type]
        ping_jobs=ping_jobs,
        slots=slots,
        settings=settings,
    )

    results = [slots[i] for i in range(len(slots))]
    duration_ms = int((time.perf_counter() - started) * 1000)

    overall_status = (
        "FAILED"
        if any(result["status"] == "FAILED" for result in results)
        else "PASSED"
    )

    payload = _validation_response_with_metrics(
        topology.id,
        overall_status,
        results,
        validation_duration_ms=duration_ms,
    )

    if emit_validation_events:
        metrics = payload.get("metrics") or {}
        emit_event(
            session=session,
            topology_id=topology.id,  # type: ignore[arg-type]
            event_type="VALIDATION_COMPLETE",
            status="SUCCESS" if overall_status == "PASSED" else "FAILED",
            message=f"Topology validation complete ({metrics.get('tests_passed')}/{metrics.get('tests_total')} passed)",
            metadata={
                "tests_passed": metrics.get("tests_passed"),
                "tests_failed": metrics.get("tests_failed"),
                "duration_ms": duration_ms,
            },
        )
        emit_event(
            session=session,
            topology_id=topology.id,  # type: ignore[arg-type]
            event_type="VALIDATION",
            status="SUCCESS" if overall_status == "PASSED" else "FAILED",
            message=f"Topology validation {overall_status}",
            metadata={"results": payload["results"]},
        )

    return payload


def _run_ping_jobs_parallel(
    session: Session,
    topology_id: int,
    ping_jobs: list[tuple[int, str, str]],
    slots: list[dict[str, Any] | None],
    settings: ValidationConcurrencySettings,
) -> None:
    if not ping_jobs:
        return

    timeout_sec = settings.validation_timeout_seconds
    max_workers = min(settings.max_parallel_validations, len(ping_jobs))
    bind = session.get_bind()

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures: list[tuple[int, str, str, Any]] = []
        for idx, src, tgt in ping_jobs:
            fut = executor.submit(
                _ping_validate_compute,
                topology_id,
                src,
                tgt,
                bind,
            )
            futures.append((idx, src, tgt, fut))

        for idx, src, tgt, fut in futures:
            try:
                status, output, out_src, out_tgt = fut.result(timeout=timeout_sec)
            except FuturesTimeoutError:
                logger.warning(
                    "validation timed out topology_id=%s %s -> %s",
                    topology_id,
                    src,
                    tgt,
                )
                status, output = "FAILED", "validation timed out"
                out_src, out_tgt = src, tgt
            _record_connectivity_result(
                session,
                topology_id,
                out_src,
                out_tgt,
                status,
                output,
            )
            slots[idx] = _result_dict_for_status(out_src, out_tgt, status, output)


def _validation_response_with_metrics(
    topology_id: int | None,
    overall_status: str,
    results: list[dict[str, Any]],
    *,
    validation_duration_ms: int | None = None,
) -> dict[str, Any]:
    counted = [r for r in results if r.get("status") != "SKIPPED"]
    tests_total = len(counted)
    tests_passed = sum(1 for r in counted if r["status"] == "PASSED")
    tests_failed = tests_total - tests_passed
    reply_latencies_ms: list[float] = []
    for r in counted:
        if r["status"] == "PASSED":
            reply_latencies_ms.extend(r.get("reply_latencies_ms") or [])
    metrics: dict[str, Any] = {
        "tests_total": tests_total,
        "tests_passed": tests_passed,
        "tests_failed": tests_failed,
        "reply_latencies_ms": reply_latencies_ms,
        "avg_latency_ms": mean_latency_ms(reply_latencies_ms),
        "p95_latency_ms": p95_latency_ms(reply_latencies_ms),
    }
    if validation_duration_ms is not None:
        metrics["validation_duration_ms"] = validation_duration_ms
    out: dict[str, Any] = {
        "topology_id": topology_id,
        "status": overall_status,
        "results": results,
        "metrics": metrics,
    }
    if validation_duration_ms is not None:
        out["duration_ms"] = validation_duration_ms
    return out


def _host_node_by_name(topology: Topology, name: str) -> Node | None:
    for node in topology.nodes:
        if node.name == name and node.type == "host":
            return node
    return None


def _server_resources_by_name(
    resources: list[DeploymentResource],
) -> dict[str, DeploymentResource]:
    return {
        resource.resource_name: resource
        for resource in resources
        if resource.resource_type in INSTANCE_RESOURCE_TYPES
    }


def _run_ping_over_ssh(source_floating_ip: str, target_fixed_ip: str) -> str:
    import paramiko

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        try:
            client.connect(
                hostname=source_floating_ip,
                username=CIRROS_USERNAME,
                password=CIRROS_PASSWORD,
                timeout=SSH_TIMEOUT_SECONDS,
                banner_timeout=SSH_TIMEOUT_SECONDS,
                auth_timeout=SSH_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            raise RuntimeError(f"SSH failed: {exc}") from exc

        command = f"ping -c 3 -W {PING_TIMEOUT_SECONDS} {target_fixed_ip}"
        _stdin, stdout, stderr = client.exec_command(
            command,
            timeout=PING_TIMEOUT_SECONDS,
        )
        exit_status = stdout.channel.recv_exit_status()
        output = _decode_ssh_output(stdout.read())
        error_output = _decode_ssh_output(stderr.read())
        combined_output = "\n".join(
            part for part in [output, error_output] if part
        )
        if exit_status != 0:
            raise RuntimeError(
                "ping failed: " + (combined_output or f"exited with {exit_status}")
            )
        return combined_output
    finally:
        client.close()


def _decode_ssh_output(output: bytes | str) -> str:
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace").strip()
    return output.strip()
