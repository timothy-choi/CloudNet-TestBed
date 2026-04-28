from typing import Any

from app.core.config import OpenStackSettings, get_openstack_settings


REQUIRED_SETTINGS = {
    "OS_AUTH_URL": "auth_url",
    "OS_USERNAME": "username",
    "OS_PASSWORD": "password",
    "OS_PROJECT_NAME": "project_name",
    "OS_USER_DOMAIN_NAME": "user_domain_name",
    "OS_PROJECT_DOMAIN_NAME": "project_domain_name",
}


def _validate_settings(settings: OpenStackSettings) -> None:
    if not settings.enabled:
        raise RuntimeError("OpenStack is disabled. Set OPENSTACK_ENABLED=true to enable it.")

    missing = [
        env_name
        for env_name, field_name in REQUIRED_SETTINGS.items()
        if not getattr(settings, field_name)
    ]
    if missing:
        raise RuntimeError(
            "Missing OpenStack environment variables: " + ", ".join(sorted(missing))
        )


def get_openstack_connection() -> Any:
    settings = get_openstack_settings()
    _validate_settings(settings)

    import openstack

    connection = openstack.connect(
        auth_url=settings.auth_url,
        username=settings.username,
        password=settings.password,
        project_name=settings.project_name,
        user_domain_name=settings.user_domain_name,
        project_domain_name=settings.project_domain_name,
        region_name=settings.region_name,
    )
    connection.authorize()
    return connection


def _resource_to_dict(resource: Any) -> dict[str, Any]:
    if hasattr(resource, "to_dict"):
        return resource.to_dict()
    if isinstance(resource, dict):
        return resource
    return {
        "id": getattr(resource, "id", None),
        "name": getattr(resource, "name", None),
    }


def list_images() -> list[dict[str, Any]]:
    connection = get_openstack_connection()
    return [_resource_to_dict(image) for image in connection.compute.images()]


def list_flavors() -> list[dict[str, Any]]:
    connection = get_openstack_connection()
    return [_resource_to_dict(flavor) for flavor in connection.compute.flavors()]


def list_networks() -> list[dict[str, Any]]:
    connection = get_openstack_connection()
    return [_resource_to_dict(network) for network in connection.network.networks()]
