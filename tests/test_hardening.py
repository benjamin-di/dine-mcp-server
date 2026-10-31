"""Regression cases from the implementation audit; all network traffic is synthetic."""

from __future__ import annotations

import asyncio
import gzip
import json
import socket
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
from conftest import FakePlaces, soon
from fastmcp import Client

from dine_mcp.auth import AuthConfig
from dine_mcp.composition import build_app
from dine_mcp.controls import call_scope
from dine_mcp.errors import DineError
from dine_mcp.models import ProviderVenue, ProviderVenueRef, VenueStatus
from dine_mcp.places import Place
from dine_mcp.server import create_server
from dine_mcp.transport import ProviderError, PublicNetworkBackend, public_address, retry_after


async def no_sleep(_):
    pass


def build(tmp_path, handler, key="sevenrooms", env=None, places=None):
    app = build_app(
        {"DINE_STATE_DIR": str(tmp_path), **(env or {})},
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        place_source=places,
    )
    for transport in app.transports.values():
        transport._sleep = no_sleep
    ref = "example-sushi" if key == "tablecheck" else "examplegrill"
    venue_id = app.store.save_venue(
        {"name": "Example Venue", "time_zone": "Asia/Singapore", "channels": [{"kind": key, "ref": ref}]},
        [f"{key}:{ref}"],
    )
    app.service.venue_info.put(
        ProviderVenue(
            ProviderVenueRef(key, ref),
            "Example Venue",
            time_zone="Asia/Singapore",
            extra={"shop_id": "0123456789abcdef01234567"},
        )
    )
    return app, venue_id


def available(day):
    return {
        "status": 200,
        "data": {
            "availability": {day: [{"times": [{"time": "19:00", "type": "book", "access_persistent_id": "synthetic"}]}]}
        },
    }


async def test_tablecheck_coverage_separates_changed_overlapping_and_overnight_windows(tmp_path):
    calls = []

    def handler(request):
        at = datetime.fromisoformat(json.loads(request.content)["start_at"].replace("Z", "+00:00")).astimezone(
            ZoneInfo("Asia/Singapore")
        )
        calls.append(at)
        slot = at - timedelta(hours=1)
        return httpx.Response(
            200,
            json={
                "availability_calendar": {
                    "type": "success",
                    "time_zone": "Asia/Singapore",
                    "data": {slot.date().isoformat(): {slot.isoformat(): {"is_available": True}}},
                }
            },
        )

    app, venue = build(tmp_path, handler, "tablecheck")
    day = soon(2).isoformat()
    for start, end, expected in [
        ("18:00", "20:00", "19:00"),
        ("12:00", "14:00", "13:00"),
        ("13:00", "15:00", "14:00"),
        ("23:00", "01:00", "00:00"),
    ]:
        result = (await app.service.check_availability("local", [venue], day, time_from=start, time_to=end))["results"][
            0
        ]
        assert result["status"] == "ok" and result["slots"][0]["start"][11:16] == expected
    assert len(calls) == 4
    assert calls[-1].date() == soon(3)
    await app.service.check_availability("local", [venue], day, time_from="12:00", time_to="14:00")
    assert len(calls) == 4


async def test_omitted_date_is_unavailable_not_negative_observation(tmp_path):
    app, venue = build(tmp_path, lambda request: httpx.Response(200, json={"data": {"availability": {}}}))
    result = (await app.service.check_availability("local", [venue], soon(2).isoformat()))["results"][0]
    assert result["status"] == "unavailable"
    assert not result["coverage"]["dates_checked"] and result["observed_at"] is None


