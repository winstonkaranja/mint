"""Server-side intelligence layer: priority scoring, cross-referencing, highlights."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from mint.models import (
    AttendeeEmail,
    CalendarEvent,
    EmailSummary,
    EnrichedEvent,
    PrepBundle,
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
        thread_id=email.thread_id,
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


_DOC_LINK_RE = re.compile(
    r"https?://(?:"
    r"docs\.google\.com|"
    r"sheets\.google\.com|"
    r"slides\.google\.com|"
    r"drive\.google\.com|"
    r"(?:www\.)?notion\.so|"
    r"sharepoint\.com"
    r")/[^\s<>\"')]*",
)


def _extract_doc_links(text: str) -> list[str]:
    """Extract document/collaboration URLs from event description text.

    Matches Google Docs/Sheets/Slides/Drive, Notion, and SharePoint links.
    Deliberately excludes meeting links (Zoom, Teams, etc.).
    """
    return list(dict.fromkeys(_DOC_LINK_RE.findall(text)))


def enrich_events(
    events: list[CalendarEvent],
    emails: list[EmailSummary],
    tz: ZoneInfo | None = None,
) -> list[EnrichedEvent]:
    """Attach unread-email counts and prep bundles to each event.

    Each prep bundle contains:
    - Unread emails from attendees (as brief AttendeeEmail summaries)
    - Most recent email subject per attendee
    - Shared document links extracted from the event description

    If *tz* is provided, populates start_local / end_local with
    human-readable times in that timezone (e.g. "9:00 AM EAT").
    """
    # Index all emails by sender address for efficient per-attendee lookup
    unread_senders: set[str] = set()
    emails_by_sender: dict[str, list[EmailSummary]] = defaultdict(list)
    for e in emails:
        addr = extract_email_address(e.sender)
        emails_by_sender[addr].append(e)
        if not e.is_read:
            unread_senders.add(addr)

    result = []
    for event in events:
        attendee_addrs = {a.lower().strip() for a in event.attendees}
        unread_count = len(attendee_addrs & unread_senders)

        # Build prep bundle
        unread_emails: list[AttendeeEmail] = []
        recent_subjects: dict[str, str] = {}

        for addr in attendee_addrs:
            sender_emails = emails_by_sender.get(addr, [])
            if not sender_emails:
                continue

            # Unread emails from this attendee
            for em in sender_emails:
                if not em.is_read:
                    unread_emails.append(
                        AttendeeEmail(
                            sender=em.sender,
                            subject=em.subject,
                            date=em.date,
                            is_read=em.is_read,
                        )
                    )

            # Most recent email subject from this attendee
            most_recent = max(sender_emails, key=lambda em: em.date)
            recent_subjects[addr] = most_recent.subject

        shared_links = (
            _extract_doc_links(event.description) if event.description else []
        )

        prep_bundle = PrepBundle(
            unread_emails=unread_emails,
            recent_subjects=recent_subjects,
            shared_links=shared_links,
        )

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
                prep_bundle=prep_bundle,
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


def detect_awaiting_replies(
    scored_emails: list[PriorityEmail],
    now: datetime,
    threshold_hours: int = 48,
) -> list[PriorityEmail]:
    """Flag threads where the user likely sent the last message and received no reply.

    Uses a signal-based heuristic that avoids extra API calls: a thread is
    considered "awaiting reply" when its most recent email is read (the user
    saw and presumably replied) and no newer unread message arrived within
    *threshold_hours*.

    Groups emails by thread_id, inspects each thread's timeline, and mutates
    the representative PriorityEmail objects in-place (setting
    ``awaiting_reply`` and ``awaiting_reply_hours``).  Emails without a
    thread_id are skipped.

    Returns the subset of emails that were flagged.
    """
    # Group by thread — None-keyed emails are standalone, skip them
    threads: dict[str, list[PriorityEmail]] = {}
    for email in scored_emails:
        if email.thread_id:
            threads.setdefault(email.thread_id, []).append(email)

    flagged: list[PriorityEmail] = []

    for thread_emails in threads.values():
        # Sort by date descending so [0] is the newest message in the thread
        thread_emails.sort(key=lambda e: e.date, reverse=True)
        newest = thread_emails[0]

        # The heuristic: if the newest message is read and old enough, the
        # user likely responded and is waiting for the other party.  If any
        # unread message exists in the thread, the other party already replied.
        has_unread = any(not e.is_read for e in thread_emails)
        if has_unread:
            continue

        hours_since = int((now - newest.date).total_seconds() / 3600)
        if newest.is_read and hours_since >= threshold_hours:
            newest.awaiting_reply = True
            newest.awaiting_reply_hours = hours_since
            flagged.append(newest)

    return flagged


def generate_action_items(
    emails: list[PriorityEmail],
    events: list[EnrichedEvent],
    all_scored_emails: list[PriorityEmail] | None = None,
    now: datetime | None = None,
) -> list[str]:
    """Generate end-of-day action items from pending emails and tomorrow's events.

    When *all_scored_emails* and *now* are provided, also detects threads
    awaiting a reply (read threads with no new messages in 48+ hours) and
    suggests follow-ups.
    """
    items: list[str] = []

    # Unread high-priority emails
    pending = [e for e in emails if not e.is_read and e.score >= 0.50]
    for e in pending:
        items.append(f"Respond to \"{e.subject}\" from {e.sender}")

    # Awaiting reply detection
    if all_scored_emails is not None and now is not None:
        detect_awaiting_replies(all_scored_emails, now)
        awaiting = [e for e in all_scored_emails if e.awaiting_reply]
        for e in awaiting:
            items.append(
                f"No reply in {e.awaiting_reply_hours}h on \"{e.subject}\" "
                f"\N{EM DASH} consider following up"
            )

    # Tomorrow's first events that need prep
    for event in events:
        if event.unread_from_attendees > 0:
            items.append(
                f"Review {event.unread_from_attendees} email(s) before "
                f"\"{event.title}\""
            )

    return items
