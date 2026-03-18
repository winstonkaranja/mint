"""Structured error responses for Mint tool handlers."""

from __future__ import annotations

from pydantic import BaseModel

# Error codes
INVALID_INPUT = "INVALID_INPUT"
EMAIL_NOT_FOUND = "EMAIL_NOT_FOUND"
EVENT_NOT_FOUND = "EVENT_NOT_FOUND"
PROVIDER_ERROR = "PROVIDER_ERROR"
RATE_LIMITED = "RATE_LIMITED"


class MintError(BaseModel):
    error_code: str
    message: str
    suggested_fix: str


class MintToolError(Exception):
    def __init__(self, error: MintError):
        self.error = error
        super().__init__(error.model_dump_json())


def invalid_input(message: str, suggested_fix: str) -> MintToolError:
    return MintToolError(MintError(
        error_code=INVALID_INPUT,
        message=message,
        suggested_fix=suggested_fix,
    ))


def not_found(resource_type: str, resource_id: str) -> MintToolError:
    code = EMAIL_NOT_FOUND if resource_type == "email" else EVENT_NOT_FOUND
    return MintToolError(MintError(
        error_code=code,
        message=f"{resource_type.capitalize()} '{resource_id}' not found",
        suggested_fix=f"Verify the {resource_type} ID exists and you have access to it",
    ))


def provider_error(message: str) -> MintToolError:
    return MintToolError(MintError(
        error_code=PROVIDER_ERROR,
        message=message,
        suggested_fix="Retry the request or check the upstream service status",
    ))


def rate_limited() -> MintToolError:
    return MintToolError(MintError(
        error_code=RATE_LIMITED,
        message="Too many requests — rate limit exceeded",
        suggested_fix="Wait a moment before retrying",
    ))
