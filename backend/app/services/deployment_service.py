import logging
from typing import Any

from sqlmodel import Session, select

from app.models import DeploymentResource, Topology
from app.providers.factory import get_provider
from app.resource_types import (
    AWS_INTERNET_GATEWAY,
    AWS_ROUTE_TABLE,
    AWS_ROUTE_TABLE_ASSOCIATION,
    AWS_SECURITY_GROUP,
    AWS_VPC,
    INSTANCE_RESOURCE_TYPES,
    NETWORK_RESOURCE_TYPES,
    SUBNET_RESOURCE_TYPES,
    non_aws_deploy_resource_labels,
)
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
        "firewall_rules": [
            {
                "name": rule.name,
                "protocol": rule.protocol,
                "port": rule.port,
                "from": rule.from_node,
                "to": rule.to_node,
            }
            for rule in topology.firewall_rules
        ],
    }


def serialize_deployment_resource(resource: DeploymentResource) -> dict[str, Any]:
    rid = resource.openstack_id
    return {
        "id": resource.id,
        "topology_id": resource.topology_id,
        "type": resource.resource_type,
        "name": resource.resource_name,
        "provider_resource_id": rid,
        "openstack_id": rid,
        "created_at": resource.created_at,
    }


def deployment_summary_resource(resource: DeploymentResource) -> dict[str, Any]:
    rid = resource.openstack_id
    return {
        "type": resource.resource_type,
        "name": resource.resource_name,
        "provider_resource_id": rid,
        "id": rid,
    }


def list_topology_resources(
    session: Session,
    topology_id: int,
) -> list[DeploymentResource]:
    statement = select(DeploymentResource).where(
        DeploymentResource.topology_id == topology_id
    ).order_by(DeploymentResource.id)
    return list(session.exec(statement).all())


def compile_deployment_plan(topology: Topology) -> dict[str, Any]:
    return compile_topology(_topology_to_input(topology))


def multi_homed_warnings(plan: dict[str, Any]) -> list[str]:
    host_names = {
        server["name"]
        for server in plan["servers"]
        if server["type"] == "host"
    }
    link_counts: dict[str, int] = {}
    for network in plan["networks"]:
        for node_name in network["attached_nodes"]:
            if node_name in host_names:
                link_counts[node_name] = link_counts.get(node_name, 0) + 1

    return [
        f"multi-homed node {node_name} appears in multiple links; "
        "attached to first subnet only"
        for node_name, count in sorted(link_counts.items())
        if count > 1
    ]


def deploy_topology(
    session: Session,
    topology: Topology,
    *,
    scenario_run_id: int | None = None,
) -> dict[str, Any]:
    if topology.id is None:
        raise DeploymentError("topology must be saved before deployment")

    existing_resources = list_topology_resources(session, topology.id)
    if existing_resources:
        raise DeploymentAlreadyExistsError(
            "topology is already deployed; delete existing resources before redeploying"
        )

    plan = compile_deployment_plan(topology)
    created_resources: list[DeploymentResource] = []
    response_resources: list[dict[str, Any]] = []
    network_ids_by_name: dict[str, str] = {}
    provider = get_provider()
    is_aws = provider.name == "aws"
    host_names = {node.name for node in topology.nodes if node.type == "host"}
    warnings = multi_homed_warnings(plan)

    try:
        if is_aws:
            host_count = len(host_names)
            max_instances = provider.max_instances_per_deploy()
            if host_count > max_instances:
                raise RuntimeError(
                    f"Topology requests {host_count} AWS instances, "
                    f"but AWS_MAX_INSTANCES_PER_DEPLOY is {max_instances}"
                )

            _deploy_aws_resources(
                session=session,
                topology=topology,
                provider=provider,
                plan=plan,
                host_names=host_names,
                created_resources=created_resources,
                response_resources=response_resources,
            )

        if not is_aws:
            net_t, subnet_t, srv_t = non_aws_deploy_resource_labels(provider.name)
            for network_plan in plan["networks"]:
                network = provider.create_network(
                    name=network_plan["name"],
                    cidr=network_plan["subnet"],
                )
                network_ids_by_name[network_plan["name"]] = network["id"]
                network_resource = DeploymentResource(
                    topology_id=topology.id,
                    resource_type=net_t,
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
                    resource_type=subnet_t,
                    resource_name=subnet["name"],
                    openstack_id=subnet["id"],
                )
                session.add(subnet_resource)
                session.commit()
                session.refresh(subnet_resource)
                created_resources.append(subnet_resource)
                response_resources.append(deployment_summary_resource(subnet_resource))

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
                    resource_type=srv_t,
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
        partial = list_topology_resources(session, topology.id)
        if partial:
            try:
                cleanup_topology_deployment(session, topology)
            except Exception:
                logger.exception("cleanup after failed deploy failed")
        if topology.id is not None:
            try:
                from app.services.local_state_store import record_deploy_failed

                record_deploy_failed(
                    topology_id=topology.id,
                    scenario_run_id=scenario_run_id,
                )
            except Exception:
                logger.exception("local state snapshot (failed deploy) skipped")
        provider_label = "OpenStack" if provider.name == "openstack" else "Provider"
        raise DeploymentError(f"{provider_label} deployment failed: {exc}") from exc

    topology.status = "ACTIVE"
    session.add(topology)
    session.commit()
    session.refresh(topology)

    rows = list_topology_resources(session, topology.id)
    try:
        from app.services.local_state_store import record_deploy_snapshot

        record_deploy_snapshot(
            topology_id=topology.id,
            scenario_run_id=scenario_run_id,
            resources=rows,
            status=str(topology.status),
        )
    except Exception:
        logger.exception("local state snapshot (deploy) skipped")

    response = {
        "topology_id": topology.id,
        "status": topology.status,
        "resources": response_resources,
    }
    if warnings:
        response["warnings"] = warnings
    return response


