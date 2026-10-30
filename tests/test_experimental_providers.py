"""Reviewed experimental reads: synthetic transport, budget, identity and coverage contracts."""

from __future__ import annotations

from dataclasses import replace
from datetime import time

import httpx
import pytest
from conftest import FakePlaces
from fastmcp import Client
from test_providers import Router, bound, day, fixture, json_response, window

from dine_mcp import links
from dine_mcp.definitions import Overrides, effective, load_providers
from dine_mcp.models import ProviderVenueRef, SlotKind, VenueStatus
from dine_mcp.providers.base import OBSERVER
from dine_mcp.server import create_server
from dine_mcp.transport import ProviderError
from dine_mcp.widgets import widget_links


async def test_zenchef_guest_reads_and_explicit_waitlist(tmp_path):
    router = Router(
        {
            ("GET", "/getWidgetParams"): json_response("zenchef/widget.json"),
            ("GET", "/getAvailabilities"): json_response("zenchef/calendar.json"),
        }
    )
    provider = bound("zenchef", router, tmp_path)
    venue = await provider.get_venue(ProviderVenueRef("zenchef", "123456"), "alex")
    assert venue.name == "Synthetic Bistro" and venue.country == "FR" and venue.time_zone is None
    with pytest.raises(ProviderError):
        await provider.availability(venue, window([day(0)]), "alex")
    assert len(router.requests) == 1  # never infer a zone from the country
    observation = await provider.availability(
        replace(venue, time_zone="Europe/Paris"), window([day(0), day(1)]), "alex"
    )
    assert provider.cost(venue.ref, window([day(0), day(1)])) == 2
    assert len(router.requests) == 3
    assert observation.dates_checked == [day(0).isoformat()]  # absent date is unknown
    assert [(s.start[11:16], s.kind) for s in observation.slots] == [
        ("18:30", SlotKind.BOOKABLE),
        ("19:00", SlotKind.WAITLIST),
    ]
    assert router.requests[-1].url.params["date_begin"] == day(1).isoformat()
    assert links.match(provider.booking_link(venue, day(0), time(19), 2).url, load_providers()) == venue.ref


@pytest.mark.parametrize("mutation", ["offer", "closed_unknown", "malformed_time", "unknown_full"])
async def test_zenchef_uncertain_contract_never_claims_sold_out(tmp_path, mutation):
    import json

    data = json.loads(fixture("zenchef/calendar.json"))
    item = data[0]["shifts"][0]["shift_slots"][0]
    if mutation == "offer":
        data[0]["shifts"][0]["is_offer_required"] = True
    elif mutation == "closed_unknown":
        del item["closed"]
    elif mutation == "unknown_full":
        del item["marked_as_full"]
    else:
        item["name"] = "tomorrow"
    provider = bound(
        "zenchef",
        Router(
            {
                ("GET", "/getWidgetParams"): json_response("zenchef/widget.json"),
                ("GET", "/getAvailabilities"): httpx.Response(200, json=data),
            }
        ),
        tmp_path,
    )
    venue = await provider.get_venue(ProviderVenueRef("zenchef", "123456"), "alex")
    observation = await provider.availability(replace(venue, time_zone="Europe/Paris"), window([day(0)]), "alex")
    assert not observation.dates_checked
    assert all(slot.kind is not SlotKind.BOOKABLE for slot in observation.slots)


def formitable_router(*, fail_third=False):
    routes = {("GET", "/api/restaurant/abc123/status"): json_response("formitable/status.json")}
    for hour in (10, 14, 18):
        routes[("GET", f"/api/product/abc123/search/{day(0)}T{hour}:00:00.000Z/2/en")] = json_response(
            "formitable/products.json"
        )
    for uid in ("abc001", "abc002", "abc003"):
        routes[("GET", f"/api/availability/abc123/ticket/day/{uid}/{day(0)}T12:00:00.000Z/2/en")] = (
            httpx.Response(503) if fail_third and uid == "abc003" else json_response("formitable/slots.json")
        )
    return Router(routes)


async def test_formitable_bounds_ticket_discovery_and_retains_partial_results(tmp_path):
    router = formitable_router()
    provider = bound("formitable", router, tmp_path)
    venue = await provider.get_venue(ProviderVenueRef("formitable", "abc123"), "alex")
    assert venue.time_zone is None
    published = []
    token = OBSERVER.set(published.append)
    try:
        observed = await provider.availability(replace(venue, time_zone="Europe/Paris"), window([day(0)]), "alex")
    finally:
        OBSERVER.reset(token)
    assert len(router.requests) == 1 + provider.cost(venue.ref, window([day(0)])) == 7
    assert len(observed.slots) == 6 and not observed.dates_checked and not observed.complete
    assert [len(part.slots) for part in published] == [2, 4, 6]
    assert all(not part.complete for part in published)
    assert observed.slots[0].requires_card is None and observed.slots[0].area == "Terrace"
    assert observed.slots[1].kind is SlotKind.WAITLIST and observed.slots[1].area is None
    assert links.match(provider.booking_link(venue, None, None, None).url, load_providers()) == venue.ref


async def test_formitable_later_failure_preserves_completed_ticket_reads(tmp_path):
    provider = bound("formitable", formitable_router(fail_third=True), tmp_path)
    venue = await provider.get_venue(ProviderVenueRef("formitable", "abc123"), "alex")
    published = []
    token = OBSERVER.set(published.append)
    try:
        with pytest.raises(ProviderError):
            await provider.availability(replace(venue, time_zone="Europe/Paris"), window([day(0)]), "alex")
    finally:
        OBSERVER.reset(token)
    assert len(published[-1].slots) == 4 and not published[-1].complete


