"""Intelligence-gated tools: 10 focused tools backed by server-side scoring and cross-referencing."""

from __future__ import annotations

import asyncio
import time
from collections import Counter
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from fastmcp import FastMCP
from fastmcp.dependencies import Depends

from mint.audit import log_tool_call
from mint.dependencies import get_calendar_provider, get_email_provider, get_user_email
from mint.errors import (
    MintToolError,
    invalid_input,
    not_found,
    provider_error,
    rate_limited,
)
from mint.intelligence import (
    build_relationship_map,
    detect_awaiting_replies,
    enrich_events,
    generate_action_items,
    generate_highlights,
    rank_emails,
    scan_for_sensitive_content,
)
from mint.models import (
    CalendarEvent,
    CreateEventRequest,
    DailyBrief,
    DaySummary,
    DraftEmailRequest,
    DraftSummary,
    EmailThread,
    EnrichedEvent,
    PriorityEmail,
    ScoringOverrides,
    ThreadMessage,
    WeeklyDigest,
)
from mint.providers.base import CalendarProvider, EmailProvider


def _resolve_timezone(timezone: str) -> ZoneInfo:
    """Parse an IANA timezone name, falling back to UTC on invalid input."""
    try:
        return ZoneInfo(timezone)
    except (KeyError, ValueError):
        return ZoneInfo("UTC")


def _raise_for_http_error(exc: httpx.HTTPStatusError, resource_type: str = "", resource_id: str = "") -> None:
    """Translate an HTTP status error into the appropriate MintToolError."""
    status = exc.response.status_code
    if status == 404 and resource_type:
        raise not_found(resource_type, resource_id) from exc
    if status == 429:
        raise rate_limited() from exc
    raise provider_error(f"HTTP {status}: {exc.response.text}") from exc


