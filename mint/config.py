from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    base_url: str = "http://localhost:8000"

    # JWT signing key for FastMCP-issued tokens. When set, this value is used
    # (via PBKDF2 key derivation) to sign all access and refresh JWTs.
    # When empty, MintProvider derives a stable key from the concatenation of
    # google_client_secret and azure_client_secret -- acceptable for single-
    # instance deployments but less ideal for key rotation. In production,
    # set MINT_SECRET_KEY to a high-entropy random string (>= 32 chars).
    secret_key: str = ""

    # Google OAuth -- required for Google provider.
    # These secrets also participate in JWT key derivation when secret_key
    # is not explicitly configured. Rotate with care.
    google_client_id: str = ""
    google_client_secret: str = ""

    # Microsoft / Azure OAuth -- required for Microsoft provider.
    azure_client_id: str = ""
    azure_client_secret: str = ""
    azure_tenant_id: str = ""

    # Postgres URL for OAuth client/token storage (encrypted with the JWT key). Without it, FastMCP
    # keeps them on local disk, which a scale-to-zero container loses, forcing users to reconnect.
    storage_url: str = ""

    # Google Maps — for travel time estimates in calendar events.
    # $200/month free credit covers ~40,000 Directions API requests.
    # When empty, location classification and free links still work.
    google_maps_api_key: str = ""

    # SSO / Enterprise OIDC
    sso_enabled: bool = False
    sso_provider: str | None = None  # "okta", "azure_ad", "google_workspace", or custom
    sso_issuer_url: str | None = None  # e.g. https://mycompany.okta.com
    sso_client_id: str | None = None
    sso_client_secret: str | None = None
    sso_enforce_only: bool = False  # if True, disable Google/Microsoft consumer login

    # Data residency — restrict processing to specific Azure regions.
    # When set, the /health endpoint reports the configured region and
    # deployment metadata. The server itself is stateless (no email content
    # persisted), so residency is enforced at the infrastructure layer
    # (Azure Container Apps region selection + ACR geo-replication).
    data_region: str | None = None  # e.g. "eu-west", "us-east", "af-south"
    data_residency_strict: bool = False  # if True, reject requests missing region header

    # SCIM bearer token for enterprise user provisioning endpoints.
    # IdPs (Okta, Azure AD) authenticate SCIM requests with this token.
    scim_bearer_token: str | None = None

    model_config = {"env_prefix": "MINT_", "env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
