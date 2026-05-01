"""Canonical resource type constants for mock/OpenStack/AWS."""

from app.resource_types import (
    PROVIDER_INSTANCE,
    instance_lookup_types,
    instance_types_filter,
    non_aws_deploy_resource_labels,
)


def test_mock_deploy_labels_are_generic() -> None:
    net, sub, inst = non_aws_deploy_resource_labels("mock")
    assert net == "provider_network"
    assert sub == "provider_subnet"
    assert inst == PROVIDER_INSTANCE


def test_openstack_deploy_labels_unchanged() -> None:
    net, sub, inst = non_aws_deploy_resource_labels("openstack")
    assert net == "neutron_network"
    assert sub == "neutron_subnet"
    assert inst == "nova_server"


def test_mock_instance_lookup_prefers_provider_then_legacy() -> None:
    assert instance_lookup_types("mock") == ("provider_instance", "nova_server")


def test_mock_reconcile_accepts_both_instance_labels() -> None:
    allowed = instance_types_filter("mock")
    assert "provider_instance" in allowed
    assert "nova_server" in allowed
