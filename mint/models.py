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
