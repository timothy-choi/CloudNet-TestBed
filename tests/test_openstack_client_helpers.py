import logging
from types import SimpleNamespace

import pytest

from app.services import openstack_client


class FakeCompute:
    def get_server(self, server_id: str):
        if server_id == "missing-server":
            return None
        return SimpleNamespace(
            id=server_id,
            name="client-a",
            status="ACTIVE",
            addresses={},
        )


class FakeNetwork:
    def __init__(self) -> None:
        self.updated_ips: list[tuple[str, str]] = []
        self.created_ips: list[str] = []
        self.ports_by_device_id = {
            "server-client-a": [
                SimpleNamespace(
                    id="port-1",
                    name="",
                    device_id="server-client-a",
                    network_id="net-1",
                    fixed_ips=[{"ip_address": "10.30.1.10"}],
                )
            ],
            "server-no-port": [],
        }
        self.floating_ips_by_port = {
            "port-with-existing-floating-ip": [
                SimpleNamespace(
                    id="fip-existing",
                    floating_ip_address="172.24.4.55",
                    status="ACTIVE",
                    port_id="port-with-existing-floating-ip",
                )
            ],
        }

    def ports(self, device_id: str):
        return self.ports_by_device_id.get(device_id, [])

    def networks(self):
        return [
            SimpleNamespace(
                id="public-net",
                name="public",
                status="ACTIVE",
                is_router_external=True,
            )
        ]

    def create_ip(self, floating_network_id: str):
        self.created_ips.append(floating_network_id)
        return SimpleNamespace(
            id="fip-new",
            floating_ip_address="172.24.4.101",
            status="DOWN",
            port_id=None,
        )

    def update_ip(self, floating_ip_id: str, port_id: str):
        self.updated_ips.append((floating_ip_id, port_id))
        return SimpleNamespace(
            id=floating_ip_id,
            floating_ip_address="172.24.4.101",
            status="ACTIVE",
            port_id=port_id,
        )

    def ips(self, port_id: str):
        return self.floating_ips_by_port.get(port_id, [])


class FakeConnection:
    def __init__(self) -> None:
        self.compute = FakeCompute()
        self.network = FakeNetwork()


@pytest.fixture
def fake_connection(monkeypatch) -> FakeConnection:
    connection = FakeConnection()
    monkeypatch.setattr(openstack_client, "get_openstack_connection", lambda: connection)
    return connection


def test_get_server_port_uses_neutron_ports(fake_connection: FakeConnection) -> None:
    port = openstack_client.get_server_port("server-client-a")

    assert port["id"] == "port-1"
    assert port["device_id"] == "server-client-a"


def test_get_server_port_raises_clear_error_when_missing(
    fake_connection: FakeConnection,
) -> None:
    with pytest.raises(RuntimeError, match="source port not found"):
        openstack_client.get_server_port("server-no-port")


def test_get_public_network_id_finds_external_network(
    fake_connection: FakeConnection,
) -> None:
    assert openstack_client.get_public_network_id() == "public-net"


def test_create_and_associate_floating_ip_use_neutron(
    fake_connection: FakeConnection,
) -> None:
    floating_ip = openstack_client.create_floating_ip("public-net")
    associated = openstack_client.associate_floating_ip_to_port(
        floating_ip_id=floating_ip["id"],
        port_id="port-1",
    )

    assert fake_connection.network.created_ips == ["public-net"]
    assert fake_connection.network.updated_ips == [("fip-new", "port-1")]
    assert associated["floating_ip_address"] == "172.24.4.101"


def test_get_or_create_floating_ip_for_server_creates_and_associates(
    fake_connection: FakeConnection,
) -> None:
    floating_ip = openstack_client.get_or_create_floating_ip_for_server(
        "server-client-a"
    )

    assert floating_ip == "172.24.4.101"
    assert fake_connection.network.created_ips == ["public-net"]
    assert fake_connection.network.updated_ips == [("fip-new", "port-1")]


class FakeConflictException(Exception):
    status_code = 409


class FakeSecurityGroupNetwork:
    def __init__(self, rules, conflict_on_create: bool = False) -> None:
        self.rules = rules
        self.created_rules: list[dict] = []
        self.conflict_on_create = conflict_on_create

    def security_group_rules(self, security_group_id: str):
        return self.rules

    def create_security_group_rule(self, **kwargs):
        if self.conflict_on_create:
            self.rules.append(SimpleNamespace(id="rule-after-conflict", **kwargs))
            raise FakeConflictException("409 Conflict: Security group rule exists")
        self.created_rules.append(kwargs)
        return SimpleNamespace(**kwargs)


def test_ensure_security_group_rule_skips_existing_icmp(caplog) -> None:
    caplog.set_level(logging.INFO)
    connection = SimpleNamespace(
        network=FakeSecurityGroupNetwork(
            [
                SimpleNamespace(
                    id="rule-icmp",
                    security_group_id="sg-1",
                    direction="ingress",
                    ethertype="IPv4",
                    protocol="icmp",
                    port_range_min=None,
                    port_range_max=None,
                    remote_ip_prefix="0.0.0.0/0",
                )
            ]
        )
    )

    rule = openstack_client.ensure_security_group_rule(connection, "sg-1", "icmp")

    assert connection.network.created_rules == []
    assert rule["id"] == "rule-icmp"
    assert "Rule already exists, skipping" in caplog.text


def test_ensure_security_group_rule_skips_existing_ssh_rule(caplog) -> None:
    caplog.set_level(logging.INFO)
    connection = SimpleNamespace(
        network=FakeSecurityGroupNetwork(
            [
                SimpleNamespace(
                    id="rule-ssh",
                    security_group_id="sg-1",
                    direction="ingress",
                    ethertype="IPv4",
                    protocol="tcp",
                    port_range_min=22,
                    port_range_max=22,
                    remote_ip_prefix="0.0.0.0/0",
                )
            ]
        )
    )

    rule = openstack_client.ensure_security_group_rule(
        connection,
        "sg-1",
        "tcp",
        port_min=22,
        port_max=22,
    )

    assert connection.network.created_rules == []
    assert rule["id"] == "rule-ssh"
    assert "Rule already exists, skipping" in caplog.text


def test_ensure_security_group_rule_creates_missing_ssh_rule() -> None:
    connection = SimpleNamespace(network=FakeSecurityGroupNetwork([]))

    rule = openstack_client.ensure_security_group_rule(
        connection,
        "sg-1",
        "tcp",
        port_min=22,
        port_max=22,
    )

    assert connection.network.created_rules == [
        {
            "security_group_id": "sg-1",
            "direction": "ingress",
            "ethertype": "IPv4",
            "protocol": "tcp",
            "remote_ip_prefix": "0.0.0.0/0",
            "port_range_min": 22,
            "port_range_max": 22,
        }
    ]
    assert rule["protocol"] == "tcp"


def test_ensure_security_group_rule_handles_conflict_by_relisting(caplog) -> None:
    caplog.set_level(logging.INFO)
    connection = SimpleNamespace(
        network=FakeSecurityGroupNetwork([], conflict_on_create=True)
    )

    rule = openstack_client.ensure_security_group_rule(
        connection,
        "sg-1",
        "tcp",
        port_min=22,
        port_max=22,
    )

    assert connection.network.created_rules == []
    assert rule["id"] == "rule-after-conflict"
    assert rule["port_range_min"] == 22
    assert "Rule already exists, skipping" in caplog.text
