"""Server-side intelligence layer: priority scoring, cross-referencing, highlights."""

from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

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
    "@e.", "@t.", "@em.", "@msg.", "@campaign.",
    "notification", "contact@", "team@", "admin@",
}
_TRANSACTIONAL_KEYWORDS = {
    "payment", "billing", "security", "password", "deactivation",
    "suspension", "verification", "receipt", "charge", "unauthorized", "fraud",
}
_PAYMENT_KEYWORDS = {"payment", "invoice", "billing", "receipt", "charge"}
_DEADLINE_KEYWORDS = {"deadline", "due", "expires", "by end of day", "by eod", "by cob"}
_DIRECT_QUESTION_PATTERNS = {"can you", "could you", "please review", "your thoughts", "?"}


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
                score += 0.50
                signals.append(f"calendar_match:today:{event_title}")
                related_event = event_title
            elif day_label == "tomorrow":
                score += 0.35
                signals.append(f"calendar_match:tomorrow:{event_title}")
                if related_event is None:
                    related_event = event_title
            break  # use first match

    # Urgency keywords in subject + snippet
    text = f"{email.subject} {email.snippet}".lower()
    keyword_score = 0.0
    for kw in _URGENCY_KEYWORDS:
        if kw in text:
            keyword_score += 0.08
            signals.append(f"keyword:{kw}")
    score += min(keyword_score, 0.25)

    # Body-text scoring on snippet
    body_score = 0.0
    if any(kw in text for kw in _PAYMENT_KEYWORDS):
        body_score += 0.12
        signals.append("body:payment_billing")
    if any(kw in text for kw in _DEADLINE_KEYWORDS):
        body_score += 0.10
        signals.append("body:deadline")
    if any(pat in text for pat in _DIRECT_QUESTION_PATTERNS):
        body_score += 0.08
        signals.append("body:direct_question")
    score += min(body_score, 0.20)

    # Unread base boost
    if not email.is_read:
        score += 0.10
        signals.append("unread")

    # Active thread: sender frequency as proxy
    if sender_counts[sender_addr] >= 3:
        score += 0.10
        signals.append(f"sender_frequency:{sender_counts[sender_addr]}")

    # Recency boost (unread only, replaces old unread_4_24h block)
    if not email.is_read:
        age = now - email.date
        if age < timedelta(hours=1):
            score += 0.20
            signals.append("recency:1h")
        elif age < timedelta(hours=4):
            score += 0.15
            signals.append("recency:4h")
        elif age < timedelta(hours=12):
            score += 0.10
            signals.append("recency:12h")
        elif age < timedelta(hours=24):
            score += 0.05
            signals.append("recency:24h")

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
        pre_ceiling_score = score
        score = min(score, 0.15)
        signals.append("newsletter_ceiling")

        # Transactional exception: restore score if subject/snippet contains
        # keywords indicating a transactional (non-promotional) email
        combined_text = f"{subject_lower} {email.snippet.lower()}"
        if any(kw in combined_text for kw in _TRANSACTIONAL_KEYWORDS):
            score = pre_ceiling_score
            signals.append("transactional_exception")

    # Final score and tier assignment
    score = round(min(score, 1.0), 2)

    if score > 0.8:
        tier = "critical"
    elif score > 0.6:
        tier = "high"
    elif score > 0.4:
        tier = "medium"
    else:
        tier = "low"

    return PriorityEmail(
        id=email.id,
        subject=email.subject,
        sender=email.sender,
        date=email.date,
        snippet=email.snippet,
        is_read=email.is_read,
        score=score,
        signals=signals,
        related_event=related_event,
        tier=tier,
    )


def rank_emails(
    emails: list[EmailSummary],
    events: list[CalendarEvent],
    now: datetime,
    limit: int = 10,
) -> list[PriorityEmail]:
    """Score, deduplicate by thread, and rank emails by priority.

    Emails sharing a thread_id are collapsed to the highest-scoring
    representative, with message_count reflecting the thread size.
    Emails with no thread_id are treated as standalone.
    """
    today = now.date()
    attendee_map = build_attendee_map(events, today)

    # Build sender frequency + thread counters
    sender_counts: Counter[str] = Counter()
    for e in emails:
        sender_counts[extract_email_address(e.sender)] += 1
        if e.thread_id:
            sender_counts[f"thread:{e.thread_id}"] += 1

    scored = [
        (e.thread_id, score_email(e, attendee_map, now, sender_counts))
        for e in emails
    ]

    # Group by thread_id; None-keyed emails stay independent
    thread_groups: dict[str, list[PriorityEmail]] = {}
    standalone: list[PriorityEmail] = []
    for thread_id, priority in scored:
        if thread_id is None:
            standalone.append(priority)
        else:
            thread_groups.setdefault(thread_id, []).append(priority)

    # Collapse each thread to its highest-scoring email
    deduplicated: list[PriorityEmail] = list(standalone)
    for group in thread_groups.values():
        group.sort(key=lambda p: p.score, reverse=True)
        representative = group[0]
        representative.message_count = len(group)
        deduplicated.append(representative)

    deduplicated.sort(key=lambda p: p.score, reverse=True)
    return deduplicated[:limit]


def _format_local_time(dt: datetime, tz: ZoneInfo) -> str:
    """Format a datetime as e.g. '9:00 AM EAT' in the given timezone.

    Converts to the target timezone, formats with 12-hour clock, strips the
    leading zero from the hour, and appends the timezone abbreviation.
    """
    local_dt = dt.astimezone(tz)
    time_str = local_dt.strftime("%I:%M %p")
    if time_str.startswith("0"):
        time_str = time_str[1:]
    return f"{time_str} {local_dt.strftime('%Z')}"


def enrich_events(
    events: list[CalendarEvent],
    emails: list[EmailSummary],
    tz: ZoneInfo | None = None,
) -> list[EnrichedEvent]:
    """Attach unread-email counts from attendees to each event.

    If *tz* is provided, populates start_local / end_local with
    human-readable times in that timezone (e.g. "9:00 AM EAT").
    """
    # Build set of senders with unread emails
    unread_senders: set[str] = set()
    for e in emails:
        if not e.is_read:
            unread_senders.add(extract_email_address(e.sender))

    result = []
    for event in events:
        attendee_addrs = {a.lower().strip() for a in event.attendees}
        unread_count = len(attendee_addrs & unread_senders)

        start_local: str | None = None
        end_local: str | None = None
        if tz is not None:
            start_local = _format_local_time(event.start, tz)
            end_local = _format_local_time(event.end, tz)

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
                start_local=start_local,
                end_local=end_local,
            )
        )
    return result


def generate_highlights(
    events: list[EnrichedEvent],
    emails: list[PriorityEmail],
    tz: ZoneInfo | None = None,
) -> list[str]:
    """Generate natural-language highlights for the daily brief.

    If *tz* is provided, event start times are shown in the user's local
    timezone (e.g. "9:00 AM EAT") instead of raw UTC.
    """
    highlights: list[str] = []

    # Meeting-related emails
    for event in events:
        if event.unread_from_attendees > 0:
            if tz is not None:
                time_str = _format_local_time(event.start, tz)
            else:
                time_str = event.start.strftime("%I:%M %p")
            highlights.append(
                f"{event.unread_from_attendees} unread email(s) from attendees "
                f"of \"{event.title}\" (starts {time_str})"
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
