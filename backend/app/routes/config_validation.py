"""Configuration and safety validation for operators and CI."""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter

from app.core.config import (
    get_aws_settings,
    get_cloudnet_provider,
    get_openstack_settings,
    get_scenario_quota_settings,
    get_validation_concurrency_settings,
)

router = APIRouter(prefix="/config", tags=["config"])


def _aws_credentials_available() -> bool:
    if os.getenv("AWS_ACCESS_KEY_ID") and os.getenv("AWS_SECRET_ACCESS_KEY"):
        return True
    try:
        import boto3

        return boto3.Session().get_credentials() is not None
    except Exception:
        return False


@router.get("/validate")
def validate_cloudnet_config() -> dict[str, Any]:
    """Return structured checks for provider selection, credentials, and safety limits."""
    checks: list[dict[str, Any]] = []
    provider = get_cloudnet_provider()

    checks.append(
        {
            "id": "provider_selected",
            "ok": True,
            "detail": provider,
        }
    )

    if provider == "aws":
        aws = get_aws_settings()
        region_ok = bool(aws.region)
        creds_ok = _aws_credentials_available()
        limits_ok = aws.max_instances_per_deploy > 0
        checks.append(
            {
                "id": "aws_region",
                "ok": region_ok,
                "detail": aws.region or "unset",
            }
        )
        checks.append(
            {
                "id": "aws_credentials",
                "ok": creds_ok,
                "detail": "credentials found" if creds_ok else "no AWS_ACCESS_KEY_ID/SECRET or role chain",
            }
        )
        checks.append(
            {
                "id": "aws_safety_limits",
                "ok": limits_ok,
                "detail": f"AWS_MAX_INSTANCES_PER_DEPLOY={aws.max_instances_per_deploy}",
            }
        )
    else:
        checks.append(
            {
                "id": "aws_credentials",
                "ok": True,
                "detail": "skipped (provider is not aws)",
            }
        )
        checks.append(
            {
                "id": "aws_region",
                "ok": True,
                "detail": "skipped (provider is not aws)",
            }
        )
        checks.append(
            {
                "id": "aws_safety_limits",
                "ok": True,
                "detail": "skipped (provider is not aws)",
            }
        )

    if provider == "openstack":
        os_settings = get_openstack_settings()
        os_ok = bool(
            os_settings.enabled
            and os_settings.auth_url
            and os_settings.username
            and os_settings.password
        )
        checks.append(
            {
                "id": "openstack_auth",
                "ok": os_ok,
                "detail": "env configured" if os_ok else "missing OS_* credentials",
            }
        )
    else:
        checks.append(
            {
                "id": "openstack_auth",
                "ok": True,
                "detail": "skipped",
            }
        )

    sq = get_scenario_quota_settings()
    checks.append(
        {
            "id": "scenario_quotas_configured",
            "ok": sq.max_host_nodes > 0 and sq.max_duration_seconds > 0,
            "detail": {
                "CLOUDNET_MAX_HOST_NODES_PER_SCENARIO": sq.max_host_nodes,
                "CLOUDNET_MAX_SCENARIO_DURATION_SECONDS": sq.max_duration_seconds,
                "CLOUDNET_MAX_SCENARIO_COST_RISK_UNITS": sq.max_cost_risk_units,
            },
        }
    )

    checks.append(
        {
            "id": "mock_mode_without_aws",
            "ok": provider != "aws" or _aws_credentials_available(),
            "detail": "mock/openstack/proxmox do not require AWS credentials",
        }
    )

    vc = get_validation_concurrency_settings()
    checks.append(
        {
            "id": "validation_concurrency",
            "ok": vc.max_parallel_validations >= 1 and vc.validation_timeout_seconds >= 1,
            "detail": {
                "MAX_PARALLEL_VALIDATIONS": vc.max_parallel_validations,
                "VALIDATION_TIMEOUT_SECONDS": vc.validation_timeout_seconds,
            },
        }
    )

    all_ok = all(bool(c.get("ok")) for c in checks)

    return {
        "ok": all_ok,
        "provider": provider,
        "checks": checks,
    }
