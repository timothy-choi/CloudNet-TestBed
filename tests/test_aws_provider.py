import sys
from types import SimpleNamespace

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

    def client(self, service_name: str, **kwargs):
        self.clients.append((service_name, kwargs))
        return FakeEC2(self)


class FakeEC2:
    def __init__(self, fake_aws: FakeAWS) -> None:
        self.fake_aws = fake_aws

    def describe_availability_zones(self):
        return {"AvailabilityZones": [{"ZoneName": "us-west-2a"}]}

    def describe_vpcs(self):
        return {
            "Vpcs": [
                {
                    "VpcId": "vpc-1",
                    "CidrBlock": "10.0.0.0/16",
                    "State": "available",
                    "IsDefault": True,
                }
            ]
        }

    def describe_subnets(self):
        return {
            "Subnets": [
                {
                    "SubnetId": "subnet-1",
                    "VpcId": "vpc-1",
                    "CidrBlock": "10.0.1.0/24",
                    "State": "available",
                    "DefaultForAz": True,
                }
            ]
        }


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
            "cidr": "10.0.0.0/16",
            "state": "available",
            "is_default": True,
        },
        {
            "id": "subnet-1",
            "cidr": "10.0.1.0/24",
            "state": "available",
            "is_default": True,
        },
    ]


def test_aws_list_images_returns_empty_without_default_ami(monkeypatch) -> None:
    set_aws_env(monkeypatch)
    monkeypatch.delenv("AWS_DEFAULT_AMI_ID", raising=False)

    assert AWSProvider().list_images() == []


def test_aws_provisioning_methods_are_not_implemented() -> None:
    provider = AWSProvider()

    for action in [
        lambda: provider.create_network("cloudnet-net", "10.0.0.0/16"),
        lambda: provider.create_subnet("vpc-1", "cloudnet-subnet", "10.0.1.0/24"),
        lambda: provider.create_router("router-1"),
        lambda: provider.create_server("client-a", "subnet-1"),
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
