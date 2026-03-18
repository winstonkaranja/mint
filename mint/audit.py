"""Structured audit logging for tool invocations.

Every tool call emits a single JSON line to stdout, captured by
Azure Container Apps for observability and compliance.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger("mint.audit")

# Keys whose values should never appear in logs.
_SENSITIVE_KEYS = {"body", "password", "secret", "token"}

_MAX_VALUE_LENGTH = 200


def _redact_sensitive(params: dict) -> dict:
    """Return a copy of *params* with sensitive values masked and long strings truncated."""
    redacted: dict = {}
    for key, value in params.items():
        if any(sensitive in key.lower() for sensitive in _SENSITIVE_KEYS):
            redacted[key] = "[REDACTED]"
        elif isinstance(value, str) and len(value) > _MAX_VALUE_LENGTH:
            redacted[key] = value[:_MAX_VALUE_LENGTH] + "..."
        else:
            redacted[key] = value
    return redacted


def log_tool_call(
    tool_name: str,
    user: str | None,
    params: dict,
    status: str,
    duration_ms: float,
    error_code: str | None = None,
) -> None:
    """Emit a structured JSON audit log entry."""
    entry: dict = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": "tool_call",
        "tool": tool_name,
        "user": user or "anonymous",
        "params": _redact_sensitive(params),
        "status": status,
        "duration_ms": round(duration_ms, 1),
    }
    if error_code:
        entry["error_code"] = error_code
    logger.info(json.dumps(entry, default=str))
