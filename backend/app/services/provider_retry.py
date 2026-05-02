"""Bounded retries with exponential backoff for transient provider failures."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from typing import TypeVar

from app.core.config import _env_bool
from app.services.provider_errors import error_summary, is_retryable

logger = logging.getLogger("cloudnet.retry")

T = TypeVar("T")

DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_SEC = (1.0, 2.0, 4.0)


def provider_max_retries() -> int:
    raw = os.getenv("CLOUDNET_PROVIDER_MAX_RETRIES", str(DEFAULT_MAX_RETRIES))
    try:
        n = int(raw)
    except ValueError:
        return DEFAULT_MAX_RETRIES
    return max(0, min(n, 10))


def call_with_retry(
    fn: Callable[[], T],
    operation: str,
    *,
    max_retries: int | None = None,
) -> T:
    """Run ``fn`` with up to ``max_retries`` retries after transient failures."""
    limit = provider_max_retries() if max_retries is None else max_retries
    delays = DEFAULT_BACKOFF_SEC
    last_exc: BaseException | None = None
    attempts = limit + 1  # initial try + ``limit`` retries

    for attempt in range(attempts):
        try:
            result = fn()
            if attempt > 0:
                logger.info("✔ %s succeeded after retry", operation)
            return result
        except BaseException as exc:
            last_exc = exc
            can_retry = is_retryable(exc) and attempt < attempts - 1
            if not can_retry:
                raise
            delay = delays[min(attempt, len(delays) - 1)]
            logger.info(
                "Retrying (%d/%d): %s",
                attempt + 1,
                limit,
                error_summary(exc),
            )
            if not _env_bool("CLOUDNET_TEST_FAST_RETRY", default=False):
                time.sleep(delay)

    assert last_exc is not None
    raise last_exc
