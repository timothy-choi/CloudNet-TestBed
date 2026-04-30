import logging
from typing import Any

from sqlmodel import Session, select

from app.models import DeploymentResource, Topology
from app.providers.factory import get_provider
from app.topology_compiler import compile_topology


logger = logging.getLogger(__name__)


class DeploymentAlreadyExistsError(Exception):
    pass


class DeploymentError(Exception):
    pass


def _topology_to_input(topology: Topology) -> dict[str, Any]:
    return {
        "name": topology.name,
        "nodes": [
            {"name": node.name, "type": node.type}
            for node in topology.nodes
        ],
        "links": [
            {
                "from": link.from_node,
                "to": link.to_node,
                "subnet": link.subnet,
            }
            for link in topology.links
        ],
    }


def serialize_deployment_resource(resource: DeploymentResource) -> dict[str, Any]:
    return {
        "id": resource.id,
        "topology_id": resource.topology_id,
        "type": resource.resource_type,
        "name": resource.resource_name,
        "openstack_id": resource.openstack_id,
        "created_at": resource.created_at,
    }


def deployment_summary_resource(resource: DeploymentResource) -> dict[str, Any]:
    return {
        "type": resource.resource_type,
        "name": resource.resource_name,
        "id": resource.openstack_id,
    }


def list_topology_resources(
    session: Session,
    topology_id: int,
) -> list[DeploymentResource]:
    statement = select(DeploymentResource).where(
        DeploymentResource.topology_id == topology_id
    ).order_by(DeploymentResource.id)
    return list(session.exec(statement).all())


def deploy_topology(session: Session, topology: Topology) -> dict[str, Any]:
    if topology.id is None:
        raise DeploymentError("topology must be saved before deployment")

    existing_resources = list_topology_resources(session, topology.id)
    if existing_resources:
        raise DeploymentAlreadyExistsError(
            "topology is already deployed; delete existing resources before redeploying"
        )

    plan = compile_topology(_topology_to_input(topology))
    created_resources: list[DeploymentResource] = []
    response_resources: list[dict[str, Any]] = []
    network_ids_by_name: dict[str, str] = {}
    provider = get_provider()
    is_aws = provider.name == "aws"
    host_names = {node.name for node in topology.nodes if node.type == "host"}
    created_host_names: set[str] = set()

    try:
        if is_aws:
            host_count = len(host_names)
            max_instances = provider.max_instances_per_deploy()
            if host_count > max_instances:
                raise RuntimeError(
                    f"Topology requests {host_count} AWS instances, "
                    f"but AWS_MAX_INSTANCES_PER_DEPLOY is {max_instances}"
                )

        for network_plan in plan["networks"]:
            network = provider.create_network(
                name=network_plan["name"],
                cidr=network_plan["subnet"],
            )
            network_ids_by_name[network_plan["name"]] = network["id"]
            network_resource = DeploymentResource(
                topology_id=topology.id,
                resource_type="aws_vpc" if is_aws else "neutron_network",
                resource_name=network["name"],
                openstack_id=network["id"],
            )
            session.add(network_resource)
            session.commit()
            session.refresh(network_resource)
            created_resources.append(network_resource)
            response_resources.append(deployment_summary_resource(network_resource))

            subnet_name = f"{network_plan['name']}-subnet"
            subnet = provider.create_subnet(
                network_id=network["id"],
                name=subnet_name,
                cidr=network_plan["subnet"],
            )
            subnet_resource = DeploymentResource(
                topology_id=topology.id,
                resource_type="aws_subnet" if is_aws else "neutron_subnet",
                resource_name=subnet["name"],
                openstack_id=subnet["id"],
            )
            session.add(subnet_resource)
            session.commit()
            session.refresh(subnet_resource)
            created_resources.append(subnet_resource)
            response_resources.append(deployment_summary_resource(subnet_resource))

            if is_aws:
                for node_name in network_plan["attached_nodes"]:
                    if node_name not in host_names or node_name in created_host_names:
                        continue
                    logger.debug("Creating EC2 instance for node %s", node_name)
                    server = provider.create_server(
                        name=node_name,
                        network_id=network["id"],
                        subnet_id=subnet["id"],
                    )
                    _record_aws_server_resource(
                        session=session,
                        topology_id=topology.id,
                        server=server,
                        created_resources=created_resources,
                        response_resources=response_resources,
                    )
                    created_host_names.add(node_name)

        if is_aws and created_host_names != host_names:
            missing_hosts = ", ".join(sorted(host_names - created_host_names))
            raise RuntimeError(
                "AWS deployment could not place host nodes on a subnet: "
                + missing_hosts
            )

        if not is_aws:
            for server_plan in plan["servers"]:
                if server_plan["type"] != "host":
                    continue

                server_network_id = _network_id_for_node(
                    node_name=server_plan["name"],
                    networks=plan["networks"],
                    network_ids_by_name=network_ids_by_name,
                )
                server = provider.create_server(
                    name=server_plan["name"],
                    network_id=server_network_id,
                )
                server_resource = DeploymentResource(
                    topology_id=topology.id,
                    resource_type="nova_server",
                    resource_name=server["name"],
                    openstack_id=server["id"],
                )
                session.add(server_resource)
                session.commit()
                session.refresh(server_resource)
                created_resources.append(server_resource)
                server_summary = deployment_summary_resource(server_resource)
                response_resources.append(server_summary)
    except Exception as exc:
        topology.status = "FAILED"
        session.add(topology)
        session.commit()
        provider_label = "OpenStack" if provider.name == "openstack" else "Provider"
        raise DeploymentError(f"{provider_label} deployment failed: {exc}") from exc

    topology.status = "ACTIVE"
    session.add(topology)
    session.commit()
    session.refresh(topology)

    return {
        "topology_id": topology.id,
        "status": topology.status,
        "resources": response_resources,
    }


def _network_id_for_node(
    node_name: str,
    networks: list[dict[str, Any]],
    network_ids_by_name: dict[str, str],
) -> str:
    for network in networks:
        if node_name in network["attached_nodes"]:
            return network_ids_by_name[network["name"]]

    raise DeploymentError(f"host node '{node_name}' is not attached to any network")


def _record_aws_server_resource(
    session: Session,
    topology_id: int,
    server: dict[str, Any],
    created_resources: list[DeploymentResource],
    response_resources: list[dict[str, Any]],
) -> None:
    if server.get("security_group_id"):
        security_group_exists = any(
            resource.openstack_id == server["security_group_id"]
            for resource in created_resources
        )
        if not security_group_exists:
            security_group_resource = DeploymentResource(
                topology_id=topology_id,
                resource_type="aws_security_group",
                resource_name="cloudnet-sg",
                openstack_id=server["security_group_id"],
            )
            session.add(security_group_resource)
            session.commit()
            session.refresh(security_group_resource)
            created_resources.append(security_group_resource)
            response_resources.append(
                deployment_summary_resource(security_group_resource)
            )

    server_resource = DeploymentResource(
        topology_id=topology_id,
        resource_type="aws_instance",
        resource_name=server["name"],
        openstack_id=server["id"],
    )
    session.add(server_resource)
    session.commit()
    session.refresh(server_resource)
    created_resources.append(server_resource)
    server_summary = deployment_summary_resource(server_resource)
    server_summary["private_ip"] = server.get("private_ip")
    server_summary["public_ip"] = server.get("public_ip")
    response_resources.append(server_summary)
