import sys
from types import SimpleNamespace

from app.providers.aws_provider import AWSProvider
from app.providers.factory import get_provider


def set_aws_env(monkeypatch) -> None:
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test-secret")
    monkeypatch.setenv("AWS_KEY_NAME", "cloudnet-key")
    monkeypatch.setenv("AWS_DEFAULT_AMI_ID", "ami-123")
    monkeypatch.setenv("AWS_DEFAULT_INSTANCE_TYPE", "t3.micro")
    monkeypatch.setenv("AWS_SSH_CIDR", "203.0.113.10/32")


class FakeAWS:
    def __init__(self) -> None:
        self.clients: list[tuple[str, dict]] = []
        self.created_vpc_tags: list[dict] = []
        self.created_subnet_tags: list[dict] = []
        self.run_instances_params: dict | None = None
        self.ingress_rules: list[dict] = []

    def client(self, service_name: str, **kwargs):
        self.clients.append((service_name, kwargs))
        if service_name == "sts":
            return FakeSTS()
        return FakeEC2(self)


class FakeSTS:
    def get_caller_identity(self) -> dict[str, str]:
        return {"Account": "123456789012"}


class FakeEC2:
    def __init__(self, fake_aws: FakeAWS) -> None:
        self.fake_aws = fake_aws

    def describe_images(self, ImageIds):
        return {
            "Images": [
                {"ImageId": ImageIds[0], "Name": "test-ami", "State": "available"}
            ]
        }

    def describe_vpcs(self):
        return {
            "Vpcs": [
                {
                    "VpcId": "vpc-1",
                    "CidrBlock": "10.0.0.0/16",
                    "State": "available",
                    "Tags": [{"Key": "Name", "Value": "cloudnet-vpc"}],
                }
            ]
        }

    def describe_subnets(self, SubnetIds=None):
        return {
            "Subnets": [
                {
                    "SubnetId": (SubnetIds or ["subnet-1"])[0],
                    "VpcId": "vpc-1",
                    "CidrBlock": "10.0.1.0/24",
                    "State": "available",
                    "AvailabilityZone": "us-west-2a",
                    "Tags": [{"Key": "Name", "Value": "cloudnet-subnet"}],
                }
            ]
        }

    def create_vpc(self, CidrBlock):
        return {"Vpc": {"VpcId": "vpc-created", "CidrBlock": CidrBlock, "State": "pending"}}

    def create_subnet(self, VpcId, CidrBlock):
        return {
            "Subnet": {
                "SubnetId": "subnet-created",
                "VpcId": VpcId,
                "CidrBlock": CidrBlock,
            }
        }

    def create_tags(self, Resources, Tags):
        record = {"Resources": Resources, "Tags": Tags}
        if Resources == ["vpc-created"]:
            self.fake_aws.created_vpc_tags.append(record)
        if Resources == ["subnet-created"]:
            self.fake_aws.created_subnet_tags.append(record)

    def modify_vpc_attribute(self, **kwargs) -> None:
        pass

    def describe_security_groups(self, Filters):
        return {"SecurityGroups": []}

    def create_security_group(self, GroupName, Description, VpcId):
        return {"GroupId": "sg-created"}

    def authorize_security_group_ingress(self, GroupId, IpPermissions):
        self.fake_aws.ingress_rules.append(
            {"GroupId": GroupId, "IpPermissions": IpPermissions}
        )

    def run_instances(self, **kwargs):
        self.fake_aws.run_instances_params = kwargs
        return {
            "Instances": [
                {
                    "InstanceId": "i-created",
                    "State": {"Name": "pending"},
                    "PrivateIpAddress": "10.0.1.10",
                }
            ]
        }

    def stop_instances(self, InstanceIds):
        return {"StoppingInstances": InstanceIds}

    def start_instances(self, InstanceIds):
        return {"StartingInstances": InstanceIds}


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
        "account": "123456789012",
    }
    assert fake_aws.clients[0][0] == "sts"


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
        {"id": "ami-123", "name": "test-ami", "status": "available"}
    ]
    assert provider.list_networks() == [
        {
            "id": "vpc-1",
            "name": "cloudnet-vpc",
            "type": "vpc",
            "cidr": "10.0.0.0/16",
            "state": "available",
        },
        {
            "id": "subnet-1",
            "name": "cloudnet-subnet",
            "type": "subnet",
            "vpc_id": "vpc-1",
            "cidr": "10.0.1.0/24",
            "state": "available",
            "availability_zone": "us-west-2a",
        },
    ]


def test_aws_create_resources_and_server(monkeypatch) -> None:
    set_aws_env(monkeypatch)
    fake_aws = mock_boto3(monkeypatch)
    provider = AWSProvider()

    assert provider.create_network("cloudnet-net", "10.0.0.0/16") == {
        "id": "vpc-created",
        "name": "cloudnet-net",
        "status": "pending",
        "cidr": "10.0.0.0/16",
    }
    assert provider.create_subnet("vpc-created", "cloudnet-subnet", "10.0.1.0/24") == {
        "id": "subnet-created",
        "name": "cloudnet-subnet",
        "cidr": "10.0.1.0/24",
        "network_id": "vpc-created",
        "vpc_id": "vpc-created",
    }
    assert provider.create_server("client-a", "subnet-created") == {
        "id": "i-created",
        "name": "client-a",
        "status": "pending",
        "addresses": {
            "private": [
                {
                    "addr": "10.0.1.10",
                    "version": 4,
                    "OS-EXT-IPS:type": "fixed",
                }
            ]
        },
    }
    assert fake_aws.run_instances_params["KeyName"] == "cloudnet-key"
    assert fake_aws.run_instances_params["SecurityGroupIds"] == ["sg-created"]
    assert fake_aws.ingress_rules[0]["IpPermissions"][0]["IpRanges"] == [
        {"CidrIp": "203.0.113.10/32"}
    ]
    assert fake_aws.ingress_rules[1]["IpPermissions"][0]["UserIdGroupPairs"] == [
        {"GroupId": "sg-created"}
    ]


def test_factory_returns_aws_provider(monkeypatch) -> None:
    monkeypatch.setenv("CLOUDNET_PROVIDER", "aws")

    assert isinstance(get_provider(), AWSProvider)
