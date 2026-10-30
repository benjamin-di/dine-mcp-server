"""Provider conformance: each adapter against synthetic fixtures through the real transport.

Every adapter must: refuse requests outside its pinned operations; make no more requests than
``cost`` said; give slots in the venue's time zone; map closed days, unknown venues and pushback to
statuses; build booking links on its own hosts that carry what they say they prefill.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest

from dine_mcp import links
from dine_mcp.definitions import load_providers
from dine_mcp.models import ProviderVenueRef, SlotKind, VenueStatus
from dine_mcp.providers import REGISTRY
from dine_mcp.providers.base import Window
from dine_mcp.providers.tablecheck import anchors
from dine_mcp.state import Store
from dine_mcp.transport import ProviderError, Transport

FIXTURES = Path(__file__).parent / "fixtures"
DEFINITIONS = load_providers()


def day(n: int, tz: str = "Asia/Singapore") -> date:
    return datetime.now(ZoneInfo(tz)).date() + timedelta(days=n + 1)


def fixture(name: str, tz: str = "Asia/Singapore") -> str:
    text = (FIXTURES / name).read_text(encoding="utf-8")
    for n in range(9, -1, -1):
        text = text.replace(f"D{n}", day(n, tz).isoformat())
    return text


class Router:
    """Answers by (method, path); records every request."""

    def __init__(self, routes: dict[tuple[str, str], Any]) -> None:
        self.routes = routes
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.routes.get((request.method, request.url.path))
        if answer is None:
            raise AssertionError(f"unrouted {request.method} {request.url}")
        return answer(request) if callable(answer) else answer


def json_response(name: str, status: int = 200, tz: str = "Asia/Singapore") -> httpx.Response:
    return httpx.Response(status, content=fixture(name, tz).encode(), headers={"content-type": "application/json"})


def bound(key: str, router: Router, tmp_path: Path) -> Any:
    provider = REGISTRY[key]()
    client = httpx.AsyncClient(transport=httpx.MockTransport(router))
    sleeps: list[float] = []

    async def no_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    transport = Transport(DEFINITIONS[key], provider.operations, Store(tmp_path), client, sleep=no_sleep)
    provider.bind(transport)
    provider.sleeps = sleeps
    return provider


def window(days: list[date], start: str = "17:00", end: str = "22:30", party: int = 2) -> Window:
    return Window(tuple(days), time.fromisoformat(start), time.fromisoformat(end), party)


# --- SevenRooms -----------------------------------------------------------------------------------------


async def test_sevenrooms(tmp_path):
    router = Router(
        {
            ("GET", "/api-yoa/dining/venue_info"): json_response("sevenrooms/venue_info.json"),
            ("GET", "/api-yoa/availability/ng/widget/range"): json_response("sevenrooms/range.json"),
        }
    )
    provider = bound("sevenrooms", router, tmp_path)
    venue = await provider.get_venue(ProviderVenueRef("sevenrooms", "ExampleGrill"), "alex")
    assert venue.name == "Example Grill" and venue.time_zone == "Asia/Singapore" and venue.country == "SG"
    assert venue.rules.max_party == 8 and venue.rules.window_days == 29
    assert router.requests[0].url.params["venue_url_key"] == "examplegrill"
    asked = window([day(0), day(1), day(2)])
    assert provider.cost(venue.ref, asked) == 1
    observation = await provider.availability(venue, asked, "alex")
    assert len(router.requests) == 2  # venue + one range request for three days
    query = router.requests[1].url.params
    assert query["num_days"] == "3" and query["party_size"] == "2" and query["start_date"] == day(0).isoformat()
    starts = [(s.start[11:16], s.kind, s.experience) for s in observation.slots]
    assert starts == [
        ("18:00", SlotKind.BOOKABLE, "Dinner Tasting"),
        ("18:30", SlotKind.BOOKABLE, "Chef's Counter"),
        ("21:00", SlotKind.REQUEST, "Dinner Tasting"),
        ("19:30", SlotKind.REQUEST, "Dinner Tasting"),
    ]
    assert observation.slots[0].start.endswith("+08:00") and observation.slots[0].requires_card is True
    assert observation.day_status == {day(1).isoformat(): VenueStatus.CLOSED}
    assert observation.dates_checked == [day(n).isoformat() for n in range(3)]
    link = provider.booking_link(venue, day(0), time(19, 0), 4)
    assert link.url.endswith(
        f"/explore/examplegrill/reservations/create/search/?date={day(0)}&party_size=4&start_time=19:00"
    )
    assert links.host_allowed(link.url, DEFINITIONS["sevenrooms"].hosts)
    assert provider.cost(venue.ref, window([day(n) for n in range(7)])) == 3
    assert provider.cost(venue.ref, window([day(0), day(2), day(4)])) == 3  # gaps break batches


async def test_sevenrooms_unknown_and_errors(tmp_path):
    router = Router(
        {
            ("GET", "/api-yoa/dining/venue_info"): json_response("sevenrooms/venue_info_unknown.json", 404),
            ("GET", "/api-yoa/availability/ng/widget/range"): json_response("sevenrooms/range_error.json", 400),
        }
    )
    provider = bound("sevenrooms", router, tmp_path)
    with pytest.raises(ProviderError) as raised:
        await provider.get_venue(ProviderVenueRef("sevenrooms", "nope"), "alex")
    assert raised.value.status is VenueStatus.UNKNOWN_VENUE


# --- TableCheck -----------------------------------------------------------------------------------------


def test_tablecheck_anchors_cover_the_window():
    assert anchors(window([day(0)], "17:00", "22:30")) == [time(19), time(23)]
    assert anchors(window([day(0)], "18:00", "20:00")) == [time(20)]
    assert anchors(window([day(0)], "23:00", "01:00")) == [time(1)]
    assert len(anchors(window([day(0)], "00:00", "23:59"))) == 6


async def test_tablecheck(tmp_path):
    router = Router(
        {
            ("GET", "/en/shops/example-sushi/reserve"): httpx.Response(
                200, text=fixture("tablecheck/reserve_page.html"), headers={"content-type": "text/html"}
            ),
            ("POST", "/v2/hub/availability_calendar_v2"): json_response("tablecheck/calendar.json"),
        }
    )
    provider = bound("tablecheck", router, tmp_path)
    venue = await provider.get_venue(ProviderVenueRef("tablecheck", "example-sushi"), "alex")
    assert venue.name == "Example Sushi" and venue.rules.max_party == 6
    assert venue.extra["shop_id"] == "0123456789abcdef01234567" and venue.time_zone == "Asia/Singapore"
    asked = window([day(0), day(1), day(2)], "17:00", "21:00")
    assert provider.cost(venue.ref, asked) == 1
    observation = await provider.availability(venue, asked, "alex")
    body = json.loads(router.requests[1].content)
    assert body["shop_id"] == "0123456789abcdef01234567" and body["num_people"] == 2
    expected_anchor = datetime.combine(day(0), time(19), tzinfo=ZoneInfo("Asia/Singapore")).astimezone(ZoneInfo("UTC"))
    assert body["start_at"] == expected_anchor.strftime("%Y-%m-%dT%H:%M:%SZ")
    starts = [s.start for s in observation.slots]
    assert starts == [
        f"{day(0)}T18:00+08:00",
        f"{day(0)}T19:30+08:00",
        f"{day(2)}T20:30+08:00",  # an alternate timeslot
    ]
    assert observation.day_status == {day(1).isoformat(): VenueStatus.CLOSED}
    assert day(9).isoformat() not in observation.dates_checked
    link = provider.booking_link(venue, day(0), time(19, 0), 2)
    assert f"start_date={day(0)}&num_people=2&start_time=19:00" in link.url
    assert links.host_allowed(link.url, DEFINITIONS["tablecheck"].hosts)


async def test_tablecheck_unknown_slug_and_wrong_zone(tmp_path):
    router = Router(
        {
            ("GET", "/en/shops/nope/reserve"): httpx.Response(302, headers={"location": "/en/not-found"}),
            ("GET", "/en/not-found"): httpx.Response(404, text=fixture("tablecheck/not_found.html")),
            ("POST", "/v2/hub/availability_calendar_v2"): json_response("tablecheck/calendar_wrong_zone.json"),
        }
    )
    provider = bound("tablecheck", router, tmp_path)
    with pytest.raises(ProviderError) as raised:
        await provider.get_venue(ProviderVenueRef("tablecheck", "nope"), "alex")
    assert raised.value.status is VenueStatus.UNKNOWN_VENUE
    from dine_mcp.models import ProviderVenue

    venue = ProviderVenue(
        ProviderVenueRef("tablecheck", "x"), "X", time_zone="Asia/Singapore", extra={"shop_id": "a" * 24}
    )
    with pytest.raises(ProviderError) as raised:
        await provider.availability(venue, window([day(0)], "18:00", "20:00"), "alex")
    assert raised.value.status is VenueStatus.UNAVAILABLE


# --- ResDiary -------------------------------------------------------------------------------------------


async def test_resdiary(tmp_path):
    tz = "Australia/Sydney"
    router = Router(
        {
            ("GET", "/api/Restaurant/ExampleBistro/Setup"): json_response("resdiary/setup.json", tz=tz),
            ("POST", "/api/Restaurant/ExampleBistro/AvailabilityForDateRange"): json_response(
                "resdiary/range.json", tz=tz
            ),
        }
    )
    provider = bound("resdiary", router, tmp_path)
    venue = await provider.get_venue(ProviderVenueRef("resdiary", "ExampleBistro/12345"), "alex")
    assert venue.time_zone == "Australia/Sydney" and venue.rules.window_days == 60 and venue.rules.max_party == 10
    asked = window([day(n, tz) for n in range(3)])
    assert provider.cost(venue.ref, asked) == 1
    observation = await provider.availability(venue, asked, "alex")
    body = json.loads(router.requests[1].content)
    assert body["DateFrom"].startswith(day(0, tz).isoformat()) and body["DateTo"].startswith(day(2, tz).isoformat())
    assert [(s.start[11:16], s.area) for s in observation.slots] == [
        ("17:00", "Main Dining Room"),
        ("19:15", "Main Dining Room, Counter"),
        ("20:00", "Counter"),
    ]
    assert observation.dates_checked == [day(n, tz).isoformat() for n in range(3)]
    link = provider.booking_link(venue, day(0, tz), time(19, 0), 3)
    assert link.url.startswith("https://booking.resdiary.com/widget/Standard/ExampleBistro/12345?date=")
    assert "partySize=3" in link.url and links.host_allowed(link.url, DEFINITIONS["resdiary"].hosts)
    portal = provider.booking_link(venue.__class__(ProviderVenueRef("resdiary", "ExampleBistro"), "x"), None, None, 2)
    assert portal.url == "https://www.resdiary.com/Restaurant/ExampleBistro?covers=2"


async def test_resdiary_empty_unknown_and_seasonal(tmp_path):
    router = Router(
        {
            ("GET", "/api/Restaurant/Gone/Setup"): json_response("resdiary/error_500.json", 500),
            ("GET", "/api/Restaurant/Seasonal/Setup"): json_response("resdiary/setup_no_services.json"),
            ("POST", "/api/Restaurant/Seasonal/AvailabilityForDateRange"): json_response("resdiary/range_empty.json"),
        }
    )
    provider = bound("resdiary", router, tmp_path)
    with pytest.raises(ProviderError) as raised:
        await provider.get_venue(ProviderVenueRef("resdiary", "Gone"), "alex")
    assert raised.value.status is VenueStatus.UNKNOWN_VENUE
    seasonal = await provider.get_venue(ProviderVenueRef("resdiary", "Seasonal"), "alex")
    assert seasonal.rules.window_days is None and seasonal.time_zone == "Europe/London"
    observation = await provider.availability(seasonal, window([day(0)]), "alex")
    # An empty range is "nothing available", whatever the reason (season, window, full).
    assert observation.status is VenueStatus.NONE_AVAILABLE and not observation.slots
    assert observation.dates_checked == [day(0).isoformat()]


# --- every provider ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("key", sorted(REGISTRY))
async def test_requests_outside_operations_are_refused(key, tmp_path):
    router = Router({})
    provider = bound(key, router, tmp_path)
    with pytest.raises(ProviderError):
        await provider.transport.request(next(iter(provider.operations)), "https://attacker.example/steal", "alex")
    assert router.requests == []


@pytest.mark.parametrize("status", [403, 429])
async def test_pushback_trips_the_breaker_and_stops(status, tmp_path):
    router = Router({("GET", "/api-yoa/dining/venue_info"): httpx.Response(status, text="denied")})
    provider = bound("sevenrooms", router, tmp_path)
    for _ in range(2):
        with pytest.raises(ProviderError) as raised:
            await provider.get_venue(ProviderVenueRef("sevenrooms", "examplegrill"), "alex")
        assert raised.value.status in (VenueStatus.BLOCKED, VenueStatus.RATE_LIMITED)
    assert len(router.requests) == 1  # the second call never left: the breaker is open


async def test_challenge_page_is_blocked_not_parsed(tmp_path):
    challenge = httpx.Response(
        200, text="<html><title>Just a moment...</title></html>", headers={"content-type": "text/html"}
    )
    router = Router({("GET", "/en/shops/example-sushi/reserve"): challenge})
    provider = bound("tablecheck", router, tmp_path)
    with pytest.raises(ProviderError) as raised:
        await provider.get_venue(ProviderVenueRef("tablecheck", "example-sushi"), "alex")
    assert raised.value.status is VenueStatus.BLOCKED


async def test_pacing_waits_between_requests(tmp_path):
    router = Router({("GET", "/api-yoa/dining/venue_info"): json_response("sevenrooms/venue_info.json")})
    provider = bound("sevenrooms", router, tmp_path)
    for _ in range(2):
        await provider.get_venue(ProviderVenueRef("sevenrooms", "examplegrill"), "alex")
    assert provider.sleeps and provider.sleeps[-1] > 1.5


def test_definition_url_patterns():
    defs = DEFINITIONS
    cases = {
        "https://www.sevenrooms.com/reservations/examplegrill?venues=a,b": ("sevenrooms", "examplegrill"),
        "https://www.sevenrooms.com/explore/examplegrill/reservations/create/search/?date=2026-11-06": (
            "sevenrooms",
            "examplegrill",
        ),
        "https://www.sevenrooms.com/experiences/examplegrill/tasting": ("sevenrooms", "examplegrill"),
        "https://www.tablecheck.com/shops/example-sushi/reserve?utm_source=google&rwg_token=x": (
            "tablecheck",
            "example-sushi",
        ),
        "https://www.tablecheck.com/en/shops/example-sushi/reserve": ("tablecheck", "example-sushi"),
        "https://www.tablecheck.com/ja/example-sushi/reserve/landing": ("tablecheck", "example-sushi"),
        "https://booking.resdiary.com/widget/Standard/ExampleBistro/12345?includeJquery=false": (
            "resdiary",
            "ExampleBistro/12345",
        ),
        "https://booking-euw.resdiary.com/widget/Standard/ExampleBistro/9": ("resdiary", "ExampleBistro/9"),
        "https://www.resdiary.com/Restaurant/ExampleBistro": ("resdiary", "ExampleBistro"),
    }
    for url, (key, ref) in cases.items():
        found = links.match(url, defs)
        assert found is not None and (found.provider, found.ref) == (key, ref), url
    for url in (
        "https://www.tablecheck.com/en/policy/terms",
        "https://www.sevenrooms.com/",
        "https://sevenrooms.com.evil.example/reservations/x",
        "https://www.opentable.com/r/example",
    ):
        assert links.match(url, defs) is None, url
