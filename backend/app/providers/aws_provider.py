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
        try:
            vpcs = ec2.describe_vpcs().get("Vpcs", [])
            subnets = ec2.describe_subnets().get("Subnets", [])
        except self._client_error_class() as exc:
            raise RuntimeError(
                f"AWS network listing failed: {self._error_detail(exc)}"
            ) from exc

        networks = [
            {
                "id": vpc.get("VpcId"),
                "name": self._tag_name(vpc.get("Tags", [])),
                "type": "vpc",
                "cidr": vpc.get("CidrBlock"),
                "state": vpc.get("State"),
                "is_default": vpc.get("IsDefault", False),
            }
            for vpc in vpcs
        ]
        networks.extend(
            {
                "id": subnet.get("SubnetId"),
                "name": self._tag_name(subnet.get("Tags", [])),
                "type": "subnet",
                "cidr": subnet.get("CidrBlock"),
                "state": subnet.get("State"),
                "is_default": bool(subnet.get("DefaultForAz", False)),
                "parent_id": subnet.get("VpcId"),
            }
            for subnet in subnets
        )
        return networks

    def create_network(
        self,
        name: str,
        cidr: str | None = None,
    ) -> dict[str, Any]:
        settings = self._validated_settings(require_default_ami=False)
        ec2 = self._client("ec2", settings)
        vpc_cidr = cidr or "10.0.0.0/16"
        try:
            vpc = ec2.create_vpc(CidrBlock=vpc_cidr)["Vpc"]
            vpc_id = vpc["VpcId"]
            ec2.create_tags(Resources=[vpc_id], Tags=[{"Key": "Name", "Value": name}])
            ec2.get_waiter("vpc_available").wait(VpcIds=[vpc_id])
        except self._client_error_class() as exc:
            raise RuntimeError(
                f"AWS VPC creation failed: {self._error_detail(exc)}"
            ) from exc

        return {
            "id": vpc_id,
            "name": name,
            "cidr": vpc_cidr,
            "state": "available",
        }

    def create_subnet(
        self,
        network_id: str,
        name: str,
        cidr: str,
    ) -> dict[str, Any]:
        settings = self._validated_settings(require_default_ami=False)
        ec2 = self._client("ec2", settings)
        try:
            subnet = ec2.create_subnet(VpcId=network_id, CidrBlock=cidr)["Subnet"]
            subnet_id = subnet["SubnetId"]
            ec2.create_tags(
                Resources=[subnet_id],
                Tags=[{"Key": "Name", "Value": name}],
            )
        except self._client_error_class() as exc:
            raise RuntimeError(
                f"AWS subnet creation failed: {self._error_detail(exc)}"
            ) from exc

        return {
            "id": subnet_id,
            "name": name,
            "cidr": cidr,
            "vpc_id": network_id,
        }

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

    def _client_error_class(self):
        from botocore.exceptions import ClientError

        return ClientError

    def _error_detail(self, exc: Exception) -> str:
        response = getattr(exc, "response", {})
        error = response.get("Error", {}) if isinstance(response, dict) else {}
        message = error.get("Message")
        code = error.get("Code")
        if code and message:
            return f"{code}: {message}"
        return str(exc)

    def _tag_name(self, tags: list[dict[str, str]]) -> str:
        for tag in tags:
            if tag.get("Key") == "Name":
                return tag.get("Value", "")
        return ""
