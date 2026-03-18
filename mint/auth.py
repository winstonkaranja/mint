"""Multi-provider OAuth proxy for Mint.

Subclasses OAuthProxy to replace the consent page with a provider picker,
dynamically route to Google or Microsoft OAuth, and embed the chosen
provider in JWT claims so downstream dependencies can resolve the correct
email/calendar implementation.

Enterprise SSO (OIDC) is supported as a third provider when configured via
the MINT_SSO_* environment variables.  When SSO is in "enforce-only" mode
the consumer Google/Microsoft buttons are hidden.
"""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import secrets
import time
from base64 import urlsafe_b64encode
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import httpx
from authlib.integrations.httpx_client import AsyncOAuth2Client
from mcp.server.auth.provider import RefreshToken
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import SecretStr
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse

from fastmcp.server.auth.auth import AccessToken, TokenVerifier
from fastmcp.server.auth.oauth_proxy import OAuthProxy
from fastmcp.server.auth.oauth_proxy.models import (
    DEFAULT_AUTH_CODE_EXPIRY_SECONDS,
    HTTP_TIMEOUT_SECONDS,
    ClientCode,
)
from fastmcp.server.auth.oauth_proxy.ui import create_error_html
from fastmcp.server.auth.providers.google import GoogleTokenVerifier
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.utilities.auth import decode_jwt_payload
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.ui import create_secure_html_response

from mint.config import Settings
from mint.oidc import OIDCDiscoveryCache, OIDCDiscoveryError, validate_id_token
from mint.pages import provider_picker_html

logger = get_logger(__name__)

# Task-local provider context — safe across concurrent asyncio coroutines.
_current_provider_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_current_provider", default=None
)

# Maximum age (seconds) for entries in _provider_choices before eviction.
_PROVIDER_CHOICE_TTL = 20 * 60  # 20 minutes (slightly longer than txn TTL)

# Standard OIDC scopes requested during SSO authorization.
_SSO_OIDC_SCOPES = ["openid", "email", "profile"]

# Human-readable labels for well-known SSO provider slugs.
_SSO_PROVIDER_LABELS: dict[str, str] = {
    "okta": "Okta",
    "azure_ad": "Azure AD",
    "google_workspace": "Google Workspace",
}


def _sso_provider_label(settings: Settings) -> str:
    """Derive a human-readable label for the SSO button."""
    slug = (settings.sso_provider or "").strip().lower()
    return _SSO_PROVIDER_LABELS.get(slug, slug.replace("_", " ").title() or "SSO")


# ---------------------------------------------------------------------------
# Upstream provider configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UpstreamConfig:
    """Endpoints and credentials for a single upstream OAuth provider."""

    name: str
    auth_endpoint: str
    token_endpoint: str
    client_id: str
    client_secret: str
    authorize_scopes: list[str]
    token_exchange_scopes: list[str]
    refresh_scopes: list[str]
    extra_authorize_params: dict[str, str] = field(default_factory=dict)


GOOGLE_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
]


def _build_configs(settings: Settings) -> dict[str, UpstreamConfig]:
    identifier_uri = f"api://{settings.azure_client_id}"
    azure_api_scopes = [f"{identifier_uri}/mcp-access"]
    azure_graph_scopes = [
        "https://graph.microsoft.com/Mail.Read",
        "https://graph.microsoft.com/Mail.ReadWrite",
        "https://graph.microsoft.com/Calendars.ReadWrite",
        "offline_access",
    ]
    return {
        "google": UpstreamConfig(
            name="google",
            auth_endpoint="https://accounts.google.com/o/oauth2/v2/auth",
            token_endpoint="https://oauth2.googleapis.com/token",
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret,
            authorize_scopes=GOOGLE_SCOPES,
            token_exchange_scopes=[],
            refresh_scopes=GOOGLE_SCOPES,
            extra_authorize_params={
                "access_type": "offline",
                "prompt": "consent",
            },
        ),
        "microsoft": UpstreamConfig(
            name="microsoft",
            auth_endpoint=(
                f"https://login.microsoftonline.com/"
                f"{settings.azure_tenant_id}/oauth2/v2.0/authorize"
            ),
            token_endpoint=(
                f"https://login.microsoftonline.com/"
                f"{settings.azure_tenant_id}/oauth2/v2.0/token"
            ),
            client_id=settings.azure_client_id,
            client_secret=settings.azure_client_secret,
            authorize_scopes=azure_api_scopes + azure_graph_scopes,
            token_exchange_scopes=azure_api_scopes + ["offline_access"],
            refresh_scopes=azure_api_scopes + ["offline_access"],
            extra_authorize_params={"prompt": "select_account"},
        ),
    }


