"""Synthetic reproductions from the post-release implementation audit."""

import asyncio
from datetime import datetime

import httpx
import pytest
from conftest import FakeProvider, soon
from test_hardening import build

from dine_mcp.availability import Checker, Task
from dine_mcp.models import ProviderVenueRef, Slot, VenueStatus
from dine_mcp.providers.base import Observation, publish
from dine_mcp.transport import ProviderError


@pytest.mark.parametrize("failure", [False, True])
async def test_partial_provider_caveat_survives_assembly_and_pushback(make_app, monkeypatch, failure):
    day = soon(2).isoformat()
    provider = FakeProvider("sevenrooms", venues={"synthetic": {}})
    app = make_app(None, {"sevenrooms": provider})
    venue = app.store.save_venue({"channels": [{"kind": "sevenrooms", "ref": "synthetic"}]}, ["sevenrooms:synthetic"])

    async def partial(*args):
        part = Observation(
            VenueStatus.NONE_AVAILABLE,
            [Slot(f"{day}T19:00+08:00", 2)],
            [day],
            "Synthetic partial experience search; omitted experiences remain unknown.",
            complete=False,
        )
        publish(part)
        if failure:
            raise ProviderError(VenueStatus.RATE_LIMITED)
        return part

    monkeypatch.setattr(provider, "availability", partial)
    result = (await app.service.check_availability("local", [venue], day))["results"][0]
    assert result["status"] == ("rate_limited" if failure else "ok")
    assert len(result["slots"]) == 1
    assert result["coverage"] == {"dates_checked": [], "dates_not_checked": [day]}
    assert "Synthetic partial experience search" in result["note"]
    if failure:
        assert "slow down" in result["note"]
    else:
        assert "missing coverage" in result["note"]


async def test_empty_partial_search_retains_its_caveat_without_inventing_an_observation(make_app, monkeypatch):
    provider = FakeProvider("sevenrooms", venues={"synthetic": {}})
    app = make_app(None, {"sevenrooms": provider})
    venue = app.store.save_venue({"channels": [{"kind": "sevenrooms", "ref": "synthetic"}]}, ["sevenrooms:synthetic"])

    async def partial(*args):
        return Observation(VenueStatus.NONE_AVAILABLE, [], [], "Synthetic incomplete search.", complete=False)

    monkeypatch.setattr(provider, "availability", partial)
    result = (await app.service.check_availability("local", [venue], soon(2).isoformat()))["results"][0]
    assert result["status"] == "unavailable" and result["observed_at"] is None
    assert not result["coverage"]["dates_checked"]
    assert "Synthetic incomplete search" in result["note"] and "unknown" in result["note"]


@pytest.mark.parametrize("cancel_running", [False, True])
async def test_checker_deadline_retains_cached_observations_before_metadata(make_app, monkeypatch, cancel_running):
    day = soon(2).isoformat()
    provider = FakeProvider("sevenrooms", venues={"synthetic": {day: ["19:00"]}})
    app = make_app(None, {"sevenrooms": provider})
    venue = app.store.save_venue({"channels": [{"kind": "sevenrooms", "ref": "synthetic"}]}, ["sevenrooms:synthetic"])
    await app.service.check_availability("local", [venue], day)
    app.service.venue_info._items.clear()
    entered = asyncio.Event()

    async def blocked_metadata(*args):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(provider, "get_venue", blocked_metadata)
    task = Task(
        venue,
        "Synthetic",
        provider,
        app.service.effective["sevenrooms"],
        ProviderVenueRef("sevenrooms", "synthetic"),
        "Asia/Singapore",
        None,
    )
    query = app.service.parse_query(day, soon(3).isoformat(), "17:00", "22:30", 2, ["bookable"])
    checker = Checker(app.service.planner, "local", query, deadline=30 if cancel_running else 0)
    if cancel_running:
        job = asyncio.create_task(checker.run([task]))
        await asyncio.wait_for(entered.wait(), timeout=5)
        job.cancel()
        with pytest.raises(asyncio.CancelledError):
            await job
    else:
        await checker.run([task])
    assert task.result is not None
    result = task.result.to_dict()
    assert result["status"] == "skipped_deadline"
    assert result["served_from"] == "cache" and len(result["slots"]) == 1
    assert result["coverage"] == {"dates_checked": [day], "dates_not_checked": [soon(3).isoformat()]}


async def test_planning_timeout_retains_already_planned_venue_cache(make_app, monkeypatch):
    day = soon(2).isoformat()
    provider = FakeProvider("sevenrooms", venues={"synthetic": {day: ["19:00"]}})
    app = make_app(None, {"sevenrooms": provider})
    venue = app.store.save_venue({"channels": [{"kind": "sevenrooms", "ref": "synthetic"}]}, ["sevenrooms:synthetic"])
    second = app.store.save_venue({"google_place_id": "ChIJ_synthetic_planning_timeout"}, [])
    await app.service.check_availability("local", [venue], day)

    async def expired_place(*args):
        raise TimeoutError

    monkeypatch.setattr(app.service.resolver, "place", expired_place)
    result = await app.service.check_availability("local", [venue, second], day)
    first, unfinished = result["results"]
    assert first["status"] == unfinished["status"] == "skipped_deadline"
    assert first["served_from"] == "cache" and len(first["slots"]) == 1
    assert first["coverage"] == {"dates_checked": [day], "dates_not_checked": []}
    assert unfinished["coverage"] == {"dates_checked": [], "dates_not_checked": [day]}


@pytest.mark.parametrize("offset", ["", "+08:00", "Z"])
@pytest.mark.parametrize("seconds", ["30", "00.500000"])
async def test_tablecheck_does_not_round_a_slot_into_an_exact_minute(tmp_path, offset, seconds):
    day = soon(2).isoformat()
    hour = "11" if offset == "Z" else "19"
    stamp = f"{day}T{hour}:00:{seconds}{offset}"
    body = {
        "availability_calendar": {
            "type": "success",
            "data": {day: {stamp: {"is_available": True}}},
        }
    }
    app, venue = build(tmp_path, lambda request: httpx.Response(200, json=body), "tablecheck")
    exact = (
        await app.service.check_availability(
            "local",
            [venue],
            day,
            time_from="19:00",
            time_to="19:00",
        )
    )["results"][0]
    assert exact["slots"] == []
    wider = (
        await app.service.check_availability(
            "local",
            [venue],
            day,
            time_from="19:00",
            time_to="19:01",
        )
    )["results"][0]
    assert len(wider["slots"]) == 1
    at = datetime.fromisoformat(wider["slots"][0]["start"])
    assert (at.second, at.microsecond) == ((30, 0) if seconds == "30" else (0, 500000))
