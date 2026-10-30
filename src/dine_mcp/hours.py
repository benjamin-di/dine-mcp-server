"""Conservative closed-day facts from the place source's dated seven-day hours."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .places import Place


def closed_days(place: Place, fetched_at: float | None) -> dict[date, float]:
    """Only explicit 'Closed', within the fetched week, with no overlapping opening period.

    Missing, malformed, undated or holiday-uncertain hours are unknown. Never extrapolate a weekly
    schedule beyond the seven days the place source returned, or infer closed from missing slots.
    """
    hours = place.opening_hours
    if fetched_at is None or not isinstance(hours, dict) or not hours or not place.time_zone:
        return {}
    try:
        tz = ZoneInfo(place.time_zone)
        first = datetime.fromtimestamp(fetched_at, tz).date()
        periods = [(_point(p["open"]), _point(p["close"])) for p in hours.get("periods", [])]
        if any(end <= start for start, end in periods):
            return {}
        uncertain = {h["date"] for h in hours.get("holidays", []) if not h.get("confirmed")}
        descriptions = set(hours.get("weekdayDescriptions", []))
    except (AttributeError, KeyError, ValueError, TypeError, ZoneInfoNotFoundError):
        return {}
    out = {}
    for offset in range(7):
        day = first + timedelta(days=offset)
        if day.isoformat() in uncertain or f"{day:%A}: Closed" not in descriptions:
            continue
        start = datetime.combine(day, datetime.min.time())
        end = start + timedelta(days=1)
        if not any(a < end and b > start for a, b in periods):
            out[day] = fetched_at
    return out


def _point(value: dict[str, Any]) -> datetime:
    day = value["date"]
    return datetime(day["year"], day["month"], day["day"], value.get("hour", 0), value.get("minute", 0))
