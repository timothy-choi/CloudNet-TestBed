from app.core.config import get_cloudnet_provider
from app.providers.base import BaseProvider
from app.providers.mock_provider import MockProvider
from app.providers.openstack_provider import OpenStackProvider
from app.providers.proxmox_provider import ProxmoxProvider


def get_provider() -> BaseProvider:
    provider = get_cloudnet_provider()
    if provider == "openstack":
        return OpenStackProvider()
    if provider == "proxmox":
        return ProxmoxProvider()
    return MockProvider()