async def test_tabelog_search_verifies_details_without_fetching_source_urls(tmp_path):
    router = Router(
        {
            ("GET", "/en/suggest/keyword_suggest"): json_response("tabelog/suggestions.json"),
            ("GET", "/en/tokyo/A1301/A130101/12345678/"): httpx.Response(200, text=fixture("tabelog/venue.html")),
        }
    )
    provider = bound("tabelog", router, tmp_path)
    venues = await provider.search_venues("Synthetic", "JP", "alex")
    assert len(venues) == 1 and venues[0].name == "Synthetic Sushi" and venues[0].time_zone == "Asia/Tokyo"
    assert len(router.requests) == 2 and router.requests[0].url.params["keyword"] == "Synthetic"
    assert await provider.search_venues("Synthetic", "KR", "alex") == []
    assert len(router.requests) == 2
    with pytest.raises(ProviderError) as caught:
        await provider.availability(venues[0], window([day(0)]), "alex")
    assert caught.value.status is VenueStatus.UNSUPPORTED


@pytest.mark.parametrize("body", ["<h1>Sign in</h1>", fixture("tabelog/venue.html").replace("12345678", "87654321")])
async def test_tabelog_generic_or_mismatched_page_is_not_a_venue(tmp_path, body):
    provider = bound(
        "tabelog", Router({("GET", "/en/tokyo/A1301/A130101/12345678/"): httpx.Response(200, text=body)}), tmp_path
    )
    with pytest.raises(ProviderError):
        await provider.get_venue(ProviderVenueRef("tabelog", "tokyo/A1301/A130101/12345678"), "alex")


async def test_catchtable_korean_discovery_is_bounded_and_listing_only(tmp_path):
    router = Router(
        {
            ("POST", "/api/v6/search/list"): json_response("catchtable/search.json"),
            ("GET", "/api/v4/shops/synthetic_internal"): json_response("catchtable/venue.json"),
        }
    )
    provider = bound("catchtable", router, tmp_path)
    found = await provider.search_venues("가상", "KR", "alex")
    assert len(found) == 1 and found[0].ref.ref == "synthetic_sushi" and found[0].country == "KR"
    assert len(router.requests) == 2
    assert links.match(provider.booking_link(found[0], None, None, None).url, load_providers()) == found[0].ref
    assert await provider.search_venues("Synthetic", "JP", "alex") == []
    assert len(router.requests) == 2


@pytest.mark.parametrize("key", ["zenchef", "formitable", "tabelog", "catchtable"])
def test_new_providers_remain_off_until_operator_acceptance(key):
    assert not effective(load_providers()[key], Overrides()).enabled


def test_widget_detection_retains_only_reviewed_identifiers():
    definitions = load_providers()
    for html, expected in [
        (
            '<div class="zc-widget-config" data-restaurant="123456"></div>',
            "https://bookings.zenchef.com/results?rid=123456",
        ),
        (
            '<a href="https://bookings.zenchef.com/results?rid=123456&secret=discard">Book</a>',
            "https://bookings.zenchef.com/results?rid=123456",
        ),
        ('<div class="ft-widget-b2" data-restaurant="abc123"></div>', "https://widget.formitable.com/side/en/abc123/"),
    ]:
        assert widget_links(html, "https://www.example.com", definitions) == [expected]
    assert widget_links('<div data-restaurant="123456"></div>', "https://www.example.com", definitions) == []


@pytest.mark.parametrize("source_failed", [False, True])
async def test_provider_fallback_on_empty_or_failed_place_source_through_mcp(make_app, tmp_path, source_failed):
    from conftest import FakeProvider

    class Searchable(FakeProvider):
        async def search_venues(self, name, country, person):
            return [await self.get_venue(ProviderVenueRef(self.key, "synthetic"), person)]

    provider = Searchable("tablecheck", venues={"synthetic": {}})
    app = make_app(FakePlaces([], fail=source_failed), {"tablecheck": provider})
    async with Client(create_server(app, stdio=True)) as client:
        result = await client.call_tool("dine_find_venue", {"name": "Synthetic"})
    assert len(result.structured_content["venues"]) == 1
    assert "not exhaustive" in result.structured_content["note"]


def test_clock_only_widget_times_refuse_dst_gaps_and_folds():
    from datetime import date
    from zoneinfo import ZoneInfo

    from dine_mcp.providers.common import unambiguous_local

    zone = ZoneInfo("Europe/Paris")
    assert unambiguous_local(date(2027, 3, 28), time(2, 30), zone) is None
    assert unambiguous_local(date(2027, 10, 31), time(2, 30), zone) is None
    assert unambiguous_local(date(2027, 10, 31), time(19), zone) == "2027-10-31T19:00+01:00"


@pytest.mark.parametrize(
    "key,ref,path",
    [
        ("zenchef", "123456", "/getWidgetParams"),
        ("formitable", "abc123", "/api/restaurant/abc123/status"),
        ("tabelog", "tokyo/A1301/A130101/12345678", "/en/tokyo/A1301/A130101/12345678/"),
        ("catchtable", "synthetic_sushi", "/api/v4/shops/synthetic_sushi"),
    ],
)
@pytest.mark.parametrize("status,outcome", [(404, VenueStatus.UNKNOWN_VENUE), (403, VenueStatus.BLOCKED)])
async def test_experimental_unknown_and_refused_venues_stop_after_one_read(tmp_path, key, ref, path, status, outcome):
    router = Router({("GET", path): httpx.Response(status)})
    provider = bound(key, router, tmp_path)
    with pytest.raises(ProviderError) as caught:
        await provider.get_venue(ProviderVenueRef(key, ref), "alex")
    assert caught.value.status is outcome and len(router.requests) == 1
