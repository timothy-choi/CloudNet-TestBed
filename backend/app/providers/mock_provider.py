import os
import random
import time
from collections import defaultdict
from typing import Any

from app.core.config import cloudnet_simulate_failures
from app.providers.base import BaseProvider


class MockProvider(BaseProvider):
    name = "mock"

    def __init__(self) -> None:
        self.server_statuses: dict[str, str] = {}
        self._fail_streak_remaining: dict[str, int] = defaultdict(int)
        self._refresh_fail_budgets()

    def _refresh_fail_budgets(self) -> None:
        for op in (
            "create_network",
            "create_subnet",
            "create_server",
            "run_ping",
            "send_ssm_command",
        ):
            raw = os.environ.get(f"CLOUDNET_MOCK_{op.upper()}_FAILS", "0")
            try:
                self._fail_streak_remaining[op] = int(raw)
            except ValueError:
                self._fail_streak_remaining[op] = 0

    def refresh_simulation_env(self) -> None:
        """Re-read ``CLOUDNET_MOCK_*_FAILS`` (for tests that change env after init)."""
        self._refresh_fail_budgets()

    def _maybe_simulated_latency(self) -> None:
        if not cloudnet_simulate_failures():
            return
        ms = float(os.environ.get("CLOUDNET_MOCK_LATENCY_MS", "0") or 0)
        if ms > 0:
            time.sleep(ms / 1000.0)

    def _maybe_simulated_random_error(self) -> None:
        if not cloudnet_simulate_failures():
            return
        rate = float(os.environ.get("CLOUDNET_MOCK_RANDOM_FAILURE_RATE", "0") or 0)
        if rate <= 0 or random.random() >= rate:
            return
        err = random.choice(
            [
                "RateLimitExceeded: (simulated)",
                "InternalServerError: (simulated)",
            ]
        )
        raise RuntimeError(err)

    def _before_op(self, op: str) -> None:
        """Streak failures apply always (for tests). Random + latency need simulation flag."""
        n = self._fail_streak_remaining.get(op, 0)
        if n > 0:
            self._fail_streak_remaining[op] = n - 1
            raise RuntimeError("RateLimitExceeded: (simulated streak)")
        self._maybe_simulated_latency()
        self._maybe_simulated_random_error()

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
        self._before_op("create_network")
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
        self._before_op("create_subnet")
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
        self._before_op("create_server")
        server_id = f"mock-server-{name}"
        self.server_statuses[server_id] = "running"
        return {
            "id": server_id,
            "name": name,
            "status": "running",
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
        self.server_statuses[server_id] = "stopped"
        return {"id": server_id, "status": "stopped"}

    def start_server(self, server_id: str) -> dict[str, Any]:
        self.server_statuses[server_id] = "running"
        return {"id": server_id, "status": "running"}

    def delete_resource(
        self,
        resource_type: str,
        resource_id: str,
    ) -> dict[str, Any]:
        return {"id": resource_id, "type": resource_type, "deleted": True}

    def get_server_status(self, server_id: str) -> str:
        return self.server_statuses.get(server_id, "running")

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

    def run_ping(self, source_server_id: str, target_ip: str) -> str:
        self._before_op("run_ping")
        source_status = self.get_server_status(source_server_id)
        if source_status != "running":
            raise RuntimeError(
                f"mock ping failed: source {source_server_id} is {source_status}"
            )
        loss_rate = float(os.environ.get("CLOUDNET_MOCK_PING_LOSS_RATE", "0"))
        if random.random() < loss_rate:
            raise RuntimeError("mock ping failed: simulated packet loss")
        base_ms = float(os.environ.get("CLOUDNET_MOCK_PING_BASE_MS", "18"))
        jitter_ms = float(os.environ.get("CLOUDNET_MOCK_PING_JITTER_MS", "4"))
        lines = []
        for i in range(3):
            t_ms = base_ms + random.random() * jitter_ms + i * 0.15
            lines.append(
                f"64 bytes from {target_ip}: icmp_seq={i + 1} ttl=64 time={t_ms:.2f} ms"
            )
        return "\n".join(lines)

    def send_ssm_command(
        self,
        instance_id: str,
        command: str,
        *,
        timeout_seconds: float = 30.0,
    ) -> dict[str, Any]:
        _ = timeout_seconds
        self._before_op("send_ssm_command")
        if self.get_server_status(instance_id) != "running":
            return {
                "status": "FAILED",
                "stdout": "",
                "stderr": f"mock: instance {instance_id} is not running",
            }
        return {
            "status": "SUCCESS",
            "stdout": f"mock:{command}",
            "stderr": "",
        }
