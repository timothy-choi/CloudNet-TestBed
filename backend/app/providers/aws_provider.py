from typing import Any

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
            identity = self._client("sts", settings).get_caller_identity()
            return {
                "provider": self.name,
                "connected": True,
                "region": settings.region,
                "account": identity.get("Account"),
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

        response = self._client("ec2", settings).describe_images(
            ImageIds=[settings.default_ami_id]
        )
        return [
            {
                "id": image.get("ImageId"),
                "name": image.get("Name"),
                "status": image.get("State"),
            }
            for image in response.get("Images", [])
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
                "name": self._tag_name(vpc.get("Tags", [])),
                "type": "vpc",
                "cidr": vpc.get("CidrBlock"),
                "state": vpc.get("State"),
            }
            for vpc in vpcs
        ]
        networks.extend(
            {
                "id": subnet.get("SubnetId"),
                "name": self._tag_name(subnet.get("Tags", [])),
                "type": "subnet",
                "vpc_id": subnet.get("VpcId"),
                "cidr": subnet.get("CidrBlock"),
                "state": subnet.get("State"),
                "availability_zone": subnet.get("AvailabilityZone"),
            }
            for subnet in subnets
        )
        return networks

    def create_network(
        self,
        name: str,
        cidr: str | None = None,
    ) -> dict[str, Any]:
        settings = self._validated_settings()
        ec2 = self._client("ec2", settings)
        vpc = ec2.create_vpc(CidrBlock=cidr or "10.0.0.0/16")["Vpc"]
        vpc_id = vpc["VpcId"]
        ec2.create_tags(Resources=[vpc_id], Tags=[{"Key": "Name", "Value": name}])
        try:
            ec2.modify_vpc_attribute(
                VpcId=vpc_id,
                EnableDnsSupport={"Value": True},
            )
            ec2.modify_vpc_attribute(
                VpcId=vpc_id,
                EnableDnsHostnames={"Value": True},
            )
        except Exception:
            pass
        return {
            "id": vpc_id,
            "name": name,
            "status": vpc.get("State"),
            "cidr": vpc.get("CidrBlock", cidr),
        }

    def create_subnet(
        self,
        network_id: str,
        name: str,
        cidr: str,
    ) -> dict[str, Any]:
        settings = self._validated_settings()
        ec2 = self._client("ec2", settings)
        subnet = ec2.create_subnet(VpcId=network_id, CidrBlock=cidr)["Subnet"]
        subnet_id = subnet["SubnetId"]
        ec2.create_tags(Resources=[subnet_id], Tags=[{"Key": "Name", "Value": name}])
        return {
            "id": subnet_id,
            "name": name,
            "cidr": subnet.get("CidrBlock", cidr),
            "network_id": network_id,
            "vpc_id": network_id,
        }

    def create_router(
        self,
        name: str,
        external_network_id: str | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError("AWS routing is not implemented yet")

    def create_server(self, name: str, network_id: str) -> dict[str, Any]:
        settings = self._validated_settings()
        ec2 = self._client("ec2", settings)
        subnet = ec2.describe_subnets(SubnetIds=[network_id])["Subnets"][0]
        security_group_id = self._get_or_create_security_group(
            ec2=ec2,
            vpc_id=subnet["VpcId"],
            settings=settings,
        )
        params: dict[str, Any] = {
            "ImageId": settings.default_ami_id,
            "InstanceType": settings.default_instance_type,
            "MinCount": 1,
            "MaxCount": 1,
            "SubnetId": network_id,
            "SecurityGroupIds": [security_group_id],
            "TagSpecifications": [
                {
                    "ResourceType": "instance",
                    "Tags": [{"Key": "Name", "Value": name}],
                }
            ],
        }
        if settings.key_name:
            params["KeyName"] = settings.key_name

        instance = ec2.run_instances(**params)["Instances"][0]
        return self._server_to_dict(instance, name)

    def stop_server(self, server_id: str) -> dict[str, Any]:
        settings = self._validated_settings(require_default_ami=False)
        self._client("ec2", settings).stop_instances(InstanceIds=[server_id])
        return {"id": server_id, "status": "stopping"}

    def start_server(self, server_id: str) -> dict[str, Any]:
        settings = self._validated_settings(require_default_ami=False)
        self._client("ec2", settings).start_instances(InstanceIds=[server_id])
        return {"id": server_id, "status": "pending"}

    def delete_resource(
        self,
        resource_type: str,
        resource_id: str,
    ) -> dict[str, Any]:
        raise NotImplementedError("AWS resource deletion is not implemented yet")

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

    def _get_or_create_security_group(
        self,
        ec2: Any,
        vpc_id: str,
        settings: AWSSettings,
    ) -> str:
        group_name = "cloudnet-testbed"
        groups = ec2.describe_security_groups(
            Filters=[
                {"Name": "vpc-id", "Values": [vpc_id]},
                {"Name": "group-name", "Values": [group_name]},
            ]
        ).get("SecurityGroups", [])
        if groups:
            security_group_id = groups[0]["GroupId"]
        else:
            security_group_id = ec2.create_security_group(
                GroupName=group_name,
                Description="CloudNet TestBed SSH and ICMP",
                VpcId=vpc_id,
            )["GroupId"]

        self._ensure_security_group_rules(ec2, security_group_id, settings.ssh_cidr)
        return security_group_id

    def _ensure_security_group_rules(
        self,
        ec2: Any,
        security_group_id: str,
        ssh_cidr: str,
    ) -> None:
        permissions = [
            {
                "IpProtocol": "tcp",
                "FromPort": 22,
                "ToPort": 22,
                "IpRanges": [{"CidrIp": ssh_cidr}],
            },
            {
                "IpProtocol": "icmp",
                "FromPort": -1,
                "ToPort": -1,
                "UserIdGroupPairs": [{"GroupId": security_group_id}],
            },
        ]
        for permission in permissions:
            try:
                ec2.authorize_security_group_ingress(
                    GroupId=security_group_id,
                    IpPermissions=[permission],
                )
            except Exception as exc:
                if "InvalidPermission.Duplicate" not in str(exc):
                    raise

    def _server_to_dict(self, instance: dict[str, Any], name: str) -> dict[str, Any]:
        return {
            "id": instance.get("InstanceId"),
            "name": name,
            "status": instance.get("State", {}).get("Name"),
            "addresses": {
                "private": [
                    {
                        "addr": instance.get("PrivateIpAddress"),
                        "version": 4,
                        "OS-EXT-IPS:type": "fixed",
                    }
                ]
                if instance.get("PrivateIpAddress")
                else []
            },
        }

    def _tag_name(self, tags: list[dict[str, str]]) -> str | None:
        for tag in tags:
            if tag.get("Key") == "Name":
                return tag.get("Value")
        return None
