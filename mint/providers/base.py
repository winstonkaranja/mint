from __future__ import annotations

from datetime import datetime
from typing import Protocol

from mint.models import (
    CalendarEvent,
    CreateEventRequest,
    DraftEmailRequest,
    DraftSummary,
    Email,
    EmailSummary,
    UpdateEventRequest,
)


class EmailProvider(Protocol):
    async def get_inbox(self, max_results: int = 20, mailbox: str | None = None) -> list[EmailSummary]: ...
    async def get_email(self, email_id: str, mailbox: str | None = None) -> Email: ...
    async def draft_email(self, request: DraftEmailRequest, mailbox: str | None = None) -> str: ...
    async def search_emails(
        self, query: str, max_results: int = 20, mailbox: str | None = None
    ) -> list[EmailSummary]: ...
    async def get_thread_messages(self, email_id: str, mailbox: str | None = None) -> list[dict]: ...
    async def list_drafts(self, max_results: int = 20, mailbox: str | None = None) -> list[DraftSummary]: ...


class CalendarProvider(Protocol):
    async def list_events(
        self, start: datetime, end: datetime, max_results: int = 20, mailbox: str | None = None
    ) -> list[CalendarEvent]: ...
    async def get_event(self, event_id: str, mailbox: str | None = None) -> CalendarEvent: ...
    async def create_event(self, request: CreateEventRequest, mailbox: str | None = None) -> CalendarEvent: ...
    async def update_event(
        self, event_id: str, request: UpdateEventRequest, mailbox: str | None = None
    ) -> CalendarEvent: ...
    async def delete_event(self, event_id: str, mailbox: str | None = None) -> bool: ...
