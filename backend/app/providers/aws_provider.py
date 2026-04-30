from typing import Any, NoReturn

from app.core.config import AWSSettings, get_aws_settings
from app.providers.base import BaseProvider


class AWSProvider(BaseProvider):
    name = "aws"

    def health(self) -> dict[str, Any]:
        settings = get_aws_settings()
        missing = self._missing_settings(settings, require_default_ami=False)
        if missing:
            return {
                "provider": self.name,
                "connected": False,
                "region": settings.region,
                "detail": "Missing AWS environment variables: " + ", ".join(missing),
            }

        try:
            self._client("ec2", settings).describe_availability_zones()
            return {
                "provider": self.name,
                "connected": True,
                "region": settings.region,
            }
        except Exception as exc:
            return {
                "provider": self.name,
                "connected": False,
                "region": settings.region,
                "detail": str(exc),
            }

    def list_images(self) -> list[dict[str, Any]]:
        settings = self._validated_settings(require_default_ami=False)
        if not settings.default_ami_id:
            return []

        return [
            {
                "id": settings.default_ami_id,
                "name": settings.default_ami_id,
                "status": "configured",
            }
        ]

    def list_flavors(self) -> list[dict[str, Any]]:
        return [
            {"id": "t3.micro", "name": "t3.micro", "vcpus": 2, "ram": 1024, "disk": 8},
            {"id": "t3.small", "name": "t3.small", "vcpus": 2, "ram": 2048, "disk": 16},
            {
                "id": "t3.medium",
                "name": "t3.medium",
                "vcpus": 2,
                "ram": 4096,
                "disk": 32,
            },
        ]

    def list_networks(self) -> list[dict[str, Any]]:
        settings = self._validated_settings(require_default_ami=False)
        ec2 = self._client("ec2", settings)
        vpcs = ec2.describe_vpcs().get("Vpcs", [])
        subnets = ec2.describe_subnets().get("Subnets", [])
        networks = [
            {
                "id": vpc.get("VpcId"),
                "cidr": vpc.get("CidrBlock"),
                "state": vpc.get("State"),
                "is_default": vpc.get("IsDefault", False),
            }
            for vpc in vpcs
        ]
        networks.extend(
            {
                "id": subnet.get("SubnetId"),
                "cidr": subnet.get("CidrBlock"),
                "state": subnet.get("State"),
                "is_default": bool(subnet.get("DefaultForAz", False)),
            }
            for subnet in subnets
        )
        return networks

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

    def _client(self, service_name: str, settings: AWSSettings) -> Any:
        import boto3

        return boto3.client(
            service_name,
            region_name=settings.region,
            aws_access_key_id=settings.access_key_id,
            aws_secret_access_key=settings.secret_access_key,
        )

    def _validated_settings(self, require_default_ami: bool = True) -> AWSSettings:
        settings = get_aws_settings()
        missing = self._missing_settings(
            settings,
            require_default_ami=require_default_ami,
        )
        if missing:
            raise RuntimeError(
                "Missing AWS environment variables: " + ", ".join(missing)
            )
        return settings

    def _missing_settings(
        self,
        settings: AWSSettings,
        require_default_ami: bool,
    ) -> list[str]:
        missing = []
        if not settings.region:
            missing.append("AWS_REGION")
        if not settings.access_key_id:
            missing.append("AWS_ACCESS_KEY_ID")
        if not settings.secret_access_key:
            missing.append("AWS_SECRET_ACCESS_KEY")
        if require_default_ami and not settings.default_ami_id:
            missing.append("AWS_DEFAULT_AMI_ID")
        return missing

    def _not_implemented(self) -> NoReturn:
        raise NotImplementedError("AWS provisioning is not implemented yet")
