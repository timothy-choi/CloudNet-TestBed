import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(REPO_ROOT / ".env")


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


SUPPORTED_PROVIDERS = {"openstack", "proxmox", "mock"}


def get_cloudnet_provider() -> str:
    configured_provider = os.getenv("CLOUDNET_PROVIDER")
    if configured_provider:
        provider = configured_provider.strip().lower()
        if provider not in SUPPORTED_PROVIDERS:
            supported = ", ".join(sorted(SUPPORTED_PROVIDERS))
            raise RuntimeError(
                f"Unsupported CLOUDNET_PROVIDER '{configured_provider}'. "
                f"Supported values: {supported}"
            )
        return provider

    return "openstack" if _env_bool("OPENSTACK_ENABLED", default=False) else "mock"


@dataclass(frozen=True)
class OpenStackSettings:
    enabled: bool
    auth_url: str | None
    username: str | None
    password: str | None
    project_name: str | None
    user_domain_name: str
    project_domain_name: str
    region_name: str


def get_openstack_settings() -> OpenStackSettings:
    return OpenStackSettings(
        enabled=_env_bool("OPENSTACK_ENABLED", default=False),
        auth_url=os.getenv("OS_AUTH_URL"),
        username=os.getenv("OS_USERNAME"),
        password=os.getenv("OS_PASSWORD"),
        project_name=os.getenv("OS_PROJECT_NAME"),
        user_domain_name=os.getenv("OS_USER_DOMAIN_NAME", "Default"),
        project_domain_name=os.getenv("OS_PROJECT_DOMAIN_NAME", "Default"),
        region_name=os.getenv("OS_REGION_NAME", "RegionOne"),
    )
