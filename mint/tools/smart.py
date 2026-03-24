"""Intelligence-gated tools: 11 focused tools backed by server-side scoring and cross-referencing."""

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
    build_travel_info,
    classify_location,
    detect_awaiting_replies,
    enrich_events,
    generate_action_items,
    generate_highlights,
    rank_emails,
    scan_for_sensitive_content,
)
from mint.maps import get_travel_time as _maps_get_travel_time
from mint.models import (
    CalendarEvent,
    CreateEventRequest,
    DailyBrief,
    DaySummary,
    DraftEmailRequest,
    DraftSummary,
    EmailThread,
    EnrichedEvent,
    LocationType,
    PriorityEmail,
    ScoringOverrides,
    ThreadMessage,
    TravelInfo,
    WeeklyDigest,
)
from mint.providers.base import CalendarProvider, EmailProvider

_MODULE_START = time.monotonic()


def _resolve_timezone(timezone: str) -> ZoneInfo:
    """Parse an IANA timezone name, falling back to UTC on invalid input."""
    try:
        return ZoneInfo(timezone)
    except (KeyError, ValueError):
        return ZoneInfo("UTC")


def _raise_for_http_error(exc: httpx.HTTPStatusError, resource_type: str = "", resource_id: str = "") -> None:
    """Translate an HTTP status error into the appropriate MintToolError."""
    status = exc.response.status_code
    if status in (404, 400) and resource_type:
        raise not_found(resource_type, resource_id) from exc
    if status == 429:
        raise rate_limited() from exc
    raise provider_error(f"HTTP {status}: {exc.response.text}") from exc


