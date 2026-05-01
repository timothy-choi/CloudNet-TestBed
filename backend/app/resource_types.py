"""Canonical `DeploymentResource.resource_type` strings."""

from __future__ import annotations

# --- AWS (unchanged) ---
AWS_VPC = "aws_vpc"
AWS_SUBNET = "aws_subnet"
AWS_INSTANCE = "aws_instance"
AWS_INTERNET_GATEWAY = "aws_internet_gateway"
AWS_ROUTE_TABLE = "aws_route_table"
AWS_ROUTE_TABLE_ASSOCIATION = "aws_route_table_association"
AWS_SECURITY_GROUP = "aws_security_group"

# --- OpenStack ---
NEUTRON_NETWORK = "neutron_network"
NEUTRON_SUBNET = "neutron_subnet"
NOVA_SERVER = "nova_server"

# --- Mock: provider-neutral labels ---
PROVIDER_NETWORK = "provider_network"
PROVIDER_SUBNET = "provider_subnet"
PROVIDER_INSTANCE = "provider_instance"

# Legacy mock rows may still use OpenStack-style names; treat as instances/subnets.
LEGACY_MOCK_INSTANCE = NOVA_SERVER
LEGACY_MOCK_SUBNET = NEUTRON_SUBNET
LEGACY_MOCK_NETWORK = NEUTRON_NETWORK

INSTANCE_RESOURCE_TYPES = frozenset(
    {AWS_INSTANCE, NOVA_SERVER, PROVIDER_INSTANCE}
)

SUBNET_RESOURCE_TYPES = frozenset(
    {AWS_SUBNET, NEUTRON_SUBNET, PROVIDER_SUBNET}
)

NETWORK_RESOURCE_TYPES = frozenset(
    {NEUTRON_NETWORK, PROVIDER_NETWORK}
)


def non_aws_deploy_resource_labels(provider_name: str) -> tuple[str, str, str]:
    """(network, subnet, server) resource_type values for non-AWS deploy."""
    if provider_name == "mock":
        return (PROVIDER_NETWORK, PROVIDER_SUBNET, PROVIDER_INSTANCE)
    return (NEUTRON_NETWORK, NEUTRON_SUBNET, NOVA_SERVER)


def instance_lookup_types(provider_name: str) -> tuple[str, ...]:
    """Order: prefer canonical mock type, then legacy."""
    if provider_name == "aws":
        return (AWS_INSTANCE,)
    if provider_name == "mock":
        return (PROVIDER_INSTANCE, NOVA_SERVER)
    return (NOVA_SERVER,)


def subnet_resource_types_for_drift(provider_name: str) -> frozenset[str]:
    if provider_name == "aws":
        return frozenset({AWS_SUBNET})
    if provider_name == "mock":
        return frozenset({PROVIDER_SUBNET, NEUTRON_SUBNET})
    return frozenset({NEUTRON_SUBNET})


def primary_instance_type_for_missing_drift(provider_name: str) -> str:
    """Label when the expected instance row is absent."""
    if provider_name == "aws":
        return AWS_INSTANCE
    if provider_name == "mock":
        return PROVIDER_INSTANCE
    return NOVA_SERVER


def instance_types_filter(provider_name: str) -> frozenset[str]:
    return frozenset(instance_lookup_types(provider_name))
