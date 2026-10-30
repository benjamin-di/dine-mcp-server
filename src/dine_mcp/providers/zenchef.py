"""Experimental Zenchef guest widget reads; no authentication or reservation endpoints.

The reviewed widget contract supplies local clock times, not a time zone. A fresh place-source
zone is therefore needed for availability. Missing/malformed dates never mean sold out.
"""

from __future__ import annotations

import re
from datetime import date, time
from typing import Any

from ..definitions import ProviderDefinition, load_providers
from ..models import BookingLink, ProviderVenue, ProviderVenueRef, Slot, SlotKind, VenueStatus
from ..transport import Operation, ProviderError, Transport
from .base import Observation, Window, publish
from .common import json_of, plain, unambiguous_local, zone

HOST = "bookings-middleware.zenchef.com"
REF = re.compile(r"[1-9][0-9]{0,11}\Z")
CLOCK = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]\Z")
NOTE = "Experimental widget read; missing dates and unrecognised offer requirements remain unknown."


class Zenchef:
    key = "zenchef"
    operations = {
        "widget_params": Operation("widget_params", "GET", HOST, re.compile(r"/getWidgetParams")),
        "availability": Operation("availability", "GET", HOST, re.compile(r"/getAvailabilities")),
    }

    def __init__(self, definition: ProviderDefinition | None = None) -> None:
        self.definition = definition or load_providers()[self.key]
        self.transport: Transport | None = None

    def bind(self, transport: Transport) -> None:
        self.transport = transport

    async def _read(self, operation: str, ref: str, person: str, **params: Any) -> Any:
        if not REF.fullmatch(ref):
            raise ProviderError(VenueStatus.UNKNOWN_VENUE, "invalid restaurant id")
        assert self.transport is not None, "provider not bound to a transport"
        path = "getWidgetParams" if operation == "widget_params" else "getAvailabilities"
        response = await self.transport.request(
            operation,
            f"https://{HOST}/{path}",
            person,
            params={"restaurantId": ref, **params},
            headers={"Accept": "application/json"},
        )
        if response.status_code == 404:
            raise ProviderError(VenueStatus.UNKNOWN_VENUE, "widget not found")
        if response.status_code != 200:
            raise ProviderError(VenueStatus.UNAVAILABLE, "widget response")
        return json_of(response)

    async def get_venue(self, ref: ProviderVenueRef, person: str) -> ProviderVenue:
        data = await self._read("widget_params", ref.ref, person)
        if not isinstance(data, dict) or str(data.get("id")) != ref.ref or not isinstance(data.get("name"), str):
            raise ProviderError(VenueStatus.UNAVAILABLE, "widget without matching venue")
        if data.get("isDisabled") not in (0, False):
            raise ProviderError(VenueStatus.UNAVAILABLE, "widget disabled or unknown")
        country = data.get("restaurantCountry")
        country = country.upper() if isinstance(country, str) and re.fullmatch(r"[A-Za-z]{2}", country) else None
        return ProviderVenue(
            ref,
            plain(data["name"]) or ref.ref,
            country=country,
            booking_url=self.booking_link(ProviderVenue(ref, ref.ref), None, None, None).url,
        )

    def cost(self, ref: ProviderVenueRef, window: Window) -> int:
        return len(window.dates)  # reviewed single-day contract; no speculative range expansion

    async def availability(self, venue: ProviderVenue, window: Window, person: str) -> Observation:
        tz = zone(venue.time_zone)
        if tz is None:
            raise ProviderError(VenueStatus.UNAVAILABLE, "venue without a known time zone")
        slots: list[Slot] = []
        checked: list[str] = []
        for day in window.dates:
            day_text = day.isoformat()
            body = await self._read("availability", venue.ref.ref, person, date_begin=day_text, date_end=day_text)
            if not isinstance(body, list):
                raise ProviderError(VenueStatus.UNAVAILABLE, "calendar without days")
            entries = [entry for entry in body if isinstance(entry, dict) and entry.get("date") == day_text]
            if len(entries) != 1 or not isinstance(entries[0].get("shifts"), list):
                continue
            complete = True
            day_slots: dict[tuple[str, str, str | None], Slot] = {}
            for shift in entries[0]["shifts"]:
                if not isinstance(shift, dict) or not isinstance(shift.get("shift_slots"), list):
                    complete = False
                    continue
                experience = plain(shift.get("name"), 80)
                for item in shift["shift_slots"]:
                    if (
                        not isinstance(item, dict)
                        or not isinstance(item.get("name"), str)
                        or not CLOCK.fullmatch(item["name"])
                    ):
                        complete = False
                        continue
                    if item.get("closed") is True:
                        continue
                    if item.get("closed") is not False:
                        complete = False
                        continue
                    at = time.fromisoformat(item["name"])
                    guests = item.get("possible_guests")
                    waitlist = item.get("waitlist_possible_guests")
                    if not isinstance(guests, list) or not isinstance(waitlist, list):
                        complete = False
                        continue
                    kinds = []
                    if (
                        any(type(n) is int and n == window.party_size for n in guests)
                        and item.get("marked_as_full") is False
                    ):
                        # An offer-required shift needs a separately reviewed eligibility/price contract.
                        if shift.get("is_offer_required") is False:
                            kinds.append(SlotKind.BOOKABLE)
                        else:
                            complete = False
                    if any(type(n) is int and n == window.party_size for n in waitlist):
                        kinds.append(SlotKind.WAITLIST)
                    if type(item.get("marked_as_full")) is not bool:
                        complete = False
                    start = unambiguous_local(day, at, tz)
                    if start is None:
                        complete = False
                        continue
                    for kind in kinds:
                        day_slots[(start, kind.value, experience)] = Slot(
                            start,
                            window.party_size,
                            kind,
                            experience=experience,
                            booking_link=self.booking_link(venue, day, at, window.party_size),
                        )
            observed = list(day_slots.values())
            slots.extend(observed)
            if complete:
                checked.append(day_text)
            publish(Observation(VenueStatus.NONE_AVAILABLE, observed, [day_text], NOTE, complete=complete))
        return Observation(VenueStatus.NONE_AVAILABLE, slots, checked, NOTE)

    def booking_link(self, venue: ProviderVenue, day: date | None, at: time | None, party: int | None) -> BookingLink:
        # Only the restaurant id is evidenced; do not advertise unsupported prefills.
        return BookingLink(f"https://bookings.zenchef.com/results?rid={venue.ref.ref}")
