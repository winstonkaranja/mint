"""Server-side intelligence layer: priority scoring, cross-referencing, highlights."""

from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime, timedelta

from mint.models import (
    CalendarEvent,
    EmailSummary,
    EnrichedEvent,
    PriorityEmail,
)

_EMAIL_RE = re.compile(r"<([^>]+)>")
_URGENCY_KEYWORDS = {"urgent", "deadline", "asap", "action required", "review", "approve"}
_NEWSLETTER_KEYWORDS = {"noreply", "no-reply", "unsubscribe", "newsletter", "notifications"}
_AUTOMATED_SENDER_PATTERNS = {
    "noreply", "no-reply", "donotreply", "do-not-reply",
    "notify", "notifications", "mailer", "info@", "news@",
    "marketing", "promo", "digest", "updates@", "alert@",
    "support@", "billing@", "receipts@", "hello@",
    "@m.", "@mail.", "@send.", "@post.", "@bulk.",
}


def extract_email_address(sender: str) -> str:
    """Extract bare email from 'Name <email>' format."""
    match = _EMAIL_RE.search(sender)
    if match:
        return match.group(1).lower()
    return sender.strip().lower()


def build_attendee_map(
    events: list[CalendarEvent], today: date
) -> dict[str, list[tuple[str, str]]]:
    """Map email addresses to (event_title, 'today'|'tomorrow') for calendar matching.

    Returns dict like {"alice@co.com": [("Design Review", "today")]}.
    """
    tomorrow = today + timedelta(days=1)
    result: dict[str, list[tuple[str, str]]] = {}
    for event in events:
        event_date = event.start.date() if isinstance(event.start, datetime) else event.start
        if event_date == today:
            day_label = "today"
        elif event_date == tomorrow:
            day_label = "tomorrow"
        else:
            continue
        for attendee in event.attendees:
            addr = attendee.lower().strip()
            if addr:
                result.setdefault(addr, []).append((event.title, day_label))
    return result


def score_email(
    email: EmailSummary,
    attendee_map: dict[str, list[tuple[str, str]]],
    now: datetime,
    sender_counts: Counter[str],
) -> PriorityEmail:
    """Score a single email with heuristic signals. Returns PriorityEmail."""
    score = 0.0
    signals: list[str] = []
    related_event: str | None = None
    sender_addr = extract_email_address(email.sender)

    # Calendar match
    if sender_addr in attendee_map:
        for event_title, day_label in attendee_map[sender_addr]:
            if day_label == "today":
                score += 0.35
                signals.append(f"calendar_match:today:{event_title}")
                related_event = event_title
            elif day_label == "tomorrow":
                score += 0.25
                signals.append(f"calendar_match:tomorrow:{event_title}")
                if related_event is None:
                    related_event = event_title
            break  # use first match

    # Urgency keywords in subject + snippet
    text = f"{email.subject} {email.snippet}".lower()
    keyword_score = 0.0
    for kw in _URGENCY_KEYWORDS:
        if kw in text:
            keyword_score += 0.05
            signals.append(f"keyword:{kw}")
    score += min(keyword_score, 0.15)

    # Direct recipient (heuristic: if sender is not the user, they're likely in To:)
    # Since we don't have the user's email easily, we use is_read as a proxy —
    # unread emails the user is in To: for vs CC: is hard to distinguish without
    # the full headers. We give the base boost for unread here.
    if not email.is_read:
        score += 0.10
        signals.append("unread")

    # Active thread: sender frequency as proxy
    if sender_counts[sender_addr] >= 3:
        score += 0.10
        signals.append(f"sender_frequency:{sender_counts[sender_addr]}")

    # Unread 4-24h window
    if not email.is_read:
        age = now - email.date
        if timedelta(hours=4) <= age <= timedelta(hours=24):
            score += 0.12
            signals.append("unread_4_24h")

    # Thread activity: emails sharing same thread_id
    if email.thread_id and sender_counts.get(f"thread:{email.thread_id}", 0) >= 3:
        score += 0.15
        signals.append("active_thread")

    # Floor/ceiling rules
    has_today_match = any("calendar_match:today" in s for s in signals)
    has_tomorrow_match = any("calendar_match:tomorrow" in s for s in signals)

    if has_today_match:
        score = max(score, 0.80)
    elif has_tomorrow_match:
        score = max(score, 0.65)

    # Newsletter ceiling
    sender_lower = email.sender.lower()
    subject_lower = email.subject.lower()
    addr_lower = sender_addr  # already lowered by extract_email_address
    is_automated = (
        any(kw in sender_lower or kw in subject_lower for kw in _NEWSLETTER_KEYWORDS)
        or any(pat in addr_lower for pat in _AUTOMATED_SENDER_PATTERNS)
    )
    if is_automated:
        score = min(score, 0.25)
        signals.append("newsletter_ceiling")

    return PriorityEmail(
        id=email.id,
        subject=email.subject,
        sender=email.sender,
        date=email.date,
        snippet=email.snippet,
        is_read=email.is_read,
        score=round(min(score, 1.0), 2),
        signals=signals,
        related_event=related_event,
    )


