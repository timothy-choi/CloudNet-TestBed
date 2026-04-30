import sys
from types import SimpleNamespace

from fastapi.testclient import TestClient
from botocore.exceptions import ClientError

from app.main import app
from app.providers.aws_provider import AWSProvider
from app.providers.factory import get_provider


def set_aws_env(monkeypatch) -> None:
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test-secret")
    monkeypatch.setenv("AWS_DEFAULT_AMI_ID", "ami-123")
    monkeypatch.setenv("AWS_DEFAULT_INSTANCE_TYPE", "t3.micro")


class FakeAWS:
    def __init__(self) -> None:
        self.clients: list[tuple[str, dict]] = []
        self.created_vpcs: list[dict] = []
        self.created_subnets: list[dict] = []
        self.deleted_subnets: list[str] = []
        self.deleted_vpcs: list[str] = []
        self.tags: list[dict] = []
        self.waits: list[dict] = []
        self.instances: list[dict] = []
        self.run_instances_params: dict | None = None
        self.ingress_permissions: list[dict] = []
        self.terminated_instances: list[str] = []
        self.deleted_security_groups: list[str] = []
        self.security_groups: list[dict] = []
        self.operations: list[str] = []
        self.vpcs_by_id: dict[str, dict] = {
            "vpc-created": {
                "VpcId": "vpc-created",
                "CidrBlock": "10.20.0.0/16",
                "State": "available",
                "IsDefault": False,
                "Tags": [
                    {"Key": "Name", "Value": "cloudnet-test"},
                    {"Key": "Project", "Value": "CloudNet"},
                    {"Key": "ManagedBy", "Value": "CloudNet"},
                ],
            },
            "vpc-default": {
                "VpcId": "vpc-default",
                "CidrBlock": "172.31.0.0/16",
                "State": "available",
                "IsDefault": True,
                "Tags": [{"Key": "Name", "Value": "default"}],
            },
            "vpc-untagged": {
                "VpcId": "vpc-untagged",
                "CidrBlock": "10.30.0.0/16",
                "State": "available",
                "IsDefault": False,
                "Tags": [{"Key": "Name", "Value": "prod"}],
            },
        }
        self.subnets_by_vpc: dict[str, list[dict]] = {
            "vpc-created": [
                {
                    "SubnetId": "subnet-a",
                    "VpcId": "vpc-created",
                    "CidrBlock": "10.20.1.0/24",
                    "State": "available",
                    "DefaultForAz": False,
                    "Tags": [
                        {"Key": "Name", "Value": "cloudnet-test-subnet-a"},
                        {"Key": "Project", "Value": "CloudNet"},
                    ],
                },
                {
                    "SubnetId": "subnet-b",
                    "VpcId": "vpc-created",
                    "CidrBlock": "10.20.2.0/24",
                    "State": "available",
                    "DefaultForAz": False,
                    "Tags": [{"Key": "Name", "Value": "cloudnet-test-subnet-b"}],
                },
            ],
            "vpc-default": [],
            "vpc-untagged": [],
        }

    def client(self, service_name: str, **kwargs):
        self.clients.append((service_name, kwargs))
        return FakeEC2(self)


