import ipaddress
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.providers.aws_provider import AWSProvider
from app.providers.factory import get_provider


router = APIRouter(prefix="/provider", tags=["provider"])


class CreateAWSNetworkRequest(BaseModel):
    name: str = Field(min_length=1)
    cidr: str | None = None
    subnet_cidr: str


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


@router.post("/networks")
def create_network(request: CreateAWSNetworkRequest) -> dict[str, dict[str, Any]]:
    provider = get_provider()
    if not isinstance(provider, AWSProvider):
        raise HTTPException(
            status_code=400,
            detail="Network creation is currently supported only for AWS provider",
        )

    cidr = request.cidr or "10.0.0.0/16"
    vpc_network = _parse_network(cidr, "cidr")
    subnet_network = _parse_network(request.subnet_cidr, "subnet_cidr")
    if not subnet_network.subnet_of(vpc_network):
        raise HTTPException(
            status_code=400,
            detail="subnet_cidr must be inside cidr",
        )

    try:
        vpc = provider.create_network(request.name, cidr)
        subnet = provider.create_subnet(
            vpc["id"],
            f"{request.name}-subnet",
            request.subnet_cidr,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return {"vpc": vpc, "subnet": subnet}


@router.delete("/networks/{vpc_id}")
def delete_network(vpc_id: str) -> dict[str, Any]:
    provider = get_provider()
    if not isinstance(provider, AWSProvider):
        raise HTTPException(
            status_code=400,
            detail="Network deletion is currently supported only for AWS provider",
        )

    try:
        return provider.delete_network(vpc_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _parse_network(value: str, field_name: str) -> ipaddress.IPv4Network:
    try:
        network = ipaddress.ip_network(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} must be a valid CIDR",
        ) from exc
    if network.version != 4:
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} must be an IPv4 CIDR",
        )
    return network
