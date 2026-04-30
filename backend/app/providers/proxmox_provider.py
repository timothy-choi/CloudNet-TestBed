from typing import Any, NoReturn

from app.providers.base import BaseProvider


class ProxmoxProvider(BaseProvider):
    name = "proxmox"

    def health(self) -> dict[str, Any]:
        return {
            "enabled": False,
            "connected": False,
            "provider": self.name,
            "detail": "Proxmox provider is planned but not implemented yet",
        }

    def _not_implemented(self) -> NoReturn:
        raise NotImplementedError("Proxmox provider is not implemented yet")

    def list_images(self) -> list[dict[str, Any]]:
        self._not_implemented()

    def list_flavors(self) -> list[dict[str, Any]]:
        self._not_implemented()

    def list_networks(self) -> list[dict[str, Any]]:
        self._not_implemented()

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

    def create_server(self, name: str, network_id: str) -> dict[str, Any]:
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
