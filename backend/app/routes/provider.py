from typing import Any

from fastapi import APIRouter, HTTPException

from app.providers.factory import get_provider


router = APIRouter(prefix="/provider", tags=["provider"])


@router.get("/health")
def provider_health() -> dict[str, Any]:
    return get_provider().health()


@router.get("/images")
def list_images() -> dict[str, list[dict[str, Any]]]:
    try:
        return {"images": get_provider().list_images()}
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Provider image listing failed: {exc}",
        ) from exc


@router.get("/flavors")
def list_flavors() -> dict[str, list[dict[str, Any]]]:
    try:
        return {"flavors": get_provider().list_flavors()}
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Provider flavor listing failed: {exc}",
        ) from exc


@router.get("/networks")
def list_networks() -> dict[str, list[dict[str, Any]]]:
    try:
        return {"networks": get_provider().list_networks()}
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Provider network listing failed: {exc}",
        ) from exc