class FakeEC2:
    def __init__(self, fake_aws: FakeAWS) -> None:
        self.fake_aws = fake_aws

    def describe_availability_zones(self):
        return {"AvailabilityZones": [{"ZoneName": "us-west-2a"}]}

    def describe_vpcs(self, VpcIds=None):
        if VpcIds is not None:
            return {
                "Vpcs": [
                    self.fake_aws.vpcs_by_id[vpc_id]
                    for vpc_id in VpcIds
                    if vpc_id in self.fake_aws.vpcs_by_id
                ]
            }
        return {
            "Vpcs": [
                {
                    "VpcId": "vpc-1",
                    "CidrBlock": "10.0.0.0/16",
                    "State": "available",
                    "IsDefault": True,
                    "Tags": [{"Key": "Name", "Value": "default"}],
                }
            ]
        }

    def describe_subnets(self, Filters=None, SubnetIds=None):
        if SubnetIds is not None:
            return {
                "Subnets": [
                    subnet
                    for subnets in self.fake_aws.subnets_by_vpc.values()
                    for subnet in subnets
                    if subnet.get("SubnetId") in SubnetIds
                ]
            }
        if Filters:
            vpc_ids = []
            for item in Filters:
                if item.get("Name") == "vpc-id":
                    vpc_ids.extend(item.get("Values", []))
            return {
                "Subnets": [
                    subnet
                    for vpc_id in vpc_ids
                    for subnet in self.fake_aws.subnets_by_vpc.get(vpc_id, [])
                ]
            }
        return {
            "Subnets": [
                {
                    "SubnetId": "subnet-1",
                    "VpcId": "vpc-1",
                    "CidrBlock": "10.0.1.0/24",
                    "State": "available",
                    "DefaultForAz": True,
                    "Tags": [],
                }
            ]
        }

    def create_vpc(self, CidrBlock):
        self.fake_aws.created_vpcs.append({"CidrBlock": CidrBlock})
        return {"Vpc": {"VpcId": "vpc-created", "CidrBlock": CidrBlock}}

    def create_subnet(self, VpcId, CidrBlock):
        self.fake_aws.created_subnets.append({"VpcId": VpcId, "CidrBlock": CidrBlock})
        return {"Subnet": {"SubnetId": "subnet-created", "CidrBlock": CidrBlock}}

    def delete_subnet(self, SubnetId):
        self.fake_aws.deleted_subnets.append(SubnetId)
        self.fake_aws.operations.append(f"delete_subnet:{SubnetId}")

    def delete_vpc(self, VpcId):
        self.fake_aws.deleted_vpcs.append(VpcId)
        self.fake_aws.operations.append(f"delete_vpc:{VpcId}")

    def describe_instances(self, Filters):
        return {"Reservations": [{"Instances": self.fake_aws.instances}]}

    def run_instances(self, **kwargs):
        self.fake_aws.run_instances_params = kwargs
        return {
            "Instances": [
                {
                    "InstanceId": "i-created",
                    "State": {"Name": "pending"},
                    "PrivateIpAddress": "10.20.1.10",
                    "PublicIpAddress": None,
                }
            ]
        }

    def terminate_instances(self, InstanceIds):
        self.fake_aws.terminated_instances.extend(InstanceIds)
        for instance_id in InstanceIds:
            self.fake_aws.operations.append(f"terminate_instance:{instance_id}")

    def describe_security_groups(self, Filters):
        return {"SecurityGroups": self.fake_aws.security_groups}

    def create_security_group(self, GroupName, Description, VpcId):
        group = {
            "GroupId": "sg-created",
            "GroupName": GroupName,
            "VpcId": VpcId,
            "Tags": [
                {"Key": "Name", "Value": GroupName},
                {"Key": "Project", "Value": "CloudNet"},
            ],
        }
        self.fake_aws.security_groups.append(group)
        return {"GroupId": group["GroupId"]}

    def authorize_security_group_ingress(self, GroupId, IpPermissions):
        self.fake_aws.ingress_permissions.extend(IpPermissions)

    def delete_security_group(self, GroupId):
        self.fake_aws.deleted_security_groups.append(GroupId)
        self.fake_aws.operations.append(f"delete_security_group:{GroupId}")

    def create_tags(self, Resources, Tags):
        self.fake_aws.tags.append({"Resources": Resources, "Tags": Tags})

    def get_waiter(self, waiter_name: str):
        return FakeWaiter(self.fake_aws, waiter_name)


class FakeWaiter:
    def __init__(self, fake_aws: FakeAWS, waiter_name: str) -> None:
        self.fake_aws = fake_aws
        self.waiter_name = waiter_name

    def wait(self, **kwargs) -> None:
        self.fake_aws.waits.append({"waiter_name": self.waiter_name, **kwargs})


def mock_boto3(monkeypatch) -> FakeAWS:
    fake_aws = FakeAWS()
    monkeypatch.setitem(sys.modules, "boto3", SimpleNamespace(client=fake_aws.client))
    return fake_aws


def test_aws_health_connected(monkeypatch) -> None:
    set_aws_env(monkeypatch)
    fake_aws = mock_boto3(monkeypatch)

    assert AWSProvider().health() == {
        "provider": "aws",
        "connected": True,
        "region": "us-west-2",
    }
    assert fake_aws.clients[0][0] == "ec2"


def test_aws_health_missing_config_returns_disconnected(monkeypatch) -> None:
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)

    response = AWSProvider().health()

    assert response["provider"] == "aws"
    assert response["connected"] is False
    assert "Missing AWS environment variables" in response["detail"]
    assert "AWS_REGION" in response["detail"]


def test_aws_list_flavors_returns_static_values() -> None:
    assert AWSProvider().list_flavors() == [
        {"id": "t3.micro", "name": "t3.micro", "vcpus": 2, "ram": 1024, "disk": 8},
        {"id": "t3.small", "name": "t3.small", "vcpus": 2, "ram": 2048, "disk": 16},
        {"id": "t3.medium", "name": "t3.medium", "vcpus": 2, "ram": 4096, "disk": 32},
    ]


