from typing import Any

from app.providers.base import BaseProvider


class MockProvider(BaseProvider):
    name = "mock"

    def health(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "connected": True,
            "provider": self.name,
            "detail": "Mock provider is ready",
        }

    def list_images(self) -> list[dict[str, Any]]:
        return [
            {"id": "mock-image-cirros", "name": "mock-cirros", "status": "active"},
        ]

    def list_flavors(self) -> list[dict[str, Any]]:
        return [
            {
                "id": "mock-flavor-small",
                "name": "mock.small",
                "vcpus": 1,
                "ram": 512,
                "disk": 1,
            },
        ]

    def list_networks(self) -> list[dict[str, Any]]:
        return [
            {
                "id": "mock-public-net",
                "name": "mock-public",
                "status": "ACTIVE",
                "is_router_external": True,
            },
        ]

    def create_network(
        self,
        name: str,
        cidr: str | None = None,
    ) -> dict[str, Any]:
        return {
            "id": f"mock-net-{name}",
            "name": name,
            "status": "ACTIVE",
            "cidr": cidr,
        }

    def create_subnet(
        self,
        network_id: str,
        name: str,
        cidr: str,
    ) -> dict[str, Any]:
        return {
            "id": f"mock-subnet-{name}",
            "name": name,
            "cidr": cidr,
            "network_id": network_id,
        }

    def create_router(
        self,
        name: str,
        external_network_id: str | None = None,
    ) -> dict[str, Any]:
        return {
            "id": f"mock-router-{name}",
            "name": name,
            "external_network_id": external_network_id,
            "status": "ACTIVE",
        }

    def create_server(
        self,
        name: str,
        network_id: str,
        subnet_id: str | None = None,
    ) -> dict[str, Any]:
        return {
            "id": f"mock-server-{name}",
            "name": name,
            "status": "ACTIVE",
            "addresses": {
                network_id: [
                    {
                        "addr": "10.0.0.10",
                        "version": 4,
                        "OS-EXT-IPS:type": "fixed",
                    },
                ],
            },
        }

    def stop_server(self, server_id: str) -> dict[str, Any]:
        return {"id": server_id, "status": "SHUTOFF"}

    def start_server(self, server_id: str) -> dict[str, Any]:
        return {"id": server_id, "status": "ACTIVE"}

    def delete_resource(
        self,
        resource_type: str,
        resource_id: str,
    ) -> dict[str, Any]:
        return {"id": resource_id, "type": resource_type, "deleted": True}

    def get_server_status(self, server_id: str) -> str:
        if "stopped" in server_id or "shutoff" in server_id:
            return "SHUTOFF"
        return "ACTIVE"

    def wait_for_server_running(self, server_id: str) -> None:
        return None

    def ensure_firewall_rules(
        self,
        security_group_id: str,
        firewall_rules: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return [
            {
                "name": rule["name"],
                "protocol": rule["protocol"],
                "result": "created",
            }
            for rule in firewall_rules
        ]

    def resource_exists(self, resource_type: str, resource_id: str) -> bool:
        return "missing" not in resource_id

    def firewall_rule_exists(
        self,
        security_group_id: str,
        firewall_rule: dict[str, Any],
    ) -> bool:
        return "missing" not in firewall_rule["name"]

    def get_server_fixed_ip(
        self,
        server_id: str,
        network_name: str | None = None,
    ) -> str:
        return "10.0.0.10"

    def get_or_create_floating_ip_for_server(self, server_id: str) -> str:
        return "203.0.113.10"
