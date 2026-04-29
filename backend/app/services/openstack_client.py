from typing import Any

from app.core.config import OpenStackSettings, get_openstack_settings


DISABLED_DETAIL = "OpenStack is disabled"
DISABLED_LIST_DETAIL = "OpenStack is disabled. Set OPENSTACK_ENABLED=true to enable it."
REQUIRED_SETTINGS = {
    "OS_AUTH_URL": "auth_url",
    "OS_USERNAME": "username",
    "OS_PASSWORD": "password",
    "OS_PROJECT_NAME": "project_name",
    "OS_USER_DOMAIN_NAME": "user_domain_name",
    "OS_PROJECT_DOMAIN_NAME": "project_domain_name",
}


def is_openstack_enabled() -> bool:
    return get_openstack_settings().enabled


def _validate_settings(settings: OpenStackSettings) -> None:
    if not settings.enabled:
        raise RuntimeError(DISABLED_LIST_DETAIL)

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


def check_openstack_connection() -> dict[str, Any]:
    if not is_openstack_enabled():
        return {
            "enabled": False,
            "connected": False,
            "detail": DISABLED_DETAIL,
        }

    try:
        connection = get_openstack_connection()
        list(connection.compute.flavors())
    except Exception as exc:
        return {
            "enabled": True,
            "connected": False,
            "detail": str(exc),
        }

    return {
        "enabled": True,
        "connected": True,
        "detail": "OpenStack connection succeeded",
    }


def _resource_value(resource: Any, key: str, default: Any = None) -> Any:
    if isinstance(resource, dict):
        return resource.get(key, default)
    return getattr(resource, key, default)


def _image_to_dict(image: Any) -> dict[str, Any]:
    return {
        "id": _resource_value(image, "id"),
        "name": _resource_value(image, "name"),
        "status": _resource_value(image, "status"),
    }


def _flavor_to_dict(flavor: Any) -> dict[str, Any]:
    return {
        "id": _resource_value(flavor, "id"),
        "name": _resource_value(flavor, "name"),
        "vcpus": _resource_value(flavor, "vcpus"),
        "ram": _resource_value(flavor, "ram"),
        "disk": _resource_value(flavor, "disk"),
    }


def _network_to_dict(network: Any) -> dict[str, Any]:
    return {
        "id": _resource_value(network, "id"),
        "name": _resource_value(network, "name"),
        "status": _resource_value(network, "status"),
        "is_router_external": _resource_value(
            network,
            "is_router_external",
            _resource_value(network, "router:external"),
        ),
    }


def list_images() -> list[dict[str, Any]]:
    connection = get_openstack_connection()
    images = connection.image.images()
    return [_image_to_dict(image) for image in images]


def list_flavors() -> list[dict[str, Any]]:
    connection = get_openstack_connection()
    flavors = connection.compute.flavors()
    return [_flavor_to_dict(flavor) for flavor in flavors]


def list_networks() -> list[dict[str, Any]]:
    connection = get_openstack_connection()
    networks = connection.network.networks()
    return [_network_to_dict(network) for network in networks]