def _deploy_aws_resources(
    session: Session,
    topology: Topology,
    provider: Any,
    plan: dict[str, Any],
    host_names: set[str],
    created_resources: list[DeploymentResource],
    response_resources: list[dict[str, Any]],
) -> None:
    if not plan["networks"]:
        raise RuntimeError("AWS deployment requires at least one link/subnet")

    vpc_plan = plan["networks"][0]
    network = provider.create_network(
        name=vpc_plan["name"],
        cidr="10.0.0.0/16",
    )
    network_resource = DeploymentResource(
        topology_id=topology.id,
        resource_type="aws_vpc",
        resource_name=network["name"],
        openstack_id=network["id"],
    )
    session.add(network_resource)
    session.commit()
    session.refresh(network_resource)
    created_resources.append(network_resource)
    response_resources.append(deployment_summary_resource(network_resource))

    first_subnet_by_node: dict[str, str] = {}
    for network_plan in plan["networks"]:
        subnet_name = f"{network_plan['name']}-subnet"
        subnet = provider.create_subnet(
            network_id=network["id"],
            name=subnet_name,
            cidr=network_plan["subnet"],
        )
        subnet_resource = DeploymentResource(
            topology_id=topology.id,
            resource_type="aws_subnet",
            resource_name=subnet["name"],
            openstack_id=subnet["id"],
        )
        session.add(subnet_resource)
        session.commit()
        session.refresh(subnet_resource)
        created_resources.append(subnet_resource)
        response_resources.append(deployment_summary_resource(subnet_resource))

        _record_optional_aws_network_resource(
            session=session,
            topology_id=topology.id,
            resource_type="aws_internet_gateway",
            resource_name=f"{subnet_name}-igw",
            resource_id=subnet.get("internet_gateway_id"),
            created_resources=created_resources,
            response_resources=response_resources,
        )
        _record_optional_aws_network_resource(
            session=session,
            topology_id=topology.id,
            resource_type="aws_route_table",
            resource_name=f"{subnet_name}-rt",
            resource_id=subnet.get("route_table_id"),
            created_resources=created_resources,
            response_resources=response_resources,
        )
        _record_optional_aws_network_resource(
            session=session,
            topology_id=topology.id,
            resource_type="aws_route_table_association",
            resource_name=f"{subnet_name}-rt-assoc",
            resource_id=subnet.get("route_table_association_id"),
            created_resources=created_resources,
            response_resources=response_resources,
        )

        for node_name in network_plan["attached_nodes"]:
            if node_name in host_names and node_name not in first_subnet_by_node:
                first_subnet_by_node[node_name] = subnet["id"]

    missing_hosts = host_names - set(first_subnet_by_node)
    if missing_hosts:
        raise RuntimeError(
            "AWS deployment could not place host nodes on a subnet: "
            + ", ".join(sorted(missing_hosts))
        )

    for node_name in sorted(host_names):
        logger.debug("Creating EC2 instance for node %s", node_name)
        server = provider.create_server(
            name=node_name,
            network_id=network["id"],
            subnet_id=first_subnet_by_node[node_name],
        )
        _record_aws_server_resource(
            session=session,
            topology_id=topology.id,
            server=server,
            created_resources=created_resources,
            response_resources=response_resources,
        )

    if plan["firewall_rules"]:
        security_group_id = _aws_security_group_id(created_resources)
        if security_group_id is None:
            raise RuntimeError("AWS deployment did not create a CloudNet security group")
        provider.ensure_firewall_rules(
            security_group_id=security_group_id,
            firewall_rules=plan["firewall_rules"],
        )