def rank_emails(
    emails: list[EmailSummary],
    events: list[CalendarEvent],
    now: datetime,
    limit: int = 10,
) -> list[PriorityEmail]:
    """Score and rank emails by priority. Returns top `limit` results."""
    today = now.date()
    attendee_map = build_attendee_map(events, today)

    # Build sender frequency + thread counters
    sender_counts: Counter[str] = Counter()
    for e in emails:
        sender_counts[extract_email_address(e.sender)] += 1
        if e.thread_id:
            sender_counts[f"thread:{e.thread_id}"] += 1

    scored = [score_email(e, attendee_map, now, sender_counts) for e in emails]
    scored.sort(key=lambda p: p.score, reverse=True)
    return scored[:limit]


def enrich_events(
    events: list[CalendarEvent], emails: list[EmailSummary]
) -> list[EnrichedEvent]:
    """Attach unread-email counts from attendees to each event."""
    # Build set of senders with unread emails
    unread_senders: set[str] = set()
    for e in emails:
        if not e.is_read:
            unread_senders.add(extract_email_address(e.sender))

    result = []
    for event in events:
        attendee_addrs = {a.lower().strip() for a in event.attendees}
        unread_count = len(attendee_addrs & unread_senders)
        result.append(
            EnrichedEvent(
                id=event.id,
                title=event.title,
                start=event.start,
                end=event.end,
                location=event.location,
                description=event.description,
                attendees=event.attendees,
                is_all_day=event.is_all_day,
                unread_from_attendees=unread_count,
            )
        )
    return result


def generate_highlights(
    events: list[EnrichedEvent], emails: list[PriorityEmail]
) -> list[str]:
    """Generate natural-language highlights for the daily brief."""
    highlights: list[str] = []

    # Meeting-related emails
    for event in events:
        if event.unread_from_attendees > 0:
            highlights.append(
                f"{event.unread_from_attendees} unread email(s) from attendees "
                f"of \"{event.title}\" (starts {event.start.strftime('%I:%M %p')})"
            )

    # Top urgent emails
    urgent = [e for e in emails if e.score >= 0.65]
    if urgent:
        highlights.append(
            f"{len(urgent)} high-priority email(s) need attention"
        )

    if not highlights:
        highlights.append("No urgent items — inbox is under control")

    return highlights


def generate_action_items(
    emails: list[PriorityEmail], events: list[EnrichedEvent]
) -> list[str]:
    """Generate end-of-day action items from pending emails and tomorrow's events."""
    items: list[str] = []

    # Unread high-priority emails
    pending = [e for e in emails if not e.is_read and e.score >= 0.50]
    for e in pending:
        items.append(f"Respond to \"{e.subject}\" from {e.sender}")

    # Tomorrow's first events that need prep
    for event in events:
        if event.unread_from_attendees > 0:
            items.append(
                f"Review {event.unread_from_attendees} email(s) before "
                f"\"{event.title}\""
            )

    return items
