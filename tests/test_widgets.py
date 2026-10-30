"""Optional website discovery: synthetic pages only, with the real service and DNS boundary."""

from __future__ import annotations

import asyncio
import socket
import time
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from conftest import FakePlaces, FakeProvider, soon
from fastmcp import Client

from dine_mcp.composition import ConfigError, build_app
from dine_mcp.controls import TRACE, call_scope
from dine_mcp.definitions import load_providers
from dine_mcp.errors import DineError
from dine_mcp.places import Place
from dine_mcp.server import create_server
from dine_mcp.state import PLACE_TTL, Store
from dine_mcp.transport import ProviderError, WebsiteTransport, provider_client, website_url
from dine_mcp.widgets import FAILURE_TTL, widget_links

HTML = (Path(__file__).parent / "fixtures/website_widgets.html").read_text()
PLACE = Place(
    "ChIJ_synthetic_website000",
    "Harbour Grill",
    address="1 Example Lane, Singapore",
    time_zone="Asia/Singapore",
    website="https://venue.example/menu?utm_source=directory",
)


async def no_sleep(_):
    pass


@pytest.fixture
async def build_site(tmp_path):
    apps = []

    def build(handler=None, *, place=PLACE, enabled=True, env=None):
        calls = []

        async def respond(request):
            calls.append(request)
            if handler:
                return await handler(request)
            return httpx.Response(200, text=HTML, headers={"content-type": "text/html"})

        provider = FakeProvider("sevenrooms", venues={"synthetic-harbour": {soon(1).isoformat(): ["19:00"]}})
        app = build_app(
            {
                "DINE_STATE_DIR": str(tmp_path / "state"),
                "DINE_WEBSITE_DISCOVERY": "1" if enabled else "0",
                **(env or {}),
            },
            client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(599))),
            website_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)) if enabled else None,
            place_source=FakePlaces([place]),
            providers=None,
        )
        # Bind only this adapter's synthetic implementation; the other adapters must stay unused.
        app.service.providers["sevenrooms"] = provider
        provider.bind(app.transports["sevenrooms"])
        if app.service.resolver.websites:
            app.service.resolver.websites.transport._sleep = no_sleep
        apps.append(app)
        return app, calls, provider

    yield build
    for app in apps:
        await app.aclose()


@pytest.mark.parametrize(
    "value",
    [
        "http://venue.example",
        "https://localhost/",
        "https://machine.local/",
        "https://machine.internal/",
        "https://127.0.0.1/",
        "https://8.8.8.8/",
        "https://127.1/",
        "https://2130706433/",
        "https://[::1]/",
        "https://[::ffff:127.0.0.1]/",
        "https://user@venue.example/",
        "https://venue.example:443/",
        "https://venue.example:bad/",
        "https://venue.example\\@other.example/",
        "https://venue.example\n/",
        "https://venue.example./",
        "https://%31%32%37.0.0.1/",
        "https://venue.example/?venue=another-restaurant",
        "https://venue.example/?cancel",
    ],
)
def test_website_source_refuses_unsafe_or_ambiguous_urls(value):
    assert website_url(value) is None


def test_website_source_removes_query_and_fragment_and_normalizes_idn():
    assert website_url("https://VENUE.example/menu?utm_source=synthetic#book") == "https://venue.example/menu"
    assert website_url("https://食堂.example/menu") == "https://xn--cks722m.example/menu"


@pytest.mark.parametrize(
    "html, expected",
    [
        (HTML, ["https://www.sevenrooms.com/reservations/synthetic-harbour"]),
        (
            '<iframe src="https://www.tablecheck.com/en/shops/synthetic-sushi/reserve"></iframe>',
            ["https://www.tablecheck.com/en/shops/synthetic-sushi/reserve"],
        ),
        (
            '<button data-reservation-url="https://booking.resdiary.com/widget/Standard/Synthetic/123">Book</button>',
            ["https://booking.resdiary.com/widget/Standard/Synthetic/123"],
        ),
        (HTML + '<a href="https://www.tablecheck.com/shops/another-venue/reserve">Other venue</a>', []),
        ('<form action="https://www.sevenrooms.com/reservations/ignored"></form>', []),
        ('<a href="https://www.opentable.com/r/unsupported">Reserve</a>', []),
    ],
)
def test_widget_detection_is_markup_only_supported_hosts_and_one_distinct_venue(html, expected):
    assert widget_links(html, "https://venue.example/menu", load_providers()) == expected


