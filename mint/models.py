from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class EmailSummary(BaseModel):
    id: str
    subject: str
    sender: str
    date: datetime
    snippet: str
    is_read: bool
    thread_id: str | None = None


class Email(BaseModel):
    id: str
    subject: str
    sender: str
    to: list[str]
    cc: list[str] = []
    date: datetime
    body: str
    is_read: bool


class DraftEmailRequest(BaseModel):
    to: list[str]
    subject: str
    body: str
    cc: list[str] = []
    bcc: list[str] = []


class CalendarEvent(BaseModel):
    id: str
    title: str
    start: datetime
    end: datetime
    location: str | None = None
    description: str | None = None
    attendees: list[str] = []
    is_all_day: bool = False


class CreateEventRequest(BaseModel):
    title: str
    start: datetime
    end: datetime
    location: str | None = None
    description: str | None = None
    attendees: list[str] = []


class UpdateEventRequest(BaseModel):
    title: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    location: str | None = None
    description: str | None = None
    attendees: list[str] | None = None


class ThreadMessage(BaseModel):
    id: str
    sender: str
    date: datetime
    body: str
    is_read: bool


class EmailThread(BaseModel):
    thread_id: str
    subject: str
    messages: list[ThreadMessage]


class PriorityEmail(BaseModel):
    id: str
    subject: str
    sender: str
    date: datetime
    snippet: str
    is_read: bool
    score: float
    signals: list[str]
    related_event: str | None = None
    tier: str = "low"
    message_count: int = 1


class EnrichedEvent(BaseModel):
    id: str
    title: str
    start: datetime
    end: datetime
    location: str | None = None
    description: str | None = None
    attendees: list[str] = []
    is_all_day: bool = False
    unread_from_attendees: int = 0
    start_local: str | None = None
    end_local: str | None = None


class DailyBrief(BaseModel):
    date: str
    meetings: list[EnrichedEvent]
    priority_emails: list[PriorityEmail]
    pending_thread_count: int
    highlights: list[str]
    timezone: str = "UTC"


class DaySummary(BaseModel):
    date: str
    emails_received: int
    emails_unread: int
    meetings: list[EnrichedEvent]
    pending_emails: list[PriorityEmail]
    tomorrow_first_events: list[EnrichedEvent]
    action_items: list[str]
    timezone: str = "UTC"
