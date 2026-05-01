from typing import Any

from sqlmodel import Session

from app.core.config import aws_use_ssm, cloudnet_allow_exec
from app.models import DeploymentResource, Topology
from app.providers.factory import get_provider
from app.services.deployment_service import list_topology_resources


class AccessError(Exception):
    pass


EXEC_TIMEOUT_SECONDS = 30.0

_HTTP_DEMO_SCRIPT = (
    "nohup python3 -m http.server 8080 </dev/null "
    ">/tmp/cloudnet-http-demo.log 2>&1 & echo CLOUDNET_HTTP_DEMO_STARTED"
)


def _instance_resource_type(provider_name: str) -> str:
    return "aws_instance" if provider_name == "aws" else "nova_server"


def command_is_forbidden(command: str) -> bool:
    lower = command.lower()
    nospace = "".join(lower.split())

    if "rm -rf /" in lower or "rm -rf /*" in lower:
        return True
    if "shutdown" in lower:
        return True
    if "reboot" in lower:
        return True
    if "mkfs" in lower:
        return True
    if ":(){ :|:& };:" in lower:
        return True
    if ":(){:|:&};:" in nospace:
        return True
    return False


def _find_instance_resource(
    resources: list[DeploymentResource],
    resource_type: str,
    node_name: str,
) -> DeploymentResource | None:
    for resource in resources:
        if resource.resource_type == resource_type and resource.resource_name == node_name:
            return resource
    return None


def _aws_access_methods() -> list[str]:
    if aws_use_ssm():
        return ["ssm_exec"]
    return []


def build_access_summary(session: Session, topology: Topology) -> dict[str, Any]:
    if topology.id is None:
        raise AccessError("topology must be saved")

    provider = get_provider()
    resources = list_topology_resources(session, topology.id)
    inst_type = _instance_resource_type(provider.name)

    nodes_out: list[dict[str, Any]] = []

    for node in topology.nodes:
        if node.type != "host":
            continue
        resource = _find_instance_resource(resources, inst_type, node.name)
        if resource is None:
            continue

        instance_id = resource.openstack_id
        private_ip: str | None = None
        public_ip: str | None = None
        ssm_available = False
        access_methods: list[str] = []

        if provider.name == "aws":
            info = provider.get_instance_network_info(instance_id)
            private_ip = info.get("private_ip")
            public_ip = info.get("public_ip")
            ssm_available = aws_use_ssm()
            access_methods = _aws_access_methods()
        elif provider.name == "mock":
            private_ip = provider.get_server_fixed_ip(instance_id)
            public_ip = None
            ssm_available = True
            access_methods = ["ssm_exec"]
        else:
            try:
                private_ip = provider.get_server_fixed_ip(instance_id)
            except Exception:
                private_ip = None
            public_ip = None
            ssm_available = False
            access_methods = []

        nodes_out.append(
            {
                "name": node.name,
                "instance_id": instance_id,
                "private_ip": private_ip,
                "public_ip": public_ip,
                "ssm_available": ssm_available,
                "access_methods": access_methods,
            }
        )

    return {
        "topology_id": topology.id,
        "provider": provider.name,
        "nodes": nodes_out,
    }


def exec_on_node(
    session: Session,
    topology: Topology,
    node_name: str,
    command: str,
) -> dict[str, Any]:
    if not cloudnet_allow_exec():
        raise AccessError("remote exec is disabled (set CLOUDNET_ALLOW_EXEC=true)")

    if topology.id is None:
        raise AccessError("topology must be saved")

    if command_is_forbidden(command):
        raise AccessError("command blocked by CloudNet safety policy")

    provider = get_provider()
    if provider.name not in {"aws", "mock"}:
        raise AccessError(
            f"exec is not supported for provider {provider.name!r}; use AWS or mock"
        )

    resources = list_topology_resources(session, topology.id)
    inst_type = _instance_resource_type(provider.name)
    resource = _find_instance_resource(resources, inst_type, node_name)
    if resource is None:
        raise AccessError(f"no deployed instance found for node {node_name!r}")

    send = getattr(provider, "send_ssm_command", None)
    if send is None:
        raise AccessError("provider does not support SSM exec")

    result = send(
        resource.openstack_id,
        command,
        timeout_seconds=EXEC_TIMEOUT_SECONDS,
    )
    return {
        "node": node_name,
        "status": result.get("status", "FAILED"),
        "stdout": result.get("stdout", ""),
        "stderr": result.get("stderr", ""),
    }


def start_http_demo_workload(
    session: Session,
    topology: Topology,
    node_name: str,
) -> dict[str, Any]:
    if not cloudnet_allow_exec():
        raise AccessError("workload deployment is disabled (set CLOUDNET_ALLOW_EXEC=true)")

    result = exec_on_node(session, topology, node_name, _HTTP_DEMO_SCRIPT)
    if result["status"] != "SUCCESS":
        raise AccessError(
            result.get("stderr")
            or result.get("stdout")
            or "http-demo workload failed to start"
        )

    return {
        "status": "STARTED",
        "node": node_name,
        "port": 8080,
    }