async def test_discovery_drives_availability_and_reuses_links_without_permanent_aliases(build_site):
    app, calls, provider = build_site()
    found = await app.service.find_venue("person-a", name=PLACE.name)
    venue = found["venues"][0]
    channel = next(c for c in venue["channels"] if c["kind"] == "sevenrooms")
    assert channel["source"] == "website" and not channel["verified"]
    assert app.store.venue_for_alias("sevenrooms:synthetic-harbour") is None
    result = await app.service.check_availability("person-b", [venue["venue_id"]], soon(1).isoformat())
    assert result["results"][0]["status"] == "ok" and result["results"][0]["slots"][0]["start"][11:16] == "19:00"
    await app.service.venue_view(venue["venue_id"], detailed=True, person="person-b")
    assert len(calls) == app.store.used("websites") == app.store.used("websites", "person-a") == 1
    assert app.store.used("websites", "person-b") == 0
    assert calls[0].url == "https://venue.example/menu" and calls[0].method == "GET"
    row = app.store._one("SELECT data FROM website_cache WHERE place_id=?", PLACE.place_id)[0]
    assert row == '["https://www.sevenrooms.com/reservations/synthetic-harbour"]'
    assert len(provider.calls) == 2
    assert app.service.status(app.transports)["website_discovery"]["used_today"] == 1


async def test_discovery_is_default_off_and_skips_existing_booking_channels(build_site):
    app, calls, _ = build_site(enabled=False)
    result = await app.service.find_venue("local", name=PLACE.name)
    assert all(c["source"] != "website" for c in result["venues"][0]["channels"]) and calls == []
    assert app.service.status(app.transports)["website_discovery"] == {"enabled": False}
    direct = replace(PLACE, reservation_links=(("https://www.sevenrooms.com/reservations/synthetic-harbour", ""),))
    app, calls, _ = build_site(place=direct)
    await app.service.find_venue("local", name=PLACE.name)
    assert calls == []


async def test_model_urls_and_unconfirmed_place_objects_never_trigger_website_reads(build_site):
    app, calls, _ = build_site()
    result = await app.service.find_venue("local", url="https://attacker.example/")
    assert not result["venues"] and not calls
    await app.service.resolver.websites.discover(PLACE, "local")
    assert not calls  # not yet backed by a place-source cache row
    app.store.cache_place(PLACE.place_id, PLACE.to_cache())
    await app.service.resolver.websites.discover(replace(PLACE, website="https://attacker.example/"), "local")
    assert not calls


@pytest.mark.parametrize(
    "location",
    [
        "https://attacker.example/",
        "http://venue.example/",
        "https://127.0.0.1/",
        "https://user@venue.example/",
        "https://venue.example:bad/",
        "https://venue.example:444/",
        "https://venue.example/booking?token=synthetic",
    ],
)
async def test_redirect_cannot_leave_the_source_https_host(build_site, location):
    async def redirect(request):
        return httpx.Response(302, headers={"location": location})

    app, calls, _ = build_site(redirect)
    result = await app.service.find_venue("local", name=PLACE.name)
    assert len(calls) == 1 and all(c["source"] != "website" for c in result["venues"][0]["channels"])


