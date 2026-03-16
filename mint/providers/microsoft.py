from __future__ import annotations

from datetime import datetime

import httpx

from mint.models import (
    CalendarEvent,
    CreateEventRequest,
    DraftEmailRequest,
    Email,
    EmailSummary,
    UpdateEventRequest,
)

GRAPH_BASE = "https://graph.microsoft.com/v1.0/me"


class OutlookProvider:
    def __init__(self, client: httpx.AsyncClient, token: str) -> None:
        self.client = client
        self.headers = {"Authorization": f"Bearer {token}"}

    async def get_inbox(self, max_results: int = 20) -> list[EmailSummary]:
        resp = await self.client.get(
            f"{GRAPH_BASE}/messages",
            headers=self.headers,
            params={
                "$top": max_results,
                "$orderby": "receivedDateTime desc",
                "$select": "id,subject,from,receivedDateTime,bodyPreview,isRead,conversationId",
            },
        )
        resp.raise_for_status()
        return [self._to_summary(m) for m in resp.json().get("value", [])]

    async def get_email(self, email_id: str) -> Email:
        resp = await self.client.get(
            f"{GRAPH_BASE}/messages/{email_id}",
            headers=self.headers,
            params={"$select": "id,subject,from,toRecipients,ccRecipients,receivedDateTime,body,isRead"},
        )
        resp.raise_for_status()
        data = resp.json()
        return Email(
            id=data["id"],
            subject=data.get("subject", ""),
            sender=self._format_address(data.get("from", {}).get("emailAddress", {})),
            to=[self._format_address(r["emailAddress"]) for r in data.get("toRecipients", [])],
            cc=[self._format_address(r["emailAddress"]) for r in data.get("ccRecipients", [])],
            date=data["receivedDateTime"],
            body=data.get("body", {}).get("content", ""),
            is_read=data.get("isRead", False),
        )

    async def draft_email(self, request: DraftEmailRequest) -> str:
        body: dict = {
            "subject": request.subject,
            "body": {"contentType": "Text", "content": request.body},
            "toRecipients": [
                {"emailAddress": {"address": a}} for a in request.to
            ],
        }
        if request.cc:
            body["ccRecipients"] = [
                {"emailAddress": {"address": a}} for a in request.cc
            ]
        if request.bcc:
            body["bccRecipients"] = [
                {"emailAddress": {"address": a}} for a in request.bcc
            ]
        resp = await self.client.post(
            f"{GRAPH_BASE}/messages",
            headers=self.headers,
            json=body,
        )
        resp.raise_for_status()
        return resp.json()["id"]

    async def search_emails(
        self, query: str, max_results: int = 20
    ) -> list[EmailSummary]:
        resp = await self.client.get(
            f"{GRAPH_BASE}/messages",
            headers=self.headers,
            params={
                "$search": f'"{query}"',
                "$top": max_results,
                "$select": "id,subject,from,receivedDateTime,bodyPreview,isRead,conversationId",
            },
        )
        resp.raise_for_status()
        return [self._to_summary(m) for m in resp.json().get("value", [])]

    async def get_thread_messages(self, email_id: str) -> list[dict]:
        # Fetch the message to get its conversationId
        resp = await self.client.get(
            f"{GRAPH_BASE}/messages/{email_id}",
            headers=self.headers,
            params={"$select": "conversationId"},
        )
        resp.raise_for_status()
        conv_id = resp.json().get("conversationId")
        if not conv_id:
            return []
        # Fetch all messages in the conversation
        resp = await self.client.get(
            f"{GRAPH_BASE}/messages",
            headers=self.headers,
            params={
                "$filter": f"conversationId eq '{conv_id}'",
                "$orderby": "receivedDateTime",
                "$select": "id,from,receivedDateTime,body,isRead",
            },
        )
        resp.raise_for_status()
        messages = []
        for msg in resp.json().get("value", []):
            messages.append({
                "id": msg["id"],
                "sender": self._format_address(msg.get("from", {}).get("emailAddress", {})),
                "date": msg["receivedDateTime"],
                "body": msg.get("body", {}).get("content", ""),
                "is_read": msg.get("isRead", False),
            })
        return messages

    @staticmethod
    def _to_summary(data: dict) -> EmailSummary:
        return EmailSummary(
            id=data["id"],
            subject=data.get("subject", ""),
            sender=OutlookProvider._format_address(
                data.get("from", {}).get("emailAddress", {})
            ),
            date=data["receivedDateTime"],
            snippet=data.get("bodyPreview", ""),
            is_read=data.get("isRead", False),
            thread_id=data.get("conversationId"),
        )

    @staticmethod
    def _format_address(addr: dict) -> str:
        name = addr.get("name", "")
        email = addr.get("address", "")
        if name:
            return f"{name} <{email}>"
        return email


