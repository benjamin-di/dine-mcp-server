"""Experimental Korean CatchTable discovery and public venue facts; no account or seat reads.

Endpoint contracts reviewed in tellang/catchtable-cli. That client also has session and booking
features: none are imported or exposed here. Auth/challenge failures stop the read.
"""

from __future__ import annotations

import re
from datetime import date, time
from typing import Any

from ..definitions import ProviderDefinition, load_providers
from ..models import BookingLink, ProviderVenue, ProviderVenueRef, VenueStatus
from ..transport import Operation, ProviderError, Transport
from .base import Observation, Window
from .common import json_of, plain

HOST = "ct-api.catchtable.co.kr"
REF = r"[A-Za-z0-9_-]{1,100}"


class CatchTable:
    key = "catchtable"
    operations = {
        "venue": Operation("venue", "GET", HOST, re.compile(rf"/api/v4/shops/{REF}")),
        "search": Operation(
            "search",
            "POST",
            HOST,
            re.compile(r"/api/v6/search/list"),
            post_reason="the venue catalogue search takes filters and pagination as JSON; no reservation",
        ),
    }

    def __init__(self, definition: ProviderDefinition | None = None) -> None:
        self.definition = definition or load_providers()[self.key]
        self.transport: Transport | None = None

    def bind(self, transport: Transport) -> None:
        self.transport = transport

    async def _read(self, operation: str, person: str, ref: str = "", body: Any = None) -> dict[str, Any]:
        assert self.transport is not None, "provider not bound to a transport"
        path = f"/api/v4/shops/{ref}" if operation == "venue" else "/api/v6/search/list"
        response = await self.transport.request(
            operation,
            f"https://{HOST}{path}",
            person,
            json_body=body,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        if response.status_code == 404:
            raise ProviderError(VenueStatus.UNKNOWN_VENUE, "venue not found")
        if response.status_code != 200:
            raise ProviderError(VenueStatus.UNAVAILABLE, "catalogue response")
        data = json_of(response)
        if not isinstance(data, dict) or data.get("isSuccess") is not True or not isinstance(data.get("data"), dict):
            raise ProviderError(VenueStatus.UNAVAILABLE, "catalogue without successful data")
        return data["data"]

    async def get_venue(self, ref: ProviderVenueRef, person: str) -> ProviderVenue:
        if not re.fullmatch(REF, ref.ref):
            raise ProviderError(VenueStatus.UNKNOWN_VENUE, "invalid venue ref")
        data = await self._read("venue", person, ref.ref)
        venue = data.get("shopDetailVO", data)
        if not isinstance(venue, dict) or not isinstance(venue.get("shopName"), str) or not venue.get("shopName"):
            raise ProviderError(VenueStatus.UNAVAILABLE, "venue without identity")
        # A URL alias can resolve to an internal shopRef. Require the returned alias or ref to bind
        # the result to this lookup rather than trusting a generic/error document.
        if ref.ref not in (venue.get("shopRef"), venue.get("shopUrl")):
            raise ProviderError(VenueStatus.UNAVAILABLE, "venue identity mismatch")
        alias = venue.get("shopUrl")
        if not isinstance(alias, str) or not re.fullmatch(REF, alias):
            raise ProviderError(VenueStatus.UNAVAILABLE, "venue without a booking alias")
        return ProviderVenue(
            ProviderVenueRef(self.key, alias),
            plain(venue["shopName"]) or alias,
            time_zone="Asia/Seoul",
            country="KR",
            address=plain(venue.get("roadAddress"), 240),
            booking_url=f"https://app.catchtable.co.kr/ct/shop/{alias}",
        )

    async def search_venues(self, name: str, country: str | None, person: str) -> list[ProviderVenue]:
        if country not in (None, "KR"):
            return []
        data = await self._read("search", person, body={"keyword": name, "paging": {"page": 1, "size": 5, "offset": 0}})
        results = data.get("shopResults")
        rows = results.get("shops") if isinstance(results, dict) else None
        if not isinstance(rows, list):
            raise ProviderError(VenueStatus.UNAVAILABLE, "search without shops")
        found: dict[str, ProviderVenue] = {}
        attempted: set[str] = set()
        for row in rows[:5]:
            meta = row.get("shopMeta", row) if isinstance(row, dict) else None
            ref = meta.get("shopRef") if isinstance(meta, dict) else None
            if not isinstance(ref, str) or not re.fullmatch(REF, ref) or ref in attempted:
                continue
            attempted.add(ref)
            try:
                venue = await self.get_venue(ProviderVenueRef(self.key, ref), person)
                found[venue.ref.ref] = venue
            except ProviderError:
                continue
        return list(found.values())

    def cost(self, ref: ProviderVenueRef, window: Window) -> int:
        return 0

    async def availability(self, venue: ProviderVenue, window: Window, person: str) -> Observation:
        raise ProviderError(VenueStatus.UNSUPPORTED, "listing only; no reviewed guest seat contract")

    def booking_link(self, venue: ProviderVenue, day: date | None, at: time | None, party: int | None) -> BookingLink:
        return BookingLink(f"https://app.catchtable.co.kr/ct/shop/{venue.ref.ref}")
