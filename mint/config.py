from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    provider: Literal["google", "microsoft"] = "google"
    base_url: str = "http://localhost:8000"

    # Google OAuth
    google_client_id: str = ""
    google_client_secret: str = ""

    # Microsoft / Azure OAuth
    azure_client_id: str = ""
    azure_client_secret: str = ""
    azure_tenant_id: str = ""

    model_config = {"env_prefix": "MINT_", "env_file": ".env"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
