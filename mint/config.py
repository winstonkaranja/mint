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

    # SSO / Enterprise OIDC
    sso_enabled: bool = False
    sso_provider: str | None = None  # "okta", "azure_ad", "google_workspace", or custom
    sso_issuer_url: str | None = None  # e.g. https://mycompany.okta.com
    sso_client_id: str | None = None
    sso_client_secret: str | None = None
    sso_enforce_only: bool = False  # if True, disable Google/Microsoft consumer login

    model_config = {"env_prefix": "MINT_", "env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