@pytest.mark.parametrize("completed_days", [0, 3])
async def test_sevenrooms_rejected_range_keeps_only_completed_coverage(tmp_path, completed_days):
    fixtures = Path(__file__).parent / "fixtures" / "sevenrooms"
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path == "/api-yoa/dining/venue_info":
            return httpx.Response(200, json=json.loads((fixtures / "venue_info.json").read_text()))
        assert request.url.path == "/api-yoa/availability/ng/widget/range"
        if completed_days and len(calls) == 2:
            first = datetime.fromisoformat(request.url.params["start_date"]).date()
            data = {}
            for offset in range(completed_days):
                data.update(available((first + timedelta(days=offset)).isoformat())["data"]["availability"])
            return httpx.Response(200, json={"data": {"availability": data}})
        return httpx.Response(400, json=json.loads((fixtures / "range_error.json").read_text()))

    app = build_app(
        {"DINE_STATE_DIR": str(tmp_path)},
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        place_source=None,
    )
    app.transports["sevenrooms"]._sleep = no_sleep
    try:
        async with Client(create_server(app, stdio=True)) as client:
            found = await client.call_tool(
                "dine_find_venue", {"url": "https://www.sevenrooms.com/reservations/examplegrill"}
            )
            venue = found.structured_content["venues"][0]["venue_id"]
            response = await client.call_tool(
                "dine_check_availability",
                {"venue_ids": [venue], "date_from": soon(2).isoformat(), "date_to": soon(8).isoformat()},
            )
        result = response.structured_content["results"][0]
        assert result["status"] == "unavailable"
        assert result["coverage"]["dates_checked"] == [soon(n).isoformat() for n in range(2, 2 + completed_days)]
        assert result["coverage"]["dates_not_checked"] == [soon(n).isoformat() for n in range(2 + completed_days, 9)]
        assert len(result["slots"]) == len(app.service.cache) == completed_days
        assert len(calls) == (3 if completed_days else 2)
        assert "availability could not be verified" in response.content[0].text
        assert "no longer" not in response.content[0].text and "invalid num_days" not in response.content[0].text
        assert result["booking_link"]["url"].startswith("https://www.sevenrooms.com/explore/examplegrill/")
        if not completed_days:
            assert result["observed_at"] is None and result["served_from"] is None
        else:
            assert result["observed_at"] and result["served_from"] == "cache"
    finally:
        await app.aclose()


async def test_sevenrooms_valid_empty_result_remains_observed(tmp_path):
    day = soon(2).isoformat()
    app, venue = build(
        tmp_path,
        lambda request: httpx.Response(200, json={"data": {"availability": {day: [{"times": []}]}}}),
    )
    try:
        result = (await app.service.check_availability("local", [venue], day))["results"][0]
        assert result["status"] == "none_available" and not result["slots"]
        assert result["coverage"]["dates_checked"] == [day] and not result["coverage"]["dates_not_checked"]
        assert result["observed_at"] and result["served_from"] == "live"
        assert len(app.service.cache) == 1
    finally:
        await app.aclose()


@pytest.mark.parametrize("cancel", [False, True])
async def test_successful_batches_survive_later_pushback_or_deadline(tmp_path, cancel, monkeypatch):
    count = 0
    deadline = None
    timeout = asyncio.timeout

    def capture_timeout(delay):
        nonlocal deadline
        manager = timeout(delay)
        if deadline is None:
            deadline = manager
        return manager

    if cancel:
        monkeypatch.setattr(asyncio, "timeout", capture_timeout)

    async def handler(request):
        nonlocal count
        count += 1
        if count > 1:
            if cancel:
                # Expire the real service timeout only after the later dispatch
                # starts; host load must not decide whether it is reached.
                assert deadline is not None
                deadline.reschedule(asyncio.get_running_loop().time())
                await asyncio.Future()
            return httpx.Response(429)
        first = datetime.fromisoformat(request.url.params["start_date"]).date()
        data = {}
        for offset in range(3):
            data.update(available((first + timedelta(days=offset)).isoformat())["data"]["availability"])
        return httpx.Response(200, json={"data": {"availability": data}})

    app, venue = build(tmp_path, handler)
    result = (await app.service.check_availability("local", [venue], soon(2).isoformat(), soon(8).isoformat()))[
        "results"
    ][0]
    assert result["status"] == ("skipped_deadline" if cancel else "rate_limited")
    assert len(result["slots"]) == len(result["coverage"]["dates_checked"]) == 3
    assert len(app.service.cache) == 3
    assert count == 2


async def test_every_redirect_is_charged_and_actual_call_cap_stops_dispatch(tmp_path):
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        if count == 1:
            return httpx.Response(302, headers={"location": "/api-yoa/dining/venue_info?next=1"})
        return httpx.Response(200, json={})

    app, _ = build(tmp_path, handler)
    transport = app.transports["sevenrooms"]
    url = "https://www.sevenrooms.com/api-yoa/dining/venue_info"
    with call_scope(2) as call:
        call.limit = 1
        with pytest.raises(DineError, match="too_broad"):
            await transport.request("venue_info", url, "local")
    assert count == app.store.used("sevenrooms") == 1
    count = 0
    await transport.request("venue_info", url, "local")
    assert count == 2 and app.store.used("sevenrooms") == 3


