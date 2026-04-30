from typing import Any

from app.providers.base import BaseProvider
from app.services import openstack_client


class OpenStackProvider(BaseProvider):
    name = "openstack"

    def health(self) -> dict[str, Any]:
        return openstack_client.check_openstack_connection()

    def list_images(self) -> list[dict[str, Any]]:
        return openstack_client.list_images()

    def list_flavors(self) -> list[dict[str, Any]]:
        return openstack_client.list_flavors()

    def list_networks(self) -> list[dict[str, Any]]:
        return openstack_client.list_networks()

    def create_network(
        self,
        name: str,
        cidr: str | None = None,
    ) -> dict[str, Any]:
        return openstack_client.create_network(name)

    def create_subnet(
        self,
        network_id: str,
        name: str,
        cidr: str,
    ) -> dict[str, Any]:
        return openstack_client.create_subnet(
            network_id=network_id,
            name=name,
            cidr=cidr,
        )

    def create_router(
        self,
        name: str,
        external_network_id: str | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError("OpenStack router creation is not implemented yet")

    def create_server(self, name: str, network_id: str) -> dict[str, Any]:
        return openstack_client.create_server(name=name, network_id=network_id)

    def stop_server(self, server_id: str) -> dict[str, Any]:
        openstack_client.stop_server(server_id)
        return {
            "id": server_id,
            "status": openstack_client.get_server_status(server_id),
        }

    def start_server(self, server_id: str) -> dict[str, Any]:
        openstack_client.start_server(server_id)
        return {
            "id": server_id,
            "status": openstack_client.get_server_status(server_id),
        }

    def delete_resource(
        self,
        resource_type: str,
        resource_id: str,
    ) -> dict[str, Any]:
        if resource_type == "nova_server":
            openstack_client.delete_server(resource_id)
        elif resource_type == "neutron_subnet":
            openstack_client.delete_subnet(resource_id)
        elif resource_type == "neutron_network":
            openstack_client.delete_network(resource_id)
        else:
            raise ValueError(f"unsupported OpenStack resource type: {resource_type}")

        return {"id": resource_id, "type": resource_type, "deleted": True}

    def get_server_status(self, server_id: str) -> str:
        return openstack_client.get_server_status(server_id)

    def get_server_fixed_ip(
        self,
        server_id: str,
        network_name: str | None = None,
    ) -> str:
        return openstack_client.get_server_fixed_ip(
            server_id=server_id,
            network_name=network_name,
        )

    def get_or_create_floating_ip_for_server(self, server_id: str) -> str:
        return openstack_client.get_or_create_floating_ip_for_server(server_id)
