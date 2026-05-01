import os
import time
from typing import Any, NoReturn

from app.core.config import AWSSettings, get_aws_settings
from app.providers.base import BaseProvider


class AWSProvider(BaseProvider):
    name = "aws"
    cloudnet_tags = [
        {"Key": "Project", "Value": "CloudNet"},
        {"Key": "ManagedBy", "Value": "CloudNet"},
    ]

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
            ec2.create_tags(
                Resources=[vpc_id],
                Tags=self._resource_tags(name),
            )
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
                Tags=self._resource_tags(name),
            )
            ec2.modify_subnet_attribute(
                SubnetId=subnet_id,
                MapPublicIpOnLaunch={"Value": True},
            )
            internet_gateways = ec2.describe_internet_gateways(
                Filters=[{"Name": "attachment.vpc-id", "Values": [network_id]}]
            ).get("InternetGateways", [])
            if internet_gateways:
                internet_gateway_id = internet_gateways[0]["InternetGatewayId"]
            else:
                internet_gateway = ec2.create_internet_gateway()["InternetGateway"]
                internet_gateway_id = internet_gateway["InternetGatewayId"]
                ec2.create_tags(
                    Resources=[internet_gateway_id],
                    Tags=self._resource_tags(f"{name}-igw"),
                )
                ec2.attach_internet_gateway(
                    InternetGatewayId=internet_gateway_id,
                    VpcId=network_id,
                )
            route_table = ec2.create_route_table(VpcId=network_id)["RouteTable"]
            route_table_id = route_table["RouteTableId"]
            ec2.create_tags(
                Resources=[route_table_id],
                Tags=self._resource_tags(f"{name}-rt"),
            )
            ec2.create_route(
                RouteTableId=route_table_id,
                DestinationCidrBlock="0.0.0.0/0",
                GatewayId=internet_gateway_id,
            )
            route_table_association_id = ec2.associate_route_table(
                RouteTableId=route_table_id,
                SubnetId=subnet_id,
            ).get("AssociationId")
        except self._client_error_class() as exc:
            raise RuntimeError(
                f"AWS subnet creation failed: {self._error_detail(exc)}"
            ) from exc

        return {
            "id": subnet_id,
            "name": name,
            "cidr": cidr,
            "vpc_id": network_id,
            "internet_gateway_id": internet_gateway_id,
            "route_table_id": route_table_id,
            "route_table_association_id": route_table_association_id,
        }

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
        settings = self._validated_settings(require_default_ami=True)
        if not settings.allow_create_instances:
            raise RuntimeError(
                "EC2 instance creation disabled. "
                "Set AWS_ALLOW_CREATE_INSTANCES=true."
            )

        ec2 = self._client("ec2", settings)
        try:
            target_subnet_id = subnet_id or self._first_subnet_id(ec2, network_id)
            subnet = ec2.describe_subnets(SubnetIds=[target_subnet_id])["Subnets"][0]
            vpc_id = subnet["VpcId"]
            security_group_id = self._get_or_create_security_group(
                ec2=ec2,
                vpc_id=vpc_id,
                settings=settings,
            )
            params: dict[str, Any] = {
                "ImageId": settings.default_ami_id,
                "InstanceType": settings.default_instance_type,
                "MinCount": 1,
                "MaxCount": 1,
                "NetworkInterfaces": [
                    {
                        "DeviceIndex": 0,
                        "SubnetId": target_subnet_id,
                        "Groups": [security_group_id],
                        "AssociatePublicIpAddress": True,
                    }
                ],
                "TagSpecifications": [
                    {
                        "ResourceType": "instance",
                        "Tags": self._resource_tags(name),
                    }
                ],
            }
            if settings.key_name:
                params["KeyName"] = settings.key_name
            instance_profile_name = os.getenv("AWS_INSTANCE_PROFILE_NAME")
            if instance_profile_name:
                params["IamInstanceProfile"] = {"Name": instance_profile_name}

            instance = ec2.run_instances(**params)["Instances"][0]
            instance_id = instance["InstanceId"]
            ec2.get_waiter("instance_exists").wait(InstanceIds=[instance_id])
            ec2.get_waiter("instance_running").wait(InstanceIds=[instance_id])
            instance = self._describe_instance_with_retry(
                ec2=ec2,
                instance_id=instance_id,
            )
            public_ip = instance.get("PublicIpAddress") or self._wait_for_public_ip(
                ec2=ec2,
                instance_id=instance_id,
            )
        except self._client_error_class() as exc:
            raise RuntimeError(
                f"AWS instance creation failed: {self._error_detail(exc)}"
            ) from exc

        return {
            "id": instance.get("InstanceId"),
            "name": name,
            "status": instance.get("State", {}).get("Name"),
            "private_ip": instance.get("PrivateIpAddress"),
            "public_ip": public_ip,
            "security_group_id": security_group_id,
        }

    def stop_server(self, server_id: str) -> dict[str, Any]:
        settings = self._validated_settings(require_default_ami=False)
        ec2 = self._client("ec2", settings)
        try:
            ec2.stop_instances(InstanceIds=[server_id])
            return {"id": server_id, "status": self.get_server_status(server_id)}
        except self._client_error_class() as exc:
            raise RuntimeError(
                f"AWS instance stop failed: {self._error_detail(exc)}"
            ) from exc

    def start_server(self, server_id: str) -> dict[str, Any]:
        settings = self._validated_settings(require_default_ami=False)
        ec2 = self._client("ec2", settings)
        try:
            ec2.start_instances(InstanceIds=[server_id])
            return {"id": server_id, "status": self.get_server_status(server_id)}
        except self._client_error_class() as exc:
            raise RuntimeError(
                f"AWS instance start failed: {self._error_detail(exc)}"
            ) from exc

    def delete_resource(
        self,
        resource_type: str,
        resource_id: str,
    ) -> dict[str, Any]:
        self._not_implemented()

    def delete_subnet(self, subnet_id: str) -> dict[str, Any]:
        settings = self._validated_settings(require_default_ami=False)
        ec2 = self._client("ec2", settings)
        try:
            ec2.delete_subnet(SubnetId=subnet_id)
        except self._client_error_class() as exc:
            raise RuntimeError(
                f"AWS subnet deletion failed: {self._error_detail(exc)}"
            ) from exc
        return {"id": subnet_id, "deleted": True}

    def delete_network(self, vpc_id: str) -> dict[str, Any]:
        settings = self._validated_settings(require_default_ami=False)
        ec2 = self._client("ec2", settings)
        try:
            vpc = self._get_vpc(ec2, vpc_id)
            self._validate_vpc_cleanup_allowed(vpc)
            instance_ids = self._cloudnet_instance_ids_for_vpc(ec2, vpc_id)
            if instance_ids:
                ec2.terminate_instances(InstanceIds=instance_ids)
                ec2.get_waiter("instance_terminated").wait(InstanceIds=instance_ids)
            deleted_security_groups = self._delete_cloudnet_security_groups(ec2, vpc_id)
            deleted_route_tables = self._delete_cloudnet_route_tables(ec2, vpc_id)
            deleted_internet_gateways = self._delete_cloudnet_internet_gateways(
                ec2,
                vpc_id,
            )
            subnets = self._subnets_for_vpc(ec2, vpc_id)
            for subnet in subnets:
                self._validate_cloudnet_resource(
                    tags=subnet.get("Tags", []),
                    resource_id=str(subnet.get("SubnetId")),
                    resource_type="subnet",
                )

            deleted_subnets = []
            for subnet in subnets:
                subnet_id = str(subnet["SubnetId"])
                self.delete_subnet(subnet_id)
                deleted_subnets.append(subnet_id)

            ec2.delete_vpc(VpcId=vpc_id)
        except self._client_error_class() as exc:
            raise RuntimeError(
                f"AWS VPC deletion failed: {self._error_detail(exc)}"
            ) from exc

        return {
            "deleted_vpc": vpc_id,
            "deleted_subnets": deleted_subnets,
            "terminated_instances": instance_ids,
            "deleted_security_groups": deleted_security_groups,
            "deleted_route_tables": deleted_route_tables,
            "deleted_internet_gateways": deleted_internet_gateways,
        }

    def max_instances_per_deploy(self) -> int:
        return get_aws_settings().max_instances_per_deploy

    def get_server_status(self, server_id: str) -> str:
        settings = self._validated_settings(require_default_ami=False)
        ec2 = self._client("ec2", settings)
        try:
            instance = self._describe_instance_with_retry(
                ec2=ec2,
                instance_id=server_id,
            )
        except self._client_error_class() as exc:
            raise RuntimeError(
                f"AWS instance status lookup failed: {self._error_detail(exc)}"
            ) from exc
        return str(instance.get("State", {}).get("Name", "unknown"))

    def wait_for_server_running(self, server_id: str) -> None:
        settings = self._validated_settings(require_default_ami=False)
        ec2 = self._client("ec2", settings)
        try:
            ec2.get_waiter("instance_running").wait(InstanceIds=[server_id])
        except self._client_error_class() as exc:
            raise RuntimeError(
                f"AWS instance wait failed: {self._error_detail(exc)}"
            ) from exc

    def ensure_firewall_rules(
        self,
        security_group_id: str,
        firewall_rules: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        settings = self._validated_settings(require_default_ami=False)
        ec2 = self._client("ec2", settings)
        results = []
        for rule in firewall_rules:
            permission = self._firewall_rule_to_ip_permission(
                security_group_id=security_group_id,
                rule=rule,
            )
            result = self._ensure_security_group_permission(
                ec2=ec2,
                security_group_id=security_group_id,
                permission=permission,
            )
            results.append(
                {
                    "name": rule["name"],
                    "protocol": rule["protocol"],
                    "result": result,
                }
            )
        return results

    def get_server_fixed_ip(
        self,
        server_id: str,
        network_name: str | None = None,
    ) -> str:
        settings = self._validated_settings(require_default_ami=False)
        ec2 = self._client("ec2", settings)
        try:
            reservations = ec2.describe_instances(InstanceIds=[server_id]).get(
                "Reservations",
                [],
            )
        except self._client_error_class() as exc:
            raise RuntimeError(
                f"AWS instance lookup failed: {self._error_detail(exc)}"
            ) from exc

        for reservation in reservations:
            for instance in reservation.get("Instances", []):
                private_ip = instance.get("PrivateIpAddress")
                if private_ip:
                    return str(private_ip)

        raise RuntimeError(f"No private IP found for AWS instance {server_id}")

    def run_ping(self, source_server_id: str, target_ip: str) -> str:
        settings = self._validated_settings(require_default_ami=False)
        ssm = self._client("ssm", settings)
        command = f"ping -c 3 {target_ip}"
        try:
            command_response = ssm.send_command(
                InstanceIds=[source_server_id],
                DocumentName="AWS-RunShellScript",
                Parameters={"commands": [command]},
            )
            command_id = command_response["Command"]["CommandId"]
            invocation = self._poll_ssm_invocation(
                ssm=ssm,
                command_id=command_id,
                instance_id=source_server_id,
            )
        except self._client_error_class() as exc:
            raise RuntimeError(
                "AWS SSM ping failed. Ensure instances have an IAM role with "
                "AmazonSSMManagedInstanceCore and an AMI with SSM Agent installed: "
                + self._error_detail(exc)
            ) from exc

        status = invocation.get("Status")
        output = "\n".join(
            part
            for part in [
                invocation.get("StandardOutputContent", "").strip(),
                invocation.get("StandardErrorContent", "").strip(),
            ]
            if part
        )
        if status != "Success":
            raise RuntimeError(
                f"AWS SSM ping failed with status {status}: "
                + (output or "no command output")
            )
        return output

    def _client(self, service_name: str, settings: AWSSettings) -> Any:
        import boto3

        return boto3.client(
            service_name,
            region_name=settings.region,
            aws_access_key_id=settings.access_key_id,
            aws_secret_access_key=settings.secret_access_key,
        )

    def _poll_ssm_invocation(
        self,
        ssm: Any,
        command_id: str,
        instance_id: str,
        max_attempts: int = 20,
        delay_seconds: float = 1.0,
    ) -> dict[str, Any]:
        pending_statuses = {"Pending", "InProgress", "Delayed"}
        for attempt in range(max_attempts):
            try:
                invocation = ssm.get_command_invocation(
                    CommandId=command_id,
                    InstanceId=instance_id,
                )
            except self._client_error_class() as exc:
                if "InvocationDoesNotExist" not in self._error_detail(exc):
                    raise
                if attempt == max_attempts - 1:
                    raise
                time.sleep(delay_seconds)
                continue

            if invocation.get("Status") not in pending_statuses:
                return invocation
            time.sleep(delay_seconds)

        raise RuntimeError("AWS SSM ping timed out waiting for command invocation")

    def _describe_instance_with_retry(
        self,
        ec2: Any,
        instance_id: str,
        max_attempts: int = 10,
        delay_seconds: float = 3.0,
    ) -> dict[str, Any]:
        for attempt in range(max_attempts):
            try:
                reservations = ec2.describe_instances(InstanceIds=[instance_id]).get(
                    "Reservations",
                    [],
                )
            except self._client_error_class() as exc:
                if not self._is_invalid_instance_not_found(exc):
                    raise
                if attempt == max_attempts - 1:
                    raise
                time.sleep(delay_seconds)
                continue

            for reservation in reservations:
                for instance in reservation.get("Instances", []):
                    if instance.get("InstanceId") == instance_id:
                        return instance
            if attempt == max_attempts - 1:
                break
            time.sleep(delay_seconds)

        raise RuntimeError(f"AWS instance not found after creation: {instance_id}")

    def _wait_for_public_ip(
        self,
        ec2: Any,
        instance_id: str,
        max_attempts: int = 60,
        delay_seconds: float = 1.0,
    ) -> str | None:
        for _ in range(max_attempts):
            try:
                instance = self._describe_instance_with_retry(
                    ec2=ec2,
                    instance_id=instance_id,
                    max_attempts=1,
                    delay_seconds=delay_seconds,
                )
            except self._client_error_class() as exc:
                if not self._is_invalid_instance_not_found(exc):
                    raise
            except RuntimeError:
                pass
            else:
                public_ip = instance.get("PublicIpAddress")
                if public_ip:
                    return str(public_ip)
            time.sleep(delay_seconds)
        return None

    def _is_invalid_instance_not_found(self, exc: Exception) -> bool:
        response = getattr(exc, "response", {})
        error = response.get("Error", {}) if isinstance(response, dict) else {}
        return error.get("Code") == "InvalidInstanceID.NotFound"

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

    def _resource_tags(self, name: str) -> list[dict[str, str]]:
        return [{"Key": "Name", "Value": name}, *self.cloudnet_tags]

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

    def _tag_value(self, tags: list[dict[str, str]], key: str) -> str:
        for tag in tags:
            if tag.get("Key") == key:
                return tag.get("Value", "")
        return ""

    def _is_cloudnet_resource(self, tags: list[dict[str, str]]) -> bool:
        return (
            self._tag_value(tags, "Project") == "CloudNet"
            or self._tag_name(tags).startswith("cloudnet-")
        )

    def _get_vpc(self, ec2: Any, vpc_id: str) -> dict[str, Any]:
        vpcs = ec2.describe_vpcs(VpcIds=[vpc_id]).get("Vpcs", [])
        if not vpcs:
            raise RuntimeError(f"AWS VPC not found: {vpc_id}")
        return vpcs[0]

    def _subnets_for_vpc(self, ec2: Any, vpc_id: str) -> list[dict[str, Any]]:
        return ec2.describe_subnets(
            Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
        ).get("Subnets", [])

    def _validate_vpc_cleanup_allowed(self, vpc: dict[str, Any]) -> None:
        vpc_id = str(vpc.get("VpcId"))
        if vpc.get("IsDefault"):
            raise RuntimeError("Refusing to delete default AWS VPC")

        self._validate_cloudnet_resource(
            tags=vpc.get("Tags", []),
            resource_id=vpc_id,
            resource_type="VPC",
        )

    def _validate_cloudnet_resource(
        self,
        tags: list[dict[str, str]],
        resource_id: str,
        resource_type: str,
    ) -> None:
        if not self._is_cloudnet_resource(tags):
            raise RuntimeError(
                f"Refusing to delete {resource_type} {resource_id}: "
                "resource is not tagged as CloudNet-managed"
            )

    def _instances_for_vpc(self, ec2: Any, vpc_id: str) -> list[dict[str, Any]]:
        reservations = ec2.describe_instances(
            Filters=[
                {"Name": "vpc-id", "Values": [vpc_id]},
                {
                    "Name": "instance-state-name",
                    "Values": ["pending", "running", "stopping", "stopped"],
                },
            ]
        ).get("Reservations", [])
        return [
            instance
            for reservation in reservations
            for instance in reservation.get("Instances", [])
        ]

    def _cloudnet_instance_ids_for_vpc(self, ec2: Any, vpc_id: str) -> list[str]:
        instances = self._instances_for_vpc(ec2, vpc_id)
        non_cloudnet_instances = [
            str(instance.get("InstanceId"))
            for instance in instances
            if not self._is_cloudnet_resource(instance.get("Tags", []))
        ]
        if non_cloudnet_instances:
            raise RuntimeError(
                "Refusing to delete AWS VPC with non-CloudNet resources: "
                + ", ".join(non_cloudnet_instances)
            )
        return [str(instance["InstanceId"]) for instance in instances]

    def _first_subnet_id(self, ec2: Any, vpc_id: str) -> str:
        subnets = self._subnets_for_vpc(ec2, vpc_id)
        if not subnets:
            raise RuntimeError(f"No AWS subnet found in VPC {vpc_id}")
        return str(subnets[0]["SubnetId"])

    def _get_or_create_security_group(
        self,
        ec2: Any,
        vpc_id: str,
        settings: AWSSettings,
    ) -> str:
        group_name = "cloudnet-sg"
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
                Description="CloudNet TestBed security group",
                VpcId=vpc_id,
            )["GroupId"]
            ec2.create_tags(
                Resources=[security_group_id],
                Tags=self._resource_tags(group_name),
            )

        self._ensure_security_group_rules(
            ec2=ec2,
            security_group_id=security_group_id,
            ssh_allowed_cidr=settings.ssh_allowed_cidr,
        )
        return str(security_group_id)

    def _ensure_security_group_rules(
        self,
        ec2: Any,
        security_group_id: str,
        ssh_allowed_cidr: str | None,
    ) -> None:
        permissions = [
            {
                "IpProtocol": "icmp",
                "FromPort": -1,
                "ToPort": -1,
                "UserIdGroupPairs": [{"GroupId": security_group_id}],
            }
        ]
        if ssh_allowed_cidr:
            permissions.append(
                {
                    "IpProtocol": "tcp",
                    "FromPort": 22,
                    "ToPort": 22,
                    "IpRanges": [{"CidrIp": ssh_allowed_cidr}],
                }
            )

        for permission in permissions:
            self._ensure_security_group_permission(
                ec2=ec2,
                security_group_id=security_group_id,
                permission=permission,
            )

    def _firewall_rule_to_ip_permission(
        self,
        security_group_id: str,
        rule: dict[str, Any],
    ) -> dict[str, Any]:
        protocol = rule["protocol"]
        if protocol == "icmp":
            return {
                "IpProtocol": "icmp",
                "FromPort": -1,
                "ToPort": -1,
                "UserIdGroupPairs": [{"GroupId": security_group_id}],
            }
        if protocol == "tcp":
            from_port = 0
            to_port = 65535
            if rule.get("port") is not None:
                from_port = int(rule["port"])
                to_port = from_port
            return {
                "IpProtocol": "tcp",
                "FromPort": from_port,
                "ToPort": to_port,
                "UserIdGroupPairs": [{"GroupId": security_group_id}],
            }
        raise RuntimeError(f"unsupported firewall protocol: {protocol}")

    def _ensure_security_group_permission(
        self,
        ec2: Any,
        security_group_id: str,
        permission: dict[str, Any],
    ) -> str:
        try:
            ec2.authorize_security_group_ingress(
                GroupId=security_group_id,
                IpPermissions=[permission],
            )
            return "created"
        except self._client_error_class() as exc:
            if "InvalidPermission.Duplicate" in self._error_detail(exc):
                return "skipped"
            raise

    def _delete_cloudnet_security_groups(self, ec2: Any, vpc_id: str) -> list[str]:
        groups = ec2.describe_security_groups(
            Filters=[
                {"Name": "vpc-id", "Values": [vpc_id]},
                {"Name": "group-name", "Values": ["cloudnet-sg"]},
            ]
        ).get("SecurityGroups", [])
        deleted_security_groups = []
        for group in groups:
            group_id = str(group.get("GroupId"))
            self._validate_cloudnet_resource(
                tags=group.get("Tags", []),
                resource_id=group_id,
                resource_type="security group",
            )
            ec2.delete_security_group(GroupId=group_id)
            deleted_security_groups.append(group_id)
        return deleted_security_groups

    def _delete_cloudnet_route_tables(self, ec2: Any, vpc_id: str) -> list[str]:
        route_tables = ec2.describe_route_tables(
            Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
        ).get("RouteTables", [])
        deleted_route_tables = []
        for route_table in route_tables:
            route_table_id = str(route_table.get("RouteTableId"))
            if not self._is_cloudnet_resource(route_table.get("Tags", [])):
                continue
            for association in route_table.get("Associations", []):
                if association.get("Main"):
                    continue
                association_id = association.get("RouteTableAssociationId")
                if association_id:
                    ec2.disassociate_route_table(AssociationId=association_id)
            ec2.delete_route_table(RouteTableId=route_table_id)
            deleted_route_tables.append(route_table_id)
        return deleted_route_tables

    def _delete_cloudnet_internet_gateways(self, ec2: Any, vpc_id: str) -> list[str]:
        internet_gateways = ec2.describe_internet_gateways(
            Filters=[{"Name": "attachment.vpc-id", "Values": [vpc_id]}]
        ).get("InternetGateways", [])
        deleted_internet_gateways = []
        for internet_gateway in internet_gateways:
            internet_gateway_id = str(internet_gateway.get("InternetGatewayId"))
            if not self._is_cloudnet_resource(internet_gateway.get("Tags", [])):
                continue
            ec2.detach_internet_gateway(
                InternetGatewayId=internet_gateway_id,
                VpcId=vpc_id,
            )
            ec2.delete_internet_gateway(InternetGatewayId=internet_gateway_id)
            deleted_internet_gateways.append(internet_gateway_id)
        return deleted_internet_gateways
