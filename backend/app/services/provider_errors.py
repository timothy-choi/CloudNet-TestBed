"""Classify provider / infrastructure errors for retry and messaging."""

from __future__ import annotations

import re

# AWS / boto3 ClientError has response["Error"]["Code"]
_AWS_RETRYABLE = frozenset(
    {
        "RequestLimitExceeded",
        "Throttling",
        "ThrottlingException",
        "ServiceUnavailable",
        "InternalServerError",
        "RequestTimeout",
        "Timeout",
        "SlowDown",
    }
)

_NON_RETRYABLE_HINTS = (
    "invalid",
    "invalidparameter",
    "not found",
    "unauthorized",
    "accessdenied",
    "malformed",
    "does not exist",
    "unknown parameter",
    "bad topology",
    "ec2 instance creation disabled",
    "aws_max_instances",
)


def _stringify(exc: BaseException) -> str:
    if exc is None:
        return ""
    parts: list[str] = [str(exc).lower()]
    r = getattr(exc, "response", None)
    if isinstance(r, dict):
        err = r.get("Error")
        if isinstance(err, dict):
            code = err.get("Code")
            if code:
                parts.append(str(code).lower())
            msg = err.get("Message")
            if msg:
                parts.append(str(msg).lower())
    return " ".join(parts)


def is_retryable(exc: BaseException) -> bool:
    """Return True for transient / throttle-style failures suitable for backoff retries."""
    text = _stringify(exc)
    if not text.strip():
        return False

    # boto ClientError
    r = getattr(exc, "response", None)
    if isinstance(r, dict):
        err = r.get("Error")
        if isinstance(err, dict):
            code = err.get("Code")
            if isinstance(code, str) and code in _AWS_RETRYABLE:
                return True

    if any(k in text for k in ("ratelimit", "rate limit", "throttl", "timeout")):
        return True
    if "internalservererror" in text or "service unavailable" in text:
        return True
    if re.search(r"\b503\b", text):
        return True

    for hint in _NON_RETRYABLE_HINTS:
        if hint in text:
            return False

    # Simulated mock failures — substring match
    if "ratelimitexceeded" in text.replace(" ", "").replace("_", ""):
        return True

    return False


def error_summary(exc: BaseException, *, max_len: int = 120) -> str:
    """Short single-line summary for logs."""
    r = getattr(exc, "response", None)
    if isinstance(r, dict):
        err = r.get("Error")
        if isinstance(err, dict):
            code = err.get("Code")
            msg = err.get("Message")
            if code and msg:
                s = f"{code}: {msg}"
                return s if len(s) <= max_len else s[: max_len - 3] + "..."
            if code:
                return str(code)
    s = str(exc)
    return s if len(s) <= max_len else s[: max_len - 3] + "..."