async def test_same_host_redirects_are_paced_and_charged_and_send_no_credentials(build_site):
    async def redirect(request):
        if request.url.path == "/menu":
            return httpx.Response(302, headers={"location": "/booking", "set-cookie": "synthetic=private; Path=/"})
        return httpx.Response(200, text=HTML, headers={"content-type": "text/html"})

    app, calls, _ = build_site(redirect)
    app.website_client.cookies.set("synthetic", "private", domain="venue.example")
    app.website_client.auth = httpx.BasicAuth("synthetic", "synthetic")
    app.website_client.headers.update({"authorization": "synthetic", "referer": "https://private.example/"})
    pauses = []

    async def paced(delay):
        pauses.append(delay)

    app.service.resolver.websites.transport._sleep = paced
    token = TRACE.set("00-" + "1" * 32 + "-" + "2" * 16 + "-01")
    try:
        await app.service.find_venue("local", name=PLACE.name)
    finally:
        TRACE.reset(token)
    assert len(calls) == app.store.used("websites") == 2 and len(pauses) == 1
    assert all(not r.headers.get(h) for r in calls for h in ("authorization", "cookie", "referer", "traceparent"))
    assert not app.website_client.cookies


@pytest.mark.parametrize("addresses", [["127.0.0.1"], ["8.8.8.8", "10.0.0.1"], ["169.254.169.254"]])
async def test_website_dns_refuses_private_and_mixed_answers_before_connecting(tmp_path, addresses):
    store = Store(tmp_path)
    async with provider_client() as client:
        backend = client._transport._pool._network_backend

        class NoSocket:
            async def connect_tcp(self, *args, **kwargs):
                raise AssertionError("unsafe DNS reached the socket connector")

        backend.backend = NoSocket()

        async def resolve(*args, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443)) for address in addresses]

        backend.resolve = resolve
        with pytest.raises(ProviderError):
            await WebsiteTransport(store, client).read("https://venue.example/", "local")
    store.close()


async def test_website_body_cap_stops_streaming_and_preserves_directory_results(build_site):
    reads = []

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            for n in range(20):
                reads.append(n)
                yield b"x" * 100_000

    async def oversized(request):
        return httpx.Response(200, stream=Body(), headers={"content-type": "text/html"})

    app, calls, _ = build_site(oversized)
    found = await app.service.find_venue("local", name=PLACE.name)
    assert found["venues"] and len(reads) == 6 and len(calls) == 1
    assert all(c["source"] != "website" for c in found["venues"][0]["channels"])


@pytest.mark.parametrize("status, content_type", [(200, "application/json"), (404, "text/html"), (429, "text/html")])
async def test_website_failures_are_negative_cached_without_losing_directory_data(build_site, status, content_type):
    async def failure(request):
        return httpx.Response(status, text=HTML, headers={"content-type": content_type})

    app, calls, _ = build_site(failure)
    first = await app.service.find_venue("local", name=PLACE.name)
    await app.service.find_venue("local", name=PLACE.name)
    assert len(calls) == 1 and len(first["venues"][0]["channels"]) == 1
    cached = app.service.resolver.websites.cached(PLACE)
    assert cached["urls"] == [] and 0 < cached["expires_at"] - time.time() <= FAILURE_TTL


async def test_discovery_obeys_actual_dispatch_cap(build_site):
    async def redirect(request):
        return httpx.Response(302, headers={"location": "/booking"})

    app, calls, _ = build_site(redirect)
    with call_scope(5) as call:
        call.limit = 1
        with pytest.raises(DineError, match="too_broad"):
            await app.service.find_venue("local", name=PLACE.name)
    assert len(calls) == 1


async def test_concurrent_resolution_coalesces_website_reads(build_site):
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed(request):
        entered.set()
        await release.wait()
        return httpx.Response(200, text=HTML, headers={"content-type": "text/html"})

    app, calls, _ = build_site(delayed)
    tasks = [asyncio.create_task(app.service.find_venue(f"person-{n}", name=PLACE.name)) for n in range(8)]
    await entered.wait()
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(*tasks)
    assert len(calls) == 1


async def test_website_associations_expire_with_source_and_disabling_purges_them(build_site, tmp_path):
    app, _, _ = build_site()
    found = await app.service.find_venue("local", name=PLACE.name)
    venue_id = found["venues"][0]["venue_id"]
    app.store._db.execute("UPDATE place_cache SET fetched_at=?", (time.time() - PLACE_TTL + 30,))
    app.store._db.execute("DELETE FROM website_cache")
    await app.service.venue_view(venue_id, detailed=False)
    cached = app.service.resolver.websites.cached(PLACE)
    assert 0 < cached["expires_at"] - time.time() <= 30
    disabled, calls, _ = build_site(enabled=False)
    assert all(c["source"] != "website" for c in disabled.store.get_venue(venue_id)["channels"])
    assert disabled.store._one("SELECT count(*) FROM website_cache")[0] == 0 and not calls


