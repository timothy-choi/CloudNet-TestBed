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


def _server_to_dict(server: Any) -> dict[str, Any]:
    return {
        "id": _resource_value(server, "id"),
        "name": _resource_value(server, "name"),
        "status": _resource_value(server, "status"),
        "addresses": _resource_value(server, "addresses", {}),
    }


def _floating_ip_to_dict(floating_ip: Any) -> dict[str, Any]:
    return {
        "id": _resource_value(floating_ip, "id"),
        "floating_ip_address": _resource_value(floating_ip, "floating_ip_address"),
        "status": _resource_value(floating_ip, "status"),
        "port_id": _resource_value(floating_ip, "port_id"),
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


def _subnet_to_dict(subnet: Any) -> dict[str, Any]:
    return {
        "id": _resource_value(subnet, "id"),
        "name": _resource_value(subnet, "name"),
        "cidr": _resource_value(subnet, "cidr"),
        "network_id": _resource_value(subnet, "network_id"),
    }


def create_network(name: str) -> dict[str, Any]:
    connection = get_openstack_connection()
    network = connection.network.create_network(name=name)
    return _network_to_dict(network)


def create_subnet(network_id: str, name: str, cidr: str) -> dict[str, Any]:
    connection = get_openstack_connection()
    subnet = connection.network.create_subnet(
        network_id=network_id,
        name=name,
        cidr=cidr,
        ip_version=4,
    )
    return _subnet_to_dict(subnet)


def delete_network(network_id: str) -> None:
    connection = get_openstack_connection()
    connection.network.delete_network(network_id, ignore_missing=True)


def delete_subnet(subnet_id: str) -> None:
    connection = get_openstack_connection()
    connection.network.delete_subnet(subnet_id, ignore_missing=True)


def get_default_image_id() -> str:
    connection = get_openstack_connection()
    return _get_default_image_id(connection)


def _get_default_image_id(connection: Any) -> str:
    images = list(connection.image.images())
    if not images:
        raise RuntimeError("No OpenStack images are available")
    image = images[0]
    image_id = _resource_value(image, "id")
    if not image_id:
        raise RuntimeError("Default OpenStack image has no id")
    return image_id


def get_default_flavor_id() -> str:
    connection = get_openstack_connection()
    return _get_default_flavor_id(connection)


def _get_default_flavor_id(connection: Any) -> str:
    flavors = list(connection.compute.flavors())
    if not flavors:
        raise RuntimeError("No OpenStack flavors are available")

    flavor = min(flavors, key=lambda item: _resource_value(item, "ram", 0) or 0)
    flavor_id = _resource_value(flavor, "id")
    if not flavor_id:
        raise RuntimeError("Default OpenStack flavor has no id")
    return flavor_id


def create_server(name: str, network_id: str) -> dict[str, Any]:
    connection = get_openstack_connection()
    security_group = get_or_create_security_group_allow_ssh_icmp()
    server = connection.compute.create_server(
        name=name,
        image_id=_get_default_image_id(connection),
        flavor_id=_get_default_flavor_id(connection),
        networks=[{"uuid": network_id}],
        security_groups=[{"name": security_group["name"]}],
    )

    try:
        server = connection.compute.wait_for_server(server)
    except Exception:
        pass

    return _server_to_dict(server)


def delete_server(server_id: str) -> None:
    connection = get_openstack_connection()
    connection.compute.delete_server(server_id, ignore_missing=True)


def get_server_console_log(server_id: str, lines: int = 100) -> str:
    connection = get_openstack_connection()
    return connection.compute.get_server_console_output(
        server_id,
        length=lines,
    )


def get_server_details(server_id: str) -> dict[str, Any]:
    connection = get_openstack_connection()
    server = connection.compute.get_server(server_id)
    return _server_to_dict(server)


def stop_server(server_id: str) -> None:
    connection = get_openstack_connection()
    connection.compute.stop_server(server_id)


def start_server(server_id: str) -> None:
    connection = get_openstack_connection()
    connection.compute.start_server(server_id)


def get_server_status(server_id: str) -> str:
    return str(get_server_details(server_id).get("status", "UNKNOWN"))


def create_floating_ip() -> dict[str, Any]:
    connection = get_openstack_connection()
    external_network = _get_external_network(connection)
    floating_ip = connection.network.create_ip(
        floating_network_id=external_network["id"],
    )
    return _floating_ip_to_dict(floating_ip)


def associate_floating_ip(server_id: str, floating_ip: str) -> dict[str, Any]:
    connection = get_openstack_connection()
    connection.compute.add_floating_ip_to_server(server_id, floating_ip)
    return get_server_details(server_id)


def get_server_fixed_ip(server_id: str, network_name: str | None = None) -> str:
    server = get_server_details(server_id)
    addresses = server.get("addresses", {})

    network_addresses = (
        {network_name: addresses.get(network_name, [])}
        if network_name is not None
        else addresses
    )
    for values in network_addresses.values():
        for address in values:
            if not isinstance(address, dict):
                continue
            address_type = address.get("OS-EXT-IPS:type")
            if address_type not in {None, "fixed"}:
                continue
            if address.get("version") not in {None, 4}:
                continue
            ip_address = address.get("addr")
            if ip_address:
                return ip_address

    raise RuntimeError(f"No fixed IPv4 address found for server {server_id}")


def get_or_create_security_group_allow_ssh_icmp() -> dict[str, Any]:
    connection = get_openstack_connection()
    group_name = "cloudnet-ssh-icmp"
    security_group = _find_security_group(connection, group_name)
    if security_group is None:
        security_group = connection.network.create_security_group(
            name=group_name,
            description="CloudNet Testbed SSH and ICMP access",
        )

    security_group_id = _resource_value(security_group, "id")
    _ensure_security_group_rule(
        connection=connection,
        security_group_id=security_group_id,
        direction="ingress",
        ethertype="IPv4",
        protocol="tcp",
        port_range_min=22,
        port_range_max=22,
    )
    _ensure_security_group_rule(
        connection=connection,
        security_group_id=security_group_id,
        direction="ingress",
        ethertype="IPv4",
        protocol="icmp",
    )

    return {
        "id": security_group_id,
        "name": _resource_value(security_group, "name"),
    }


def _get_external_network(connection: Any) -> dict[str, Any]:
    for network in connection.network.networks():
        network_dict = _network_to_dict(network)
        if network_dict.get("is_router_external"):
            return network_dict

    raise RuntimeError("No external OpenStack network found for floating IPs")


def _find_security_group(connection: Any, name: str) -> Any | None:
    for security_group in connection.network.security_groups(name=name):
        if _resource_value(security_group, "name") == name:
            return security_group
    return None


def _ensure_security_group_rule(
    connection: Any,
    security_group_id: str,
    direction: str,
    ethertype: str,
    protocol: str,
    port_range_min: int | None = None,
    port_range_max: int | None = None,
) -> None:
    for rule in connection.network.security_group_rules(
        security_group_id=security_group_id
    ):
        if (
            _resource_value(rule, "direction") == direction
            and _resource_value(rule, "ethertype") == ethertype
            and _resource_value(rule, "protocol") == protocol
            and _resource_value(rule, "port_range_min") == port_range_min
            and _resource_value(rule, "port_range_max") == port_range_max
        ):
            return

    connection.network.create_security_group_rule(
        security_group_id=security_group_id,
        direction=direction,
        ethertype=ethertype,
        protocol=protocol,
        port_range_min=port_range_min,
        port_range_max=port_range_max,
    )
