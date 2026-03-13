from __future__ import annotations

from datetime import datetime

from fastmcp import FastMCP
from fastmcp.dependencies import Depends

from mint.dependencies import get_calendar_provider
from mint.models import CalendarEvent, CreateEventRequest, UpdateEventRequest
from mint.providers.base import CalendarProvider


def register_calendar_tools(mcp: FastMCP) -> None:

    @mcp.tool(annotations={"readOnlyHint": True})
    async def list_events(
        start: datetime,
        end: datetime,
        max_results: int = 20,
        provider: CalendarProvider = Depends(get_calendar_provider),
    ) -> list[CalendarEvent]:
        """List calendar events in a date range."""
        return await provider.list_events(start, end, max_results=max_results)

    @mcp.tool(annotations={"readOnlyHint": True})
    async def get_event(
        event_id: str,
        provider: CalendarProvider = Depends(get_calendar_provider),
    ) -> CalendarEvent:
        """Get a specific calendar event by ID."""
        return await provider.get_event(event_id)

    @mcp.tool()
    async def create_event(
        title: str,
        start: datetime,
        end: datetime,
        location: str | None = None,
        description: str | None = None,
        attendees: list[str] = [],
        provider: CalendarProvider = Depends(get_calendar_provider),
    ) -> CalendarEvent:
        """Create a new calendar event."""
        request = CreateEventRequest(
            title=title,
            start=start,
            end=end,
            location=location,
            description=description,
            attendees=attendees,
        )
        return await provider.create_event(request)

    @mcp.tool()
    async def update_event(
        event_id: str,
        title: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        location: str | None = None,
        description: str | None = None,
        attendees: list[str] | None = None,
        provider: CalendarProvider = Depends(get_calendar_provider),
    ) -> CalendarEvent:
        """Update an existing calendar event. Only provided fields are changed."""
        request = UpdateEventRequest(
            title=title,
            start=start,
            end=end,
            location=location,
            description=description,
            attendees=attendees,
        )
        return await provider.update_event(event_id, request)

    @mcp.tool(annotations={"destructiveHint": True})
    async def delete_event(
        event_id: str,
        provider: CalendarProvider = Depends(get_calendar_provider),
    ) -> bool:
        """Delete a calendar event by ID."""
        return await provider.delete_event(event_id)