async def test_waiting_caller_stops_after_breaker_and_retry_after_is_honoured(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return httpx.Response(429, headers={"retry-after": "3600"})

    app, _ = build(tmp_path, handler)
    transport = app.transports["sevenrooms"]
    args = ("venue_info", "https://www.sevenrooms.com/api-yoa/dining/venue_info", "local")
    first = asyncio.create_task(transport.request(*args))
    await entered.wait()
    second = asyncio.create_task(transport.request(*args))
    await asyncio.sleep(0)
    release.set()
    outcomes = await asyncio.gather(first, second, return_exceptions=True)
    assert all(isinstance(outcome, ProviderError) for outcome in outcomes)
    assert calls == 1 and app.store.breaker("sevenrooms").open_until - time.time() > 3500
    assert retry_after("Infinity") == 86400
    assert retry_after("invalid") == retry_after("-10") == 0
    assert retry_after("Thu, 01 Jan 1970 00:00:20 GMT", now=10) == 10


async def test_provider_body_is_stopped_during_streaming(tmp_path, monkeypatch):
    from dine_mcp import transport as module

    monkeypatch.setattr(module, "MAX_BODY", 100)
    reads = []

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            for n in range(10):
                reads.append(n)
                yield b"x" * 64

    app, _ = build(tmp_path, lambda request: httpx.Response(200, stream=Body()))
    with pytest.raises(ProviderError):
        await app.transports["sevenrooms"].request(
            "venue_info", "https://www.sevenrooms.com/api-yoa/dining/venue_info", "local"
        )
    assert reads == [0, 1]


@pytest.mark.parametrize("compressed", [False, True])
async def test_decoded_provider_response_is_not_decoded_twice(tmp_path, compressed):
    payload = b'{"data":{"venue_info":{"name":"Example Venue"}}}'
    headers = {"content-type": "application/json", "retry-after": "7"}
    body = payload
    if compressed:
        body = gzip.compress(payload)
        headers["content-encoding"] = "gzip"
    headers["content-length"] = str(len(body))

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield body[:9]
            yield body[9:]

    app, _ = build(tmp_path, lambda request: httpx.Response(200, headers=headers, stream=Body()))
    try:
        response = await app.transports["sevenrooms"].request(
            "venue_info", "https://www.sevenrooms.com/api-yoa/dining/venue_info", "local"
        )
        assert response.json()["data"]["venue_info"]["name"] == "Example Venue"
        assert response.content == payload
        assert response.headers["content-length"] == str(len(payload))
        assert response.headers["retry-after"] == "7"
        assert "content-encoding" not in response.headers
        assert app.transports["sevenrooms"].budget()["used_today"] == 1
    finally:
        await app.aclose()


async def test_compressed_provider_body_limit_counts_decoded_bytes(tmp_path):
    encoded = gzip.compress(b"x" * 2000)
    assert len(encoded) < 100

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield encoded

    app, _ = build(tmp_path, lambda request: httpx.Response(200, headers={"content-encoding": "gzip"}, stream=Body()))
    app.transports["sevenrooms"].max_body = 100
    try:
        with pytest.raises(ProviderError) as caught:
            await app.transports["sevenrooms"].request(
                "venue_info", "https://www.sevenrooms.com/api-yoa/dining/venue_info", "local"
            )
        assert caught.value.log_detail == "response too large"
    finally:
        await app.aclose()


@pytest.mark.parametrize(
    "status,extra,expected",
    [
        (429, {"retry-after": "3600"}, VenueStatus.RATE_LIMITED),
        (403, {"cf-mitigated": "challenge"}, VenueStatus.BLOCKED),
    ],
)
async def test_compressed_provider_pushback_still_opens_the_breaker(tmp_path, status, extra, expected):
    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield gzip.compress(b'{"error":"synthetic refusal"}')

    app, _ = build(
        tmp_path,
        lambda request: httpx.Response(status, headers={"content-encoding": "gzip", **extra}, stream=Body()),
    )
    try:
        with pytest.raises(ProviderError) as caught:
            await app.transports["sevenrooms"].request(
                "venue_info", "https://www.sevenrooms.com/api-yoa/dining/venue_info", "local"
            )
        assert caught.value.status is expected
        assert app.store.breaker("sevenrooms").is_open()
        if status == 429:
            assert app.store.breaker("sevenrooms").open_until - time.time() > 3500
    finally:
        await app.aclose()


async def test_dns_policy_refuses_private_mixed_and_mapped_addresses():
    class Backend:
        calls = []

        async def connect_tcp(self, host, port, **kwargs):
            self.calls.append(host)
            return "stream"

    backend = Backend()
    for ips in [["127.0.0.1"], ["8.8.8.8", "10.0.0.1"], ["::ffff:127.0.0.1"], ["169.254.169.254"]]:

        async def resolve(*args, ips=ips, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, 443)) for ip in ips]

        with pytest.raises(httpx.ConnectError):
            await PublicNetworkBackend(backend, resolve).connect_tcp("www.example.com", 443)
    assert not backend.calls
    assert public_address("8.8.8.8") and not public_address("224.0.0.1")


