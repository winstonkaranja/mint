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
    ScoringOverrides,
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
_NEGATIVE_SENTIMENT = {
    "disappointed", "frustrated", "unacceptable", "urgent", "escalat",
    "complain", "furious", "angry", "critical issue", "broken", "failure",
    "outage",
}
_POSITIVE_SENTIMENT = {
    "thank you", "great news", "congratulations", "well done", "approved",
    "good news", "pleased to",
}
_REPLY_REQUESTED_PATTERNS = frozenset({
    "please reply", "your response", "rsvp", "please respond",
    "awaiting your", "waiting for your", "get back to",
})
# Alert detection: requires TOPIC + ACTION co-occurrence
_SECURITY_TOPICS = frozenset({
    "security", "password", "credential", "secret", "key", "token",
    "account", "sign-in", "login",
})
_SECURITY_ACTIONS = frozenset({
    "alert", "warning", "detected", "leaked", "exposed", "breach",
    "unauthorized", "suspicious", "compromised", "vulnerability",
    "incident", "found", "revoked", "blocked",
})
_BILLING_TOPICS = frozenset({
    "billing", "payment", "invoice", "subscription", "charge", "balance",
})
_BILLING_ACTIONS = frozenset({
    "failed", "overdue", "past due", "suspended", "declined", "due",
    "alert", "warning", "action required",
})
_MARKETING_SIGNALS = frozenset({
    "upgrade", "premium", "subscribe", "discount", "offer", "trial",
    "pricing", "plan", "promo", "deal", "save", "off", "free",
    "limited time", "exclusive",
})

