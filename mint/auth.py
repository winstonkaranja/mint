"""Multi-provider OAuth proxy for Mint.

Subclasses OAuthProxy to replace the consent page with a provider picker,
dynamically route to Google or Microsoft OAuth, and embed the chosen
provider in JWT claims so downstream dependencies can resolve the correct
email/calendar implementation.
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
from mint.pages import provider_picker_html

logger = get_logger(__name__)

# Task-local provider context — safe across concurrent asyncio coroutines.
_current_provider_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_current_provider", default=None
)

# Maximum age (seconds) for entries in _provider_choices before eviction.
_PROVIDER_CHOICE_TTL = 20 * 60  # 20 minutes (slightly longer than txn TTL)


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
    """OAuthProxy subclass that lets the user pick Google or Microsoft."""

    def __init__(self, settings: Settings):
        self._configs = _build_configs(settings)

        # Per-transaction provider choice: maps txn_id → (provider, timestamp).
        # Entries are evicted in _evict_stale_choices() on every write.
        self._provider_choices: dict[str, tuple[str, float]] = {}

        # Serialises credential-swapping in exchange_refresh_token.
        self._credential_lock = asyncio.Lock()

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

        html = provider_picker_html(txn_id, csrf_token)
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
        """Intercept the POST to capture the provider choice, then delegate."""
        form = await request.form()
        txn_id = str(form.get("txn_id", ""))
        provider = str(form.get("provider", "google"))
        if provider not in self._configs:
            provider = "google"
        if txn_id:
            self._store_choice(txn_id, provider)
        return await super()._submit_consent(request)

    # ------------------------------------------------------------------
    # Build upstream authorize URL for the chosen provider
    # ------------------------------------------------------------------

    def _build_upstream_authorize_url(
        self, txn_id: str, transaction: dict[str, Any]
    ) -> str:
        provider = self._get_choice(txn_id)
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

    # ------------------------------------------------------------------
    # Scope preparation hooks (called by parent during token exchange)
    # ------------------------------------------------------------------

    def _prepare_scopes_for_token_exchange(self, scopes: list[str]) -> list[str]:
        if _current_provider_var.get() == "microsoft":
            return self._configs["microsoft"].token_exchange_scopes
        return scopes

    def _prepare_scopes_for_upstream_refresh(self, scopes: list[str]) -> list[str]:
        if _current_provider_var.get() == "microsoft":
            return self._configs["microsoft"].refresh_scopes
        return scopes

    # ------------------------------------------------------------------
    # IdP callback — override to use provider-specific credentials
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

            oauth_client = AsyncOAuth2Client(
                client_id=config.client_id,
                client_secret=config.client_secret,
                token_endpoint_auth_method=self._token_endpoint_auth_method,
                timeout=HTTP_TIMEOUT_SECONDS,
            )

            try:
                redirect_uri = (
                    f"{str(self.base_url).rstrip('/')}{self._redirect_path}"
                )
                token_params: dict[str, Any] = {
                    "url": config.token_endpoint,
                    "code": idp_code,
                    "redirect_uri": redirect_uri,
                }

                verifier = transaction.get("proxy_code_verifier")
                if verifier:
                    token_params["code_verifier"] = verifier

                exchange_scopes = self._prepare_scopes_for_token_exchange(
                    transaction.get("scopes") or []
                )
                if exchange_scopes:
                    token_params["scope"] = " ".join(exchange_scopes)

                idp_tokens: dict[str, Any] = await oauth_client.fetch_token(
                    **token_params
                )
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
                self._upstream_token_endpoint = config.token_endpoint
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
        access_token = idp_tokens.get("access_token", "")
        provider = idp_tokens.get(
            "_mint_provider", self._detect_provider(access_token)
        )

        claims: dict[str, Any] = {"provider": provider}

        if provider == "microsoft":
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
            # For Google, the verifier already extracts user info.  We just
            # record the provider so dependencies can route correctly.
            pass

        return claims

    # ------------------------------------------------------------------
    # Token loading — inject upstream_claims into AccessToken
    # ------------------------------------------------------------------

    async def load_access_token(self, token: str) -> AccessToken | None:
        """Augment the validated AccessToken with upstream_claims from our JWT."""
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
