import os
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv()


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class OpenStackSettings:
    enabled: bool
    auth_url: str | None
    username: str | None
    password: str | None
    project_name: str | None
    user_domain_name: str | None
    project_domain_name: str | None
    region_name: str | None


def get_openstack_settings() -> OpenStackSettings:
    return OpenStackSettings(
        enabled=_env_bool("OPENSTACK_ENABLED", default=False),
        auth_url=os.getenv("OS_AUTH_URL"),
        username=os.getenv("OS_USERNAME"),
        password=os.getenv("OS_PASSWORD"),
        project_name=os.getenv("OS_PROJECT_NAME"),
        user_domain_name=os.getenv("OS_USER_DOMAIN_NAME"),
        project_domain_name=os.getenv("OS_PROJECT_DOMAIN_NAME"),
        region_name=os.getenv("OS_REGION_NAME"),
    )
