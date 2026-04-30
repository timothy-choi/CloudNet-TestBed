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


SUPPORTED_PROVIDERS = {"aws", "openstack", "proxmox", "mock"}


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


@dataclass(frozen=True)
class ProxmoxSettings:
    host: str | None
    port: int
    user: str | None
    password: str | None
    verify_ssl: bool
    node: str | None


@dataclass(frozen=True)
class AWSSettings:
    region: str | None
    access_key_id: str | None
    secret_access_key: str | None
    key_name: str | None
    default_ami_id: str | None
    default_instance_type: str
    ssh_cidr: str


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


def get_proxmox_settings() -> ProxmoxSettings:
    return ProxmoxSettings(
        host=os.getenv("PROXMOX_HOST"),
        port=int(os.getenv("PROXMOX_PORT", "8006")),
        user=os.getenv("PROXMOX_USER"),
        password=os.getenv("PROXMOX_PASSWORD"),
        verify_ssl=_env_bool("PROXMOX_VERIFY_SSL", default=False),
        node=os.getenv("PROXMOX_NODE"),
    )


def get_aws_settings() -> AWSSettings:
    return AWSSettings(
        region=os.getenv("AWS_REGION"),
        access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        key_name=os.getenv("AWS_KEY_NAME"),
        default_ami_id=os.getenv("AWS_DEFAULT_AMI_ID"),
        default_instance_type=os.getenv("AWS_DEFAULT_INSTANCE_TYPE", "t3.micro"),
        ssh_cidr=os.getenv("AWS_SSH_CIDR", "0.0.0.0/0"),
    )