def _aws_security_group_id(resources: list[DeploymentResource]) -> str | None:
    for resource in resources:
        if resource.resource_type == "aws_security_group":
            return resource.openstack_id
    return None


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


def _record_optional_aws_network_resource(
    session: Session,
    topology_id: int,
    resource_type: str,
    resource_name: str,
    resource_id: str | None,
    created_resources: list[DeploymentResource],
    response_resources: list[dict[str, Any]],
) -> None:
    if not resource_id:
        return
    if any(resource.openstack_id == resource_id for resource in created_resources):
        return

    resource = DeploymentResource(
        topology_id=topology_id,
        resource_type=resource_type,
        resource_name=resource_name,
        openstack_id=resource_id,
    )
    session.add(resource)
    session.commit()
    session.refresh(resource)
    created_resources.append(resource)
    response_resources.append(deployment_summary_resource(resource))


def _deployment_resource_delete_order(resource: DeploymentResource) -> tuple[int, int]:
    """Instances first, then subnets and attachments, then VPC/network last."""
    rt = resource.resource_type
    if rt in INSTANCE_RESOURCE_TYPES:
        tier = 0
    elif rt in SUBNET_RESOURCE_TYPES:
        tier = 1
    elif rt in (
        AWS_SECURITY_GROUP,
        AWS_INTERNET_GATEWAY,
        AWS_ROUTE_TABLE,
        AWS_ROUTE_TABLE_ASSOCIATION,
    ):
        tier = 2
    elif rt == AWS_VPC or rt in NETWORK_RESOURCE_TYPES:
        tier = 4
    else:
        tier = 3
    rid = resource.id or 0
    return (tier, -rid)


def teardown_provider_resources(provider: Any, resources: list[DeploymentResource]) -> None:
    """Delete resources at the provider only (no DB). Shared by cleanup and janitor."""
    if not resources:
        return
    if provider.name == "aws":
        vpc_rows = [r for r in resources if r.resource_type == AWS_VPC]
        if not vpc_rows:
            raise DeploymentError(
                "AWS topology has deployment resources but no aws_vpc row; "
                "manual cleanup may be required"
            )
        provider.delete_network(vpc_rows[0].openstack_id)
        return
    ordered = sorted(resources, key=_deployment_resource_delete_order)
    for r in ordered:
        provider.delete_resource(r.resource_type, r.openstack_id)


def cleanup_topology_deployment(session: Session, topology: Topology) -> dict[str, Any]:
    """Remove recorded provider resources for this topology (same operations as manual teardown).

    AWS: deletes the VPC via ``delete_network``, which terminates instances and subnets.
    Mock / OpenStack-style: deletes each resource via ``delete_resource`` in dependency order.
    """
    if topology.id is None:
        raise DeploymentError("topology must be saved before cleanup")

    resources = list_topology_resources(session, topology.id)
    if not resources:
        topology.status = "CREATED"
        session.add(topology)
        session.commit()
        try:
            from app.services.local_state_store import remove_local_deployment

            remove_local_deployment(topology.id)
        except Exception:
            logger.exception("local state cleanup skipped")
        return {"status": "SKIPPED", "detail": "no deployment resources"}

    provider = get_provider()
    n = len(resources)

    try:
        teardown_provider_resources(provider, resources)
    except Exception:
        logger.exception("provider teardown failed during cleanup_topology_deployment")

    for r in resources:
        session.delete(r)
    session.commit()

    topology.status = "CREATED"
    session.add(topology)
    session.commit()
    session.refresh(topology)

    try:
        from app.services.local_state_store import remove_local_deployment

        remove_local_deployment(topology.id)
    except Exception:
        logger.exception("local state cleanup skipped")

    return {"status": "CLEANED", "resources_removed": n}