class MicrosoftCalendarProvider:
    def __init__(self, client: httpx.AsyncClient, token: str) -> None:
        self.client = client
        self.headers = {"Authorization": f"Bearer {token}"}

    async def list_events(
        self, start: datetime, end: datetime, max_results: int = 20
    ) -> list[CalendarEvent]:
        resp = await self.client.get(
            f"{GRAPH_BASE}/calendarView",
            headers=self.headers,
            params={
                "startDateTime": start.isoformat() + "Z",
                "endDateTime": end.isoformat() + "Z",
                "$top": max_results,
                "$orderby": "start/dateTime",
                "$select": "id,subject,start,end,location,body,attendees,isAllDay",
            },
        )
        resp.raise_for_status()
        return [self._parse_event(e) for e in resp.json().get("value", [])]

    async def get_event(self, event_id: str) -> CalendarEvent:
        resp = await self.client.get(
            f"{GRAPH_BASE}/events/{event_id}",
            headers=self.headers,
        )
        resp.raise_for_status()
        return self._parse_event(resp.json())

    async def create_event(self, request: CreateEventRequest) -> CalendarEvent:
        body = self._build_event_body(request)
        resp = await self.client.post(
            f"{GRAPH_BASE}/events",
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
            f"{GRAPH_BASE}/events/{event_id}",
            headers=self.headers,
            json=body,
        )
        resp.raise_for_status()
        return self._parse_event(resp.json())

    async def delete_event(self, event_id: str) -> bool:
        resp = await self.client.delete(
            f"{GRAPH_BASE}/events/{event_id}",
            headers=self.headers,
        )
        resp.raise_for_status()
        return True

    @staticmethod
    def _parse_event(data: dict) -> CalendarEvent:
        start = data.get("start", {}).get("dateTime", "")
        end = data.get("end", {}).get("dateTime", "")
        return CalendarEvent(
            id=data["id"],
            title=data.get("subject", ""),
            start=start,
            end=end,
            location=data.get("location", {}).get("displayName"),
            description=data.get("body", {}).get("content"),
            attendees=[
                a.get("emailAddress", {}).get("address", "")
                for a in data.get("attendees", [])
            ],
            is_all_day=data.get("isAllDay", False),
        )

    @staticmethod
    def _build_event_body(req: CreateEventRequest) -> dict:
        body: dict = {
            "subject": req.title,
            "start": {"dateTime": req.start.isoformat(), "timeZone": "UTC"},
            "end": {"dateTime": req.end.isoformat(), "timeZone": "UTC"},
        }
        if req.location:
            body["location"] = {"displayName": req.location}
        if req.description:
            body["body"] = {"contentType": "Text", "content": req.description}
        if req.attendees:
            body["attendees"] = [
                {"emailAddress": {"address": a}, "type": "required"}
                for a in req.attendees
            ]
        return body

    @staticmethod
    def _build_patch_body(req: UpdateEventRequest) -> dict:
        body: dict = {}
        if req.title is not None:
            body["subject"] = req.title
        if req.start is not None:
            body["start"] = {"dateTime": req.start.isoformat(), "timeZone": "UTC"}
        if req.end is not None:
            body["end"] = {"dateTime": req.end.isoformat(), "timeZone": "UTC"}
        if req.location is not None:
            body["location"] = {"displayName": req.location}
        if req.description is not None:
            body["body"] = {"contentType": "Text", "content": req.description}
        if req.attendees is not None:
            body["attendees"] = [
                {"emailAddress": {"address": a}, "type": "required"}
                for a in req.attendees
            ]
        return body
