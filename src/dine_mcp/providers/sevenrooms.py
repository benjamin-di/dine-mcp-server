"""SevenRooms: the public guest reservation widget (no account).

- Venue: ``GET /api-yoa/dining/venue_info?venue_url_key=<key>&widget_type=res_widget`` gives the time
  zone, party size limits and the booking window (``maxDaysOut``, inclusive of today). 404: no venue.
- Availability: ``GET /api-yoa/availability/ng/widget/range`` answers whole days, at most 3 per
  request. ``type == "book"`` with an access id is bookable; ``type == "request"`` is a request slot.
  A closed day is an empty list. Beyond the window and above the party maximum the answer looks like
  "sold out" (request entries only), so both are decided from the venue settings before asking.
- Link: ``/explore/<key>/reservations/create/search/?date&party_size&start_time``.
"""

from __future__ import annotations

import re
from datetime import date, time
from typing import Any

from ..definitions import ProviderDefinition, load_providers
from ..models import BookingLink, BookingRules, ProviderVenue, ProviderVenueRef, Slot, SlotKind, VenueStatus
from ..transport import Operation, ProviderError, Transport
from .base import Observation, Window, publish
from .common import as_int, batches, json_of, money, parse_hhmm, plain, unambiguous_local, zone

HOST = "www.sevenrooms.com"
BASE = f"https://{HOST}"
DAYS_PER_REQUEST = 3