async def test_expired_website_channels_do_not_survive_source_failure(build_site):
    app, _, _ = build_site()
    venue_id = (await app.service.find_venue("local", name=PLACE.name))["venues"][0]["venue_id"]
    record = app.store.get_venue(venue_id)
    for channel in record["channels"]:
        if channel.get("source") == "website":
            channel["expires_at"] = 0
    app.store.save_venue(record, [])
    app.store._db.execute("UPDATE place_cache SET fetched_at=0")
    app.service.resolver.places.fail = True
    app.store.prune()
    result = await app.service.venue_view(venue_id, detailed=False)
    assert not any(c["source"] == "website" for c in result["channels"])


async def test_changed_source_website_invalidates_previous_association(build_site):
    app, calls, _ = build_site()
    venue_id = (await app.service.find_venue("local", name=PLACE.name))["venues"][0]["venue_id"]
    changed = replace(PLACE, website="https://changed.example/")
    app.store.cache_place(PLACE.place_id, changed.to_cache())

    async def no_widget(request):
        return httpx.Response(200, text="<p>No widget</p>", headers={"content-type": "text/html"})

    app.website_client._transport = httpx.MockTransport(no_widget)
    result = await app.service.venue_view(venue_id, detailed=False)
    assert not any(c["source"] == "website" for c in result["channels"])
    assert len(calls) == 1 and app.store.used("websites") == 2


async def test_optional_timeout_preserves_directory_and_restart_reuses_success(build_site, monkeypatch):
    app, calls, _ = build_site()
    await app.service.find_venue("local", name=PLACE.name)
    restarted, second_calls, _ = build_site()
    result = await restarted.service.find_venue("local", name=PLACE.name)
    assert any(c["source"] == "website" for c in result["venues"][0]["channels"])
    assert not second_calls and len(calls) == 1 and restarted.store.used("websites") == 1
    restarted.store._db.execute("DELETE FROM website_cache")

    async def timeout(*args):
        raise TimeoutError

    monkeypatch.setattr(restarted.service.resolver.websites.transport, "read", timeout)
    result = await restarted.service.find_venue("local", name=PLACE.name)
    assert result["venues"] and not any(c["source"] == "website" for c in result["venues"][0]["channels"])


async def test_discovery_budget_and_pushback_survive_restart(build_site):
    app, calls, _ = build_site()
    assert app.store.spend("websites", "local", 1, 1)
    # Exhaust the configured per-person share without making network requests.
    app.store._db.execute("UPDATE budgets SET used=25 WHERE provider='websites'")
    result = await app.service.find_venue("local", name=PLACE.name)
    assert result["venues"] and not calls
    assert not any(c["source"] == "website" for c in result["venues"][0]["channels"])
    app.store._db.execute("DELETE FROM website_cache")
    app.store.trip("websites", "synthetic refusal")
    restarted, calls, _ = build_site()
    result = await restarted.service.find_venue("person-b", name=PLACE.name)
    assert result["venues"] and not calls
    assert restarted.service.status(restarted.transports)["website_discovery"]["breaker_open_until"]


async def test_global_disable_refuses_discovery_and_invalid_opt_in_is_rejected(build_site, tmp_path):
    overrides = tmp_path / "overrides.toml"
    overrides.write_text("disabled = true\n")
    app, calls, _ = build_site(env={"DINE_OVERRIDES": str(overrides)})
    assert app.service.resolver.websites is None
    with pytest.raises(DineError, match="disabled"):
        await app.service.find_venue("local", name=PLACE.name)
    assert not calls
    with pytest.raises(ConfigError, match="DINE_WEBSITE_DISCOVERY"):
        build_site(env={"DINE_WEBSITE_DISCOVERY": "true"})


