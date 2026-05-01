import ipaddress
from itertools import combinations
from typing import Any


SUPPORTED_NODE_TYPES = {"host", "router"}
SUPPORTED_FIREWALL_PROTOCOLS = {"icmp", "tcp"}


def compile_topology(topology: dict[str, Any]) -> dict[str, Any]:
    """Compile a topology definition into a provider-neutral deployment plan."""
    topology_name = topology.get("name")
    if not topology_name:
        raise ValueError("topology must have a name")

    nodes = topology.get("nodes", [])
    links = topology.get("links", [])
    firewall_rules = topology.get("firewall_rules", [])

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

    parsed_links: list[tuple[int, str, str, str, ipaddress._BaseNetwork]] = []
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
        if not isinstance(subnet, str):
            raise ValueError(f"link {index} subnet must be a string CIDR")
        try:
            net = ipaddress.ip_network(subnet.strip(), strict=False)
        except ValueError as exc:
            raise ValueError(
                f"link {index} has invalid subnet CIDR {subnet!r}"
            ) from exc
        parsed_links.append((index, from_node, to_node, subnet.strip(), net))

    for (i, _, _, si, ni), (j, _, _, sj, nj) in combinations(parsed_links, 2):
        if ni.version != nj.version:
            continue
        if ni.overlaps(nj):
            raise ValueError(
                f"link {i} subnet {si!r} overlaps link {j} subnet {sj!r}"
            )

    _reject_cycles(parsed_links)

    networks: list[dict[str, Any]] = []
    for index, from_node, to_node, subnet, _net in parsed_links:
        networks.append(
            {
                "name": f"{topology_name}-net-{index}",
                "subnet": subnet,
                "attached_nodes": [from_node, to_node],
            }
        )

    compiled_firewall_rules: list[dict[str, Any]] = []
    firewall_rule_names: set[str] = set()
    for index, rule in enumerate(firewall_rules, start=1):
        rule_name = rule.get("name")
        protocol = rule.get("protocol")
        from_node = rule.get("from")
        to_node = rule.get("to")
        port = rule.get("port")

        if not rule_name:
            raise ValueError(f"firewall rule {index} must have a name")
        if rule_name in firewall_rule_names:
            raise ValueError(f"duplicate firewall rule name '{rule_name}'")
        if protocol not in SUPPORTED_FIREWALL_PROTOCOLS:
            raise ValueError(
                f"firewall rule '{rule_name}' has unsupported protocol "
                f"'{protocol}'; supported protocols are: "
                f"{', '.join(sorted(SUPPORTED_FIREWALL_PROTOCOLS))}"
            )
        if from_node not in node_names:
            raise ValueError(
                f"firewall rule '{rule_name}' references unknown from node "
                f"'{from_node}'"
            )
        if to_node not in node_names:
            raise ValueError(
                f"firewall rule '{rule_name}' references unknown to node '{to_node}'"
            )
        firewall_rule_names.add(rule_name)
        compiled_rule = {
            "name": rule_name,
            "protocol": protocol,
            "from": from_node,
            "to": to_node,
        }
        if port is not None:
            compiled_rule["port"] = port
        compiled_firewall_rules.append(compiled_rule)

    return {
        "topology_name": topology_name,
        "networks": networks,
        "servers": servers,
        "firewall_rules": compiled_firewall_rules,
    }


def _reject_cycles(
    parsed_links: list[tuple[int, str, str, str, ipaddress._BaseNetwork]],
) -> None:
    """Current support is paths/chains, not rings or arbitrary cyclic graphs."""
    adjacency: dict[str, list[str]] = {}
    link_counts: dict[str, int] = {}
    for _index, from_node, to_node, _subnet, _net in parsed_links:
        if from_node == to_node:
            continue
        adjacency.setdefault(from_node, []).append(to_node)
        adjacency.setdefault(to_node, []).append(from_node)
        link_counts[from_node] = link_counts.get(from_node, 0) + 1
        link_counts[to_node] = link_counts.get(to_node, 0) + 1

    for node_name, count in sorted(link_counts.items()):
        if count > 2:
            raise ValueError(
                f"arbitrary mesh topology is unsupported: node '{node_name}' "
                f"appears in {count} links"
            )

    visited: set[str] = set()

    def visit(node_name: str, parent: str | None, path: list[str]) -> None:
        visited.add(node_name)
        for next_node in adjacency.get(node_name, []):
            if next_node == parent:
                continue
            if next_node in visited:
                cycle = " -> ".join(path + [node_name, next_node])
                raise ValueError(f"cycle topology is unsupported: {cycle}")
            visit(next_node, node_name, path + [node_name])

    for node_name in list(adjacency):
        if node_name not in visited:
            visit(node_name, None, [])
