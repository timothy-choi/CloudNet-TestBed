from app.core.config import get_cloudnet_provider
from app.providers.aws_provider import AWSProvider
from app.providers.base import BaseProvider
from app.providers.mock_provider import MockProvider
from app.providers.openstack_provider import OpenStackProvider
from app.providers.proxmox_provider import ProxmoxProvider


_mock_provider = MockProvider()


def get_provider() -> BaseProvider:
    provider = get_cloudnet_provider()
    if provider == "aws":
        return AWSProvider()
    if provider == "openstack":
        return OpenStackProvider()
    if provider == "proxmox":
        return ProxmoxProvider()
    return _mock_provider
