from __future__ import annotations

from datetime import datetime
from typing import Protocol

from mint.models import (
    CalendarEvent,
    CreateEventRequest,
    DraftEmailRequest,
    Email,
    EmailSummary,
    UpdateEventRequest,
)


class EmailProvider(Protocol):
    async def get_inbox(self, max_results: int = 20) -> list[EmailSummary]: ...
    async def get_email(self, email_id: str) -> Email: ...
    async def draft_email(self, request: DraftEmailRequest) -> str: ...
    async def search_emails(
        self, query: str, max_results: int = 20
    ) -> list[EmailSummary]: ...


class CalendarProvider(Protocol):
    async def list_events(
        self, start: datetime, end: datetime, max_results: int = 20
    ) -> list[CalendarEvent]: ...
    async def get_event(self, event_id: str) -> CalendarEvent: ...
    async def create_event(self, request: CreateEventRequest) -> CalendarEvent: ...
    async def update_event(
        self, event_id: str, request: UpdateEventRequest
    ) -> CalendarEvent: ...
    async def delete_event(self, event_id: str) -> bool: ...
