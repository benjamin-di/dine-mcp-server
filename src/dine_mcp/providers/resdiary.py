"""ResDiary: the public booking widget's API (no account).

- Venue: ``GET booking.resdiary.com/api/Restaurant/<slug>/Setup`` gives party limits, how many days
  ahead it books (sometimes null), areas, and a Windows time zone name. Its ``Services`` describe one
  date only (none without a ``date``), so they never mark a venue closed. An unknown or departed
  venue answers HTTP 500.
- Availability: ``POST …/AvailabilityForDateRange`` answers up to 7 days per request; dates without
  availability are left out, and an empty range is a bare ``[]``. Beyond the window, above the party
  maximum and closed days all look empty, so the first two are decided from Setup first.
- Refs: the widget slug, plus ``/<ThemeId>`` when the link carried one (the widget page needs it).
- Link: the widget with ``?date&time&partySize`` when the ThemeId is known, else the portal page.
"""

from __future__ import annotations

import re
from datetime import date, time
from typing import Any

from ..definitions import ProviderDefinition, load_providers
from ..models import BookingLink, BookingRules, ProviderVenue, ProviderVenueRef, Slot, SlotKind, VenueStatus
from ..transport import Operation, ProviderError, Transport
from .base import Observation, Window, publish
from .common import as_int, batches, json_of, parse_hhmm, plain, unambiguous_local, zone

HOST = "booking.resdiary.com"
BASE = f"https://{HOST}"
DAYS_PER_REQUEST = 7
SLUG = r"[A-Za-z0-9_-]{1,80}"

# Windows time zone names ResDiary uses -> IANA (CLDR windowsZones, territory 001).
WINDOWS_ZONES = {
    "GMT Standard Time": "Europe/London",
    "Greenwich Standard Time": "Atlantic/Reykjavik",
    "W. Europe Standard Time": "Europe/Berlin",
    "Central Europe Standard Time": "Europe/Budapest",
    "Central European Standard Time": "Europe/Warsaw",
    "Romance Standard Time": "Europe/Paris",
    "E. Europe Standard Time": "Europe/Chisinau",
    "GTB Standard Time": "Europe/Bucharest",
    "FLE Standard Time": "Europe/Kiev",
    "AUS Eastern Standard Time": "Australia/Sydney",
    "E. Australia Standard Time": "Australia/Brisbane",
    "Cen. Australia Standard Time": "Australia/Adelaide",
    "AUS Central Standard Time": "Australia/Darwin",
    "W. Australia Standard Time": "Australia/Perth",
    "Tasmania Standard Time": "Australia/Hobart",
    "New Zealand Standard Time": "Pacific/Auckland",
    "Singapore Standard Time": "Asia/Singapore",
    "China Standard Time": "Asia/Shanghai",
    "Taipei Standard Time": "Asia/Taipei",
    "Tokyo Standard Time": "Asia/Tokyo",
    "Korea Standard Time": "Asia/Seoul",
    "SE Asia Standard Time": "Asia/Bangkok",
    "India Standard Time": "Asia/Kolkata",
    "Arabian Standard Time": "Asia/Dubai",
    "Arab Standard Time": "Asia/Riyadh",
    "Israel Standard Time": "Asia/Jerusalem",
    "South Africa Standard Time": "Africa/Johannesburg",
    "Eastern Standard Time": "America/New_York",
    "Central Standard Time": "America/Chicago",
    "Mountain Standard Time": "America/Denver",
    "Pacific Standard Time": "America/Los_Angeles",
    "Canada Central Standard Time": "America/Regina",
    "Atlantic Standard Time": "America/Halifax",
    "UTC": "Etc/UTC",
}


def split_ref(ref: str) -> tuple[str, str | None]:
    slug, _, theme = ref.partition("/")
    return slug, theme if theme.isdigit() else None


