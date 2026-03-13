from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from fastmcp.dependencies import CurrentAccessToken, Depends
from fastmcp.server.dependencies import AccessToken

from mint.config import get_settings
from mint.providers.base import CalendarProvider, EmailProvider
from mint.providers.google import GmailProvider, GoogleCalendarProvider
from mint.providers.microsoft import MicrosoftCalendarProvider, OutlookProvider


@asynccontextmanager
async def get_http_client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(timeout=30) as client:
        yield client


async def get_email_provider(
    client: httpx.AsyncClient = Depends(get_http_client),
    access_token: AccessToken = CurrentAccessToken(),
) -> EmailProvider:
    settings = get_settings()
    token = access_token.token
    if settings.provider == "microsoft":
        return OutlookProvider(client=client, token=token)
    return GmailProvider(client=client, token=token)


async def get_calendar_provider(
    client: httpx.AsyncClient = Depends(get_http_client),
    access_token: AccessToken = CurrentAccessToken(),
) -> CalendarProvider:
    settings = get_settings()
    token = access_token.token
    if settings.provider == "microsoft":
        return MicrosoftCalendarProvider(client=client, token=token)
    return GoogleCalendarProvider(client=client, token=token)