def register_smart_tools(mcp: FastMCP) -> None:

    @mcp.tool(annotations={"readOnlyHint": True})
    async def get_daily_brief(
        timezone: str = "UTC",
        scoring_overrides: ScoringOverrides | None = None,
        origin: str | None = None,
        mailbox: str | None = None,
        user_email: str | None = Depends(get_user_email),
        email_provider: EmailProvider = Depends(get_email_provider),
        calendar_provider: CalendarProvider = Depends(get_calendar_provider),
    ) -> DailyBrief:
        """Morning snapshot: today's meetings enriched with related unread emails, top priority emails scored by urgency, and cross-referenced highlights. One call replaces reading inbox + checking calendar.

        Example: get_daily_brief(timezone="Africa/Nairobi") — returns brief with times as "9:00 AM EAT". Pass scoring_overrides to boost/demote specific senders or domains. Pass origin="Westlands, Nairobi" for travel directions to physical meetings. Pass mailbox="sales@company.com" to access a shared or delegated mailbox."""
        _start = time.monotonic()
        _params = {"timezone": timezone}
        try:
            tz = _resolve_timezone(timezone)
            now = datetime.now(tz)
            now_utc = datetime.utcnow()  # naive UTC for scoring (email dates are naive)
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            today_end = today_start + timedelta(days=2)  # include tomorrow for scoring

            try:
                emails_task = email_provider.get_inbox(max_results=50, mailbox=mailbox)
                events_task = calendar_provider.list_events(today_start, today_end, max_results=30, mailbox=mailbox)
                emails, events = await asyncio.gather(emails_task, events_task)
            except httpx.HTTPStatusError as exc:
                _raise_for_http_error(exc)
            except Exception as exc:
                raise provider_error(str(exc)) from exc

            priority = rank_emails(emails, events, now_utc, limit=10, overrides=scoring_overrides)
            relationship_map = build_relationship_map(emails)
            today_events = [e for e in events if e.start.date() == now.date()]
            enriched = enrich_events(today_events, emails, tz=tz, relationship_map=relationship_map, origin=origin)

            # Fetch travel times for physical locations when Maps API key is configured
            from mint.config import get_settings
            maps_key = get_settings().google_maps_api_key
            if maps_key and origin:
                travel_tasks = []
                travel_indices = []
                for i, ev in enumerate(enriched):
                    if ev.travel_info and ev.travel_info.location_type == LocationType.PHYSICAL:
                        travel_tasks.append(
                            _maps_get_travel_time(httpx.AsyncClient(), maps_key, origin, ev.travel_info.original_location, ev.start)
                        )
                        travel_indices.append(i)
                if travel_tasks:
                    travel_results = await asyncio.gather(*travel_tasks, return_exceptions=True)
                    for idx, result_data in zip(travel_indices, travel_results):
                        if isinstance(result_data, dict):
                            ti = enriched[idx].travel_info
                            ti.travel_minutes = result_data["duration_minutes"]
                            ti.travel_mode = "driving"
                            leave_dt = result_data["leave_by"]
                            if tz:
                                local_dt = leave_dt.astimezone(tz)
                                t = local_dt.strftime("%I:%M %p")
                                ti.leave_by = f"{t.lstrip('0')} {local_dt.strftime('%Z')}"
                            else:
                                ti.leave_by = leave_dt.strftime("%I:%M %p")

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
        mailbox: str | None = None,
        user_email: str | None = Depends(get_user_email),
        email_provider: EmailProvider = Depends(get_email_provider),
        calendar_provider: CalendarProvider = Depends(get_calendar_provider),
    ) -> list[PriorityEmail]:
        """Top emails scored by urgency. Each email gets a 0.0-1.0 priority score based on calendar relevance, urgency keywords, recency, and thread activity. Returns the top results, not the full inbox.

        Example: get_priority_emails(limit=5) — top 5 emails. With overrides: get_priority_emails(scoring_overrides={"boost_senders": ["ceo@company.com"], "demote_domains": ["marketing.com"]}). Pass mailbox="sales@company.com" to access a shared or delegated mailbox."""
        _start = time.monotonic()
        _params = {"limit": limit}
        try:
            now = datetime.utcnow()
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            today_end = today_start + timedelta(days=2)

            try:
                emails_task = email_provider.get_inbox(max_results=50, mailbox=mailbox)
                events_task = calendar_provider.list_events(today_start, today_end, max_results=30, mailbox=mailbox)
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
        mailbox: str | None = None,
        user_email: str | None = Depends(get_user_email),
        email_provider: EmailProvider = Depends(get_email_provider),
    ) -> EmailThread:
        """Get the full conversation thread for an email. Returns all messages in the thread with sender, date, and body.

        Example: get_email_thread(email_id="18f3a4b2c1d0e5f6") — returns all messages in that thread. Use an email ID from get_priority_emails or get_daily_brief results. Pass mailbox="sales@company.com" to access a shared or delegated mailbox."""
        _start = time.monotonic()
        _params = {"email_id": email_id}
        try:
            try:
                email = await email_provider.get_email(email_id, mailbox=mailbox)
                raw_messages = await email_provider.get_thread_messages(email_id, mailbox=mailbox)
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
        mailbox: str | None = None,
        user_email: str | None = Depends(get_user_email),
        email_provider: EmailProvider = Depends(get_email_provider),
    ) -> str:
        """Draft a reply to an email. Reads the original email, creates a reply draft with Re: subject addressed to the sender. Never sends — the user reviews and sends from their mail client. Returns the draft ID. Body is scanned for sensitive content (SSNs, credit cards, API keys) before drafting.

        Example: draft_email_reply(email_id="18f3a4b2c1d0e5f6", body="Thanks, I'll review by Friday."). Pass mailbox="sales@company.com" to access a shared or delegated mailbox."""
        _start = time.monotonic()
        _params = {"email_id": email_id, "body": body, "cc": cc, "bcc": bcc}
        try:
            try:
                original = await email_provider.get_email(email_id, mailbox=mailbox)
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

            request = DraftEmailRequest(
                to=[original.sender],
                subject=subject,
                body=body,
                cc=cc,
                bcc=bcc,
            )

            try:
                result = await email_provider.draft_email(request, mailbox=mailbox)
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
        origin: str | None = None,
        mailbox: str | None = None,
        user_email: str | None = Depends(get_user_email),
        email_provider: EmailProvider = Depends(get_email_provider),
        calendar_provider: CalendarProvider = Depends(get_calendar_provider),
    ) -> list[EnrichedEvent]:
        """Calendar events for the next N days, enriched with unread email counts from attendees. Shows which meetings have related unread emails so you can prepare.

        Example: get_upcoming_events(days=3, timezone="US/Eastern") — next 3 days with times in ET. Pass origin="Westlands, Nairobi" for travel directions. Pass mailbox="sales@company.com" to access a shared or delegated mailbox."""
        _start = time.monotonic()
        _params = {"days": days, "timezone": timezone}
        try:
            tz = _resolve_timezone(timezone)
            now = datetime.now(tz)
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=days)

            try:
                emails_task = email_provider.get_inbox(max_results=50, mailbox=mailbox)
                events_task = calendar_provider.list_events(start, end, max_results=50, mailbox=mailbox)
                emails, events = await asyncio.gather(emails_task, events_task)
            except httpx.HTTPStatusError as exc:
                _raise_for_http_error(exc)
            except Exception as exc:
                raise provider_error(str(exc)) from exc

            relationship_map = build_relationship_map(emails)
            result = enrich_events(events, emails, tz=tz, relationship_map=relationship_map, origin=origin)
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
        location: str | None = None,
        user_email: str | None = Depends(get_user_email),
        calendar_provider: CalendarProvider = Depends(get_calendar_provider),
    ) -> CalendarEvent:
        """Create a personal calendar reminder. Sets a 30-minute event with no attendees. Use for tasks, follow-ups, or prep reminders.

        Example: create_reminder(title="Review Q3 budget", start="2026-03-20T09:00:00", description="Check billing alerts", location="KICC, Nairobi")"""
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
                location=location,
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
        origin: str | None = None,
        mailbox: str | None = None,
        user_email: str | None = Depends(get_user_email),
        email_provider: EmailProvider = Depends(get_email_provider),
        calendar_provider: CalendarProvider = Depends(get_calendar_provider),
    ) -> DaySummary:
        """End-of-day summary: emails received, unread count, today's meetings with attendee email activity, pending high-priority emails, tomorrow's first events, and suggested action items.

        Example: summarize_day(timezone="Europe/London") — summary with times in GMT/BST. Pass origin="Westlands, Nairobi" for travel directions. Pass mailbox="sales@company.com" to access a shared or delegated mailbox."""
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
                emails_task = email_provider.get_inbox(max_results=50, mailbox=mailbox)
                today_events_task = calendar_provider.list_events(today_start, today_end, max_results=30, mailbox=mailbox)
                tomorrow_events_task = calendar_provider.list_events(today_end, tomorrow_end, max_results=5, mailbox=mailbox)
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
            enriched_today = enrich_events(today_events, emails, tz=tz, relationship_map=relationship_map, origin=origin)
            enriched_tomorrow = enrich_events(tomorrow_events, emails, tz=tz, relationship_map=relationship_map, origin=origin)

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
        mailbox: str | None = None,
        user_email: str | None = Depends(get_user_email),
        email_provider: EmailProvider = Depends(get_email_provider),
        calendar_provider: CalendarProvider = Depends(get_calendar_provider),
    ) -> WeeklyDigest:
        """Weekly activity digest: total emails and unread count, top 5 senders, busiest day by email volume, and total meetings over the past 7 days. One call for a quick retrospective.

        Example: get_weekly_digest(timezone="Asia/Tokyo") — week summary with JST timestamps. Pass mailbox="sales@company.com" to access a shared or delegated mailbox."""
        _start = time.monotonic()
        _params = {"timezone": timezone}
        try:
            tz = _resolve_timezone(timezone)
            now = datetime.now(tz)
            week_end = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            week_start = week_end - timedelta(days=7)

            try:
                emails_task = email_provider.get_inbox(max_results=200, mailbox=mailbox)
                events_task = calendar_provider.list_events(week_start, week_end, max_results=200, mailbox=mailbox)
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
        mailbox: str | None = None,
        user_email: str | None = Depends(get_user_email),
        email_provider: EmailProvider = Depends(get_email_provider),
    ) -> list[DraftSummary]:
        """List all email drafts so you can review AI-drafted replies before sending. Returns draft ID, subject, recipients, and a snippet for each pending draft.

        Example: get_pending_drafts(max_results=5) — latest 5 drafts. Pass mailbox="sales@company.com" to access a shared or delegated mailbox."""
        _start = time.monotonic()
        _params = {"max_results": max_results}
        try:
            try:
                result = await email_provider.list_drafts(max_results, mailbox=mailbox)
            except httpx.HTTPStatusError as exc:
                _raise_for_http_error(exc)
            except Exception as exc:
                raise provider_error(str(exc)) from exc

            log_tool_call("get_pending_drafts", user_email, _params, "success", (time.monotonic() - _start) * 1000)
            return result
        except MintToolError as exc:
            log_tool_call("get_pending_drafts", user_email, _params, "error", (time.monotonic() - _start) * 1000, error_code=exc.error.error_code)
            raise

    @mcp.tool(annotations={"readOnlyHint": True})
    async def get_travel_time(
        origin: str,
        destination: str,
        arrive_by: datetime | None = None,
        mode: str = "driving",
        user_email: str | None = Depends(get_user_email),
    ) -> TravelInfo:
        """Get travel directions and estimated travel time between two locations. Returns Google Maps link, Uber deep link, and travel time estimate (when Maps API key is configured).

        Example: get_travel_time(origin="Westlands, Nairobi", destination="KICC, Nairobi", arrive_by="2026-03-24T14:00:00", mode="driving")"""
        _start = time.monotonic()
        _params = {"origin": origin, "destination": destination, "mode": mode}
        try:
            info = build_travel_info(destination, origin=origin)

            # Fetch travel time if Maps API key is configured
            from mint.config import get_settings
            maps_key = get_settings().google_maps_api_key
            if maps_key and info.location_type == LocationType.PHYSICAL and arrive_by:
                async with httpx.AsyncClient() as client:
                    result_data = await _maps_get_travel_time(client, maps_key, origin, destination, arrive_by, mode)
                if result_data:
                    info.travel_minutes = result_data["duration_minutes"]
                    info.travel_mode = mode
                    leave_dt = result_data["leave_by"]
                    info.leave_by = leave_dt.strftime("%I:%M %p")

            log_tool_call("get_travel_time", user_email, _params, "success", (time.monotonic() - _start) * 1000)
            return info
        except MintToolError as exc:
            log_tool_call("get_travel_time", user_email, _params, "error", (time.monotonic() - _start) * 1000, error_code=exc.error.error_code)
            raise

    @mcp.tool(annotations={"readOnlyHint": True})
    async def server_status() -> dict:
        """Server health and version info accessible through MCP. Returns status, version, uptime, supported providers, tool count, and compliance metadata. Use this to verify the server is running correctly.

        Example: server_status() — returns {"status": "healthy", "version": "2.0.0", "uptime_seconds": 3600, ...}"""
        from mint.config import get_settings

        settings = get_settings()
        return {
            "status": "healthy",
            "server": "Mint",
            "version": "2.0.0",
            "uptime_seconds": round(time.monotonic() - _MODULE_START),
            "providers": ["google", "microsoft"],
            "tools_count": 11,
            "compliance": {
                "encryption_in_transit": "TLS 1.3 (Azure-managed)",
                "data_at_rest": "none (stateless — no email content persisted)",
                "hipaa_ready": True,
                "data_region": settings.data_region,
            },
        }
