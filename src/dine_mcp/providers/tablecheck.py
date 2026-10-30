"""TableCheck: the public reservation page and its availability calendar (no account).

- Venue: ``GET www.tablecheck.com/en/shops/<slug>/reserve`` (works for every venue, including those
  outside TableCheck's marketplace, such as Singapore). The page carries the shop id, time zone,
  country, currency and the party maximum. An unknown slug redirects to ``/en/not-found``.
- Availability: ``POST production.tablecheck.com/v2/hub/availability_calendar_v2`` with the shop id,
  party size and an anchor time. One request covers about two months of dates, but only the anchor
  ±2 hours in 30-minute steps, so a wide time window needs one request per 4 hours.
  ``closed_dates`` are closed. The calendar has no request or waitlist slots.
- TableCheck does not publish its booking window: dates not yet released look full.
- Link: ``/en/shops/<slug>/reserve?start_date&num_people&start_time``.
"""

from __future__ import annotations

import html
import re
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from ..definitions import ProviderDefinition, load_providers
from ..models import BookingLink, BookingRules, ProviderVenue, ProviderVenueRef, Slot, SlotKind, VenueStatus
from ..transport import Operation, ProviderError, Transport
from .base import Observation, Window, publish
from .common import batches, json_of, plain, unambiguous_local, zone

PAGE_HOST = "www.tablecheck.com"
API_HOST = "production.tablecheck.com"
DAYS_PER_REQUEST = 30
HALF_SPAN = timedelta(hours=2)
SHOP_ID = re.compile(r'"shop_id"\s*:\s*"([0-9a-f]{24})"')
TIME_ZONE = re.compile(r'"time_zone"\s*:\s*"([A-Za-z_]+(?:/[A-Za-z0-9_+-]+)*)"')
COUNTRY = re.compile(r'"country"\s*:\s*"([A-Z]{2})"')
CURRENCY = re.compile(r'"currency"\s*:\s*"([A-Z]{3})"')
MAX_PEOPLE = re.compile(r"max_num_people\s*=\s*(\d+)")
TITLE = re.compile(r"<title>\s*([^<]{1,200}?)\s*(?:[-|–]\s*TableCheck)?\s*</title>", re.I)
RELEASE_NOTE = "TableCheck does not publish its booking window; unreleased dates look full"


def anchors(window: Window) -> list[time]:
    """Anchor times whose ±2 h coverage spans the window (in steps of 4 h)."""
    start = datetime.combine(date(2000, 1, 1), window.time_from)
    end = datetime.combine(date(2000, 1, 1), window.time_to)
    if end < start:
        end += timedelta(days=1)
    out = []
    anchor = start + HALF_SPAN
    while True:
        out.append(anchor.time())
        if anchor + HALF_SPAN >= end:
            return out
        anchor += 2 * HALF_SPAN


