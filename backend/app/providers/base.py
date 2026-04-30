from abc import ABC, abstractmethod
from typing import Any


class BaseProvider(ABC):
    name: str

    @abstractmethod
    def health(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def list_images(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def list_flavors(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def list_networks(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def create_network(
        self,
        name: str,
        cidr: str | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def create_subnet(
        self,
        network_id: str,
        name: str,
        cidr: str,
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def create_router(
        self,
        name: str,
        external_network_id: str | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def create_server(
        self,
        name: str,
        network_id: str,
        subnet_id: str | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def stop_server(self, server_id: str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def start_server(self, server_id: str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def delete_resource(
        self,
        resource_type: str,
        resource_id: str,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def get_server_status(self, server_id: str) -> str:
        raise NotImplementedError

    def get_server_fixed_ip(
        self,
        server_id: str,
        network_name: str | None = None,
    ) -> str:
        raise NotImplementedError

    def get_or_create_floating_ip_for_server(self, server_id: str) -> str:
        raise NotImplementedError