def test_aws_lists_images_and_networks(monkeypatch) -> None:
    set_aws_env(monkeypatch)
    mock_boto3(monkeypatch)
    provider = AWSProvider()

    assert provider.list_images() == [
        {"id": "ami-123", "name": "ami-123", "status": "configured"}
    ]
    assert provider.list_networks() == [
        {
            "id": "vpc-1",
            "name": "default",
            "type": "vpc",
            "cidr": "10.0.0.0/16",
            "state": "available",
            "is_default": True,
        },
        {
            "id": "subnet-1",
            "name": "",
            "type": "subnet",
            "cidr": "10.0.1.0/24",
            "state": "available",
            "is_default": True,
            "parent_id": "vpc-1",
        },
    ]


def test_provider_networks_route_wraps_flat_aws_networks(monkeypatch) -> None:
    set_aws_env(monkeypatch)
    mock_boto3(monkeypatch)
    monkeypatch.setenv("CLOUDNET_PROVIDER", "aws")

    response = TestClient(app).get("/provider/networks")

    assert response.status_code == 200
    assert response.json() == {
        "networks": [
            {
                "id": "vpc-1",
                "name": "default",
                "type": "vpc",
                "cidr": "10.0.0.0/16",
                "state": "available",
                "is_default": True,
            },
            {
                "id": "subnet-1",
                "name": "",
                "type": "subnet",
                "cidr": "10.0.1.0/24",
                "state": "available",
                "is_default": True,
                "parent_id": "vpc-1",
            },
        ]
    }


