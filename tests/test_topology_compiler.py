import pytest

from app.topology_compiler import compile_topology


def test_valid_topology_compiles_correctly() -> None:
    topology = {
        "name": "simple-two-node-lab",
        "nodes": [
            {"name": "client-a", "type": "host"},
            {"name": "client-b", "type": "host"},
        ],
        "links": [
            {"from": "client-a", "to": "client-b", "subnet": "10.10.1.0/24"},
        ],
    }

    assert compile_topology(topology) == {
        "topology_name": "simple-two-node-lab",
        "networks": [
            {
                "name": "simple-two-node-lab-net-1",
                "subnet": "10.10.1.0/24",
                "attached_nodes": ["client-a", "client-b"],
            }
        ],
        "servers": [
            {"name": "client-a", "type": "host"},
            {"name": "client-b", "type": "host"},
        ],
        "firewall_rules": [],
    }


def test_duplicate_node_names_rejected() -> None:
    topology = {
        "name": "duplicate-node-lab",
        "nodes": [
            {"name": "client-a", "type": "host"},
            {"name": "client-a", "type": "host"},
        ],
        "links": [],
    }

    with pytest.raises(ValueError, match="duplicate node name 'client-a'"):
        compile_topology(topology)


def test_link_referencing_unknown_node_rejected() -> None:
    topology = {
        "name": "unknown-node-lab",
        "nodes": [{"name": "client-a", "type": "host"}],
        "links": [
            {"from": "client-a", "to": "client-b", "subnet": "10.10.1.0/24"},
        ],
    }

    with pytest.raises(ValueError, match="link 1 references unknown node 'client-b'"):
        compile_topology(topology)


def test_missing_subnet_rejected() -> None:
    topology = {
        "name": "missing-subnet-lab",
        "nodes": [
            {"name": "client-a", "type": "host"},
            {"name": "client-b", "type": "host"},
        ],
        "links": [
            {"from": "client-a", "to": "client-b"},
        ],
    }

    with pytest.raises(ValueError, match="link 1 must have a subnet"):
        compile_topology(topology)


def test_unsupported_node_type_rejected() -> None:
    topology = {
        "name": "unsupported-type-lab",
        "nodes": [{"name": "client-a", "type": "database"}],
        "links": [],
    }

    with pytest.raises(ValueError, match="unsupported type 'database'"):
        compile_topology(topology)
