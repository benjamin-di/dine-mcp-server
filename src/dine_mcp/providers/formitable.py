"""Experimental Formitable ticket availability, with bounded and explicitly partial discovery.

The public SDK's status read verifies a live widget. Three time-sensitive product searches are
not an exhaustive list of experiences: positive slots are useful, but empty results remain unknown.
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

HOST = "widget-api.formitable.com"
UID = r"[a-f0-9]{6,64}"
STAMP = r"[0-9]{4}-[0-9]{2}-[0-9]{2}T(?:10|12|14|18):00:00\.000Z"
NOTE = "Experimental partial experience search; absent slots or experiences remain unknown."
MAX_TICKETS = 3


class Formitable:
    key = "formitable"
    operations = {
        "widget_status": Operation("widget_status", "GET", HOST, re.compile(rf"/api/restaurant/{UID}/status")),
        "products": Operation(
            "products", "GET", HOST, re.compile(rf"/api/product/{UID}/search/{STAMP}/[1-9][0-9]?/en")
        ),
        "availability": Operation(
            "availability", "GET", HOST, re.compile(rf"/api/availability/{UID}/ticket/day/{UID}/{STAMP}/[1-9][0-9]?/en")
        ),
    }

    def __init__(self, definition: ProviderDefinition | None = None) -> None:
        self.definition = definition or load_providers()[self.key]
        self.transport: Transport | None = None

    def bind(self, transport: Transport) -> None:
        self.transport = transport

    async def _read(self, operation: str, path: str, person: str) -> Any:
        assert self.transport is not None, "provider not bound to a transport"
        response = await self.transport.request(
            operation, f"https://{HOST}/api/{path}", person, headers={"Accept": "application/json"}
        )
        if response.status_code == 404:
            raise ProviderError(VenueStatus.UNKNOWN_VENUE, "widget not found")
        if response.status_code != 200:
            raise ProviderError(VenueStatus.UNAVAILABLE, "widget response")
        return json_of(response)

    async def get_venue(self, ref: ProviderVenueRef, person: str) -> ProviderVenue:
        if not re.fullmatch(UID, ref.ref):
            raise ProviderError(VenueStatus.UNKNOWN_VENUE, "invalid restaurant uid")
        body = await self._read("widget_status", f"restaurant/{ref.ref}/status", person)
        if not isinstance(body, dict) or body.get("live") is not True or body.get("zenchefId"):
            raise ProviderError(VenueStatus.UNAVAILABLE, "widget not live or migrated")
        # Status publishes no name/time zone. Keep the reference label explicit; place identity supplies
        # the display name and zone when this widget belongs to a current place-source record.
        return ProviderVenue(
            ref,
            f"Formitable widget {ref.ref}",
            booking_url=self.booking_link(ProviderVenue(ref, ref.ref), None, None, None).url,
        )

    def cost(self, ref: ProviderVenueRef, window: Window) -> int:
        return (3 + MAX_TICKETS) * len(window.dates)

    async def availability(self, venue: ProviderVenue, window: Window, person: str) -> Observation:
        tz = zone(venue.time_zone)
        if tz is None:
            raise ProviderError(VenueStatus.UNAVAILABLE, "venue without a known time zone")
        slots: list[Slot] = []
        for day in window.dates:
            tickets: dict[str, dict[str, Any]] = {}
            for hour in (10, 14, 18):
                stamp = f"{day.isoformat()}T{hour}:00:00.000Z"
                body = await self._read(
                    "products", f"product/{venue.ref.ref}/search/{stamp}/{window.party_size}/en", person
                )
                if not isinstance(body, list):
                    raise ProviderError(VenueStatus.UNAVAILABLE, "products without rows")
                for item in body[:200]:
                    if isinstance(item, dict) and isinstance(item.get("uid"), str) and re.fullmatch(UID, item["uid"]):
                        tickets.setdefault(item["uid"], item)
            day_slots: list[Slot] = []
            for uid, ticket in list(tickets.items())[:MAX_TICKETS]:
                stamp = f"{day.isoformat()}T12:00:00.000Z"
                body = await self._read(
                    "availability",
                    f"availability/{venue.ref.ref}/ticket/day/{uid}/{stamp}/{window.party_size}/en",
                    person,
                )
                if not isinstance(body, list):
                    raise ProviderError(VenueStatus.UNAVAILABLE, "availability without rows")
                observed: dict[tuple[str, str], Slot] = {}
                for item in body[:1000]:
                    if (
                        not isinstance(item, dict)
                        or type(item.get("partySize")) is not int
                        or item["partySize"] != window.party_size
                    ):
                        continue
                    kind = {"AVAILABLE": SlotKind.BOOKABLE, "WAITLIST": SlotKind.WAITLIST}.get(str(item.get("status")))
                    raw = item.get("timeString")
                    if (
                        not kind
                        or not isinstance(raw, str)
                        or not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9](?::00)?", raw)
                    ):
                        continue
                    start = unambiguous_local(day, time.fromisoformat(raw), tz)
                    if start is None:
                        continue
                    observed[(start, kind.value)] = Slot(
                        start,
                        window.party_size,
                        kind,
                        area=plain(item.get("area"), 80) if item.get("showAreaToGuest") is True else None,
                        experience=plain(ticket.get("title"), 80),
                        booking_link=self.booking_link(venue, day, time.fromisoformat(raw), window.party_size),
                    )
                day_slots.extend(observed.values())
                slots.extend(observed.values())
                publish(
                    Observation(VenueStatus.NONE_AVAILABLE, list(day_slots), [day.isoformat()], NOTE, complete=False)
                )
        return Observation(VenueStatus.NONE_AVAILABLE, slots, [], NOTE, complete=False)

    def booking_link(self, venue: ProviderVenue, day: date | None, at: time | None, party: int | None) -> BookingLink:
        return BookingLink(f"https://widget.formitable.com/side/en/{venue.ref.ref}/")
