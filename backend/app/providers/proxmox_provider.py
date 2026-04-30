import ipaddress
from typing import Any, NoReturn

from app.core.config import ProxmoxSettings, get_proxmox_settings
from app.providers.base import BaseProvider


class ProxmoxProvider(BaseProvider):
    name = "proxmox"

    def health(self) -> dict[str, Any]:
        settings = get_proxmox_settings()
        missing = self._missing_settings(settings)
        if missing:
            return {
                "provider": self.name,
                "connected": False,
                "node": settings.node,
                "detail": "Missing Proxmox environment variables: "
                + ", ".join(missing),
            }

        try:
            proxmox = self._connect(settings)
            version = proxmox.version.get()
            return {
                "provider": self.name,
                "connected": True,
                "node": settings.node,
                "version": version.get("version"),
            }
        except Exception as exc:
            return {
                "provider": self.name,
                "connected": False,
                "node": settings.node,
                "detail": str(exc),
            }

    def list_images(self) -> list[dict[str, Any]]:
        settings = self._validated_settings()
        proxmox = self._connect(settings)
        templates = []
        for vm in proxmox.nodes(settings.node).qemu.get():
            if not vm.get("template"):
                continue
            templates.append(
                {
                    "id": str(vm.get("vmid")),
                    "name": vm.get("name") or str(vm.get("vmid")),
                    "type": "template",
                }
            )
        return templates

    def list_flavors(self) -> list[dict[str, Any]]:
        return [
            {
                "id": "small",
                "name": "small",
                "vcpus": 1,
                "ram": 512,
                "disk": 8,
            },
            {
                "id": "medium",
                "name": "medium",
                "vcpus": 2,
                "ram": 2048,
                "disk": 16,
            },
            {
                "id": "large",
                "name": "large",
                "vcpus": 4,
                "ram": 4096,
                "disk": 32,
            },
        ]

    def list_networks(self) -> list[dict[str, Any]]:
        settings = self._validated_settings()
        proxmox = self._connect(settings)
        networks = []
        for network in proxmox.nodes(settings.node).network.get():
            if network.get("type") != "bridge":
                continue
            networks.append(
                {
                    "name": network.get("iface"),
                    "type": network.get("type"),
                    "active": self._network_is_active(network),
                    "cidr": self._network_cidr(network),
                }
            )
        return networks

    def _connect(self, settings: ProxmoxSettings) -> Any:
        from proxmoxer import ProxmoxAPI

        return ProxmoxAPI(
            settings.host,
            user=settings.user,
            password=settings.password,
            port=settings.port,
            verify_ssl=settings.verify_ssl,
        )

    def _validated_settings(self) -> ProxmoxSettings:
        settings = get_proxmox_settings()
        missing = self._missing_settings(settings)
        if missing:
            raise RuntimeError(
                "Missing Proxmox environment variables: " + ", ".join(missing)
            )
        return settings

    def _missing_settings(self, settings: ProxmoxSettings) -> list[str]:
        missing = []
        if not settings.host:
            missing.append("PROXMOX_HOST")
        if not settings.user:
            missing.append("PROXMOX_USER")
        if not settings.password:
            missing.append("PROXMOX_PASSWORD")
        if not settings.node:
            missing.append("PROXMOX_NODE")
        return missing

    def _network_cidr(self, network: dict[str, Any]) -> str | None:
        cidr = network.get("cidr")
        if cidr:
            return str(cidr)

        address = network.get("address")
        netmask = network.get("netmask")
        if address and netmask:
            try:
                prefix_length = ipaddress.IPv4Network(f"0.0.0.0/{netmask}").prefixlen
            except ValueError:
                prefix_length = netmask
            return f"{address}/{prefix_length}"
        return None

    def _network_is_active(self, network: dict[str, Any]) -> bool:
        active = network.get("active")
        if isinstance(active, str):
            return active.strip().lower() in {"1", "true", "yes", "on"}
        return bool(active)

    def _not_implemented(self) -> NoReturn:
        raise NotImplementedError("Proxmox VM operations are not implemented yet")

    def create_network(
        self,
        name: str,
        cidr: str | None = None,
    ) -> dict[str, Any]:
        self._not_implemented()

    def create_subnet(
        self,
        network_id: str,
        name: str,
        cidr: str,
    ) -> dict[str, Any]:
        self._not_implemented()

    def create_router(
        self,
        name: str,
        external_network_id: str | None = None,
    ) -> dict[str, Any]:
        self._not_implemented()

    def create_server(
        self,
        name: str,
        network_id: str,
        subnet_id: str | None = None,
    ) -> dict[str, Any]:
        self._not_implemented()

    def stop_server(self, server_id: str) -> dict[str, Any]:
        self._not_implemented()

    def start_server(self, server_id: str) -> dict[str, Any]:
        self._not_implemented()

    def delete_resource(
        self,
        resource_type: str,
        resource_id: str,
    ) -> dict[str, Any]:
        self._not_implemented()