async def test_discovered_channels_and_status_cross_the_mcp_boundary(build_site):
    app, calls, _ = build_site()
    async with Client(create_server(app, stdio=True)) as client:
        result = (await client.call_tool("dine_find_venue", {"name": PLACE.name})).structured_content
        channel = next(c for c in result["venues"][0]["channels"] if c["source"] == "website")
        assert channel["kind"] == "sevenrooms" and not channel["verified"]
        status = await client.call_tool("dine_status", {})
        assert status.structured_content["website_discovery"]["used_today"] == 1
        assert "Website discovery: on" in status.content[0].text
    assert len(calls) == 1


async def test_provider_and_website_clients_cannot_share_credentials(tmp_path):
    async with httpx.AsyncClient() as client:
        with pytest.raises(ConfigError, match="separate HTTP client"):
            build_app(
                {"DINE_STATE_DIR": str(tmp_path), "DINE_WEBSITE_DISCOVERY": "1"},
                client=client,
                website_client=client,
                place_source=None,
            )


async def test_provider_hosts_cannot_bypass_adapter_operations_or_budgets(build_site):
    app, calls, _ = build_site()
    reader = app.service.resolver.websites.transport
    for url in [
        "https://www.sevenrooms.com/api-yoa/anything",
        "https://www.tablecheck.com/en/shops/synthetic-sushi/reserve",
        "https://production.tablecheck.com/v2/hub/anything",
        "https://booking.resdiary.com/api/anything",
    ]:
        with pytest.raises(ProviderError, match="unavailable"):
            await reader.read(url, "local")
    assert not calls and not app.store.used("websites")
    direct = replace(PLACE, website="https://www.sevenrooms.com/reservations/synthetic-harbour")
    direct_app, calls, _ = build_site(place=direct)
    found = await direct_app.service.find_venue("local", name=PLACE.name)
    assert any(c["kind"] == "sevenrooms" for c in found["venues"][0]["channels"]) and not calls


async def test_discovery_discards_result_if_source_changes_during_read(build_site):
    async def changed_source(request):
        app.store.cache_place(PLACE.place_id, replace(PLACE, website="https://changed.example/").to_cache())
        return httpx.Response(200, text=HTML, headers={"content-type": "text/html"})

    app, calls, _ = build_site(changed_source)
    app.store.cache_place(PLACE.place_id, PLACE.to_cache())
    await app.service.resolver.websites.discover(PLACE, "local")
    assert len(calls) == 1
    assert app.service.resolver.websites.cached(PLACE) is None
    assert app.store._one("SELECT count(*) FROM website_cache")[0] == 0


async def test_discovery_keeps_original_source_expiry_across_refresh(build_site):
    async def refresh(request):
        app.store.cache_place(PLACE.place_id, PLACE.to_cache())
        return httpx.Response(200, text=HTML, headers={"content-type": "text/html"})

    app, _, _ = build_site(refresh)
    app.store.cache_place(PLACE.place_id, PLACE.to_cache())
    app.store._db.execute("UPDATE place_cache SET fetched_at=?", (time.time() - PLACE_TTL + 30,))
    original_expiry = app.store.place_fetched_at(PLACE.place_id) + PLACE_TTL
    await app.service.resolver.websites.discover(PLACE, "local")
    assert app.service.resolver.websites.cached(PLACE)["expires_at"] <= original_expiry


async def test_discovery_waiting_for_same_place_lock_has_own_timeout(build_site, monkeypatch):
    import dine_mcp.widgets as widgets

    app, calls, _ = build_site()
    app.store.cache_place(PLACE.place_id, PLACE.to_cache())
    discovery = app.service.resolver.websites
    lock = asyncio.Lock()
    await lock.acquire()
    discovery._locks[PLACE.place_id] = lock
    real_timeout = asyncio.timeout
    monkeypatch.setattr(widgets.asyncio, "timeout", lambda _: real_timeout(0.01))
    try:
        await asyncio.wait_for(discovery.discover(PLACE, "local"), timeout=0.2)
    finally:
        lock.release()
    assert not calls