# ---------------------------------------------------------------------------
# Composite token verifier
# ---------------------------------------------------------------------------


class MintTokenVerifier(TokenVerifier):
    """Routes token verification to the correct upstream verifier.

    Azure tokens are JWTs (three dot-separated segments) → JWTVerifier.
    Google tokens are opaque → GoogleTokenVerifier (calls tokeninfo API).
    """

    def __init__(
        self,
        google: GoogleTokenVerifier,
        azure: JWTVerifier,
    ):
        super().__init__(required_scopes=[])
        self._google = google
        self._azure = azure

    async def verify_token(self, token: str) -> AccessToken | None:
        if token.count(".") == 2:
            result = await self._azure.verify_token(token)
            if result:
                return result
        return await self._google.verify_token(token)


# ---------------------------------------------------------------------------
# MintProvider — the multi-provider OAuth proxy
# ---------------------------------------------------------------------------


class MintProvider(OAuthProxy):
    """OAuthProxy subclass that lets the user pick Google, Microsoft, or SSO."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._configs = _build_configs(settings)

        # Per-transaction provider choice: maps txn_id -> (provider, timestamp).
        # Entries are evicted in _evict_stale_choices() on every write.
        self._provider_choices: dict[str, tuple[str, float]] = {}

        # Serialises credential-swapping in exchange_refresh_token.
        self._credential_lock = asyncio.Lock()

        # --- SSO / OIDC setup ---
        self._sso_enabled = settings.sso_enabled and bool(
            settings.sso_issuer_url
            and settings.sso_client_id
            and settings.sso_client_secret
        )
        self._sso_enforce_only = self._sso_enabled and settings.sso_enforce_only
        self._sso_label = _sso_provider_label(settings)

        self._oidc_cache: OIDCDiscoveryCache | None = None
        if self._sso_enabled:
            assert settings.sso_issuer_url is not None
            self._oidc_cache = OIDCDiscoveryCache(settings.sso_issuer_url)

            # Register a placeholder SSO config.  The auth/token endpoints
            # are populated lazily from OIDC discovery -- the dataclass fields
            # store empty strings as placeholders until first use.
            assert settings.sso_client_id is not None
            assert settings.sso_client_secret is not None
            self._configs["sso"] = UpstreamConfig(
                name="sso",
                auth_endpoint="",  # resolved dynamically via OIDC discovery
                token_endpoint="",  # resolved dynamically via OIDC discovery
                client_id=settings.sso_client_id,
                client_secret=settings.sso_client_secret,
                authorize_scopes=_SSO_OIDC_SCOPES,
                token_exchange_scopes=[],
                refresh_scopes=_SSO_OIDC_SCOPES,
                extra_authorize_params={"prompt": "consent"},
            )

            logger.info(
                "SSO enabled for %s (issuer: %s, enforce_only: %s)",
                self._sso_label,
                settings.sso_issuer_url,
                self._sso_enforce_only,
            )

        # --- build composite verifier ---
        google_verifier = GoogleTokenVerifier(required_scopes=GOOGLE_SCOPES)

        tenant = settings.azure_tenant_id
        azure_verifier = JWTVerifier(
            jwks_uri=f"https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys",
            issuer=f"https://login.microsoftonline.com/{tenant}/v2.0",
            audience=settings.azure_client_id,
            algorithm="RS256",
            required_scopes=["mcp-access"],
        )
        verifier = MintTokenVerifier(google=google_verifier, azure=azure_verifier)

        # Derive a stable JWT signing key from both client secrets when no
        # explicit secret_key is configured.
        jwt_key: str | None = settings.secret_key or None
        if jwt_key is None:
            jwt_key = (
                f"{settings.google_client_secret}:{settings.azure_client_secret}"
            )

        google = self._configs["google"]
        super().__init__(
            upstream_authorization_endpoint=google.auth_endpoint,
            upstream_token_endpoint=google.token_endpoint,
            upstream_client_id=google.client_id,
            upstream_client_secret=google.client_secret,
            token_verifier=verifier,
            base_url=settings.base_url,
            jwt_signing_key=jwt_key,
            require_authorization_consent=True,
            extra_authorize_params=google.extra_authorize_params,
        )

    # ------------------------------------------------------------------
    # Provider choice helpers
    # ------------------------------------------------------------------

    def _detect_provider(self, upstream_access_token: str) -> str:
        """Azure tokens are JWTs; Google tokens are opaque."""
        return "microsoft" if upstream_access_token.count(".") == 2 else "google"

    def _store_choice(self, txn_id: str, provider: str) -> None:
        self._provider_choices[txn_id] = (provider, time.time())
        self._evict_stale_choices()

    def _pop_choice(self, txn_id: str) -> str:
        entry = self._provider_choices.pop(txn_id, None)
        return entry[0] if entry else "google"

    def _get_choice(self, txn_id: str) -> str:
        entry = self._provider_choices.get(txn_id)
        return entry[0] if entry else "google"

    def _evict_stale_choices(self) -> None:
        cutoff = time.time() - _PROVIDER_CHOICE_TTL
        stale = [k for k, (_, ts) in self._provider_choices.items() if ts < cutoff]
        for k in stale:
            del self._provider_choices[k]

    # ------------------------------------------------------------------
    # Consent page → provider picker
    # ------------------------------------------------------------------

    async def _show_consent_page(
        self, request: Request
    ) -> HTMLResponse | RedirectResponse:
        """Replace the default consent screen with a provider picker."""
        txn_id = request.query_params.get("txn_id")
        if not txn_id:
            return create_secure_html_response(
                "<h1>Error</h1><p>Invalid or expired transaction</p>",
                status_code=400,
            )

        txn_model = await self._transaction_store.get(key=txn_id)
        if not txn_model:
            return create_secure_html_response(
                "<h1>Error</h1><p>Invalid or expired transaction</p>",
                status_code=400,
            )

        # Generate CSRF token (mirrors parent logic).
        csrf_token = secrets.token_urlsafe(32)
        csrf_expires_at = time.time() + 15 * 60
        txn_model.csrf_token = csrf_token
        txn_model.csrf_expires_at = csrf_expires_at
        await self._transaction_store.put(key=txn_id, value=txn_model, ttl=15 * 60)

        html = provider_picker_html(
            txn_id,
            csrf_token,
            sso_enabled=self._sso_enabled,
            sso_label=self._sso_label,
            sso_enforce_only=self._sso_enforce_only,
        )
        response = create_secure_html_response(html)
        self._set_list_cookie(
            response,
            "MCP_CONSENT_STATE",
            self._encode_list_cookie([csrf_token]),
            max_age=15 * 60,
        )
        return response

    async def _submit_consent(
        self, request: Request
    ) -> RedirectResponse | HTMLResponse:
        """Intercept the POST to capture the provider choice, then delegate.

        For non-SSO providers we delegate entirely to the parent's
        ``_submit_consent`` which calls our ``_build_upstream_authorize_url``
        override (synchronous, returns a real URL).

        For SSO, the authorization endpoint must be fetched via async OIDC
        discovery, so we replicate the relevant parent logic here to avoid
        the sync/async mismatch.
        """
        form = await request.form()
        txn_id = str(form.get("txn_id", ""))
        provider = str(form.get("provider", "google"))
        if provider not in self._configs:
            provider = "google"
        if txn_id:
            self._store_choice(txn_id, provider)

        if provider != "sso":
            return await super()._submit_consent(request)

        # --- SSO-specific consent approval ---
        # We must resolve the IdP URL asynchronously, so we inline the
        # parent's approval logic here.
        if not txn_id:
            return create_secure_html_response(
                "<h1>Error</h1><p>Invalid or expired transaction</p>",
                status_code=400,
            )

        txn_model = await self._transaction_store.get(key=txn_id)
        if not txn_model:
            return create_secure_html_response(
                "<h1>Error</h1><p>Invalid or expired transaction</p>",
                status_code=400,
            )

        txn = txn_model.model_dump()

        # CSRF validation
        csrf_token = str(form.get("csrf_token", ""))
        expected_csrf = txn.get("csrf_token")
        expires_at = float(txn.get("csrf_expires_at") or 0)
        if not expected_csrf or csrf_token != expected_csrf or time.time() > expires_at:
            return create_secure_html_response(
                "<h1>Error</h1><p>Invalid or expired consent token</p>",
                status_code=400,
            )

        # Record consent approval cookie (mirrors parent)
        client_key = self._make_client_key(
            txn["client_id"], txn["client_redirect_uri"]
        )
        approved = set(self._decode_list_cookie(request, "MCP_APPROVED_CLIENTS"))
        approved.add(client_key)
        approved_b64 = self._encode_list_cookie(sorted(approved))

        consent_token = secrets.token_urlsafe(32)
        txn_model.consent_token = consent_token
        await self._transaction_store.put(key=txn_id, value=txn_model, ttl=15 * 60)

        # Build the SSO authorize URL asynchronously.
        try:
            upstream_url = await self._build_sso_authorize_url_async(txn_id, txn)
        except OIDCDiscoveryError as exc:
            logger.error("SSO OIDC discovery failed: %s", exc)
            html = create_error_html(
                error_title="SSO Error",
                error_message=(
                    "Could not reach your organisation's identity provider. "
                    "Please try again later."
                ),
            )
            return HTMLResponse(content=html, status_code=502)

        response = RedirectResponse(url=upstream_url, status_code=302)
        self._set_list_cookie(
            response, "MCP_APPROVED_CLIENTS", approved_b64, max_age=365 * 24 * 3600
        )
        self._set_list_cookie(
            response,
            "MCP_CONSENT_STATE",
            self._encode_list_cookie([]),
            max_age=60,
        )
        self._set_consent_binding_cookie(request, response, txn_id, consent_token)
        return response

    # ------------------------------------------------------------------
    # Build upstream authorize URL for the chosen provider
    # ------------------------------------------------------------------

    def _build_upstream_authorize_url(
        self, txn_id: str, transaction: dict[str, Any]
    ) -> str:
        provider = self._get_choice(txn_id)

        # SSO requires async OIDC discovery; the authorize URL is built
        # in _submit_consent / _build_sso_authorize_url_async instead.
        # This branch should not be reached because _submit_consent
        # handles SSO before calling the parent.  Return a safe fallback
        # that will surface clearly if it does get used.
        if provider == "sso":
            logger.warning(
                "_build_upstream_authorize_url called for SSO provider "
                "(txn_id=%s) -- this indicates an unexpected code path",
                txn_id,
            )
            return f"{str(self.base_url).rstrip('/')}/consent?txn_id={txn_id}&error=sso_sync_fallback"

        config = self._configs[provider]

        query: dict[str, Any] = {
            "response_type": "code",
            "client_id": config.client_id,
            "redirect_uri": (
                f"{str(self.base_url).rstrip('/')}{self._redirect_path}"
            ),
            "state": txn_id,
        }

        if config.authorize_scopes:
            query["scope"] = " ".join(config.authorize_scopes)

        # Forward proxy PKCE if present.
        verifier = transaction.get("proxy_code_verifier")
        if verifier:
            digest = hashlib.sha256(verifier.encode()).digest()
            challenge = urlsafe_b64encode(digest).decode().rstrip("=")
            query["code_challenge"] = challenge
            query["code_challenge_method"] = "S256"

        if resource := transaction.get("resource"):
            query["resource"] = resource

        if config.extra_authorize_params:
            query.update(config.extra_authorize_params)

        sep = "&" if "?" in config.auth_endpoint else "?"
        return f"{config.auth_endpoint}{sep}{urlencode(query)}"

    async def _build_sso_authorize_url_async(
        self, txn_id: str, transaction: dict[str, Any]
    ) -> str:
        """Construct the SSO authorize URL using OIDC discovery (async)."""
        assert self._oidc_cache is not None
        config = self._configs["sso"]
        auth_endpoint = await self._oidc_cache.authorization_endpoint()

        query: dict[str, Any] = {
            "response_type": "code",
            "client_id": config.client_id,
            "redirect_uri": (
                f"{str(self.base_url).rstrip('/')}{self._redirect_path}"
            ),
            "state": txn_id,
            "scope": " ".join(config.authorize_scopes),
        }

        verifier = transaction.get("proxy_code_verifier")
        if verifier:
            digest = hashlib.sha256(verifier.encode()).digest()
            challenge = urlsafe_b64encode(digest).decode().rstrip("=")
            query["code_challenge"] = challenge
            query["code_challenge_method"] = "S256"

        if resource := transaction.get("resource"):
            query["resource"] = resource

        if config.extra_authorize_params:
            query.update(config.extra_authorize_params)

        sep = "&" if "?" in auth_endpoint else "?"
        return f"{auth_endpoint}{sep}{urlencode(query)}"

    # ------------------------------------------------------------------
    # Scope preparation hooks (called by parent during token exchange)
    # ------------------------------------------------------------------

    def _prepare_scopes_for_token_exchange(self, scopes: list[str]) -> list[str]:
        provider = _current_provider_var.get()
        if provider == "microsoft":
            return self._configs["microsoft"].token_exchange_scopes
        if provider == "sso":
            return self._configs["sso"].token_exchange_scopes
        return scopes

    def _prepare_scopes_for_upstream_refresh(self, scopes: list[str]) -> list[str]:
        provider = _current_provider_var.get()
        if provider == "microsoft":
            return self._configs["microsoft"].refresh_scopes
        if provider == "sso":
            return self._configs["sso"].refresh_scopes
        return scopes

    # ------------------------------------------------------------------
    # SSO token exchange helpers
    # ------------------------------------------------------------------

    async def _sso_token_exchange(
        self, code: str, redirect_uri: str, code_verifier: str | None
    ) -> dict[str, Any]:
        """Exchange an authorization code with the SSO IdP's token endpoint.

        Returns the full token response dict (access_token, id_token, etc.).
        """
        assert self._oidc_cache is not None
        config = self._configs["sso"]
        token_endpoint = await self._oidc_cache.token_endpoint()

        payload: dict[str, str] = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": config.client_id,
            "client_secret": config.client_secret,
        }
        if code_verifier:
            payload["code_verifier"] = code_verifier

        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            response = await client.post(token_endpoint, data=payload)

        if response.status_code != 200:
            raise RuntimeError(
                f"SSO token exchange failed ({response.status_code}): "
                f"{response.text[:500]}"
            )

        return response.json()

    async def _validate_sso_id_token(self, id_token_raw: str) -> dict[str, Any]:
        """Validate an SSO ID token and return its claims."""
        assert self._oidc_cache is not None
        assert self._settings.sso_issuer_url is not None
        assert self._settings.sso_client_id is not None

        return await validate_id_token(
            id_token_raw,
            self._oidc_cache,
            client_id=self._settings.sso_client_id,
            issuer_url=self._settings.sso_issuer_url,
        )

    # ------------------------------------------------------------------
    # IdP callback -- override to use provider-specific credentials
    # ------------------------------------------------------------------

    async def _handle_idp_callback(
        self, request: Request
    ) -> HTMLResponse | RedirectResponse:
        """Exchange the upstream auth code using the correct provider creds.

        This is a copy of OAuthProxy._handle_idp_callback with the OAuth
        client construction modified to use per-transaction credentials.
        """
        try:
            idp_code = request.query_params.get("code")
            txn_id = request.query_params.get("state")
            error = request.query_params.get("error")

            if error:
                desc = request.query_params.get("error_description")
                html = create_error_html(
                    error_title="OAuth Error",
                    error_message=f"Authentication failed: {desc or 'Unknown error'}",
                    error_details={"Error Code": error} if error else None,
                )
                return HTMLResponse(content=html, status_code=400)

            if not idp_code or not txn_id:
                html = create_error_html(
                    error_title="OAuth Error",
                    error_message="Missing authorization code or transaction ID.",
                )
                return HTMLResponse(content=html, status_code=400)

            transaction_model = await self._transaction_store.get(key=txn_id)
            if not transaction_model:
                html = create_error_html(
                    error_title="OAuth Error",
                    error_message="Invalid or expired authorization transaction.",
                )
                return HTMLResponse(content=html, status_code=400)

            # Consent-binding verification (uses original _upstream_client_secret
            # for cookie HMAC, which is fine — we never swap that for cookies).
            if self._require_authorization_consent:
                consent_token = transaction_model.consent_token
                if not consent_token:
                    html = create_error_html(
                        error_title="Authorization Error",
                        error_message="Invalid authorization flow.",
                    )
                    return HTMLResponse(content=html, status_code=403)

                if not self._verify_consent_binding_cookie(
                    request, txn_id, consent_token
                ):
                    html = create_error_html(
                        error_title="Authorization Error",
                        error_message=(
                            "Authorization session mismatch. Please try again."
                        ),
                    )
                    return HTMLResponse(content=html, status_code=403)

            transaction = transaction_model.model_dump()

            # --- provider-specific token exchange ---
            provider_name = self._pop_choice(txn_id)
            config = self._configs[provider_name]
            _token = _current_provider_var.set(provider_name)

            try:
                redirect_uri = (
                    f"{str(self.base_url).rstrip('/')}{self._redirect_path}"
                )
                verifier = transaction.get("proxy_code_verifier")

                if provider_name == "sso":
                    # SSO path: exchange code via httpx, then validate the
                    # ID token against the issuer's JWKS.
                    idp_tokens: dict[str, Any] = await self._sso_token_exchange(
                        code=idp_code,
                        redirect_uri=redirect_uri,
                        code_verifier=verifier,
                    )

                    id_token_raw = idp_tokens.get("id_token")
                    if id_token_raw:
                        try:
                            id_claims = await self._validate_sso_id_token(
                                id_token_raw
                            )
                            idp_tokens["_sso_id_claims"] = id_claims
                        except OIDCDiscoveryError as exc:
                            logger.warning(
                                "SSO ID token validation failed: %s", exc
                            )
                    else:
                        logger.warning("SSO token response missing id_token")

                else:
                    # Google / Microsoft path -- use authlib.
                    oauth_client = AsyncOAuth2Client(
                        client_id=config.client_id,
                        client_secret=config.client_secret,
                        token_endpoint_auth_method=self._token_endpoint_auth_method,
                        timeout=HTTP_TIMEOUT_SECONDS,
                    )

                    token_params: dict[str, Any] = {
                        "url": config.token_endpoint,
                        "code": idp_code,
                        "redirect_uri": redirect_uri,
                    }

                    if verifier:
                        token_params["code_verifier"] = verifier

                    exchange_scopes = self._prepare_scopes_for_token_exchange(
                        transaction.get("scopes") or []
                    )
                    if exchange_scopes:
                        token_params["scope"] = " ".join(exchange_scopes)

                    idp_tokens = await oauth_client.fetch_token(**token_params)

            except Exception as e:
                logger.error("IdP token exchange failed: %s", e)
                html = create_error_html(
                    error_title="OAuth Error",
                    error_message=f"Token exchange failed: {e}",
                )
                return HTMLResponse(content=html, status_code=500)
            finally:
                _current_provider_var.reset(_token)

            # Tag the token response so we can recover the provider later
            # (flows through ClientCode.idp_tokens → UpstreamTokenSet.raw_token_data).
            idp_tokens["_mint_provider"] = provider_name

            # --- generate client code (identical to parent logic) ---
            client_code = secrets.token_urlsafe(32)
            code_expires_at = int(
                time.time() + DEFAULT_AUTH_CODE_EXPIRY_SECONDS
            )
            await self._code_store.put(
                key=client_code,
                value=ClientCode(
                    code=client_code,
                    client_id=transaction["client_id"],
                    redirect_uri=transaction["client_redirect_uri"],
                    code_challenge=transaction["code_challenge"],
                    code_challenge_method=transaction["code_challenge_method"],
                    scopes=transaction["scopes"],
                    idp_tokens=idp_tokens,
                    expires_at=code_expires_at,
                    created_at=time.time(),
                ),
                ttl=DEFAULT_AUTH_CODE_EXPIRY_SECONDS,
            )

            await self._transaction_store.delete(key=txn_id)

            callback_params = {
                "code": client_code,
                "state": transaction["client_state"],
            }
            sep = "&" if "?" in transaction["client_redirect_uri"] else "?"
            url = f"{transaction['client_redirect_uri']}{sep}{urlencode(callback_params)}"

            response = RedirectResponse(url=url, status_code=302)
            self._clear_consent_binding_cookie(request, response, txn_id)
            return response

        except Exception as e:
            logger.error("Error in IdP callback handler: %s", e, exc_info=True)
            html = create_error_html(
                error_title="OAuth Error",
                error_message="Internal error during OAuth callback. Please try again.",
            )
            return HTMLResponse(content=html, status_code=500)

    # ------------------------------------------------------------------
    # Refresh token — swap credentials for the correct provider
    # ------------------------------------------------------------------

    async def _detect_refresh_provider(self, refresh_jwt: str) -> str:
        """Determine the upstream provider from a FastMCP refresh token."""
        try:
            payload = self.jwt_issuer.verify_token(refresh_jwt)
            jti = payload["jti"]
            mapping = await self._jti_mapping_store.get(key=jti)
            if not mapping:
                return "google"
            upstream = await self._upstream_token_store.get(
                key=mapping.upstream_token_id
            )
            if not upstream:
                return "google"
            # Check explicit tag first, then fall back to format detection.
            stored = upstream.raw_token_data.get("_mint_provider")
            if stored in self._configs:
                return stored
            return self._detect_provider(upstream.access_token)
        except Exception:
            return "google"

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        provider = await self._detect_refresh_provider(refresh_token.token)
        config = self._configs[provider]

        # SSO token endpoint must be resolved from OIDC discovery.
        token_endpoint = config.token_endpoint
        if provider == "sso" and self._oidc_cache is not None:
            token_endpoint = await self._oidc_cache.token_endpoint()

        # ContextVar is per-task, so no lock needed for _current_provider_var.
        # The credential swap on instance attributes still needs the lock to
        # serialise against concurrent refreshes.
        _token = _current_provider_var.set(provider)
        try:
            async with self._credential_lock:
                saved = (
                    self._upstream_client_id,
                    self._upstream_client_secret,
                    self._upstream_token_endpoint,
                    self._extra_token_params,
                )
                self._upstream_client_id = config.client_id
                self._upstream_client_secret = SecretStr(config.client_secret)
                self._upstream_token_endpoint = token_endpoint
                self._extra_token_params = {}
                try:
                    return await super().exchange_refresh_token(
                        client, refresh_token, scopes
                    )
                finally:
                    (
                        self._upstream_client_id,
                        self._upstream_client_secret,
                        self._upstream_token_endpoint,
                        self._extra_token_params,
                    ) = saved
        finally:
            _current_provider_var.reset(_token)

    # ------------------------------------------------------------------
    # Claims — embed provider in the FastMCP JWT
    # ------------------------------------------------------------------

    async def _extract_upstream_claims(
        self, idp_tokens: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Extract user-identifying claims from upstream IdP tokens.

        These claims are embedded in the FastMCP JWT under "upstream_claims"
        and serve two critical multi-tenant isolation purposes:

        1. **User identity**: Every JWT contains `sub` and `email` regardless
           of provider, enabling audit logging and access control decisions
           without additional API calls.

        2. **Provider routing**: The `provider` claim tells downstream
           dependencies which email/calendar implementation to instantiate.

        For Microsoft, claims are extracted from the Azure AD JWT access token.
        For Google, claims are extracted from the OIDC id_token (returned when
        the `openid` scope is requested). This ensures parity between providers
        -- both embed user identity at token issuance time.
        """
        access_token = idp_tokens.get("access_token", "")
        provider = idp_tokens.get(
            "_mint_provider", self._detect_provider(access_token)
        )

        claims: dict[str, Any] = {"provider": provider}

        if provider == "sso":
            # Prefer validated ID token claims stashed during the callback.
            id_claims = idp_tokens.get("_sso_id_claims", {})
            for key in ("sub", "email", "name", "preferred_username"):
                if key in id_claims:
                    claims[key] = id_claims[key]

            # Fallback: if the access token is a JWT, try extracting claims
            # from it (some enterprise IdPs issue JWT access tokens).
            if "sub" not in claims and access_token.count(".") == 2:
                try:
                    payload = decode_jwt_payload(access_token)
                    for key in ("sub", "email", "name", "preferred_username"):
                        if key in payload and key not in claims:
                            claims[key] = payload[key]
                except Exception:
                    pass

        elif provider == "microsoft":
            try:
                payload = decode_jwt_payload(access_token)
                for key in (
                    "sub", "oid", "tid", "name",
                    "preferred_username", "email",
                ):
                    if key in payload:
                        claims[key] = payload[key]
            except Exception:
                pass
        else:
            # Google returns an id_token when the `openid` scope is requested.
            # Decode it (without signature verification -- we received it
            # directly from Google's token endpoint over TLS) to extract user
            # identity claims for embedding in the FastMCP JWT.
            id_token = idp_tokens.get("id_token", "")
            if id_token:
                try:
                    payload = decode_jwt_payload(id_token)
                    for key in ("sub", "email", "name", "picture"):
                        if key in payload:
                            claims[key] = payload[key]
                except Exception:
                    logger.debug("Failed to decode Google id_token for claims extraction")

        return claims

    # ------------------------------------------------------------------
    # Token loading — inject upstream_claims into AccessToken
    # ------------------------------------------------------------------

    async def load_access_token(self, token: str) -> AccessToken | None:
        """Validate the FastMCP JWT and augment AccessToken with upstream_claims.

        Multi-tenant isolation is enforced through a two-layer validation chain:

        1. **FastMCP JWT verification** (this layer): Verifies HS256 signature,
           expiry (`exp`), issuer (`iss`), and audience (`aud`). Extracts the
           `upstream_claims` embedded at issuance time (containing `sub`,
           `email`, and `provider`).

        2. **Upstream token verification** (parent `OAuthProxy.load_access_token`):
           Looks up the real upstream access token via JTI mapping and validates
           it with the actual provider (Google tokeninfo API or Azure JWKS).
           This ensures the upstream token has not been revoked.

        Both layers must pass for the request to proceed. The upstream access
        token is then used by provider instances (see `dependencies.py`) to
        call Gmail/Outlook/Calendar APIs, ensuring each user can only access
        their own data.
        """
        try:
            payload = self.jwt_issuer.verify_token(token)
            upstream_claims = payload.get("upstream_claims") or {}
        except Exception:
            upstream_claims = {}

        validated = await super().load_access_token(token)
        if not validated:
            return None

        if upstream_claims:
            merged = {**validated.claims, "upstream_claims": upstream_claims}
            return validated.model_copy(update={"claims": merged})
        return validated