def test_provider_networks_post_creates_aws_vpc_and_subnet(monkeypatch) -> None:
    set_aws_env(monkeypatch)
    fake_aws = mock_boto3(monkeypatch)
    monkeypatch.setenv("CLOUDNET_PROVIDER", "aws")

    response = TestClient(app).post(
        "/provider/networks",
        json={
            "name": "cloudnet-test",
            "cidr": "10.20.0.0/16",
            "subnet_cidr": "10.20.1.0/24",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "vpc": {
            "id": "vpc-created",
            "name": "cloudnet-test",
            "cidr": "10.20.0.0/16",
            "state": "available",
        },
        "subnet": {
            "id": "subnet-created",
            "name": "cloudnet-test-subnet",
            "cidr": "10.20.1.0/24",
            "vpc_id": "vpc-created",
        },
    }
    assert fake_aws.created_vpcs == [{"CidrBlock": "10.20.0.0/16"}]
    assert fake_aws.created_subnets == [
        {"VpcId": "vpc-created", "CidrBlock": "10.20.1.0/24"}
    ]


def test_provider_networks_post_rejects_subnet_outside_vpc(monkeypatch) -> None:
    set_aws_env(monkeypatch)
    monkeypatch.setenv("CLOUDNET_PROVIDER", "aws")

    response = TestClient(app).post(
        "/provider/networks",
        json={
            "name": "cloudnet-test",
            "cidr": "10.20.0.0/16",
            "subnet_cidr": "10.21.1.0/24",
        },
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "subnet_cidr must be inside cidr"}


def test_aws_list_images_returns_empty_without_default_ami(monkeypatch) -> None:
    set_aws_env(monkeypatch)
    monkeypatch.delenv("AWS_DEFAULT_AMI_ID", raising=False)

    assert AWSProvider().list_images() == []


def test_aws_creates_vpc_and_subnet(monkeypatch) -> None:
    set_aws_env(monkeypatch)
    fake_aws = mock_boto3(monkeypatch)
    provider = AWSProvider()

    assert provider.create_network("cloudnet-net", None) == {
        "id": "vpc-created",
        "name": "cloudnet-net",
        "cidr": "10.0.0.0/16",
        "state": "available",
    }
    assert provider.create_subnet("vpc-created", "cloudnet-subnet", "10.0.1.0/24") == {
        "id": "subnet-created",
        "name": "cloudnet-subnet",
        "cidr": "10.0.1.0/24",
        "vpc_id": "vpc-created",
    }
    assert fake_aws.created_vpcs == [{"CidrBlock": "10.0.0.0/16"}]
    assert fake_aws.created_subnets == [
        {"VpcId": "vpc-created", "CidrBlock": "10.0.1.0/24"}
    ]
    assert fake_aws.tags == [
        {
            "Resources": ["vpc-created"],
            "Tags": [
                {"Key": "Name", "Value": "cloudnet-net"},
                {"Key": "Project", "Value": "CloudNet"},
                {"Key": "ManagedBy", "Value": "CloudNet"},
            ],
        },
        {
            "Resources": ["subnet-created"],
            "Tags": [
                {"Key": "Name", "Value": "cloudnet-subnet"},
                {"Key": "Project", "Value": "CloudNet"},
                {"Key": "ManagedBy", "Value": "CloudNet"},
            ],
        },
    ]
    assert fake_aws.waits == [
        {"waiter_name": "vpc_available", "VpcIds": ["vpc-created"]}
    ]


def test_aws_create_network_wraps_client_error(monkeypatch) -> None:
    set_aws_env(monkeypatch)

    class FailingEC2:
        def create_vpc(self, CidrBlock):
            raise ClientError(
                {"Error": {"Code": "AuthFailure", "Message": "not allowed"}},
                "CreateVpc",
            )

    provider = AWSProvider()
    monkeypatch.setattr(provider, "_client", lambda service_name, settings: FailingEC2())

    try:
        provider.create_network("cloudnet-net", "10.0.0.0/16")
    except RuntimeError as exc:
        assert str(exc) == "AWS VPC creation failed: AuthFailure: not allowed"
    else:
        raise AssertionError("AWS ClientError was not wrapped")


def test_aws_create_instance_disabled_returns_error(monkeypatch) -> None:
    set_aws_env(monkeypatch)
    monkeypatch.delenv("AWS_ALLOW_CREATE_INSTANCES", raising=False)
    mock_boto3(monkeypatch)

    try:
        AWSProvider().create_server("client-a", "vpc-created", "subnet-a")
    except RuntimeError as exc:
        assert str(exc) == (
            "EC2 instance creation disabled. "
            "Set AWS_ALLOW_CREATE_INSTANCES=true."
        )
    else:
        raise AssertionError("AWS instance creation was not disabled")


def test_aws_create_instance_runs_when_enabled(monkeypatch) -> None:
    set_aws_env(monkeypatch)
    monkeypatch.setenv("AWS_ALLOW_CREATE_INSTANCES", "true")
    monkeypatch.setenv("AWS_KEY_NAME", "cloudnet-key")
    monkeypatch.setenv("AWS_SSH_ALLOWED_CIDR", "203.0.113.10/32")
    fake_aws = mock_boto3(monkeypatch)

    server = AWSProvider().create_server("client-a", "vpc-created", "subnet-a")

    assert server == {
        "id": "i-created",
        "name": "client-a",
        "status": "pending",
        "private_ip": "10.20.1.10",
        "public_ip": None,
        "security_group_id": "sg-created",
    }
    assert fake_aws.run_instances_params["ImageId"] == "ami-123"
    assert fake_aws.run_instances_params["InstanceType"] == "t3.micro"
    assert fake_aws.run_instances_params["SubnetId"] == "subnet-a"
    assert fake_aws.run_instances_params["SecurityGroupIds"] == ["sg-created"]
    assert fake_aws.run_instances_params["KeyName"] == "cloudnet-key"
    assert fake_aws.ingress_permissions == [
        {
            "IpProtocol": "icmp",
            "FromPort": -1,
            "ToPort": -1,
            "UserIdGroupPairs": [{"GroupId": "sg-created"}],
        },
        {
            "IpProtocol": "tcp",
            "FromPort": 22,
            "ToPort": 22,
            "IpRanges": [{"CidrIp": "203.0.113.10/32"}],
        },
    ]


def test_aws_security_group_rules_ignore_duplicates(monkeypatch) -> None:
    set_aws_env(monkeypatch)
    monkeypatch.setenv("AWS_ALLOW_CREATE_INSTANCES", "true")

    class DuplicateRuleEC2(FakeEC2):
        def authorize_security_group_ingress(self, GroupId, IpPermissions):
            raise ClientError(
                {
                    "Error": {
                        "Code": "InvalidPermission.Duplicate",
                        "Message": "already exists",
                    }
                },
                "AuthorizeSecurityGroupIngress",
            )

    fake_aws = FakeAWS()
    monkeypatch.setitem(
        sys.modules,
        "boto3",
        SimpleNamespace(client=lambda service_name, **kwargs: DuplicateRuleEC2(fake_aws)),
    )

    server = AWSProvider().create_server("client-a", "vpc-created", "subnet-a")

    assert server["id"] == "i-created"


def test_provider_networks_delete_removes_subnets_before_vpc(monkeypatch) -> None:
    set_aws_env(monkeypatch)
    fake_aws = mock_boto3(monkeypatch)
    monkeypatch.setenv("CLOUDNET_PROVIDER", "aws")

    response = TestClient(app).delete("/provider/networks/vpc-created")

    assert response.status_code == 200
    assert response.json() == {
        "deleted_vpc": "vpc-created",
        "deleted_subnets": ["subnet-a", "subnet-b"],
        "terminated_instances": [],
        "deleted_security_groups": [],
    }
    assert fake_aws.deleted_subnets == ["subnet-a", "subnet-b"]
    assert fake_aws.deleted_vpcs == ["vpc-created"]


def test_provider_networks_delete_rejects_default_vpc(monkeypatch) -> None:
    set_aws_env(monkeypatch)
    fake_aws = mock_boto3(monkeypatch)
    monkeypatch.setenv("CLOUDNET_PROVIDER", "aws")

    response = TestClient(app).delete("/provider/networks/vpc-default")

    assert response.status_code == 400
    assert response.json() == {"detail": "Refusing to delete default AWS VPC"}
    assert fake_aws.deleted_subnets == []
    assert fake_aws.deleted_vpcs == []


def test_provider_networks_delete_rejects_untagged_vpc(monkeypatch) -> None:
    set_aws_env(monkeypatch)
    fake_aws = mock_boto3(monkeypatch)
    monkeypatch.setenv("CLOUDNET_PROVIDER", "aws")

    response = TestClient(app).delete("/provider/networks/vpc-untagged")

    assert response.status_code == 400
    assert response.json() == {
        "detail": (
            "Refusing to delete VPC vpc-untagged: "
            "resource is not tagged as CloudNet-managed"
        )
    }
    assert fake_aws.deleted_subnets == []
    assert fake_aws.deleted_vpcs == []


def test_provider_networks_delete_rejects_vpc_with_instances(monkeypatch) -> None:
    set_aws_env(monkeypatch)
    fake_aws = mock_boto3(monkeypatch)
    fake_aws.instances = [{"InstanceId": "i-prod"}]
    monkeypatch.setenv("CLOUDNET_PROVIDER", "aws")

    response = TestClient(app).delete("/provider/networks/vpc-created")

    assert response.status_code == 400
    assert response.json() == {
        "detail": "Refusing to delete AWS VPC with non-CloudNet resources: i-prod"
    }
    assert fake_aws.deleted_subnets == []
    assert fake_aws.deleted_vpcs == []


def test_provider_networks_delete_terminates_cloudnet_instances(monkeypatch) -> None:
    set_aws_env(monkeypatch)
    fake_aws = mock_boto3(monkeypatch)
    fake_aws.instances = [
        {
            "InstanceId": "i-cloudnet",
            "Tags": [{"Key": "Project", "Value": "CloudNet"}],
        }
    ]
    fake_aws.security_groups = [
        {
            "GroupId": "sg-created",
            "GroupName": "cloudnet-sg",
            "Tags": [{"Key": "Project", "Value": "CloudNet"}],
        }
    ]
    monkeypatch.setenv("CLOUDNET_PROVIDER", "aws")

    response = TestClient(app).delete("/provider/networks/vpc-created")

    assert response.status_code == 200
    assert response.json() == {
        "deleted_vpc": "vpc-created",
        "deleted_subnets": ["subnet-a", "subnet-b"],
        "terminated_instances": ["i-cloudnet"],
        "deleted_security_groups": ["sg-created"],
    }
    assert fake_aws.terminated_instances == ["i-cloudnet"]
    assert fake_aws.waits == [
        {"waiter_name": "instance_terminated", "InstanceIds": ["i-cloudnet"]}
    ]
    assert fake_aws.deleted_subnets == ["subnet-a", "subnet-b"]
    assert fake_aws.deleted_security_groups == ["sg-created"]
    assert fake_aws.deleted_vpcs == ["vpc-created"]
    assert fake_aws.operations == [
        "terminate_instance:i-cloudnet",
        "delete_subnet:subnet-a",
        "delete_subnet:subnet-b",
        "delete_security_group:sg-created",
        "delete_vpc:vpc-created",
    ]


def test_aws_unimplemented_compute_methods_are_not_implemented() -> None:
    provider = AWSProvider()

    for action in [
        lambda: provider.create_router("router-1"),
        lambda: provider.stop_server("i-123"),
        lambda: provider.start_server("i-123"),
        lambda: provider.delete_resource("instance", "i-123"),
    ]:
        try:
            action()
        except NotImplementedError as exc:
            assert str(exc) == "AWS provisioning is not implemented yet"
        else:
            raise AssertionError("AWS provisioning method did not raise")


def test_factory_returns_aws_provider(monkeypatch) -> None:
    monkeypatch.setenv("CLOUDNET_PROVIDER", "aws")

    assert isinstance(get_provider(), AWSProvider)
