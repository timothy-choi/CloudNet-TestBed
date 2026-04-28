from typing import Any


SUPPORTED_NODE_TYPES = {"host", "router"}


def compile_topology(topology: dict[str, Any]) -> dict[str, Any]:
    """Compile a topology definition into a provider-neutral deployment plan."""
    topology_name = topology.get("name")
    if not topology_name:
        raise ValueError("topology must have a name")

    nodes = topology.get("nodes", [])
    links = topology.get("links", [])

    node_names: set[str] = set()
    servers: list[dict[str, str]] = []

    for index, node in enumerate(nodes, start=1):
        node_name = node.get("name")
        node_type = node.get("type")

        if not node_name:
            raise ValueError(f"node {index} must have a name")
        if not node_type:
            raise ValueError(f"node '{node_name}' must have a type")
        if node_type not in SUPPORTED_NODE_TYPES:
            raise ValueError(
                f"node '{node_name}' has unsupported type '{node_type}'; "
                f"supported types are: {', '.join(sorted(SUPPORTED_NODE_TYPES))}"
            )
        if node_name in node_names:
            raise ValueError(f"duplicate node name '{node_name}'")

        node_names.add(node_name)
        servers.append({"name": node_name, "type": node_type})

    networks: list[dict[str, Any]] = []
    for index, link in enumerate(links, start=1):
        from_node = link.get("from")
        to_node = link.get("to")
        subnet = link.get("subnet")

        if from_node not in node_names:
            raise ValueError(f"link {index} references unknown node '{from_node}'")
        if to_node not in node_names:
            raise ValueError(f"link {index} references unknown node '{to_node}'")
        if not subnet:
            raise ValueError(f"link {index} must have a subnet")

        networks.append(
            {
                "name": f"{topology_name}-net-{index}",
                "subnet": subnet,
                "attached_nodes": [from_node, to_node],
            }
        )

    return {
        "topology_name": topology_name,
        "networks": networks,
        "servers": servers,
    }
