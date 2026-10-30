"""Scheduling, monitoring, metadata and date-boundary regressions; no external traffic."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, time

import pytest
from conftest import FakeProvider
from fastmcp import Client

from dine_mcp.auth import AuthConfig, person_for
from dine_mcp.availability import Query, filter_slots
from dine_mcp.models import Slot, SlotKind
from dine_mcp.server import create_server
from dine_mcp.transport import FairQueue


async def test_round_robin_and_cancelled_waiters_release_the_queue():
    queue = FairQueue()
    entered, release = asyncio.Event(), asyncio.Event()
    order = []

    async def request(person, first=False):
        async with queue.turn(person):
            order.append(person)
            if first:
                entered.set()
                await release.wait()

    active = asyncio.create_task(request("a", True))
    await entered.wait()
    jobs = [asyncio.create_task(request(p)) for p in ["a", "a", "a", "b", "b", "c"]]
    cancelled = asyncio.create_task(request("cancelled"))
    await asyncio.sleep(0)
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    release.set()
    await asyncio.gather(active, *jobs)
    assert order == ["a", "a", "b", "c", "a", "b", "a"]
    assert queue.active is None and not queue.queues


async def test_canaries_are_explicit_cooled_down_and_restart_safe(make_app):
    providers = {key: FakeProvider(key) for key in ["sevenrooms", "tablecheck", "resdiary"]}
    for provider in providers.values():
        provider.venues[provider.definition.canary["ref"]] = {}
    app = make_app(providers=providers)
    assert all(not provider.calls for provider in providers.values())
    app.service.status(app.transports)
    assert all(not provider.calls for provider in providers.values())
    results = await asyncio.gather(*(app.service.checked_status(app.transports, "monitor") for _ in range(2)))
    assert all(item["check"]["status"] == "ok" for item in results[0]["providers"] if item["key"] in providers)
    assert all("check" not in item for item in results[0]["providers"] if item["key"] not in providers)
    assert all(len(provider.calls) == 2 for provider in providers.values())
    restarted = make_app(providers=providers)
    await restarted.service.checked_status(restarted.transports, "monitor")
    assert all(len(provider.calls) == 2 for provider in providers.values())


async def test_gateway_first_contact_and_missing_identity(make_app):
    app = make_app()
    config = AuthConfig.from_env({"MCP_BEARER_TOKEN": "x" * 40, "DINE_PERSON_HEADER": "X-Person"})
    assert not app.store.has_person("new-person")
    assert person_for(config, "gateway", {"x-person": "new-person"}, app.store) == "new-person"
    assert app.store.has_person("new-person")
    from dine_mcp.errors import DineError

    for client, headers in [(None, {"x-person": "forged"}), ("gateway", {}), ("gateway", {"x-person": "../bad"})]:
        with pytest.raises(DineError):
            person_for(config, client, headers, app.store)
    assert not app.store.has_person("forged")


async def test_completion_log_joins_person_outcome_and_trace_without_arguments(make_app, caplog):
    app = make_app()
    caplog.set_level(logging.INFO, logger="mcp")
    trace = "00-" + "1" * 32 + "-" + "2" * 16 + "-01"
    async with Client(create_server(app, stdio=True)) as client:
        await client.call_tool("dine_status", {}, meta={"traceparent": trace})
        await client.call_tool("dine_get_venue", {"venue_id": "v_private-input"}, raise_on_error=False)
    ends = [r for r in caplog.records if getattr(r, "state", None) == "end"]
    assert len(ends) == 2
    assert [r.outcome for r in ends] == ["ok", "unknown_venue_id"]
    assert all(r.person and r.person != "local" and r.duration_ms >= 0 and r.req_id for r in ends)
    assert "v_private-input" not in json.dumps([r.__dict__ for r in ends], default=str)


@pytest.mark.parametrize("offset", ["+14:00", "-12:00", "-04:00", "-05:00"])
def test_overnight_and_dst_offsets_keep_the_venue_local_service_date(offset):
    query = Query(date(2026, 11, 1), date(2026, 11, 1), time(23), time(2), 2, frozenset({SlotKind.BOOKABLE}))
    stamps = [f"2026-11-01T01:30{offset}", f"2026-11-01T23:30{offset}", f"2026-11-02T01:30{offset}"]
    assert [s.start for s in filter_slots([Slot(s, 2) for s in stamps], query)] == stamps[1:]


async def test_public_backend_connects_to_the_vetted_ip_with_original_tls_hostname():

    from dine_mcp.transport import PublicNetworkBackend, provider_client

    events = []

    class Stream:
        async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
            events.append(("tls", server_hostname))
            return self

        async def write(self, buffer, timeout=None):
            pass

        async def read(self, max_bytes, timeout=None):
            return b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}"

        async def aclose(self):
            pass

        def get_extra_info(self, info):
            return None

    class Backend:
        async def connect_tcp(self, host, port, **kwargs):
            events.append(("tcp", host))
            return Stream()

    async def resolve(host, port, **kwargs):
        events.append(("dns", host))
        return [(2, 1, 0, "", ("8.8.8.8", port))]

    async with provider_client() as client:
        assert isinstance(client._transport._pool._network_backend, PublicNetworkBackend)
        client._transport._pool._network_backend = PublicNetworkBackend(Backend(), resolve)
        assert (await client.get("https://www.example.com/")).status_code == 200
    assert events == [("dns", "www.example.com"), ("tcp", "8.8.8.8"), ("tls", "www.example.com")]
