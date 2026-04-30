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
