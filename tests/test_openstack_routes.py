import sys

from fastapi.testclient import TestClient

from app.main import app
from app.services import openstack_client


client = TestClient(app)


def test_app_starts_when_openstack_env_is_missing(monkeypatch) -> None:
    monkeypatch.delenv("OPENSTACK_ENABLED", raising=False)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_openstack_health_returns_disabled_status(monkeypatch) -> None:
    monkeypatch.setenv("CLOUDNET_PROVIDER", "openstack")
    monkeypatch.setenv("OPENSTACK_ENABLED", "false")

    response = client.get("/openstack/health")

    assert response.status_code == 200
    assert response.json() == {
        "enabled": False,
        "connected": False,
        "detail": "OpenStack is disabled",
    }


def test_openstack_routes_are_in_openapi_schema() -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/openstack/health" in paths
    assert "/openstack/images" in paths
    assert "/openstack/flavors" in paths
    assert "/openstack/networks" in paths
    assert "/provider/health" in paths
    assert "/provider/images" in paths
    assert "/provider/flavors" in paths
    assert "/provider/networks" in paths


def test_openstack_route_functions_exist() -> None:
    routes = {
        route.path
        for route in app.routes
        if getattr(route, "path", "").startswith("/openstack")
    }

    assert "/openstack/health" in routes
    assert "/openstack/images" in routes
    assert "/openstack/flavors" in routes
    assert "/openstack/networks" in routes


def test_provider_route_functions_exist() -> None:
    routes = {
        route.path
        for route in app.routes
        if getattr(route, "path", "").startswith("/provider")
    }

    assert "/provider/health" in routes
    assert "/provider/images" in routes
    assert "/provider/flavors" in routes
    assert "/provider/networks" in routes


def test_disabled_openstack_images_returns_503_without_importing_sdk(monkeypatch) -> None:
    def fail_if_real_sdk_imported(name, *args, **kwargs):
        if name == "openstack":
            raise AssertionError("test attempted to import the real OpenStack SDK")
        return original_import(name, *args, **kwargs)

    original_import = __import__
    monkeypatch.setenv("CLOUDNET_PROVIDER", "openstack")
    monkeypatch.setenv("OPENSTACK_ENABLED", "false")
    monkeypatch.delitem(sys.modules, "openstack", raising=False)
    monkeypatch.setattr("builtins.__import__", fail_if_real_sdk_imported)

    response = client.get("/openstack/images")

    assert response.status_code == 503
    assert response.json() == {
        "detail": "OpenStack is disabled. Set OPENSTACK_ENABLED=true to enable it."
    }


def test_enabled_list_routes_use_mocked_client(monkeypatch) -> None:
    monkeypatch.setenv("CLOUDNET_PROVIDER", "openstack")
    monkeypatch.setenv("OPENSTACK_ENABLED", "true")
    monkeypatch.setattr(openstack_client, "list_images", lambda: [{"id": "image-1"}])
    monkeypatch.setattr(openstack_client, "list_flavors", lambda: [{"id": "flavor-1"}])
    monkeypatch.setattr(openstack_client, "list_networks", lambda: [{"id": "network-1"}])

    assert client.get("/openstack/images").json() == {"images": [{"id": "image-1"}]}
    assert client.get("/openstack/flavors").json() == {"flavors": [{"id": "flavor-1"}]}
    assert client.get("/openstack/networks").json() == {"networks": [{"id": "network-1"}]}


def test_default_provider_is_mock_when_openstack_is_disabled(monkeypatch) -> None:
    monkeypatch.delenv("CLOUDNET_PROVIDER", raising=False)
    monkeypatch.setenv("OPENSTACK_ENABLED", "false")

    response = client.get("/provider/health")

    assert response.status_code == 200
    assert response.json()["provider"] == "mock"