class ResDiary:
    key = "resdiary"
    operations = {
        "setup": Operation("setup", "GET", HOST, re.compile(rf"/api/Restaurant/{SLUG}/Setup")),
        "availability_range": Operation(
            "availability_range",
            "POST",
            HOST,
            re.compile(rf"/api/Restaurant/{SLUG}/AvailabilityForDateRange"),
            post_reason="the widget calendar posts its date range as JSON and gets availability only",
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
        slug, _theme = split_ref(ref.ref)
        response = await self._t().request(
            "setup",
            f"{BASE}/api/Restaurant/{slug}/Setup",
            person,
            # Areas and services are per date; today's describe the venue well enough.
            params={"date": date.today().isoformat(), "channelCode": "ONLINE"},
            headers={"Accept": "application/json"},
            accept=(500,),
        )
        if response.status_code in (404, 500):
            raise ProviderError(VenueStatus.UNKNOWN_VENUE, f"setup {response.status_code}")
        if response.status_code != 200:
            raise ProviderError(VenueStatus.UNAVAILABLE, f"setup {response.status_code}")
        setup = json_of(response)
        if not isinstance(setup, dict) or not setup.get("Name"):
            raise ProviderError(VenueStatus.UNAVAILABLE, "setup without a venue")
        rules = BookingRules(
            window_days=as_int(setup.get("AcceptBookingsDaysInAdvance")),
            min_party=as_int(setup.get("MinOnlinePartySize")),
            max_party=as_int(setup.get("MaxOnlinePartySize")),
            source="resdiary setup",
        )
        areas = {a.get("Id"): plain(a.get("Name"), 60) or "" for a in setup.get("Areas") or [] if isinstance(a, dict)}
        return ProviderVenue(
            ref,
            name=plain(setup["Name"]) or slug,
            time_zone=WINDOWS_ZONES.get(str(setup.get("TimeZoneName") or "")),
            country=(setup.get("Iso2CountryCode") or "").upper() or None,
            rules=rules,
            booking_url=self._link(ref.ref, None, None, None),
            currency=setup.get("CurrencyCode"),
            extra={"areas": areas},
        )

    def cost(self, ref: ProviderVenueRef, window: Window) -> int:
        return len(batches(window.dates, DAYS_PER_REQUEST))

    async def availability(self, venue: ProviderVenue, window: Window, person: str) -> Observation:
        tz = zone(venue.time_zone)
        if tz is None:
            raise ProviderError(VenueStatus.UNAVAILABLE, "venue without a known time zone")
        slug, _theme = split_ref(venue.ref.ref)
        areas: dict[Any, str] = venue.extra.get("areas") or {}
        slots: list[Slot] = []
        checked: list[str] = []
        for batch in batches(window.dates, DAYS_PER_REQUEST):
            response = await self._t().request(
                "availability_range",
                f"{BASE}/api/Restaurant/{slug}/AvailabilityForDateRange",
                person,
                json_body={
                    "DateFrom": f"{batch[0].isoformat()}T00:00:00",
                    "DateTo": f"{batch[-1].isoformat()}T00:00:00",
                    "PartySize": window.party_size,
                    "ChannelCode": "ONLINE",
                    "PromotionId": None,
                    "AreaId": None,
                    "AvailabilityType": "Reservation",
                },
                headers={"Accept": "application/json", "Content-Type": "application/json"},
            )
            if response.status_code != 200:
                raise ProviderError(VenueStatus.UNAVAILABLE, f"range {response.status_code}")
            body = json_of(response)
            dates = body if isinstance(body, list) else (body or {}).get("AvailableDates")
            if not isinstance(dates, list):
                raise ProviderError(VenueStatus.UNAVAILABLE, "range without dates")
            wanted = {d.isoformat() for d in batch}
            incomplete: set[str] = set()
            for item in dates:
                if not isinstance(item, dict) or not isinstance(item.get("Date"), str):
                    incomplete |= wanted
                    continue
                day_text = item["Date"][:10]
                if day_text not in wanted:
                    # A malformed date cannot establish which requested day it describes.
                    try:
                        date.fromisoformat(day_text)
                    except ValueError:
                        incomplete |= wanted
                    continue
                day = date.fromisoformat(day_text)
                if not isinstance(item.get("AvailableTimes"), list):
                    incomplete.add(day_text)
                    continue
                for entry in item["AvailableTimes"]:
                    if not isinstance(entry, dict):
                        incomplete.add(day_text)
                        continue
                    at = parse_hhmm(str(entry.get("TimeSlot") or ""))
                    start = unambiguous_local(day, at, tz) if at is not None else None
                    if start is None:
                        incomplete.add(day_text)
                        continue
                    names = [areas.get(i) for i in entry.get("AvailableAreaIds") or [] if areas.get(i)]
                    slots.append(
                        Slot(
                            start=start,
                            party_size=window.party_size,
                            kind=SlotKind.BOOKABLE,
                            area=", ".join(n for n in names if n)[:120] or None,
                        )
                    )
            checked.extend(d.isoformat() for d in batch if d.isoformat() not in incomplete)
            for day_text in sorted(incomplete):
                publish(
                    Observation(
                        VenueStatus.NONE_AVAILABLE,
                        [s for s in slots if s.start[:10] == day_text],
                        [day_text],
                        complete=False,
                    )
                )
            publish(
                Observation(
                    VenueStatus.NONE_AVAILABLE,
                    [s for s in slots if s.start[:10] in wanted],
                    [d for d in checked if d in wanted],
                )
            )
        return Observation(VenueStatus.NONE_AVAILABLE, slots, checked)

    def _link(self, ref: str, day: date | None, at: time | None, party: int | None) -> str:
        slug, theme = split_ref(ref)
        if theme:
            params = [
                p
                for p in (
                    f"date={day.isoformat()}" if day else "",
                    f"time={at.strftime('%H:%M')}" if at else "",
                    f"partySize={party}" if party else "",
                )
                if p
            ]
            return f"{BASE}/widget/Standard/{slug}/{theme}" + ("?" + "&".join(params) if params else "")
        params = [
            p
            for p in (
                f"covers={party}" if party else "",
                f"bookingDate={day.isoformat()}" if day else "",
                f"bookingTime={at.strftime('%H:%M')}" if at else "",
            )
            if p
        ]
        return f"https://www.resdiary.com/Restaurant/{slug}" + ("?" + "&".join(params) if params else "")

    def booking_link(self, venue: ProviderVenue, day: date | None, at: time | None, party: int | None) -> BookingLink:
        prefills = tuple(name for name, value in (("date", day), ("time", at), ("party_size", party)) if value)
        return BookingLink(self._link(venue.ref.ref, day, at, party), prefills)
