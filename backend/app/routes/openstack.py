from typing import Any

from fastapi import APIRouter, HTTPException

from app.services import openstack_client


router = APIRouter(prefix="/openstack", tags=["openstack"])


def _disabled_error() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail=openstack_client.DISABLED_LIST_DETAIL,
    )


@router.get("/health")
def openstack_health() -> dict[str, Any]:
    return openstack_client.check_openstack_connection()


@router.get("/images")
def list_images() -> dict[str, list[dict[str, Any]]]:
    if not openstack_client.is_openstack_enabled():
        raise _disabled_error()

    try:
        return {"images": openstack_client.list_images()}
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"OpenStack image listing failed: {exc}",
        ) from exc


@router.get("/flavors")
def list_flavors() -> dict[str, list[dict[str, Any]]]:
    if not openstack_client.is_openstack_enabled():
        raise _disabled_error()

    try:
        return {"flavors": openstack_client.list_flavors()}
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"OpenStack flavor listing failed: {exc}",
        ) from exc


@router.get("/networks")
def list_networks() -> dict[str, list[dict[str, Any]]]:
    if not openstack_client.is_openstack_enabled():
        raise _disabled_error()

    try:
        return {"networks": openstack_client.list_networks()}
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"OpenStack network listing failed: {exc}",
        ) from exc
