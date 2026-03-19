from __future__ import annotations

import base64
from datetime import datetime, timezone
from email.mime.text import MIMEText

import httpx

from mint.models import (
    CalendarEvent,
    CreateEventRequest,
    DraftEmailRequest,
    DraftSummary,
    Email,
    EmailSummary,
    UpdateEventRequest,
)

GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
GCAL_BASE = "https://www.googleapis.com/calendar/v3/calendars/primary"


class GmailProvider:
    def __init__(self, client: httpx.AsyncClient, token: str) -> None:
        self.client = client
        self.headers = {"Authorization": f"Bearer {token}"}

    async def get_inbox(self, max_results: int = 20) -> list[EmailSummary]:
        resp = await self.client.get(
            f"{GMAIL_BASE}/messages",
            headers=self.headers,
            params={"maxResults": max_results, "labelIds": "INBOX"},
        )
        resp.raise_for_status()
        messages = resp.json().get("messages", [])
        return [await self._to_summary(m["id"]) for m in messages]

    async def get_email(self, email_id: str) -> Email:
        resp = await self.client.get(
            f"{GMAIL_BASE}/messages/{email_id}",
            headers=self.headers,
            params={"format": "full"},
        )
        resp.raise_for_status()
        data = resp.json()
        headers = {h["name"].lower(): h["value"] for h in data["payload"]["headers"]}
        body = self._extract_body(data["payload"])
        return Email(
            id=data["id"],
            subject=headers.get("subject", ""),
            sender=headers.get("from", ""),
            to=self._parse_addresses(headers.get("to", "")),
            cc=self._parse_addresses(headers.get("cc", "")),
            date=datetime.fromtimestamp(int(data["internalDate"]) / 1000),
            body=body,
            is_read="UNREAD" not in data.get("labelIds", []),
        )

    async def draft_email(self, request: DraftEmailRequest) -> str:
        msg = MIMEText(request.body)
        msg["To"] = ", ".join(request.to)
        msg["Subject"] = request.subject
        if request.cc:
            msg["Cc"] = ", ".join(request.cc)
        if request.bcc:
            msg["Bcc"] = ", ".join(request.bcc)
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        resp = await self.client.post(
            f"{GMAIL_BASE}/drafts",
            headers=self.headers,
            json={"message": {"raw": raw}},
        )
        resp.raise_for_status()
        return resp.json()["id"]

    async def search_emails(
        self, query: str, max_results: int = 20
    ) -> list[EmailSummary]:
        resp = await self.client.get(
            f"{GMAIL_BASE}/messages",
            headers=self.headers,
            params={"q": query, "maxResults": max_results},
        )
        resp.raise_for_status()
        messages = resp.json().get("messages", [])
        return [await self._to_summary(m["id"]) for m in messages]

    async def get_thread_messages(self, email_id: str) -> list[dict]:
        # Fetch the message to get its threadId
        resp = await self.client.get(
            f"{GMAIL_BASE}/messages/{email_id}",
            headers=self.headers,
            params={"format": "metadata", "metadataHeaders": ["From"]},
        )
        resp.raise_for_status()
        thread_id = resp.json().get("threadId")
        if not thread_id:
            return []
        # Fetch the full thread
        resp = await self.client.get(
            f"{GMAIL_BASE}/threads/{thread_id}",
            headers=self.headers,
            params={"format": "full"},
        )
        resp.raise_for_status()
        messages = []
        for msg in resp.json().get("messages", []):
            hdrs = {h["name"].lower(): h["value"] for h in msg["payload"]["headers"]}
            messages.append({
                "id": msg["id"],
                "sender": hdrs.get("from", ""),
                "date": datetime.fromtimestamp(int(msg["internalDate"]) / 1000).isoformat(),
                "body": self._extract_body(msg["payload"]),
                "is_read": "UNREAD" not in msg.get("labelIds", []),
                "thread_id": thread_id,
            })
        return messages

    async def list_drafts(self, max_results: int = 20) -> list[DraftSummary]:
        resp = await self.client.get(
            f"{GMAIL_BASE}/drafts",
            headers=self.headers,
            params={"maxResults": max_results},
        )
        resp.raise_for_status()
        drafts = resp.json().get("drafts", [])
        results: list[DraftSummary] = []
        for draft in drafts:
            msg_id = draft["message"]["id"]
            msg_resp = await self.client.get(
                f"{GMAIL_BASE}/messages/{msg_id}",
                headers=self.headers,
                params={"format": "metadata", "metadataHeaders": ["Subject", "To", "Date"]},
            )
            msg_resp.raise_for_status()
            data = msg_resp.json()
            headers = {h["name"].lower(): h["value"] for h in data["payload"]["headers"]}
            results.append(DraftSummary(
                id=draft["id"],
                subject=headers.get("subject", ""),
                to=self._parse_addresses(headers.get("to", "")),
                snippet=data.get("snippet", ""),
                date=datetime.fromtimestamp(int(data["internalDate"]) / 1000),
            ))
        return results

    async def _to_summary(self, message_id: str) -> EmailSummary:
        resp = await self.client.get(
            f"{GMAIL_BASE}/messages/{message_id}",
            headers=self.headers,
            params={"format": "metadata", "metadataHeaders": ["Subject", "From", "Date"]},
        )
        resp.raise_for_status()
        data = resp.json()
        headers = {h["name"].lower(): h["value"] for h in data["payload"]["headers"]}
        return EmailSummary(
            id=data["id"],
            subject=headers.get("subject", ""),
            sender=headers.get("from", ""),
            date=datetime.fromtimestamp(int(data["internalDate"]) / 1000),
            snippet=data.get("snippet", ""),
            is_read="UNREAD" not in data.get("labelIds", []),
            thread_id=data.get("threadId"),
        )

    @staticmethod
    def _extract_body(payload: dict) -> str:
        if payload.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(payload["body"]["data"]).decode(errors="replace")
        for part in payload.get("parts", []):
            if part["mimeType"] == "text/plain" and part.get("body", {}).get("data"):
                return base64.urlsafe_b64decode(part["body"]["data"]).decode(errors="replace")
        for part in payload.get("parts", []):
            if part["mimeType"] == "text/html" and part.get("body", {}).get("data"):
                return base64.urlsafe_b64decode(part["body"]["data"]).decode(errors="replace")
        return ""

    @staticmethod
    def _parse_addresses(raw: str) -> list[str]:
        if not raw:
            return []
        return [a.strip() for a in raw.split(",")]


