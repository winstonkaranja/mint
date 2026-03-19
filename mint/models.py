from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class ScoringOverrides(BaseModel):
    boost_senders: list[str] = []
    demote_senders: list[str] = []
    boost_domains: list[str] = []
    demote_domains: list[str] = []
    boost_keywords: list[str] = []
    demote_keywords: list[str] = []


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


class DraftSummary(BaseModel):
    id: str
    subject: str
    to: list[str]
    snippet: str
    date: datetime


class CalendarEvent(BaseModel):
    id: str
    title: str
    start: datetime
    end: datetime
    location: str | None = None
    description: str | None = None
    attendees: list[str] = []
    is_all_day: bool = False
    recurring_event_id: str | None = None


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
    thread_id: str | None = None
    awaiting_reply: bool = False
    awaiting_reply_hours: int | None = None
    participants: list[str] = []
    last_reply_by: str | None = None


class AttendeeEmail(BaseModel):
    sender: str
    subject: str
    date: datetime
    is_read: bool


class PrepBundle(BaseModel):
    unread_emails: list[AttendeeEmail] = []
    recent_subjects: dict[str, str] = {}  # attendee_email -> most recent subject
    shared_links: list[str] = []
    contact_frequency: dict[str, int] = {}


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
    prep_bundle: PrepBundle | None = None
    conflicts_with: list[str] = []
    recurring_event_id: str | None = None
    is_recurring: bool = False
    recurrence_label: str | None = None


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


class WeeklyDigest(BaseModel):
    week_start: str
    week_end: str
    total_emails: int
    total_unread: int
    top_senders: list[dict]
    busiest_day: str | None
    total_meetings: int
    timezone: str = "UTC"
