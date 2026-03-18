"""Provider registry for multi-provider email and calendar backends.

New providers are registered at module import time so that the rest of the
application can resolve a provider class by name (e.g. "google", "microsoft")
without hard-coding if/else chains.  To add a third provider:

    1. Create ``mint/providers/acme.py`` with classes satisfying the
       ``EmailProvider`` / ``CalendarProvider`` protocols.
    2. Import and register them at the bottom of this file.

The registry intentionally falls back to the "google" provider when an
unknown name is requested, preserving backward compatibility with tokens
that predate the ``provider`` claim.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mint.providers.base import CalendarProvider, EmailProvider

if TYPE_CHECKING:
    import httpx

# ---------------------------------------------------------------------------
# Registry internals
# ---------------------------------------------------------------------------

# Maps a provider name (e.g. "google") to its concrete class.  The classes
# must accept ``(client: httpx.AsyncClient, token: str)`` as __init__ args
# and satisfy the corresponding Protocol from ``mint.providers.base``.
_email_providers: dict[str, type] = {}
_calendar_providers: dict[str, type] = {}

_DEFAULT_PROVIDER = "google"


def register_email_provider(name: str, cls: type) -> None:
    """Register an ``EmailProvider`` implementation under *name*."""
    _email_providers[name] = cls


def register_calendar_provider(name: str, cls: type) -> None:
    """Register a ``CalendarProvider`` implementation under *name*."""
    _calendar_providers[name] = cls


def get_email_provider_class(name: str) -> type:
    """Look up an email provider class by name, defaulting to Google."""
    return _email_providers.get(name) or _email_providers[_DEFAULT_PROVIDER]


def get_calendar_provider_class(name: str) -> type:
    """Look up a calendar provider class by name, defaulting to Google."""
    return _calendar_providers.get(name) or _calendar_providers[_DEFAULT_PROVIDER]


# ---------------------------------------------------------------------------
# Provider registration
# ---------------------------------------------------------------------------

from mint.providers.google import GmailProvider, GoogleCalendarProvider  # noqa: E402
from mint.providers.microsoft import (  # noqa: E402
    MicrosoftCalendarProvider,
    OutlookProvider,
)

register_email_provider("google", GmailProvider)
register_email_provider("microsoft", OutlookProvider)
register_calendar_provider("google", GoogleCalendarProvider)
register_calendar_provider("microsoft", MicrosoftCalendarProvider)
