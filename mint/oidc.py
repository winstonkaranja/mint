"""OIDC discovery and ID token validation for enterprise SSO.

Fetches the OpenID Connect discovery document from an issuer, caches the
result, and provides helpers for validating ID tokens using the issuer's
JWKS.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
from authlib.jose import JsonWebKey, JsonWebToken
from authlib.jose.errors import JoseError

from fastmcp.utilities.auth import decode_jwt_header
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)

# How long to cache the OIDC discovery document and JWKS keys (seconds).
_DISCOVERY_CACHE_TTL = 3600
_JWKS_CACHE_TTL = 3600

# Required fields in a well-formed OIDC discovery document.
_REQUIRED_DISCOVERY_FIELDS = frozenset(
    {
        "authorization_endpoint",
        "token_endpoint",
        "jwks_uri",
    }
)


class OIDCDiscoveryError(Exception):
    """Raised when OIDC discovery or ID token validation fails."""


class OIDCDiscoveryCache:
    """Thread-safe, TTL-aware cache for OIDC discovery documents and JWKS.

    One instance should be shared per issuer.  All network I/O uses httpx so
    it plays nicely with the rest of Mint's async stack.
    """

    def __init__(self, issuer_url: str, *, http_timeout: float = 10.0) -> None:
        self._issuer_url = issuer_url.rstrip("/")
        self._http_timeout = http_timeout

        # Discovery cache
        self._discovery: dict[str, Any] | None = None
        self._discovery_fetched_at: float = 0.0

        # JWKS cache — maps kid → public key object
        self._jwks_keys: dict[str, Any] = {}
        self._jwks_fetched_at: float = 0.0

    # ------------------------------------------------------------------
    # Discovery document
    # ------------------------------------------------------------------

    async def discover(self) -> dict[str, Any]:
        """Return the cached OIDC discovery document, refreshing if stale."""
        if self._discovery and (time.time() - self._discovery_fetched_at) < _DISCOVERY_CACHE_TTL:
            return self._discovery

        url = f"{self._issuer_url}/.well-known/openid-configuration"
        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            response = await client.get(url)
            if response.status_code != 200:
                raise OIDCDiscoveryError(
                    f"OIDC discovery failed: {url} returned {response.status_code}"
                )
            doc = response.json()

        missing = _REQUIRED_DISCOVERY_FIELDS - doc.keys()
        if missing:
            raise OIDCDiscoveryError(
                f"OIDC discovery document missing required fields: {', '.join(sorted(missing))}"
            )

        self._discovery = doc
        self._discovery_fetched_at = time.time()
        return doc

    async def authorization_endpoint(self) -> str:
        return (await self.discover())["authorization_endpoint"]

    async def token_endpoint(self) -> str:
        return (await self.discover())["token_endpoint"]

    async def userinfo_endpoint(self) -> str | None:
        return (await self.discover()).get("userinfo_endpoint")

    async def jwks_uri(self) -> str:
        return (await self.discover())["jwks_uri"]

    # ------------------------------------------------------------------
    # JWKS key resolution
    # ------------------------------------------------------------------

    async def _refresh_jwks(self) -> None:
        """Fetch (or refresh) the JWKS from the issuer."""
        uri = await self.jwks_uri()
        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            response = await client.get(uri)
            if response.status_code != 200:
                raise OIDCDiscoveryError(
                    f"JWKS fetch failed: {uri} returned {response.status_code}"
                )
            jwks_data = response.json()

        self._jwks_keys = {}
        for key_data in jwks_data.get("keys", []):
            kid = key_data.get("kid", "_default")
            try:
                jwk = JsonWebKey.import_key(key_data)
                self._jwks_keys[kid] = jwk.get_public_key()
            except Exception:
                logger.debug("Skipping unparseable JWK kid=%s", kid)

        if not self._jwks_keys:
            raise OIDCDiscoveryError("JWKS contained no usable keys")

        self._jwks_fetched_at = time.time()

    async def get_signing_key(self, kid: str | None) -> Any:
        """Resolve a signing key by kid, refreshing the cache if needed."""
        if (time.time() - self._jwks_fetched_at) >= _JWKS_CACHE_TTL:
            await self._refresh_jwks()

        if kid and kid in self._jwks_keys:
            return self._jwks_keys[kid]

        # Kid not found — try a single forced refresh in case keys rotated.
        await self._refresh_jwks()

        if kid and kid in self._jwks_keys:
            return self._jwks_keys[kid]

        # No kid in token — return the sole key if there is exactly one.
        if kid is None and len(self._jwks_keys) == 1:
            return next(iter(self._jwks_keys.values()))

        raise OIDCDiscoveryError(
            f"No matching key found in JWKS (kid={kid!r}, "
            f"available={list(self._jwks_keys.keys())})"
        )


# ---------------------------------------------------------------------------
# ID token validation
# ---------------------------------------------------------------------------


async def validate_id_token(
    id_token: str,
    discovery_cache: OIDCDiscoveryCache,
    client_id: str,
    issuer_url: str,
) -> dict[str, Any]:
    """Decode and validate an OIDC ID token.

    Checks:
    - Signature against the issuer's JWKS
    - ``iss`` matches the expected issuer
    - ``aud`` contains our client_id
    - ``exp`` has not passed

    Returns the validated claims dict.
    """
    # Supported signing algorithms for OIDC ID tokens.
    _SUPPORTED_ALGS = ["RS256", "RS384", "RS512", "ES256", "ES384", "ES512"]

    # Extract kid from the token header for JWKS key lookup.
    try:
        jwt_lib = JsonWebToken(_SUPPORTED_ALGS)
        header = decode_jwt_header(id_token)
        kid = header.get("kid")
    except Exception as exc:
        raise OIDCDiscoveryError(f"Failed to decode ID token header: {exc}") from exc

    key = await discovery_cache.get_signing_key(kid)

    try:
        claims = jwt_lib.decode(id_token, key)
    except JoseError as exc:
        raise OIDCDiscoveryError(f"ID token signature verification failed: {exc}") from exc

    # --- claim validation ---

    now = time.time()

    if claims.get("exp") and claims["exp"] < now:
        raise OIDCDiscoveryError("ID token has expired")

    # Issuer check — normalize trailing slashes.
    token_iss = (claims.get("iss") or "").rstrip("/")
    expected_iss = issuer_url.rstrip("/")
    if token_iss != expected_iss:
        raise OIDCDiscoveryError(
            f"ID token issuer mismatch: got {token_iss!r}, expected {expected_iss!r}"
        )

    # Audience check — aud may be a string or a list.
    aud = claims.get("aud")
    if isinstance(aud, str):
        aud = [aud]
    if not aud or client_id not in aud:
        raise OIDCDiscoveryError(
            f"ID token audience mismatch: got {aud!r}, expected {client_id!r}"
        )

    return dict(claims)