class TableCheck:
    key = "tablecheck"
    windowed_availability = True
    operations = {
        "venue_page": Operation(
            "venue_page", "GET", PAGE_HOST, re.compile(r"/en/(?:shops/[A-Za-z0-9][A-Za-z0-9-]{0,80}/reserve|not-found)")
        ),
        "availability_calendar": Operation(
            "availability_calendar",
            "POST",
            API_HOST,
            re.compile(r"/v2/hub/availability_calendar_v2"),
            post_reason="the availability calendar takes its query as a JSON body and returns availability only",
        ),
    }

    def __init__(self, definition: ProviderDefinition | None = None) -> None:
        self.definition = definition or load_providers()[self.key]
        self.transport: Transport | None = None

    def bind(self, transport: Transport) -> None:
        self.transport = transport

    def _t(self) -> Transport:
        assert self.transport is not None, "provider not bound to a transport"
        return self.transport

    async def get_venue(self, ref: ProviderVenueRef, person: str) -> ProviderVenue:
        slug = ref.ref.lower()
        response = await self._t().request(
            "venue_page", f"https://{PAGE_HOST}/en/shops/{slug}/reserve", person, headers={"Accept": "text/html"}
        )
        if response.status_code == 404 or response.url.path.endswith("/not-found"):
            raise ProviderError(VenueStatus.UNKNOWN_VENUE, "not-found")
        if response.status_code != 200:
            raise ProviderError(VenueStatus.UNAVAILABLE, f"reserve page {response.status_code}")
        page = response.text
        shop_id = SHOP_ID.search(page)
        if not shop_id:
            raise ProviderError(VenueStatus.UNAVAILABLE, "reserve page without a shop id")
        title = TITLE.search(page)
        max_people = MAX_PEOPLE.search(page)
        found_zone = TIME_ZONE.search(page)
        country = COUNTRY.search(page)
        currency = CURRENCY.search(page)
        return ProviderVenue(
            ProviderVenueRef(self.key, slug),
            name=(plain(html.unescape(title.group(1))) if title else None) or slug,
            time_zone=found_zone.group(1) if found_zone else None,
            country=country.group(1) if country else None,
            rules=BookingRules(max_party=int(max_people.group(1)) if max_people else None, source="tablecheck page"),
            booking_url=f"https://{PAGE_HOST}/en/shops/{slug}/reserve",
            currency=currency.group(1) if currency else None,
            extra={"shop_id": shop_id.group(1)},
        )

    def cost(self, ref: ProviderVenueRef, window: Window) -> int:
        return len(batches(window.dates, DAYS_PER_REQUEST)) * len(anchors(window))

    async def availability(self, venue: ProviderVenue, window: Window, person: str) -> Observation:
        tz = zone(venue.time_zone)
        shop_id = venue.extra.get("shop_id")
        if not shop_id or tz is None:
            raise ProviderError(VenueStatus.UNAVAILABLE, "venue without shop id or time zone")
        wanted = {d.isoformat() for d in window.dates}
        slots: dict[str, Slot] = {}
        checked: set[str] = set()
        closed: set[str] = set()
        for batch in batches(window.dates, DAYS_PER_REQUEST):
            time_anchors = anchors(window)
            batch_days = {d.isoformat() for d in batch}
            coverage: set[str] | None = None
            seen: set[str] = set()
            incomplete: set[str] = set()
            for anchor_index in range(len(time_anchors)):
                start = (
                    datetime.combine(batch[0], window.time_from, tzinfo=tz)
                    + HALF_SPAN
                    + timedelta(hours=4 * anchor_index)
                ).astimezone(UTC)
                response = await self._t().request(
                    "availability_calendar",
                    f"https://{API_HOST}/v2/hub/availability_calendar_v2",
                    person,
                    json_body={
                        "shop_id": shop_id,
                        "locale": "en",
                        "start_at": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "num_people": window.party_size,
                    },
                    headers={"Accept": "application/json", "Content-Type": "application/json"},
                )
                if response.status_code == 404:
                    raise ProviderError(VenueStatus.UNKNOWN_VENUE, "calendar 404")
                if response.status_code != 200:
                    raise ProviderError(VenueStatus.UNAVAILABLE, f"calendar {response.status_code}")
                calendar = (json_of(response) or {}).get("availability_calendar") or {}
                if calendar.get("type") != "success" or not isinstance(calendar.get("data"), dict):
                    raise ProviderError(VenueStatus.UNAVAILABLE, "calendar not a success")
                if calendar.get("time_zone") and calendar["time_zone"] != venue.time_zone:
                    raise ProviderError(VenueStatus.UNAVAILABLE, "calendar time zone differs from the venue's")
                closed |= set(calendar.get("closed_dates") or []) & wanted
                anchor_days = set(closed)
                for day, times in calendar["data"].items():
                    if day not in batch_days:
                        continue
                    seen.add(day)
                    if not isinstance(times, dict):
                        incomplete.add(day)
                        continue
                    anchor_days.add(day)
                    for stamp, state in times.items():
                        if (
                            not isinstance(state, dict)
                            or type(state.get("is_available")) is not bool
                            or state["is_available"]
                            and not self._add(slots, stamp, tz, window.party_size, day)
                        ):
                            incomplete.add(day)
                alternates = calendar.get("alternate_timeslots", {})
                if not isinstance(alternates, dict):
                    incomplete |= batch_days
                    alternates = {}
                for day, stamps in alternates.items():
                    if day in batch_days:
                        seen.add(day)
                        if not isinstance(stamps, list):
                            incomplete.add(day)
                            continue
                        for stamp in stamps:
                            if not self._add(slots, stamp, tz, window.party_size, day):
                                incomplete.add(day)
                anchor_days -= incomplete
                seen |= anchor_days & batch_days
                coverage = anchor_days if coverage is None else coverage & anchor_days
                publish(
                    Observation(
                        VenueStatus.NONE_AVAILABLE,
                        sorted(
                            [s for s in slots.values() if s.start[:10] in batch_days],
                            key=lambda s: s.start,
                        ),
                        sorted(seen),
                        day_status={day: VenueStatus.CLOSED for day in closed},
                        complete=False,
                    )
                )
            complete_days = ((coverage or set()) | closed) & batch_days
            checked |= complete_days
            batch_slots = sorted([s for s in slots.values() if s.start[:10] in complete_days], key=lambda s: s.start)
            publish(
                Observation(
                    VenueStatus.NONE_AVAILABLE,
                    batch_slots,
                    sorted(complete_days),
                    note=None if batch_slots else RELEASE_NOTE,
                    day_status={day: VenueStatus.CLOSED for day in closed},
                )
            )
        day_status = {day: VenueStatus.CLOSED for day in closed}
        checked |= closed
        note = None if slots else RELEASE_NOTE
        return Observation(
            VenueStatus.NONE_AVAILABLE,
            sorted(slots.values(), key=lambda s: s.start),
            sorted(checked),
            note,
            None,
            day_status,
        )

    @staticmethod
    def _add(slots: dict[str, Slot], stamp: Any, tz: Any, party: int, day: str | None = None) -> bool:
        try:
            at = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
            if at.tzinfo is None:
                start = unambiguous_local(at.date(), at.time(), tz)
                if start is None:
                    return False
            else:
                start = at.astimezone(tz).isoformat(timespec="minutes")
        except ValueError:
            return False
        if day is not None and start[:10] != day:
            return False
        slots.setdefault(start, Slot(start=start, party_size=party, kind=SlotKind.BOOKABLE))
        return True

    def booking_link(self, venue: ProviderVenue, day: date | None, at: time | None, party: int | None) -> BookingLink:
        params = []
        prefills = []
        if day:
            params.append(f"start_date={day.isoformat()}")
            prefills.append("date")
        if party:
            params.append(f"num_people={party}")
            prefills.append("party_size")
        if at:
            params.append(f"start_time={at.strftime('%H:%M')}")
            prefills.append("time")
        query = ("?" + "&".join(params)) if params else ""
        return BookingLink(f"https://{PAGE_HOST}/en/shops/{venue.ref.ref}/reserve{query}", tuple(prefills))
