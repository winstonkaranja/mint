from __future__ import annotations

from mint.config import Settings


def build_auth(settings: Settings):
    if settings.provider == "google":
        from fastmcp.server.auth.providers.google import GoogleProvider

        return GoogleProvider(
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret,
            base_url=settings.base_url,
            required_scopes=[
                "openid",
                "https://www.googleapis.com/auth/userinfo.email",
                "https://www.googleapis.com/auth/userinfo.profile",
                "https://www.googleapis.com/auth/gmail.modify",
                "https://www.googleapis.com/auth/calendar",
            ],
        )

    if settings.provider == "microsoft":
        from fastmcp.server.auth.providers.azure import AzureProvider

        return AzureProvider(
            client_id=settings.azure_client_id,
            client_secret=settings.azure_client_secret,
            tenant_id=settings.azure_tenant_id,
            base_url=settings.base_url,
            required_scopes=["mcp-access"],
            additional_authorize_scopes=[
                "https://graph.microsoft.com/Mail.Read",
                "https://graph.microsoft.com/Mail.ReadWrite",
                "https://graph.microsoft.com/Calendars.ReadWrite",
                "offline_access",
            ],
        )

    raise ValueError(f"Unknown provider: {settings.provider}")
