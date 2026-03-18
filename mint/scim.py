"""SCIM 2.0 User provisioning endpoints.

Provides a SCIM-compatible API surface that enterprise IdPs (Okta, Azure AD,
Google Workspace) use to provision and deprovision user accounts.

The user roster lives in-memory for now. Replace ``_users`` with a persistent
store (Postgres, Cosmos DB, etc.) when moving to production.

RFC 7643 — SCIM Core Schema
RFC 7644 — SCIM Protocol
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from mint.config import get_settings

# ---------------------------------------------------------------------------
# In-memory user store — keyed by SCIM ``id`` (a server-assigned UUID).
# ---------------------------------------------------------------------------
_users: dict[str, dict[str, Any]] = {}

SCIM_CONTENT_TYPE = "application/scim+json"

# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

def _authenticate(request: Request) -> bool:
    """Validate the bearer token sent by the enterprise IdP.

    Returns True when authentication succeeds.  When no
    ``MINT_SCIM_BEARER_TOKEN`` is configured the endpoints are open
    (useful during local development).
    """
    expected = get_settings().scim_bearer_token
    if not expected:
        return True

    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        return False
    return auth_header[7:] == expected


def _unauthorized() -> JSONResponse:
    return JSONResponse(
        {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
            "detail": "Bearer token missing or invalid",
            "status": "401",
        },
        status_code=401,
        headers={"Content-Type": SCIM_CONTENT_TYPE},
    )


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------

def _scim_user(user: dict[str, Any]) -> dict[str, Any]:
    """Format an internal user record as a SCIM 2.0 User resource."""
    return {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "id": user["id"],
        "userName": user["userName"],
        "name": user.get("name", {}),
        "emails": user.get("emails", []),
        "active": user.get("active", True),
        "meta": {
            "resourceType": "User",
            "created": user["created"],
            "lastModified": user.get("lastModified", user["created"]),
        },
    }


def _not_found(user_id: str) -> JSONResponse:
    return JSONResponse(
        {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
            "detail": f"User {user_id} not found",
            "status": "404",
        },
        status_code=404,
        headers={"Content-Type": SCIM_CONTENT_TYPE},
    )


def _conflict(detail: str) -> JSONResponse:
    return JSONResponse(
        {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
            "detail": detail,
            "status": "409",
        },
        status_code=409,
        headers={"Content-Type": SCIM_CONTENT_TYPE},
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

async def scim_get_users(request: Request) -> JSONResponse:
    """GET /scim/v2/Users -- List users with optional filter and pagination."""
    if not _authenticate(request):
        return _unauthorized()

    filter_param = request.query_params.get("filter", "")
    start_index = max(int(request.query_params.get("startIndex", "1")), 1)
    count = int(request.query_params.get("count", "100"))

    users = list(_users.values())

    # Basic filter: ``userName eq "alice@example.com"``
    # IdPs rely on this to check for existing users before creating.
    if 'userName eq' in filter_param:
        value = filter_param.split('"')[1] if '"' in filter_param else ""
        users = [u for u in users if u.get("userName") == value]

    total = len(users)

    # SCIM pagination is 1-indexed.
    page = users[start_index - 1 : start_index - 1 + count]
    resources = [_scim_user(u) for u in page]

    return JSONResponse(
        {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
            "totalResults": total,
            "startIndex": start_index,
            "itemsPerPage": len(resources),
            "Resources": resources,
        },
        headers={"Content-Type": SCIM_CONTENT_TYPE},
    )


async def scim_get_user(request: Request) -> JSONResponse:
    """GET /scim/v2/Users/{user_id} -- Retrieve a single user."""
    if not _authenticate(request):
        return _unauthorized()

    user_id = request.path_params["user_id"]
    user = _users.get(user_id)
    if not user:
        return _not_found(user_id)

    return JSONResponse(
        _scim_user(user),
        headers={"Content-Type": SCIM_CONTENT_TYPE},
    )


async def scim_create_user(request: Request) -> JSONResponse:
    """POST /scim/v2/Users -- Provision a new user."""
    if not _authenticate(request):
        return _unauthorized()

    body = await request.json()
    user_name = body.get("userName", "")

    # SCIM requires userName uniqueness (RFC 7643 Section 4.1.1).
    for existing in _users.values():
        if existing["userName"] == user_name:
            return _conflict(f"User with userName '{user_name}' already exists")

    now = datetime.now(timezone.utc).isoformat()
    user_id = str(uuid.uuid4())
    user: dict[str, Any] = {
        "id": user_id,
        "userName": user_name,
        "name": body.get("name", {}),
        "emails": body.get("emails", []),
        "active": body.get("active", True),
        "created": now,
        "lastModified": now,
    }
    _users[user_id] = user

    return JSONResponse(
        _scim_user(user),
        status_code=201,
        headers={"Content-Type": SCIM_CONTENT_TYPE},
    )


async def scim_delete_user(request: Request) -> Response:
    """DELETE /scim/v2/Users/{user_id} -- Deprovision a user."""
    if not _authenticate(request):
        return _unauthorized()

    user_id = request.path_params["user_id"]
    _users.pop(user_id, None)

    # 204 No Content -- body must be empty per HTTP spec.
    return Response(status_code=204)


async def scim_patch_user(request: Request) -> JSONResponse:
    """PATCH /scim/v2/Users/{user_id} -- Partial update (RFC 7644 Section 3.5.2).

    IdPs commonly use PATCH to toggle ``active`` for soft-deprovisioning.
    """
    if not _authenticate(request):
        return _unauthorized()

    user_id = request.path_params["user_id"]
    user = _users.get(user_id)
    if not user:
        return _not_found(user_id)

    body = await request.json()
    for operation in body.get("Operations", []):
        path = operation.get("path", "")
        value = operation.get("value")

        if path == "active":
            # ``value`` may arrive as a bool or as a dict like ``{"active": false}``.
            if isinstance(value, dict):
                user["active"] = bool(value.get("active", True))
            else:
                user["active"] = bool(value)

    user["lastModified"] = datetime.now(timezone.utc).isoformat()

    return JSONResponse(
        _scim_user(user),
        headers={"Content-Type": SCIM_CONTENT_TYPE},
    )