# DLP (Data Loss Prevention) patterns for outbound draft scanning
_DLP_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "Possible SSN detected"),
    (re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b"), "Possible credit card number detected"),
    (re.compile(r"\b(?:sk|pk|api|key|token)[-_][a-zA-Z0-9]{20,}\b"), "Possible API key detected"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "Possible AWS access key detected"),
    (re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"), "Possible private key detected"),
]


def scan_for_sensitive_content(text: str) -> list[str]:
    """Scan text for sensitive data patterns (SSNs, credit cards, API keys, etc.).

    Returns a list of human-readable finding descriptions for each pattern
    matched. Returns an empty list if no sensitive content is detected.
    """
    return [description for pattern, description in _DLP_PATTERNS if pattern.search(text)]


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


def detect_vip_senders(
    sender_counts: Counter[str],
    attendee_addrs: set[str],
) -> frozenset[str]:
    """Identify VIP senders using frequency and calendar presence.

    A sender qualifies as VIP through either path:
    - frequency >= 5 AND appears as a calendar event attendee
    - frequency >= 7 regardless
    """
    vip: set[str] = set()
    for addr, freq in sender_counts.items():
        if addr.startswith("thread:"):
            continue
        if freq >= 7:
            vip.add(addr)
        elif freq >= 5 and addr in attendee_addrs:
            vip.add(addr)
    return frozenset(vip)


def build_relationship_map(emails: list[EmailSummary]) -> dict[str, int]:
    """Count total emails per sender address to quantify communication density."""
    freq: Counter[str] = Counter()
    for e in emails:
        freq[extract_email_address(e.sender)] += 1
    return dict(freq)


def score_email(
    email: EmailSummary,
    attendee_map: dict[str, list[tuple[str, str]]],
    now: datetime,
    sender_counts: Counter[str],
    vip_senders: frozenset[str] = frozenset(),
    overrides: ScoringOverrides | None = None,
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
            keyword_score += 0.12
            signals.append(f"keyword:{kw}")
    score += min(keyword_score, 0.35)

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
    score += min(body_score, 0.30)

    # Reply-requested signal
    if any(pat in text for pat in _REPLY_REQUESTED_PATTERNS):
        score += 0.10
        signals.append("reply_requested")

    # Sentiment signals
    has_negative = any(kw in text for kw in _NEGATIVE_SENTIMENT)
    has_positive = any(kw in text for kw in _POSITIVE_SENTIMENT)
    if has_negative:
        score += 0.08
        signals.append("negative_sentiment")
    elif has_positive:
        signals.append("positive_sentiment")

    # Unread base boost
    if not email.is_read:
        score += 0.10
        signals.append("unread")

    # Sender frequency (graduated)
    freq = sender_counts[sender_addr]
    if freq >= 7:
        score += 0.20
        signals.append(f"sender_frequency:{freq}:high")
    elif freq >= 5:
        score += 0.15
        signals.append(f"sender_frequency:{freq}:medium")
    elif freq >= 3:
        score += 0.10
        signals.append(f"sender_frequency:{freq}:low")

    # VIP sender boost
    if sender_addr in vip_senders:
        score += 0.10
        signals.append("vip_sender")

    # Recency boost: full weight for unread, half for read
    age = now - email.date
    recency_multiplier = 1.0 if not email.is_read else 0.5
    if age < timedelta(hours=1):
        score += round(0.20 * recency_multiplier, 2)
        signals.append("recency:1h")
    elif age < timedelta(hours=4):
        score += round(0.15 * recency_multiplier, 2)
        signals.append("recency:4h")
    elif age < timedelta(hours=12):
        score += round(0.10 * recency_multiplier, 2)
        signals.append("recency:12h")
    elif age < timedelta(hours=24):
        score += round(0.05 * recency_multiplier, 2)
        signals.append("recency:24h")

    # Thread activity: emails sharing same thread_id
    if email.thread_id and sender_counts.get(f"thread:{email.thread_id}", 0) >= 3:
        score += 0.20
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
        score = min(score, 0.10)
        signals.append("newsletter_ceiling")

        # Transactional exception: restore score if subject/snippet contains
        # transactional keywords AND no marketing signals (prevents upsell
        # emails from escaping the newsletter ceiling)
        combined_text = f"{subject_lower} {email.snippet.lower()}"
        has_marketing = any(kw in combined_text for kw in _MARKETING_SIGNALS)
        if not has_marketing and any(kw in combined_text for kw in _TRANSACTIONAL_KEYWORDS):
            score = pre_ceiling_score
            signals.append("transactional_exception")

        # Priority alert boosts — additive, not floors, to preserve score
        # variation. Requires TOPIC + ACTION co-occurrence and no marketing.
        if not has_marketing:
            has_sec_topic = any(kw in combined_text for kw in _SECURITY_TOPICS)
            has_sec_action = any(kw in combined_text for kw in _SECURITY_ACTIONS)
            has_bill_topic = any(kw in combined_text for kw in _BILLING_TOPICS)
            has_bill_action = any(kw in combined_text for kw in _BILLING_ACTIONS)

            if has_sec_topic and has_sec_action:
                score += 0.30
                signals.append("security_alert_boost")
            elif has_bill_topic and has_bill_action:
                score += 0.20
                signals.append("billing_alert_boost")

    # Custom scoring overrides
    if overrides is not None:
        sender_domain = sender_addr.split("@", 1)[1] if "@" in sender_addr else ""
        if sender_addr in {s.lower() for s in overrides.boost_senders}:
            score += 0.15
            signals.append("custom_boost:sender")
        if sender_addr in {s.lower() for s in overrides.demote_senders}:
            score -= 0.15
            signals.append("custom_demote:sender")
        if sender_domain and sender_domain in {d.lower() for d in overrides.boost_domains}:
            score += 0.10
            signals.append("custom_boost:domain")
        if sender_domain and sender_domain in {d.lower() for d in overrides.demote_domains}:
            score -= 0.10
            signals.append("custom_demote:domain")
        override_text = f"{email.subject} {email.snippet}".lower()
        for kw in overrides.boost_keywords:
            if kw.lower() in override_text:
                score += 0.10
                signals.append(f"custom_boost:keyword:{kw}")
        for kw in overrides.demote_keywords:
            if kw.lower() in override_text:
                score -= 0.10
                signals.append(f"custom_demote:keyword:{kw}")

    # Final score and tier assignment
    score = round(max(0.0, min(score, 1.0)), 2)

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


_SUBJECT_NOISE_RE = re.compile(
    r"\$[\d,]+\.?\d*"       # dollar amounts ($19.14, $1,234.56)
    r"|#\d+"                # ticket/order numbers (#12345)
    r"|\b\d{1,2}[/-]\d{1,2}([/-]\d{2,4})?\b"  # dates (3/19, 03-19-2026)
    r"|\b\d+\b",            # standalone numbers
    re.IGNORECASE,
)


def _normalize_subject(subject: str) -> str:
    """Strip noise and truncate to first 2 words for near-match grouping.

    Same sender + same first 2 meaningful words = same type of email.
    Strips numbers, dollar amounts, dates, path-like tokens (repo/URL),
    and punctuation-only tokens before taking the first 2 words.
    """
    s = subject.lower().strip()
    s = re.sub(r"^(re|fwd|fw)\s*:\s*", "", s)
    s = _SUBJECT_NOISE_RE.sub("", s)
    s = re.sub(r"\s+", " ", s).strip()
    # Strip path-like tokens (repo/url paths) and punctuation-only tokens
    words = [w for w in s.split() if "/" not in w and re.search(r"[a-z]", w)]
    return " ".join(words[:2])


def rank_emails(
    emails: list[EmailSummary],
    events: list[CalendarEvent],
    now: datetime,
    limit: int = 10,
    overrides: ScoringOverrides | None = None,
    sender_limit: int = 3,
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

    # Collect all attendee addresses for VIP detection
    all_attendee_addrs: set[str] = set()
    for event in events:
        for attendee in event.attendees:
            addr = attendee.lower().strip()
            if addr:
                all_attendee_addrs.add(addr)
    vip_senders = detect_vip_senders(sender_counts, all_attendee_addrs)

    scored = [
        (e.thread_id, score_email(e, attendee_map, now, sender_counts, vip_senders, overrides))
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

    # Subject+sender dedup: collapse near-identical automated emails
    # (same sender, same normalized subject) into one entry.
    subject_groups: dict[tuple[str, str], list[PriorityEmail]] = {}
    unique: list[PriorityEmail] = []
    for p in deduplicated:
        addr = extract_email_address(p.sender)
        norm = _normalize_subject(p.subject)
        key = (addr, norm)
        if norm:
            subject_groups.setdefault(key, []).append(p)
        else:
            unique.append(p)

    for group in subject_groups.values():
        group.sort(key=lambda p: p.score, reverse=True)
        best = group[0]
        best.message_count = sum(p.message_count for p in group)
        unique.append(best)

    deduplicated = unique

    deduplicated.sort(key=lambda p: p.score, reverse=True)

    # Sender diversity: cap emails per sender to prevent notification floods
    if sender_limit > 0:
        sender_seen: dict[str, int] = {}
        diverse: list[PriorityEmail] = []
        deferred: list[PriorityEmail] = []
        for p in deduplicated:
            addr = extract_email_address(p.sender)
            count = sender_seen.get(addr, 0)
            if count < sender_limit:
                diverse.append(p)
                sender_seen[addr] = count + 1
            else:
                deferred.append(p)
        # Backfill: if fewer than limit after capping, add deferred to fill slots
        remaining = limit - len(diverse)
        if remaining > 0 and deferred:
            diverse.extend(deferred[:remaining])
        deduplicated = diverse

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
    relationship_map: dict[str, int] | None = None,
) -> list[EnrichedEvent]:
    """Attach unread-email counts and prep bundles to each event.

    Each prep bundle contains:
    - Unread emails from attendees (as brief AttendeeEmail summaries)
    - Most recent email subject per attendee
    - Shared document links extracted from the event description
    - Contact frequency per attendee (when relationship_map is provided)

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

        contact_frequency: dict[str, int] = {}
        if relationship_map is not None:
            for addr in attendee_addrs:
                count = relationship_map.get(addr, 0)
                if count > 0:
                    contact_frequency[addr] = count

        prep_bundle = PrepBundle(
            unread_emails=unread_emails,
            recent_subjects=recent_subjects,
            shared_links=shared_links,
            contact_frequency=contact_frequency,
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
