from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    base_url: str = "http://localhost:8000"
    secret_key: str = ""

    # Google OAuth
    google_client_id: str = ""
    google_client_secret: str = ""

    # Microsoft / Azure OAuth
    azure_client_id: str = ""
    azure_client_secret: str = ""
    azure_tenant_id: str = ""

    model_config = {"env_prefix": "MINT_", "env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
