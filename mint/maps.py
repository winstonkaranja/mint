"""Google Maps Directions API client for travel time estimates."""

from __future__ import annotations

from datetime import datetime, timedelta

import httpx

DIRECTIONS_URL = "https://maps.googleapis.com/maps/api/directions/json"


async def get_travel_time(
    client: httpx.AsyncClient,
    api_key: str,
    origin: str,
    destination: str,
    arrival_time: datetime,
    mode: str = "driving",
) -> dict | None:
    """Fetch travel duration from Google Maps Directions API.

    Returns {"duration_minutes": int, "duration_text": str, "leave_by": datetime}
    or None when the API returns no routes or on any error.
    """
    if not api_key:
        return None

    try:
        resp = await client.get(
            DIRECTIONS_URL,
            params={
                "origin": origin,
                "destination": destination,
                "mode": mode,
                "arrival_time": str(int(arrival_time.timestamp())),
                "key": api_key,
            },
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError):
        return None

    routes = data.get("routes", [])
    if not routes:
        return None

    leg = routes[0].get("legs", [{}])[0]
    duration = leg.get("duration", {})
    duration_seconds = duration.get("value")
    if duration_seconds is None:
        return None

    duration_minutes = round(duration_seconds / 60)
    leave_by = arrival_time - timedelta(seconds=duration_seconds)

    return {
        "duration_minutes": duration_minutes,
        "duration_text": duration.get("text", f"{duration_minutes} mins"),
        "leave_by": leave_by,
    }