def _to_rfc3339_utc(dt: datetime) -> str:
    """Format a datetime as an RFC 3339 string with 'Z' suffix.

    If the datetime is timezone-aware, it is converted to UTC first.
    If naive, it is assumed to already represent UTC.
    """
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.isoformat() + "Z"


class GoogleCalendarProvider:
    def __init__(self, client: httpx.AsyncClient, token: str) -> None:
        self.client = client
        self.headers = {"Authorization": f"Bearer {token}"}

    async def list_events(
        self, start: datetime, end: datetime, max_results: int = 20
    ) -> list[CalendarEvent]:
        resp = await self.client.get(
            f"{GCAL_BASE}/events",
            headers=self.headers,
            params={
                "timeMin": _to_rfc3339_utc(start),
                "timeMax": _to_rfc3339_utc(end),
                "maxResults": max_results,
                "singleEvents": True,
                "orderBy": "startTime",
            },
        )
        resp.raise_for_status()
        return [self._parse_event(e) for e in resp.json().get("items", [])]

    async def get_event(self, event_id: str) -> CalendarEvent:
        resp = await self.client.get(
            f"{GCAL_BASE}/events/{event_id}",
            headers=self.headers,
        )
        resp.raise_for_status()
        return self._parse_event(resp.json())

    async def create_event(self, request: CreateEventRequest) -> CalendarEvent:
        body = self._build_event_body(request)
        resp = await self.client.post(
            f"{GCAL_BASE}/events",
            headers=self.headers,
            json=body,
        )
        resp.raise_for_status()
        return self._parse_event(resp.json())

    async def update_event(
        self, event_id: str, request: UpdateEventRequest
    ) -> CalendarEvent:
        body = self._build_patch_body(request)
        resp = await self.client.patch(
            f"{GCAL_BASE}/events/{event_id}",
            headers=self.headers,
            json=body,
        )
        resp.raise_for_status()
        return self._parse_event(resp.json())

    async def delete_event(self, event_id: str) -> bool:
        resp = await self.client.delete(
            f"{GCAL_BASE}/events/{event_id}",
            headers=self.headers,
        )
        resp.raise_for_status()
        return True

    @staticmethod
    def _parse_event(data: dict) -> CalendarEvent:
        start_raw = data.get("start", {})
        end_raw = data.get("end", {})
        is_all_day = "date" in start_raw
        start = start_raw.get("dateTime") or start_raw.get("date", "")
        end = end_raw.get("dateTime") or end_raw.get("date", "")
        return CalendarEvent(
            id=data["id"],
            title=data.get("summary", ""),
            start=start,
            end=end,
            location=data.get("location"),
            description=data.get("description"),
            attendees=[
                a.get("email", "") for a in data.get("attendees", [])
            ],
            is_all_day=is_all_day,
            recurring_event_id=data.get("recurringEventId"),
        )

    @staticmethod
    def _build_event_body(req: CreateEventRequest) -> dict:
        body: dict = {
            "summary": req.title,
            "start": {"dateTime": req.start.isoformat(), "timeZone": "UTC"},
            "end": {"dateTime": req.end.isoformat(), "timeZone": "UTC"},
        }
        if req.location:
            body["location"] = req.location
        if req.description:
            body["description"] = req.description
        if req.attendees:
            body["attendees"] = [{"email": a} for a in req.attendees]
        return body

    @staticmethod
    def _build_patch_body(req: UpdateEventRequest) -> dict:
        body: dict = {}
        if req.title is not None:
            body["summary"] = req.title
        if req.start is not None:
            body["start"] = {"dateTime": req.start.isoformat(), "timeZone": "UTC"}
        if req.end is not None:
            body["end"] = {"dateTime": req.end.isoformat(), "timeZone": "UTC"}
        if req.location is not None:
            body["location"] = req.location
        if req.description is not None:
            body["description"] = req.description
        if req.attendees is not None:
            body["attendees"] = [{"email": a} for a in req.attendees]
        return body
