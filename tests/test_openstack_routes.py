import sys

from fastapi.testclient import TestClient

from app.main import app
from app.routes import openstack as openstack_routes
from app.services import openstack_client


client = TestClient(app)


def test_openstack_health_handles_disabled_mode(monkeypatch) -> None:
    def fail_if_called():
        raise RuntimeError("OpenStack is disabled. Set OPENSTACK_ENABLED=true to enable it.")

    monkeypatch.setattr(
        openstack_routes.openstack_client,
        "get_openstack_connection",
        fail_if_called,
    )

    response = client.get("/openstack/health")

    assert response.status_code == 503
    assert response.json() == {
        "detail": "OpenStack is disabled. Set OPENSTACK_ENABLED=true to enable it."
    }


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


def test_openstack_list_routes_use_mocked_client(monkeypatch) -> None:
    def fail_if_real_sdk_imported(name, *args, **kwargs):
        if name == "openstack":
            raise AssertionError("test attempted to import the real OpenStack SDK")
        return original_import(name, *args, **kwargs)

    original_import = __import__
    monkeypatch.delitem(sys.modules, "openstack", raising=False)
    monkeypatch.setattr("builtins.__import__", fail_if_real_sdk_imported)
    monkeypatch.setattr(
        openstack_routes.openstack_client,
        "list_images",
        lambda: [{"id": "image-1", "name": "ubuntu"}],
    )
    monkeypatch.setattr(
        openstack_routes.openstack_client,
        "list_flavors",
        lambda: [{"id": "flavor-1", "name": "small"}],
    )
    monkeypatch.setattr(
        openstack_routes.openstack_client,
        "list_networks",
        lambda: [{"id": "network-1", "name": "private"}],
    )

    assert client.get("/openstack/images").json() == [
        {"id": "image-1", "name": "ubuntu"}
    ]
    assert client.get("/openstack/flavors").json() == [
        {"id": "flavor-1", "name": "small"}
    ]
    assert client.get("/openstack/networks").json() == [
        {"id": "network-1", "name": "private"}
    ]


def test_disabled_openstack_service_does_not_import_sdk(monkeypatch) -> None:
    def fail_if_real_sdk_imported(name, *args, **kwargs):
        if name == "openstack":
            raise AssertionError("test attempted to import the real OpenStack SDK")
        return original_import(name, *args, **kwargs)

    original_import = __import__
    monkeypatch.setenv("OPENSTACK_ENABLED", "false")
    monkeypatch.delitem(sys.modules, "openstack", raising=False)
    monkeypatch.setattr("builtins.__import__", fail_if_real_sdk_imported)

    try:
        openstack_client.get_openstack_connection()
    except RuntimeError as exc:
        assert str(exc) == "OpenStack is disabled. Set OPENSTACK_ENABLED=true to enable it."
    else:
        raise AssertionError("disabled OpenStack should raise RuntimeError")
