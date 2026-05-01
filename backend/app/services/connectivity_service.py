from typing import Any

from sqlmodel import Session, select

from app.models import ConnectivityTest, DeploymentResource, Node, Topology
from app.providers.factory import get_provider
from app.services.deployment_service import list_topology_resources


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


def create_ping_test(
    session: Session,
    topology: Topology,
    source: str,
    target: str,
) -> ConnectivityTest:
    if topology.id is None:
        raise ConnectivityTestError("topology must be saved before testing")

    source_node = _host_node_by_name(topology, source)
    target_node = _host_node_by_name(topology, target)
    if source_node is None:
        raise ConnectivityTestError(f"unknown source host '{source}'")
    if target_node is None:
        raise ConnectivityTestError(f"unknown target host '{target}'")

    server_resources = _server_resources_by_name(
        list_topology_resources(session, topology.id)
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


def validate_topology_links(
    session: Session,
    topology: Topology,
) -> dict[str, Any]:
    results: list[dict[str, str]] = []

    if topology.firewall_rules:
        for rule in topology.firewall_rules:
            if rule.protocol != "icmp":
                results.append(
                    {
                        "source": rule.from_node,
                        "target": rule.to_node,
                        "status": "SKIPPED",
                    }
                )
                continue

            test = create_ping_test(
                session=session,
                topology=topology,
                source=rule.from_node,
                target=rule.to_node,
            )
            results.append(
                {
                    "source": rule.from_node,
                    "target": rule.to_node,
                    "status": test.status,
                }
            )

        overall_status = (
            "FAILED"
            if any(result["status"] == "FAILED" for result in results)
            else "PASSED"
        )
        return {
            "topology_id": topology.id,
            "status": overall_status,
            "results": results,
        }

    for link in topology.links:
        test = create_ping_test(
            session=session,
            topology=topology,
            source=link.from_node,
            target=link.to_node,
        )

        results.append(
            {
                "source": link.from_node,
                "target": link.to_node,
                "status": test.status,
            }
        )

    overall_status = (
        "PASSED"
        if results and all(result["status"] == "PASSED" for result in results)
        else "FAILED"
    )
    return {
        "topology_id": topology.id,
        "status": overall_status,
        "results": results,
    }


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
        if resource.resource_type in {"nova_server", "aws_instance"}
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