async def test_http_rejects_unlisted_authority_origin_and_oversized_body(tmp_path):
    token = "x" * 40
    app, _ = build(tmp_path, lambda request: httpx.Response(200))
    server = create_server(app, auth=AuthConfig.from_env({"MCP_BEARER_TOKEN": token}))
    http = server.http_app(stateless_http=True, json_response=True)
    async with (
        http.router.lifespan_context(http),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=http), base_url="http://127.0.0.1") as client,
    ):
        headers = {"authorization": f"Bearer {token}"}
        assert (
            await client.post("/mcp", content=b"{}", headers={**headers, "host": "unlisted.example"})
        ).status_code == 403
        assert (
            await client.post("/mcp", content=b"{}", headers={**headers, "origin": "https://unlisted.example"})
        ).status_code == 403

        async def large():
            yield b"x" * 600_000
            yield b"x" * 600_000

        assert (await client.post("/mcp", content=large(), headers=headers)).status_code == 413
        assert (await client.get("/health")).status_code == 200


async def test_disabled_get_refuses_before_place_lookup(tmp_path):
    overrides = tmp_path / "overrides.toml"
    overrides.write_text("disabled = true\n")
    places = FakePlaces([Place("synthetic-place", "Example")])
    app, venue = build(
        tmp_path / "state", lambda request: httpx.Response(500), env={"DINE_OVERRIDES": str(overrides)}, places=places
    )
    async with Client(create_server(app, stdio=True)) as client:
        result = await client.call_tool("dine_get_venue", {"venue_id": venue}, raise_on_error=False)
        assert result.is_error and "disabled" in result.content[0].text
    assert not places.calls


async def test_deadline_covers_place_lookup_and_joins_workers(tmp_path):
    class SlowPlaces(FakePlaces):
        async def get(self, place_id):
            await asyncio.sleep(10)

    app, _ = build(tmp_path, lambda request: httpx.Response(500), places=SlowPlaces())
    app.service.settings.deadline_seconds = 0.02
    with pytest.raises(DineError, match="deadline_exceeded"):
        await app.service.find_venue("local", google_place_id="synthetic-place")


async def test_expired_google_channels_and_cache_rows_are_removed(tmp_path):
    place = Place("synthetic-place", "Example", phone="+65 0000 0000", website="https://www.example.com")
    places = FakePlaces([place])
    app, _ = build(tmp_path, lambda request: httpx.Response(500), places=places)
    venue = (await app.service.find_venue("local", google_place_id=place.place_id))["venues"][0]["venue_id"]
    record = app.store.get_venue(venue)
    for channel in record["channels"]:
        channel["expires_at"] = 0
    app.store.save_venue(record, [])
    app.store._db.execute("UPDATE place_cache SET fetched_at=0")
    places.fail = True
    app.store.prune()
    result = await app.service.venue_view(venue, detailed=False)
    assert result["channels"] == []
    assert app.store._db.execute("SELECT count(*) FROM place_cache").fetchone()[0] == 0
    persisted = json.loads(app.store._db.execute("SELECT data FROM venues WHERE venue_id=?", (venue,)).fetchone()[0])
    assert persisted["channels"] == []


async def test_tablecheck_partial_anchor_never_becomes_complete_negative_coverage(tmp_path):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        # A wide query needs two anchors. Only the first returns this day.
        data = {soon(2).isoformat(): {}} if calls % 2 else {}
        return httpx.Response(200, json={"availability_calendar": {"type": "success", "data": data}})

    app, venue = build(tmp_path, handler, "tablecheck")
    for _ in range(2):
        result = (await app.service.check_availability("local", [venue], soon(2).isoformat()))["results"][0]
        assert result["status"] == "unavailable" and not result["coverage"]["dates_checked"]
    assert calls == 4


async def test_changed_tablecheck_window_does_not_reuse_stale_dinner_for_lunch(tmp_path):
    fail = False

    def handler(request):
        if fail:
            return httpx.Response(429)
        at = datetime.fromisoformat(json.loads(request.content)["start_at"].replace("Z", "+00:00"))
        slot = (at - timedelta(hours=1)).astimezone(ZoneInfo("Asia/Singapore"))
        return httpx.Response(
            200,
            json={
                "availability_calendar": {
                    "type": "success",
                    "data": {slot.date().isoformat(): {slot.isoformat(): {"is_available": True}}},
                }
            },
        )

    app, venue = build(tmp_path, handler, "tablecheck")
    await app.service.check_availability("local", [venue], soon(2).isoformat(), time_from="18:00", time_to="20:00")
    fail = True
    lunch = (
        await app.service.check_availability("local", [venue], soon(2).isoformat(), time_from="12:00", time_to="14:00")
    )["results"][0]
    assert lunch["status"] == "rate_limited" and lunch["slots"] == [] and not lunch["coverage"]["dates_checked"]
