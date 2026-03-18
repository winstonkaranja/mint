"""Per-request dependency injection for multi-tenant provider isolation.

Security architecture
---------------------
Every MCP tool invocation flows through FastMCP's dependency injection system.
The critical invariant is that **each request gets a fresh provider instance
bound to the authenticated user's upstream access token**:

    Request -> Bearer JWT -> AccessToken (validated by MintProvider.load_access_token)
                                |
                                +-> access_token.token  = upstream OAuth token (Google/Microsoft)
                                +-> access_token.claims = {"upstream_claims": {"provider", "sub", "email", ...}}
                                                |
                                                +-> get_email_provider()    -> GmailProvider(token=...) or OutlookProvider(token=...)
                                                +-> get_calendar_provider() -> GoogleCalendarProvider(token=...) or MicrosoftCalendarProvider(token=...)
                                                +-> get_current_user()      -> {"email": ..., "sub": ..., "provider": ...}

There is **no shared mutable state** between requests:
- httpx.AsyncClient is created fresh per request (get_http_client context manager)
- Provider instances are created fresh per request with the user's specific token
- No global caches, no connection pools shared across users

This means User A's Gmail token can never be used for User B's request, even
under concurrent load, because the token is extracted from the per-request
AccessToken object (set by auth middleware) and never stored in a shared location.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import httpx
from fastmcp.dependencies import CurrentAccessToken, Depends
from fastmcp.server.dependencies import AccessToken

from mint.providers.base import CalendarProvider, EmailProvider
from mint.providers.google import GmailProvider, GoogleCalendarProvider
from mint.providers.microsoft import MicrosoftCalendarProvider, OutlookProvider


def _provider_from_token(access_token: AccessToken) -> str:
    """Read the provider tag embedded during the OAuth flow."""
    claims = access_token.claims or {}
    upstream = claims.get("upstream_claims") or {}
    return upstream.get("provider", "google")


@asynccontextmanager
async def get_http_client() -> AsyncIterator[httpx.AsyncClient]:
    """Create a fresh HTTP client for this request.

    Each request gets its own client instance to prevent any possibility of
    connection reuse leaking authorization headers between users.
    """
    async with httpx.AsyncClient(timeout=30) as client:
        yield client


async def get_email_provider(
    client: httpx.AsyncClient = Depends(get_http_client),
    access_token: AccessToken = CurrentAccessToken(),
) -> EmailProvider:
    """Create a request-scoped email provider bound to the authenticated user's token.

    The provider choice (Gmail vs Outlook) and the OAuth token are both derived
    from the validated AccessToken, ensuring complete user isolation.
    """
    token = access_token.token
    if _provider_from_token(access_token) == "microsoft":
        return OutlookProvider(client=client, token=token)
    return GmailProvider(client=client, token=token)


async def get_calendar_provider(
    client: httpx.AsyncClient = Depends(get_http_client),
    access_token: AccessToken = CurrentAccessToken(),
) -> CalendarProvider:
    """Create a request-scoped calendar provider bound to the authenticated user's token.

    The provider choice (Google Calendar vs Microsoft Calendar) and the OAuth
    token are both derived from the validated AccessToken, ensuring complete
    user isolation.
    """
    token = access_token.token
    if _provider_from_token(access_token) == "microsoft":
        return MicrosoftCalendarProvider(client=client, token=token)
    return GoogleCalendarProvider(client=client, token=token)


# ---------------------------------------------------------------------------
# User identity extraction
# ---------------------------------------------------------------------------


def _extract_upstream_claims(access_token: AccessToken) -> dict[str, Any]:
    """Extract the upstream_claims dict from a validated AccessToken.

    These claims were embedded in the FastMCP JWT at issuance time by
    MintProvider._extract_upstream_claims and injected into the AccessToken
    by MintProvider.load_access_token.
    """
    claims = access_token.claims or {}
    return claims.get("upstream_claims") or {}


def _extract_email(access_token: AccessToken) -> str | None:
    """Best-effort extraction of the user's email from token claims.

    Checks both top-level claims (populated by GoogleTokenVerifier during
    upstream validation) and upstream_claims (embedded in the FastMCP JWT
    at issuance time). This dual lookup ensures email resolution works
    regardless of whether the claims come from JWT decode or live validation.
    """
    claims = access_token.claims or {}

    # Google tokens: email is a top-level claim set by GoogleTokenVerifier.
    if email := claims.get("email"):
        return email

    # Both providers: check upstream_claims embedded in the FastMCP JWT.
    upstream = _extract_upstream_claims(access_token)
    return (
        upstream.get("email")
        or upstream.get("preferred_username")
    )


async def get_user_email(
    access_token: AccessToken = CurrentAccessToken(),
) -> str | None:
    """Resolve the authenticated user's email for audit logging."""
    return _extract_email(access_token)


async def get_current_user(
    access_token: AccessToken = CurrentAccessToken(),
) -> dict[str, Any]:
    """Extract and validate the current user's identity from the request JWT.

    Returns a normalized user identity dict with consistent fields regardless
    of whether the user authenticated via Google or Microsoft:

        {
            "email": "user@example.com",      # User's email (may be None if unavailable)
            "sub": "google-sub-id-or-oid",     # Unique user identifier from the IdP
            "provider": "google" | "microsoft" # Which OAuth provider was used
        }

    This dependency is intended for audit logging, access control decisions,
    and any feature that needs the authenticated user's identity without
    caring about the specific provider.

    The values come from upstream_claims embedded in the FastMCP JWT at
    issuance time (see MintProvider._extract_upstream_claims), so they are
    available without additional API calls and are cryptographically bound
    to the JWT signature.
    """
    upstream = _extract_upstream_claims(access_token)

    provider = upstream.get("provider", "google")
    email = _extract_email(access_token)

    # Resolve the most specific unique identifier available.
    # Microsoft: prefer `oid` (immutable object ID) over `sub` (app-specific).
    # Google: `sub` is the stable user ID from the id_token.
    sub = upstream.get("oid") or upstream.get("sub")

    return {
        "email": email,
        "sub": sub,
        "provider": provider,
    }