def register_smart_tools(mcp: FastMCP) -> None:

    @mcp.tool(annotations={"readOnlyHint": True})
    async def get_daily_brief(
        timezone: str = "UTC",
        scoring_overrides: ScoringOverrides | None = None,
        user_email: str | None = Depends(get_user_email),
        email_provider: EmailProvider = Depends(get_email_provider),
        calendar_provider: CalendarProvider = Depends(get_calendar_provider),
    ) -> DailyBrief:
        """Morning snapshot: today's meetings enriched with related unread emails, top priority emails scored by urgency, and cross-referenced highlights. One call replaces reading inbox + checking calendar."""
        _start = time.monotonic()
        _params = {"timezone": timezone}
        try:
            tz = _resolve_timezone(timezone)
            now = datetime.now(tz)
            now_utc = datetime.utcnow()  # naive UTC for scoring (email dates are naive)
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            today_end = today_start + timedelta(days=2)  # include tomorrow for scoring

            try:
                emails_task = email_provider.get_inbox(max_results=50)
                events_task = calendar_provider.list_events(today_start, today_end, max_results=30)
                emails, events = await asyncio.gather(emails_task, events_task)
            except httpx.HTTPStatusError as exc:
                _raise_for_http_error(exc)
            except Exception as exc:
                raise provider_error(str(exc)) from exc

            priority = rank_emails(emails, events, now_utc, limit=10, overrides=scoring_overrides)
            relationship_map = build_relationship_map(emails)
            today_events = [e for e in events if e.start.date() == now.date()]
            enriched = enrich_events(today_events, emails, tz=tz, relationship_map=relationship_map)
            highlights = generate_highlights(enriched, priority, tz=tz)

            # Detect threads awaiting a reply and surface them in highlights
            awaiting = detect_awaiting_replies(priority, now_utc)
            if awaiting:
                highlights.append(
                    f"{len(awaiting)} thread(s) awaiting reply for 48+ hours"
                )

            # Count distinct threads with unread emails
            unread_threads = {e.thread_id for e in emails if not e.is_read and e.thread_id}
            pending_count = len(unread_threads) or sum(1 for e in emails if not e.is_read)

            result = DailyBrief(
                date=now.strftime("%Y-%m-%d"),
                meetings=enriched,
                priority_emails=priority,
                pending_thread_count=pending_count,
                highlights=highlights,
                timezone=timezone,
            )
            log_tool_call("get_daily_brief", user_email, _params, "success", (time.monotonic() - _start) * 1000)
            return result
        except MintToolError as exc:
            log_tool_call("get_daily_brief", user_email, _params, "error", (time.monotonic() - _start) * 1000, error_code=exc.error.error_code)
            raise

    @mcp.tool(annotations={"readOnlyHint": True})
    async def get_priority_emails(
        limit: int = 10,
        scoring_overrides: ScoringOverrides | None = None,
        user_email: str | None = Depends(get_user_email),
        email_provider: EmailProvider = Depends(get_email_provider),
        calendar_provider: CalendarProvider = Depends(get_calendar_provider),
    ) -> list[PriorityEmail]:
        """Top emails scored by urgency. Each email gets a 0.0-1.0 priority score based on calendar relevance, urgency keywords, recency, and thread activity. Returns the top results, not the full inbox."""
        _start = time.monotonic()
        _params = {"limit": limit}
        try:
            now = datetime.utcnow()
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            today_end = today_start + timedelta(days=2)

            try:
                emails_task = email_provider.get_inbox(max_results=50)
                events_task = calendar_provider.list_events(today_start, today_end, max_results=30)
                emails, events = await asyncio.gather(emails_task, events_task)
            except httpx.HTTPStatusError as exc:
                _raise_for_http_error(exc)
            except Exception as exc:
                raise provider_error(str(exc)) from exc

            result = rank_emails(emails, events, now, limit=limit, overrides=scoring_overrides)
            log_tool_call("get_priority_emails", user_email, _params, "success", (time.monotonic() - _start) * 1000)
            return result
        except MintToolError as exc:
            log_tool_call("get_priority_emails", user_email, _params, "error", (time.monotonic() - _start) * 1000, error_code=exc.error.error_code)
            raise

    @mcp.tool(annotations={"readOnlyHint": True})
    async def get_email_thread(
        email_id: str,
        user_email: str | None = Depends(get_user_email),
        email_provider: EmailProvider = Depends(get_email_provider),
    ) -> EmailThread:
        """Get the full conversation thread for an email. Returns all messages in the thread with sender, date, and body."""
        _start = time.monotonic()
        _params = {"email_id": email_id}
        try:
            try:
                email = await email_provider.get_email(email_id)
                raw_messages = await email_provider.get_thread_messages(email_id)
            except httpx.HTTPStatusError as exc:
                _raise_for_http_error(exc, resource_type="email", resource_id=email_id)
            except Exception as exc:
                raise provider_error(str(exc)) from exc

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

            result = EmailThread(
                thread_id=thread_id,
                subject=email.subject,
                messages=messages,
            )
            log_tool_call("get_email_thread", user_email, _params, "success", (time.monotonic() - _start) * 1000)
            return result
        except MintToolError as exc:
            log_tool_call("get_email_thread", user_email, _params, "error", (time.monotonic() - _start) * 1000, error_code=exc.error.error_code)
            raise

    @mcp.tool()
    async def draft_email_reply(
        email_id: str,
        body: str,
        cc: list[str] = [],
        bcc: list[str] = [],
        user_email: str | None = Depends(get_user_email),
        email_provider: EmailProvider = Depends(get_email_provider),
    ) -> str:
        """Draft a reply to an email. Reads the original email, creates a reply draft with Re: subject addressed to the sender. Never sends — the user reviews and sends from their mail client. Returns the draft ID."""
        _start = time.monotonic()
        _params = {"email_id": email_id, "body": body, "cc": cc, "bcc": bcc}
        try:
            try:
                original = await email_provider.get_email(email_id)
            except httpx.HTTPStatusError as exc:
                _raise_for_http_error(exc, resource_type="email", resource_id=email_id)
            except Exception as exc:
                raise provider_error(str(exc)) from exc

            subject = original.subject
            if not subject.lower().startswith("re:"):
                subject = f"Re: {subject}"

            # DLP: block drafts containing sensitive data patterns
            findings = scan_for_sensitive_content(body)
            if findings:
                raise invalid_input(
                    f"Draft blocked: sensitive content detected \N{EM DASH} {', '.join(findings)}",
                    "Remove or redact sensitive data before drafting",
                )

            # DLP scan before drafting
            findings = scan_for_sensitive_content(body)
            if findings:
                raise invalid_input(
                    f"Draft blocked: sensitive content detected — {', '.join(findings)}",
                    "Remove or redact sensitive data before drafting",
                )

            request = DraftEmailRequest(
                to=[original.sender],
                subject=subject,
                body=body,
                cc=cc,
                bcc=bcc,
            )

            try:
                result = await email_provider.draft_email(request)
            except httpx.HTTPStatusError as exc:
                _raise_for_http_error(exc)
            except Exception as exc:
                raise provider_error(str(exc)) from exc

            log_tool_call("draft_email_reply", user_email, _params, "success", (time.monotonic() - _start) * 1000)
            return result
        except MintToolError as exc:
            log_tool_call("draft_email_reply", user_email, _params, "error", (time.monotonic() - _start) * 1000, error_code=exc.error.error_code)
            raise

    @mcp.tool(annotations={"readOnlyHint": True})
    async def get_upcoming_events(
        days: int = 7,
        timezone: str = "UTC",
        user_email: str | None = Depends(get_user_email),
        email_provider: EmailProvider = Depends(get_email_provider),
        calendar_provider: CalendarProvider = Depends(get_calendar_provider),
    ) -> list[EnrichedEvent]:
        """Calendar events for the next N days, enriched with unread email counts from attendees. Shows which meetings have related unread emails so you can prepare."""
        _start = time.monotonic()
        _params = {"days": days, "timezone": timezone}
        try:
            tz = _resolve_timezone(timezone)
            now = datetime.now(tz)
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=days)

            try:
                emails_task = email_provider.get_inbox(max_results=50)
                events_task = calendar_provider.list_events(start, end, max_results=50)
                emails, events = await asyncio.gather(emails_task, events_task)
            except httpx.HTTPStatusError as exc:
                _raise_for_http_error(exc)
            except Exception as exc:
                raise provider_error(str(exc)) from exc

            relationship_map = build_relationship_map(emails)
            result = enrich_events(events, emails, tz=tz, relationship_map=relationship_map)
            log_tool_call("get_upcoming_events", user_email, _params, "success", (time.monotonic() - _start) * 1000)
            return result
        except MintToolError as exc:
            log_tool_call("get_upcoming_events", user_email, _params, "error", (time.monotonic() - _start) * 1000, error_code=exc.error.error_code)
            raise

    @mcp.tool()
    async def create_reminder(
        title: str,
        start: datetime,
        description: str | None = None,
        user_email: str | None = Depends(get_user_email),
        calendar_provider: CalendarProvider = Depends(get_calendar_provider),
    ) -> CalendarEvent:
        """Create a personal calendar reminder. Sets a 30-minute event with no attendees. Use for tasks, follow-ups, or prep reminders."""
        _start_t = time.monotonic()
        _params = {"title": title, "start": start, "description": description}
        try:
            if not title.strip():
                raise invalid_input(
                    "Reminder title cannot be empty",
                    "Provide a non-empty title string",
                )

            request = CreateEventRequest(
                title=title,
                start=start,
                end=start + timedelta(minutes=30),
                description=description,
                attendees=[],
            )

            try:
                result = await calendar_provider.create_event(request)
            except httpx.HTTPStatusError as exc:
                _raise_for_http_error(exc)
            except Exception as exc:
                raise provider_error(str(exc)) from exc

            log_tool_call("create_reminder", user_email, _params, "success", (time.monotonic() - _start_t) * 1000)
            return result
        except MintToolError as exc:
            log_tool_call("create_reminder", user_email, _params, "error", (time.monotonic() - _start_t) * 1000, error_code=exc.error.error_code)
            raise

    @mcp.tool(annotations={"readOnlyHint": True})
    async def summarize_day(
        timezone: str = "UTC",
        scoring_overrides: ScoringOverrides | None = None,
        user_email: str | None = Depends(get_user_email),
        email_provider: EmailProvider = Depends(get_email_provider),
        calendar_provider: CalendarProvider = Depends(get_calendar_provider),
    ) -> DaySummary:
        """End-of-day summary: emails received, unread count, today's meetings with attendee email activity, pending high-priority emails, tomorrow's first events, and suggested action items."""
        _start = time.monotonic()
        _params = {"timezone": timezone}
        try:
            tz = _resolve_timezone(timezone)
            now = datetime.now(tz)
            now_utc = datetime.utcnow()  # naive UTC for scoring (email dates are naive)
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            today_end = today_start + timedelta(days=1)
            tomorrow_end = today_end + timedelta(days=1)

            try:
                emails_task = email_provider.get_inbox(max_results=50)
                today_events_task = calendar_provider.list_events(today_start, today_end, max_results=30)
                tomorrow_events_task = calendar_provider.list_events(today_end, tomorrow_end, max_results=5)
                emails, today_events, tomorrow_events = await asyncio.gather(
                    emails_task, today_events_task, tomorrow_events_task
                )
            except httpx.HTTPStatusError as exc:
                _raise_for_http_error(exc)
            except Exception as exc:
                raise provider_error(str(exc)) from exc

            # Include tomorrow's events for scoring
            all_events = today_events + tomorrow_events
            priority = rank_emails(emails, all_events, now_utc, limit=10, overrides=scoring_overrides)
            relationship_map = build_relationship_map(emails)
            enriched_today = enrich_events(today_events, emails, tz=tz, relationship_map=relationship_map)
            enriched_tomorrow = enrich_events(tomorrow_events, emails, tz=tz, relationship_map=relationship_map)

            pending = [e for e in priority if not e.is_read and e.score >= 0.40]
            action_items = generate_action_items(
                pending, enriched_tomorrow,
                all_scored_emails=priority, now=now_utc,
            )

            today_emails = [e for e in emails if e.date.date() == now.date()]

            result = DaySummary(
                date=now.strftime("%Y-%m-%d"),
                emails_received=len(today_emails),
                emails_unread=sum(1 for e in today_emails if not e.is_read),
                meetings=enriched_today,
                pending_emails=pending,
                tomorrow_first_events=enriched_tomorrow,
                action_items=action_items,
                timezone=timezone,
            )
            log_tool_call("summarize_day", user_email, _params, "success", (time.monotonic() - _start) * 1000)
            return result
        except MintToolError as exc:
            log_tool_call("summarize_day", user_email, _params, "error", (time.monotonic() - _start) * 1000, error_code=exc.error.error_code)
            raise

    @mcp.tool(annotations={"readOnlyHint": True})
    async def get_weekly_digest(
        timezone: str = "UTC",
        user_email: str | None = Depends(get_user_email),
        email_provider: EmailProvider = Depends(get_email_provider),
        calendar_provider: CalendarProvider = Depends(get_calendar_provider),
    ) -> WeeklyDigest:
        """Weekly activity digest: total emails and unread count, top 5 senders, busiest day by email volume, and total meetings over the past 7 days. One call for a quick retrospective."""
        _start = time.monotonic()
        _params = {"timezone": timezone}
        try:
            tz = _resolve_timezone(timezone)
            now = datetime.now(tz)
            week_end = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            week_start = week_end - timedelta(days=7)

            try:
                emails_task = email_provider.get_inbox(max_results=200)
                events_task = calendar_provider.list_events(week_start, week_end, max_results=200)
                emails, events = await asyncio.gather(emails_task, events_task)
            except httpx.HTTPStatusError as exc:
                _raise_for_http_error(exc)
            except Exception as exc:
                raise provider_error(str(exc)) from exc

            week_emails = [e for e in emails if e.date.date() >= week_start.date()]

            sender_counts = Counter(e.sender for e in week_emails)
            top_senders = [
                {"sender": sender, "count": count}
                for sender, count in sender_counts.most_common(5)
            ]

            day_counts: dict[str, int] = {}
            for e in week_emails:
                day_key = e.date.strftime("%Y-%m-%d")
                day_counts[day_key] = day_counts.get(day_key, 0) + 1

            busiest_day = max(day_counts, key=day_counts.get) if day_counts else None

            result = WeeklyDigest(
                week_start=week_start.strftime("%Y-%m-%d"),
                week_end=(week_end - timedelta(days=1)).strftime("%Y-%m-%d"),
                total_emails=len(week_emails),
                total_unread=sum(1 for e in week_emails if not e.is_read),
                top_senders=top_senders,
                busiest_day=busiest_day,
                total_meetings=len(events),
                timezone=timezone,
            )
            log_tool_call("get_weekly_digest", user_email, _params, "success", (time.monotonic() - _start) * 1000)
            return result
        except MintToolError as exc:
            log_tool_call("get_weekly_digest", user_email, _params, "error", (time.monotonic() - _start) * 1000, error_code=exc.error.error_code)
            raise

    @mcp.tool(annotations={"readOnlyHint": True})
    async def get_pending_drafts(
        max_results: int = 20,
        user_email: str | None = Depends(get_user_email),
        email_provider: EmailProvider = Depends(get_email_provider),
    ) -> list[DraftSummary]:
        """List all email drafts so you can review AI-drafted replies before sending. Returns draft ID, subject, recipients, and a snippet for each pending draft."""
        _start = time.monotonic()
        _params = {"max_results": max_results}
        try:
            try:
                result = await email_provider.list_drafts(max_results)
            except httpx.HTTPStatusError as exc:
                _raise_for_http_error(exc)
            except Exception as exc:
                raise provider_error(str(exc)) from exc

            log_tool_call("get_pending_drafts", user_email, _params, "success", (time.monotonic() - _start) * 1000)
            return result
        except MintToolError as exc:
            log_tool_call("get_pending_drafts", user_email, _params, "error", (time.monotonic() - _start) * 1000, error_code=exc.error.error_code)
            raise
