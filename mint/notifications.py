"""Server-Sent Events notification system for real-time alerts.

Provides a publish/subscribe mechanism for broadcasting events to connected
SSE clients. Subscribers receive events about high-priority emails and
upcoming calendar meetings as they happen.

Architecture is intentionally simple: an in-memory list of asyncio.Queue
objects, one per connected client. No persistence, no replay — if a client
disconnects, missed events are gone. This is the right tradeoff for
ephemeral alerts that lose value within minutes.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, AsyncIterator

# Each connected SSE client gets its own queue. The list is the source of
# truth for "who is listening right now."
_subscribers: list[asyncio.Queue[dict[str, Any]]] = []


async def subscribe() -> AsyncIterator[str]:
    """Yield SSE-formatted events as they arrive for a single client.

    The caller should pass this async generator directly to a
    StreamingResponse with media_type="text/event-stream". The generator
    cleans up its queue from the subscriber list on disconnect (via the
    finally block).
    """
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    _subscribers.append(queue)
    try:
        while True:
            event = await queue.get()
            data = json.dumps(event, default=str)
            yield f"event: {event.get('type', 'notification')}\ndata: {data}\n\n"
    finally:
        _subscribers.remove(queue)


async def publish(event_type: str, payload: dict[str, Any]) -> None:
    """Broadcast an event to every connected SSE subscriber.

    Events are fire-and-forget: if a subscriber's queue is full (slow
    consumer), that event is silently dropped rather than blocking the
    publisher or other subscribers.
    """
    event = {
        "type": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **payload,
    }
    for queue in list(_subscribers):
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            pass


def subscriber_count() -> int:
    """Return the number of active SSE subscribers."""
    return len(_subscribers)


# ---------------------------------------------------------------------------
# Convenience publishers for common notification types
# ---------------------------------------------------------------------------


async def notify_high_priority(email_subject: str, sender: str, score: float) -> None:
    """Publish a high-priority email alert to all subscribers."""
    await publish("high_priority_email", {
        "subject": email_subject,
        "sender": sender,
        "score": score,
    })


async def notify_upcoming_event(title: str, starts_in_minutes: int) -> None:
    """Publish an upcoming calendar event alert to all subscribers."""
    await publish("upcoming_event", {
        "title": title,
        "starts_in_minutes": starts_in_minutes,
    })
