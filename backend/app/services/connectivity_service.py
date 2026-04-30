from typing import Any

from sqlmodel import Session, select

from app.models import ConnectivityTest, DeploymentResource, Node, Topology
from app.services import openstack_client
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
        target_fixed_ip = openstack_client.get_server_fixed_ip(target_server_id)
        source_floating_ip = _get_or_create_source_floating_ip(source_server_id)
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
        if resource.resource_type == "nova_server"
    }


def _get_or_create_source_floating_ip(source_server_id: str) -> str:
    server = openstack_client.get_server_details(source_server_id)
    existing_ip = _server_floating_ip(server)
    if existing_ip:
        return existing_ip

    floating_ip = openstack_client.create_floating_ip()
    floating_ip_address = floating_ip.get("floating_ip_address")
    if not floating_ip_address:
        raise RuntimeError("OpenStack created a floating IP without an address")

    openstack_client.associate_floating_ip(
        server_id=source_server_id,
        floating_ip=floating_ip_address,
    )
    return floating_ip_address


def _server_floating_ip(server: dict[str, Any]) -> str | None:
    for addresses in server.get("addresses", {}).values():
        for address in addresses:
            if not isinstance(address, dict):
                continue
            if address.get("OS-EXT-IPS:type") != "floating":
                continue
            ip_address = address.get("addr")
            if ip_address:
                return ip_address
    return None


def _run_ping_over_ssh(source_floating_ip: str, target_fixed_ip: str) -> str:
    import paramiko

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=source_floating_ip,
            username=CIRROS_USERNAME,
            password=CIRROS_PASSWORD,
            timeout=SSH_TIMEOUT_SECONDS,
            banner_timeout=SSH_TIMEOUT_SECONDS,
            auth_timeout=SSH_TIMEOUT_SECONDS,
        )
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
            raise RuntimeError(combined_output or f"ping exited with {exit_status}")
        return combined_output
    finally:
        client.close()


def _decode_ssh_output(output: bytes | str) -> str:
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace").strip()
    return output.strip()
