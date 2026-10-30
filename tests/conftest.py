"""Shared fixtures: a fake provider, a fake place source and a built app, with no network."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest

from dine_mcp.composition import App, build_app
from dine_mcp.definitions import ProviderDefinition, load_providers, parse_provider
from dine_mcp.models import BookingLink, BookingRules, ProviderVenue, ProviderVenueRef, Slot, SlotKind, VenueStatus
from dine_mcp.places import Place, PlaceSourceError
from dine_mcp.providers import REGISTRY
from dine_mcp.providers.base import Observation, Window
from dine_mcp.transport import Operation, ProviderError, Transport

HK = ZoneInfo("Asia/Singapore")


def fake_definition(key: str = "fake", **changes: Any) -> ProviderDefinition:
    data = {
        "schema": 1,
        "key": key,
        "name": key.title(),
        "access_mode": "public",
        "maturity": "beta",
        "countries": ["SG"],
        "hosts": [f"www.{key}.example"],
        "url_patterns": [rf"^https://www\.{key}\.example/r/(?P<ref>[a-z0-9-]+)"],
        "request_interval_seconds": 0.5,
        "daily_request_budget": 100,
        "person_daily_share": 0.5,
        "cache_ttl_seconds": 120,
        "terms": "test",
    }
    data.update(changes)
    return parse_provider(data, f"{key}.toml")


@dataclass
class FakeProvider:
    """Answers from a table: ref -> {date: [HH:MM, ...]}; counts calls."""

    key: str = "fake"
    venues: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    rules: BookingRules = field(default_factory=BookingRules)
    fail: VenueStatus | None = None
    calls: list[str] = field(default_factory=list)
    calendar: bool = True
    zones: dict[str, str] = field(default_factory=dict)  # ref -> IANA zone (default Singapore)

    def __post_init__(self) -> None:
        real = load_providers()
        self.definition = real[self.key] if self.key in real else fake_definition(self.key)
        self.host = self.definition.hosts[0]
        self.operations = {"get": Operation("get", "GET", self.host, re.compile(r"/api/.*"))}

    def bind(self, transport: Transport) -> None:
        self.transport = transport

    async def get_venue(self, ref: ProviderVenueRef, person: str) -> ProviderVenue:
        self.calls.append(f"venue:{ref.ref}")
        if ref.ref not in self.venues:
            raise ProviderError(VenueStatus.UNKNOWN_VENUE, "unknown")
        return ProviderVenue(
            ref, f"Venue {ref.ref}", time_zone=self.zones.get(ref.ref, "Asia/Singapore"), rules=self.rules
        )

    def cost(self, ref: ProviderVenueRef, window: Window) -> int:
        return 1 if self.calendar else len(window.dates)

    async def availability(self, venue: ProviderVenue, window: Window, person: str) -> Observation:
        self.calls.append(f"avail:{venue.ref.ref}:{len(window.dates)}")
        if self.fail:
            raise ProviderError(self.fail, "test")
        table = self.venues[venue.ref.ref]
        tz = ZoneInfo(venue.time_zone or "Asia/Singapore")
        slots = []
        for day in window.dates:
            for hhmm in table.get(day.isoformat(), []):
                at = datetime.combine(day, time.fromisoformat(hhmm), tzinfo=tz)
                slots.append(Slot(at.isoformat(), window.party_size, SlotKind.BOOKABLE))
        return Observation(VenueStatus.NONE_AVAILABLE, slots, [d.isoformat() for d in window.dates])

    def booking_link(self, venue: ProviderVenue, day: date | None, at: time | None, party: int | None) -> BookingLink:
        if self.key in REGISTRY:  # the real adapter's link format
            return REGISTRY[self.key]().booking_link(venue, day, at, party)
        return BookingLink(f"https://{self.host}/r/{venue.ref.ref}", ())


class FakePlaces:
    name = "fake"

    def __init__(self, places: list[Place] | None = None, fail: bool = False) -> None:
        self.places = {p.place_id: p for p in places or []}
        self.fail = fail
        self.calls: list[str] = []

    async def search(self, text: str, limit: int) -> list[Place]:
        self.calls.append(f"search:{text}")
        if self.fail:
            raise PlaceSourceError("blocked")
        words = text.split(",")[0].casefold()
        return [p for p in self.places.values() if words in p.name.casefold()][:limit]

    async def get(self, place_id: str) -> Place | None:
        self.calls.append(f"get:{place_id}")
        if self.fail:
            raise PlaceSourceError("blocked")
        return self.places.get(place_id)


def soon(days: int = 1) -> date:
    return datetime.now(HK).date() + timedelta(days=days)


@pytest.fixture
def no_network() -> httpx.AsyncClient:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected network request to {request.url.host}")

    return httpx.AsyncClient(transport=httpx.MockTransport(refuse))


@pytest.fixture
def make_app(tmp_path, no_network):
    """Build the real app with the real providers' definitions, replacing providers as given."""

    def build(places: Any = None, providers: dict[str, Any] | None = None, env: dict[str, str] | None = None) -> App:
        registry = {key: cls() for key, cls in REGISTRY.items()}
        registry.update(providers or {})
        return build_app(
            {"DINE_STATE_DIR": str(tmp_path / "state"), **(env or {})},
            client=no_network,
            place_source=places,
            providers=registry,
        )

    return build
