from typing import Any

from fastapi import APIRouter, HTTPException

from app.core.config import get_openstack_settings
from app.services import openstack_client


router = APIRouter(prefix="/openstack", tags=["openstack"])


def _openstack_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=503, detail=str(exc))


@router.get("/health")
def openstack_health() -> dict[str, bool]:
    settings = get_openstack_settings()
    try:
        openstack_client.get_openstack_connection()
    except RuntimeError as exc:
        raise _openstack_error(exc) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"OpenStack connection failed: {exc}",
        ) from exc

    return {"enabled": settings.enabled, "connected": True}


@router.get("/images")
def list_images() -> list[dict[str, Any]]:
    try:
        return openstack_client.list_images()
    except RuntimeError as exc:
        raise _openstack_error(exc) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"OpenStack image listing failed: {exc}",
        ) from exc


@router.get("/flavors")
def list_flavors() -> list[dict[str, Any]]:
    try:
        return openstack_client.list_flavors()
    except RuntimeError as exc:
        raise _openstack_error(exc) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"OpenStack flavor listing failed: {exc}",
        ) from exc


@router.get("/networks")
def list_networks() -> list[dict[str, Any]]:
    try:
        return openstack_client.list_networks()
    except RuntimeError as exc:
        raise _openstack_error(exc) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"OpenStack network listing failed: {exc}",
        ) from exc
