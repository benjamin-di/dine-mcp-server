"""Helpers shared by provider adapters."""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..models import Money, VenueStatus
from ..transport import ProviderError


def batches(dates: Iterable[date], size: int) -> list[list[date]]:
    """Consecutive runs of dates, each at most ``size`` long."""
    out: list[list[date]] = []
    for day in sorted(set(dates)):
        if out and len(out[-1]) < size and out[-1][-1] + timedelta(days=1) == day:
            out[-1].append(day)
        else:
            out.append([day])
    return out


def plain(value: Any, limit: int = 120) -> str | None:
    """Provider-controlled text as data: control and format characters removed, whitespace collapsed,
    length capped. None when nothing is left."""
    if value is None:
        return None
    text = "".join(" " if unicodedata.category(ch)[0] in "CZ" else ch for ch in str(value))
    text = " ".join(text.split())[:limit].strip()
    return text or None


def zone(name: str | None) -> ZoneInfo | None:
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def unambiguous_local(day: date, at: time, tz: tzinfo) -> str | None:
    """Widget clock-only times cannot disambiguate a DST fold or describe a missing hour."""
    first = datetime.combine(day, at, tzinfo=tz)
    second = first.replace(fold=1)
    if first.utcoffset() != second.utcoffset():
        return None
    if first.astimezone(UTC).astimezone(tz).replace(tzinfo=None) != first.replace(tzinfo=None):
        return None
    return first.isoformat(timespec="minutes")


def parse_hhmm(text: str) -> time | None:
    # These widgets report minute-aligned local clocks, sometimes with zero seconds.
    # Truncating arbitrary text could turn a changed contract into invented inventory.
    if not re.fullmatch(r"[0-2][0-9]:[0-5][0-9](?::00(?:\.0{1,6})?)?", text):
        return None
    try:
        return time.fromisoformat(text)
    except (ValueError, TypeError):
        return None


def json_of(response: Any) -> Any:
    """The response's JSON, or ``unavailable`` when the provider answered something else."""
    try:
        return json.loads(response.content)
    except (ValueError, UnicodeDecodeError):
        raise ProviderError(VenueStatus.UNAVAILABLE, "not JSON") from None


def money(amount: float | int | None, currency: str | None) -> Money | None:
    if not amount or not currency or amount <= 0:
        return None
    zero_decimal = currency.upper() in {"JPY", "KRW", "VND", "CLP", "ISK"}
    minor = int(round(float(amount) * (1 if zero_decimal else 100)))
    shown = f"{currency.upper()} {amount:,.0f}" if zero_decimal else f"{currency.upper()} {amount:,.2f}"
    return Money(minor, currency.upper(), shown)


def as_int(value: Any) -> int | None:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None
