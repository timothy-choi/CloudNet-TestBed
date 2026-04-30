from typing import Any

from fastapi import APIRouter, HTTPException

from app.providers.factory import get_provider
from app.services.openstack_client import DISABLED_LIST_DETAIL


router = APIRouter(prefix="/openstack", tags=["openstack"])


def _disabled_error() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail=DISABLED_LIST_DETAIL,
    )


@router.get("/health")
def openstack_health() -> dict[str, Any]:
    return get_provider().health()


@router.get("/images")
def list_images() -> dict[str, list[dict[str, Any]]]:
    provider = get_provider()
    provider_label = "OpenStack" if provider.name == "openstack" else "Provider"
    try:
        return {"images": provider.list_images()}
    except RuntimeError as exc:
        if str(exc) == DISABLED_LIST_DETAIL:
            raise _disabled_error() from exc
        raise HTTPException(
            status_code=503,
            detail=f"{provider_label} image listing failed: {exc}",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"{provider_label} image listing failed: {exc}",
        ) from exc


@router.get("/flavors")
def list_flavors() -> dict[str, list[dict[str, Any]]]:
    provider = get_provider()
    provider_label = "OpenStack" if provider.name == "openstack" else "Provider"
    try:
        return {"flavors": provider.list_flavors()}
    except RuntimeError as exc:
        if str(exc) == DISABLED_LIST_DETAIL:
            raise _disabled_error() from exc
        raise HTTPException(
            status_code=503,
            detail=f"{provider_label} flavor listing failed: {exc}",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"{provider_label} flavor listing failed: {exc}",
        ) from exc


@router.get("/networks")
def list_networks() -> dict[str, list[dict[str, Any]]]:
    provider = get_provider()
    provider_label = "OpenStack" if provider.name == "openstack" else "Provider"
    try:
        return {"networks": provider.list_networks()}
    except RuntimeError as exc:
        if str(exc) == DISABLED_LIST_DETAIL:
            raise _disabled_error() from exc
        raise HTTPException(
            status_code=503,
            detail=f"{provider_label} network listing failed: {exc}",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"{provider_label} network listing failed: {exc}",
        ) from exc
