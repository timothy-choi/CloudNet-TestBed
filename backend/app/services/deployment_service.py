from typing import Any

from sqlmodel import Session, select

from app.models import DeploymentResource, Topology
from app.services import openstack_client
from app.topology_compiler import compile_topology


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


def deployment_summary_resource(resource: DeploymentResource) -> dict[str, str]:
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
    network_ids_by_name: dict[str, str] = {}

    try:
        for network_plan in plan["networks"]:
            network = openstack_client.create_network(network_plan["name"])
            network_ids_by_name[network_plan["name"]] = network["id"]
            network_resource = DeploymentResource(
                topology_id=topology.id,
                resource_type="neutron_network",
                resource_name=network["name"],
                openstack_id=network["id"],
            )
            session.add(network_resource)
            session.commit()
            session.refresh(network_resource)
            created_resources.append(network_resource)

            subnet_name = f"{network_plan['name']}-subnet"
            subnet = openstack_client.create_subnet(
                network_id=network["id"],
                name=subnet_name,
                cidr=network_plan["subnet"],
            )
            subnet_resource = DeploymentResource(
                topology_id=topology.id,
                resource_type="neutron_subnet",
                resource_name=subnet["name"],
                openstack_id=subnet["id"],
            )
            session.add(subnet_resource)
            session.commit()
            session.refresh(subnet_resource)
            created_resources.append(subnet_resource)

        for server_plan in plan["servers"]:
            if server_plan["type"] != "host":
                continue

            server_network_id = _network_id_for_node(
                node_name=server_plan["name"],
                networks=plan["networks"],
                network_ids_by_name=network_ids_by_name,
            )
            server = openstack_client.create_server(
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
    except Exception as exc:
        topology.status = "FAILED"
        session.add(topology)
        session.commit()
        raise DeploymentError(f"OpenStack deployment failed: {exc}") from exc

    topology.status = "ACTIVE"
    session.add(topology)
    session.commit()
    session.refresh(topology)

    return {
        "topology_id": topology.id,
        "status": topology.status,
        "resources": [
            deployment_summary_resource(resource)
            for resource in created_resources
        ],
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