class SevenRooms:
    key = "sevenrooms"
    operations = {
        "venue_info": Operation("venue_info", "GET", HOST, re.compile(r"/api-yoa/dining/venue_info")),
        "availability_range": Operation(
            "availability_range", "GET", HOST, re.compile(r"/api-yoa/availability/ng/widget/range")
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
        key = ref.ref.lower()
        response = await self._t().request(
            "venue_info",
            f"{BASE}/api-yoa/dining/venue_info",
            person,
            params={"venue_url_key": key, "widget_type": "res_widget"},
            headers={"Accept": "application/json"},
        )
        if response.status_code in (400, 404):
            raise ProviderError(VenueStatus.UNKNOWN_VENUE, f"venue_info {response.status_code}")
        if response.status_code != 200:
            raise ProviderError(VenueStatus.UNAVAILABLE, f"venue_info {response.status_code}")
        data = (json_of(response) or {}).get("data") or {}
        info = data.get("venue_info") or {}
        settings = data.get("widget_settings") or {}
        if not info.get("name"):
            raise ProviderError(VenueStatus.UNAVAILABLE, "venue_info without a venue")
        max_days = as_int(settings.get("maxDaysOut"))
        # Only a rolling window is understood; anything else is left unknown rather than guessed.
        rolling = (settings.get("searchableWindowType") or "rolling") == "rolling"
        rules = BookingRules(
            window_days=(max_days - 1) if max_days and rolling else None,
            release_time=None,
            min_party=as_int(settings.get("min_guests")),
            max_party=as_int(settings.get("max_guests")),
            source="sevenrooms widget settings",
        )
        return ProviderVenue(
            ProviderVenueRef(self.key, info.get("url_key") or key),
            name=plain(info["name"]) or key,
            time_zone=info.get("timezone"),
            country=(info.get("country_code") or "").upper() or None,
            locality=plain(info.get("city"), 80),
            address=plain(info.get("address"), 200),
            rules=rules,
            booking_url=f"{BASE}/reservations/{info.get('url_key') or key}",
            currency=info.get("currency_code"),
        )

    def cost(self, ref: ProviderVenueRef, window: Window) -> int:
        return len(batches(window.dates, DAYS_PER_REQUEST))

    async def availability(self, venue: ProviderVenue, window: Window, person: str) -> Observation:
        tz = zone(venue.time_zone)
        if tz is None:
            raise ProviderError(VenueStatus.UNAVAILABLE, "venue without a known time zone")
        slots: list[Slot] = []
        checked: list[str] = []
        day_status: dict[str, VenueStatus] = {}
        for batch in batches(window.dates, DAYS_PER_REQUEST):
            response = await self._t().request(
                "availability_range",
                f"{BASE}/api-yoa/availability/ng/widget/range",
                person,
                params={
                    "venue": venue.ref.ref,
                    "party_size": window.party_size,
                    "start_date": batch[0].isoformat(),
                    "num_days": len(batch),
                    "time_slot": window.time_from.strftime("%H:%M"),
                    "halo_size_interval": 100,
                    "channel": "SEVENROOMS_WIDGET",
                    "exclude_pdr": "true",
                },
                headers={"Accept": "application/json"},
            )
            if response.status_code == 400:
                raise ProviderError(VenueStatus.UNKNOWN_VENUE, "range 400")
            body = json_of(response) if response.status_code == 200 else None
            availability = ((body or {}).get("data") or {}).get("availability")
            if not isinstance(availability, dict) or (body or {}).get("status") not in (200, None):
                raise ProviderError(VenueStatus.UNAVAILABLE, f"range {response.status_code}")
            for day in batch:
                iso_day = day.isoformat()
                shifts = availability.get(iso_day)
                if shifts is None:
                    continue
                if not isinstance(shifts, list):
                    continue
                observed, complete = self._slots(day, shifts, window.party_size, tz, venue.currency)
                slots.extend(observed)
                if complete:
                    checked.append(iso_day)
                if complete and all(
                    s.get("is_closed") is True or s.get("is_forced_empty_availability") is True for s in shifts
                ):
                    day_status[iso_day] = VenueStatus.CLOSED
                publish(
                    Observation(
                        VenueStatus.NONE_AVAILABLE,
                        observed,
                        [iso_day],
                        complete=complete,
                        day_status={iso_day: day_status[iso_day]} if iso_day in day_status else {},
                    )
                )
        return Observation(VenueStatus.NONE_AVAILABLE, slots, checked, day_status=day_status)

    def _slots(
        self, day: date, shifts: list[Any], party: int, tz: Any, currency: str | None
    ) -> tuple[list[Slot], bool]:
        out: list[Slot] = []
        complete = True
        seen: set[tuple[str, str, str | None]] = set()
        for shift in shifts:
            if not isinstance(shift, dict) or any(
                key in shift and type(shift[key]) is not bool for key in ("is_closed", "is_forced_empty_availability")
            ):
                complete = False
                continue
            if shift.get("is_closed") is True or shift.get("is_forced_empty_availability") is True:
                continue
            if not isinstance(shift.get("times"), list):
                complete = False
                continue
            for entry in shift["times"]:
                if not isinstance(entry, dict):
                    complete = False
                    continue
                at = parse_hhmm(str(entry.get("time") or ""))
                start = unambiguous_local(day, at, tz) if at is not None else None
                if start is None:
                    complete = False
                    continue
                kind_text = entry.get("type")
                if kind_text == "book" and entry.get("access_persistent_id"):
                    kind = SlotKind.BOOKABLE
                elif kind_text == "request" or entry.get("is_requestable") is True:
                    kind = SlotKind.REQUEST
                else:
                    continue
                experience = entry.get("public_description_title") or entry.get("public_time_slot_description")
                experience = plain(experience)
                marker = (start, kind.value, experience)
                if marker in seen:
                    continue
                seen.add(marker)
                out.append(
                    Slot(
                        start=start,
                        party_size=party,
                        kind=kind,
                        experience=experience,
                        price=money(entry.get("cost"), currency) if kind is SlotKind.BOOKABLE else None,
                        requires_card=bool(entry.get("require_credit_card")) if kind is SlotKind.BOOKABLE else None,
                    )
                )
        return out, complete

    def booking_link(self, venue: ProviderVenue, day: date | None, at: time | None, party: int | None) -> BookingLink:
        params = []
        prefills = []
        if day:
            params.append(f"date={day.isoformat()}")
            prefills.append("date")
        if party:
            params.append(f"party_size={party}")
            prefills.append("party_size")
        if at:
            params.append(f"start_time={at.strftime('%H:%M')}")
            prefills.append("time")
        query = ("?" + "&".join(params)) if params else ""
        return BookingLink(f"{BASE}/explore/{venue.ref.ref}/reservations/create/search/{query}", tuple(prefills))
