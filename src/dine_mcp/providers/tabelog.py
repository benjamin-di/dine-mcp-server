"""Experimental Tabelog public name discovery and venue facts, never seat inventory.

English suggestion and detail contracts reviewed in printing-press-library. Reservation eligibility
text, opening hours and review scores do not establish availability for a date and party.
"""

from __future__ import annotations

import json
import re
from datetime import date, time
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin

from .. import links
from ..definitions import ProviderDefinition, load_providers
from ..models import BookingLink, ProviderVenue, ProviderVenueRef, VenueStatus
from ..transport import Operation, ProviderError, Transport
from .base import Observation, Window
from .common import json_of, plain

HOST = "tabelog.com"
REF = r"[a-z-]{2,24}/A[0-9]{4}/A[0-9]{6}/[0-9]{7,10}"


class Detail(HTMLParser):
    """Keep only canonical links and bounded Restaurant JSON-LD, never execute markup."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.canonical: list[str] = []
        self.schemas: list[Any] = []
        self._script: list[str] | None = None
        self._size = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "link" and "canonical" in (values.get("rel") or "").split():
            self.canonical.append(values.get("href") or "")
        if tag == "script" and values.get("type") == "application/ld+json":
            self._script, self._size = [], 0

    def handle_data(self, data: str) -> None:
        if self._script is not None:
            self._size += len(data)
            if self._size <= 128_000:
                self._script.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._script is not None:
            if self._size <= 128_000 and len(self.schemas) < 16:
                try:
                    self.schemas.append(json.loads("".join(self._script)))
                except ValueError:
                    pass
            self._script = None

    def restaurants(self) -> list[dict[str, Any]]:
        def visit(value: Any, depth: int = 0) -> list[dict[str, Any]]:
            if depth > 8:
                return []
            if isinstance(value, list):
                return [found for row in value[:100] for found in visit(row, depth + 1)]
            if isinstance(value, dict):
                kind = value.get("@type")
                if kind == "Restaurant" or isinstance(kind, list) and "Restaurant" in kind:
                    return [value]
                return visit(value.get("@graph"), depth + 1)
            return []

        return [row for schema in self.schemas for row in visit(schema)]


class Tabelog:
    key = "tabelog"
    operations = {
        "suggestions": Operation("suggestions", "GET", HOST, re.compile(r"/en/suggest/keyword_suggest")),
        "venue_page": Operation("venue_page", "GET", HOST, re.compile(rf"/en/{REF}/")),
    }

    def __init__(self, definition: ProviderDefinition | None = None) -> None:
        self.definition = definition or load_providers()[self.key]
        self.transport: Transport | None = None

    def bind(self, transport: Transport) -> None:
        self.transport = transport

    def _ref(self, url: Any) -> ProviderVenueRef | None:
        if not isinstance(url, str):
            return None
        return links.match(urljoin(f"https://{HOST}", url), {self.key: self.definition})

    async def get_venue(self, ref: ProviderVenueRef, person: str) -> ProviderVenue:
        if not re.fullmatch(REF, ref.ref):
            raise ProviderError(VenueStatus.UNKNOWN_VENUE, "invalid restaurant ref")
        assert self.transport is not None, "provider not bound to a transport"
        response = await self.transport.request("venue_page", f"https://{HOST}/en/{ref.ref}/", person)
        if response.status_code == 404:
            raise ProviderError(VenueStatus.UNKNOWN_VENUE, "detail not found")
        if response.status_code != 200:
            raise ProviderError(VenueStatus.UNAVAILABLE, "detail response")
        parser = Detail()
        parser.feed(response.text)
        if not parser.canonical or any(self._ref(url) != ref for url in parser.canonical):
            raise ProviderError(VenueStatus.UNAVAILABLE, "detail identity mismatch")
        for row in parser.restaurants():
            if row.get("url") is not None and self._ref(row["url"]) != ref:
                continue
            name = plain(row.get("name")) if isinstance(row.get("name"), str) else None
            if name:
                address = row.get("address")
                street = plain(address.get("streetAddress"), 240) if isinstance(address, dict) else None
                return ProviderVenue(
                    ref,
                    name,
                    time_zone="Asia/Tokyo",
                    country="JP",
                    address=street,
                    booking_url=self.booking_link(ProviderVenue(ref, name), None, None, None).url,
                )
        raise ProviderError(VenueStatus.UNAVAILABLE, "detail without restaurant identity")

    async def search_venues(self, name: str, country: str | None, person: str) -> list[ProviderVenue]:
        if country not in (None, "JP"):
            return []
        assert self.transport is not None, "provider not bound to a transport"
        response = await self.transport.request(
            "suggestions",
            f"https://{HOST}/en/suggest/keyword_suggest",
            person,
            params={"keyword": name},
            headers={"Accept": "application/json"},
        )
        if response.status_code != 200:
            raise ProviderError(VenueStatus.UNAVAILABLE, "suggestions response")
        rows = json_of(response)
        if not isinstance(rows, list):
            raise ProviderError(VenueStatus.UNAVAILABLE, "suggestions without rows")
        found: list[ProviderVenue] = []
        attempted: set[str] = set()
        for row in rows[:100]:
            if not isinstance(row, dict) or row.get("datatype") != "AreaRestaurant":
                continue
            ref = self._ref(row.get("url"))
            if ref is None or ref.ref in attempted:
                continue
            attempted.add(ref.ref)
            try:
                found.append(await self.get_venue(ref, person))
            except ProviderError:
                pass
            if len(attempted) == 5:
                break
        return found

    def cost(self, ref: ProviderVenueRef, window: Window) -> int:
        return 0

    async def availability(self, venue: ProviderVenue, window: Window, person: str) -> Observation:
        raise ProviderError(VenueStatus.UNSUPPORTED, "listing only; no reviewed seat contract")

    def booking_link(self, venue: ProviderVenue, day: date | None, at: time | None, party: int | None) -> BookingLink:
        return BookingLink(f"https://{HOST}/en/{venue.ref.ref}/")
