"""Intelligence-gated tools: 7 focused tools backed by server-side scoring and cross-referencing."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from fastmcp import FastMCP
from fastmcp.dependencies import Depends

from mint.dependencies import get_calendar_provider, get_email_provider
from mint.intelligence import (
    enrich_events,
    generate_action_items,
    generate_highlights,
    rank_emails,
)
from mint.models import (
    CalendarEvent,
    CreateEventRequest,
    DailyBrief,
    DaySummary,
    DraftEmailRequest,
    EmailThread,
    EnrichedEvent,
    PriorityEmail,
    ThreadMessage,
)
from mint.providers.base import CalendarProvider, EmailProvider


def register_smart_tools(mcp: FastMCP) -> None:

    @mcp.tool(annotations={"readOnlyHint": True})
    async def get_daily_brief(
        email_provider: EmailProvider = Depends(get_email_provider),
        calendar_provider: CalendarProvider = Depends(get_calendar_provider),
    ) -> DailyBrief:
        """Morning snapshot: today's meetings enriched with related unread emails, top priority emails scored by urgency, and cross-referenced highlights. One call replaces reading inbox + checking calendar."""
        now = datetime.utcnow()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timedelta(days=2)  # include tomorrow for scoring

        emails_task = email_provider.get_inbox(max_results=50)
        events_task = calendar_provider.list_events(today_start, today_end, max_results=30)
        emails, events = await asyncio.gather(emails_task, events_task)

        priority = rank_emails(emails, events, now, limit=10)
        today_events = [e for e in events if e.start.date() == now.date()]
        enriched = enrich_events(today_events, emails)
        highlights = generate_highlights(enriched, priority)

        # Count distinct threads with unread emails
        unread_threads = {e.thread_id for e in emails if not e.is_read and e.thread_id}
        pending_count = len(unread_threads) or sum(1 for e in emails if not e.is_read)

        return DailyBrief(
            date=now.strftime("%Y-%m-%d"),
            meetings=enriched,
            priority_emails=priority,
            pending_thread_count=pending_count,
            highlights=highlights,
        )

    @mcp.tool(annotations={"readOnlyHint": True})
    async def get_priority_emails(
        limit: int = 10,
        email_provider: EmailProvider = Depends(get_email_provider),
        calendar_provider: CalendarProvider = Depends(get_calendar_provider),
    ) -> list[PriorityEmail]:
        """Top emails scored by urgency. Each email gets a 0.0-1.0 priority score based on calendar relevance, urgency keywords, recency, and thread activity. Returns the top results, not the full inbox."""
        now = datetime.utcnow()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timedelta(days=2)

        emails_task = email_provider.get_inbox(max_results=50)
        events_task = calendar_provider.list_events(today_start, today_end, max_results=30)
        emails, events = await asyncio.gather(emails_task, events_task)

        return rank_emails(emails, events, now, limit=limit)

    @mcp.tool(annotations={"readOnlyHint": True})
    async def get_email_thread(
        email_id: str,
        email_provider: EmailProvider = Depends(get_email_provider),
    ) -> EmailThread:
        """Get the full conversation thread for an email. Returns all messages in the thread with sender, date, and body."""
        # Get the email first for the subject
        email = await email_provider.get_email(email_id)
        raw_messages = await email_provider.get_thread_messages(email_id)

        messages = [
            ThreadMessage(
                id=m["id"],
                sender=m["sender"],
                date=m["date"],
                body=m["body"],
                is_read=m["is_read"],
            )
            for m in raw_messages
        ]

        thread_id = email_id  # fallback
        if raw_messages:
            thread_id = raw_messages[0].get("thread_id", email_id)

        return EmailThread(
            thread_id=thread_id,
            subject=email.subject,
            messages=messages,
        )

    @mcp.tool()
    async def draft_email_reply(
        email_id: str,
        body: str,
        cc: list[str] = [],
        bcc: list[str] = [],
        email_provider: EmailProvider = Depends(get_email_provider),
    ) -> str:
        """Draft a reply to an email. Reads the original email, creates a reply draft with Re: subject addressed to the sender. Never sends — the user reviews and sends from their mail client. Returns the draft ID."""
        original = await email_provider.get_email(email_id)
        subject = original.subject
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"

        request = DraftEmailRequest(
            to=[original.sender],
            subject=subject,
            body=body,
            cc=cc,
            bcc=bcc,
        )
        return await email_provider.draft_email(request)

    @mcp.tool(annotations={"readOnlyHint": True})
    async def get_upcoming_events(
        days: int = 7,
        email_provider: EmailProvider = Depends(get_email_provider),
        calendar_provider: CalendarProvider = Depends(get_calendar_provider),
    ) -> list[EnrichedEvent]:
        """Calendar events for the next N days, enriched with unread email counts from attendees. Shows which meetings have related unread emails so you can prepare."""
        now = datetime.utcnow()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=days)

        emails_task = email_provider.get_inbox(max_results=50)
        events_task = calendar_provider.list_events(start, end, max_results=50)
        emails, events = await asyncio.gather(emails_task, events_task)

        return enrich_events(events, emails)

    @mcp.tool()
    async def create_reminder(
        title: str,
        start: datetime,
        description: str | None = None,
        calendar_provider: CalendarProvider = Depends(get_calendar_provider),
    ) -> CalendarEvent:
        """Create a personal calendar reminder. Sets a 30-minute event with no attendees. Use for tasks, follow-ups, or prep reminders."""
        if not title.strip():
            raise ValueError("Reminder title cannot be empty")
        request = CreateEventRequest(
            title=title,
            start=start,
            end=start + timedelta(minutes=30),
            description=description,
            attendees=[],
        )
        return await calendar_provider.create_event(request)

    @mcp.tool(annotations={"readOnlyHint": True})
    async def summarize_day(
        email_provider: EmailProvider = Depends(get_email_provider),
        calendar_provider: CalendarProvider = Depends(get_calendar_provider),
    ) -> DaySummary:
        """End-of-day summary: emails received, unread count, today's meetings with attendee email activity, pending high-priority emails, tomorrow's first events, and suggested action items."""
        now = datetime.utcnow()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timedelta(days=1)
        tomorrow_end = today_end + timedelta(days=1)

        emails_task = email_provider.get_inbox(max_results=50)
        today_events_task = calendar_provider.list_events(today_start, today_end, max_results=30)
        tomorrow_events_task = calendar_provider.list_events(today_end, tomorrow_end, max_results=5)
        emails, today_events, tomorrow_events = await asyncio.gather(
            emails_task, today_events_task, tomorrow_events_task
        )

        # Include tomorrow's events for scoring
        all_events = today_events + tomorrow_events
        priority = rank_emails(emails, all_events, now, limit=10)
        enriched_today = enrich_events(today_events, emails)
        enriched_tomorrow = enrich_events(tomorrow_events, emails)

        pending = [e for e in priority if not e.is_read and e.score >= 0.40]
        action_items = generate_action_items(pending, enriched_tomorrow)

        today_emails = [e for e in emails if e.date.date() == now.date()]

        return DaySummary(
            date=now.strftime("%Y-%m-%d"),
            emails_received=len(today_emails),
            emails_unread=sum(1 for e in today_emails if not e.is_read),
            meetings=enriched_today,
            pending_emails=pending,
            tomorrow_first_events=enriched_tomorrow,
            action_items=action_items,
        )
