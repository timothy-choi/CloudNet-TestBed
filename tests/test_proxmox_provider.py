import sys
from types import SimpleNamespace

from app.providers.factory import get_provider
from app.providers.proxmox_provider import ProxmoxProvider


def set_proxmox_env(monkeypatch) -> None:
    monkeypatch.setenv("PROXMOX_HOST", "192.168.1.50")
    monkeypatch.setenv("PROXMOX_PORT", "8006")
    monkeypatch.setenv("PROXMOX_USER", "root@pam")
    monkeypatch.setenv("PROXMOX_PASSWORD", "secret")
    monkeypatch.setenv("PROXMOX_VERIFY_SSL", "false")
    monkeypatch.setenv("PROXMOX_NODE", "pve")


def mock_proxmoxer(monkeypatch) -> None:
    class FakeNode:
        qemu = SimpleNamespace(
            get=lambda: [
                {"vmid": 100, "name": "debian-template", "template": 1},
                {"vmid": 101, "name": "running-vm", "template": 0},
            ]
        )
        network = SimpleNamespace(
            get=lambda: [
                {
                    "iface": "vmbr0",
                    "type": "bridge",
                    "active": 1,
                    "address": "192.168.1.50",
                    "netmask": "24",
                },
                {"iface": "eno1", "type": "eth", "active": 1},
            ]
        )

    class FakeNodes:
        def __call__(self, node: str) -> FakeNode:
            assert node == "pve"
            return FakeNode()

    class FakeProxmox:
        version = SimpleNamespace(get=lambda: {"version": "8.2.4"})
        nodes = FakeNodes()

    def proxmox_api(*args, **kwargs) -> FakeProxmox:
        assert args == ("192.168.1.50",)
        assert kwargs["user"] == "root@pam"
        assert kwargs["password"] == "secret"
        assert kwargs["port"] == 8006
        assert kwargs["verify_ssl"] is False
        return FakeProxmox()

    monkeypatch.setitem(
        sys.modules,
        "proxmoxer",
        SimpleNamespace(ProxmoxAPI=proxmox_api),
    )


def test_proxmox_health_connected(monkeypatch) -> None:
    set_proxmox_env(monkeypatch)
    mock_proxmoxer(monkeypatch)

    assert ProxmoxProvider().health() == {
        "provider": "proxmox",
        "connected": True,
        "node": "pve",
        "version": "8.2.4",
    }


def test_proxmox_health_missing_config_returns_disconnected(monkeypatch) -> None:
    monkeypatch.delenv("PROXMOX_HOST", raising=False)
    monkeypatch.delenv("PROXMOX_USER", raising=False)
    monkeypatch.delenv("PROXMOX_PASSWORD", raising=False)
    monkeypatch.delenv("PROXMOX_NODE", raising=False)

    response = ProxmoxProvider().health()

    assert response["provider"] == "proxmox"
    assert response["connected"] is False
    assert "Missing Proxmox environment variables" in response["detail"]
    assert "PROXMOX_HOST" in response["detail"]


def test_proxmox_list_flavors_returns_static_values() -> None:
    assert ProxmoxProvider().list_flavors() == [
        {"id": "small", "name": "small", "vcpus": 1, "ram": 512, "disk": 8},
        {"id": "medium", "name": "medium", "vcpus": 2, "ram": 2048, "disk": 16},
        {"id": "large", "name": "large", "vcpus": 4, "ram": 4096, "disk": 32},
    ]


def test_proxmox_lists_templates_and_bridges(monkeypatch) -> None:
    set_proxmox_env(monkeypatch)
    mock_proxmoxer(monkeypatch)
    provider = ProxmoxProvider()

    assert provider.list_images() == [
        {"id": "100", "name": "debian-template", "type": "template"},
    ]
    assert provider.list_networks() == [
        {
            "name": "vmbr0",
            "type": "bridge",
            "active": True,
            "cidr": "192.168.1.50/24",
        },
    ]


def test_factory_returns_proxmox_provider(monkeypatch) -> None:
    monkeypatch.setenv("CLOUDNET_PROVIDER", "proxmox")

    assert isinstance(get_provider(), ProxmoxProvider)
