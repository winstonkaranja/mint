"""Google Routes API client for travel time estimates."""

from __future__ import annotations

from datetime import datetime, timedelta

import httpx

ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"

_MODE_MAP = {
    "driving": "DRIVE",
    "walking": "WALK",
    "bicycling": "BICYCLE",
    "transit": "TRANSIT",
    "two_wheeler": "TWO_WHEELER",
}


async def get_travel_time(
    client: httpx.AsyncClient,
    api_key: str,
    origin: str,
    destination: str,
    arrival_time: datetime,
    mode: str = "driving",
) -> dict | None:
    """Fetch travel duration from Google Routes API (traffic-aware).

    Returns {"duration_minutes": int, "duration_text": str, "leave_by": datetime}
    or None when the API returns no routes or on any error.
    """
    if not api_key:
        return None

    travel_mode = _MODE_MAP.get(mode, "DRIVE")

    body: dict = {
        "origin": {"address": origin},
        "destination": {"address": destination},
        "travelMode": travel_mode,
        "routingPreference": "TRAFFIC_AWARE",
    }

    # Routes API uses departureTime (not arrivalTime) for traffic estimation.
    # We make an initial call to get duration, then compute leave_by from arrival_time.
    try:
        resp = await client.post(
            ROUTES_URL,
            headers={
                "X-Goog-Api-Key": api_key,
                "X-Goog-FieldMask": "routes.duration,routes.distanceMeters",
            },
            json=body,
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError):
        return None

    routes = data.get("routes", [])
    if not routes:
        return None

    duration_str = routes[0].get("duration", "")
    if not duration_str:
        return None

    # Duration comes as e.g. "1523s"
    try:
        duration_seconds = int(duration_str.rstrip("s"))
    except (ValueError, TypeError):
        return None

    duration_minutes = round(duration_seconds / 60)
    leave_by = arrival_time - timedelta(seconds=duration_seconds)

    if duration_minutes < 60:
        duration_text = f"{duration_minutes} mins"
    else:
        hours = duration_minutes // 60
        mins = duration_minutes % 60
        duration_text = f"{hours} hr {mins} min" if mins else f"{hours} hr"

    return {
        "duration_minutes": duration_minutes,
        "duration_text": duration_text,
        "leave_by": leave_by,
    }
