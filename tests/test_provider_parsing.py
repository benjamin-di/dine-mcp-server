"""Malformed guest calendars must not invent inventory or complete negative coverage."""

from datetime import date, time
from zoneinfo import ZoneInfo

import httpx
import pytest
from conftest import soon
from test_hardening import build

from dine_mcp.availability import opens_at
from dine_mcp.models import ProviderVenue, ProviderVenueRef
from dine_mcp.providers.base import OBSERVER, Window


def calendar(key, day, clocks):
    if key == "sevenrooms":
        return {
            "data": {
                "availability": {
                    day: [
                        {
                            "times": [
                                {"time": clock, "type": "book", "access_persistent_id": "synthetic"} for clock in clocks
                            ]
                        }
                    ]
                }
            }
        }
    if key == "resdiary":
        return [{"Date": day, "AvailableTimes": [{"TimeSlot": clock} for clock in clocks]}]
    return {
        "availability_calendar": {
            "type": "success",
            "data": {day: {f"{day}T{clock}:00": {"is_available": True} for clock in clocks}},
        }
    }


@pytest.mark.parametrize("key", ["sevenrooms", "resdiary", "tablecheck"])
@pytest.mark.parametrize("day", ["2027-03-28", "2027-10-31"])
async def test_dst_gap_or_fold_keeps_positive_slots_but_not_complete_coverage(tmp_path, key, day):
    app, _ = build(tmp_path, lambda request: httpx.Response(200, json=calendar(key, day, ["02:30", "19:00"])), key)
    venue = ProviderVenue(
        ProviderVenueRef(key, "synthetic"), "Synthetic", time_zone="Europe/Paris", extra={"shop_id": "0" * 24}
    )
    parts = []
    token = OBSERVER.set(parts.append)
    try:
        observation = await app.service.providers[key].availability(
            venue, Window((date.fromisoformat(day),), time(0), time(3), 2), "local"
        )
    finally:
        OBSERVER.reset(token)
    assert [slot.start[11:16] for slot in observation.slots] == ["19:00"]
    assert observation.dates_checked == []
    assert any(not part.complete and part.slots for part in parts)


@pytest.mark.parametrize(
    "shifts",
    [
        {},
        [{}],
        [{"times": {}}],
        [{"is_closed": "false", "times": []}],
        [{"times": [{"time": "garbage", "type": "book", "access_persistent_id": "x"}]}],
    ],
)
async def test_malformed_sevenrooms_day_remains_unknown_and_is_not_negative_cached(tmp_path, shifts):
    day = soon(2).isoformat()
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"data": {"availability": {day: shifts}}})

    app, venue = build(tmp_path, handler)
    for _ in range(2):
        result = (await app.service.check_availability("local", [venue], day))["results"][0]
        assert result["status"] == "unavailable"
        assert result["coverage"] == {"dates_checked": [], "dates_not_checked": [day]}
    assert len(calls) == 2


@pytest.mark.parametrize("times", [None, {}, [{"TimeSlot": "25:00"}]])
async def test_malformed_resdiary_day_does_not_become_no_availability(tmp_path, times):
    day = soon(2).isoformat()
    app, venue = build(
        tmp_path, lambda request: httpx.Response(200, json=[{"Date": day, "AvailableTimes": times}]), "resdiary"
    )
    result = (await app.service.check_availability("local", [venue], day))["results"][0]
    assert result["status"] == "unavailable"
    assert result["coverage"] == {"dates_checked": [], "dates_not_checked": [day]}


@pytest.mark.parametrize("state", [{"is_available": "false"}, {}, None])
async def test_tablecheck_unknown_availability_is_not_inventory_or_negative_coverage(tmp_path, state):
    day = soon(2).isoformat()
    body = {"availability_calendar": {"type": "success", "data": {day: {f"{day}T19:00:00+08:00": state}}}}
    app, venue = build(tmp_path, lambda request: httpx.Response(200, json=body), "tablecheck")
    result = (await app.service.check_availability("local", [venue], day))["results"][0]
    assert result["status"] == "unavailable" and result["slots"] == []
    assert result["coverage"] == {"dates_checked": [], "dates_not_checked": [day]}


@pytest.mark.parametrize("key", ["sevenrooms", "resdiary", "tablecheck"])
async def test_malformed_time_retains_valid_positive_without_complete_day(tmp_path, key):
    day = soon(2).isoformat()
    app, venue = build(tmp_path, lambda request: httpx.Response(200, json=calendar(key, day, ["25:00", "19:00"])), key)
    result = (await app.service.check_availability("local", [venue], day))["results"][0]
    assert result["status"] == "ok"
    assert [slot["start"][11:16] for slot in result["slots"]] == ["19:00"]
    assert result["coverage"] == {"dates_checked": [], "dates_not_checked": [day]}


@pytest.mark.parametrize("release", ["25:00", "12:60", "bad"])
def test_invalid_release_time_stays_unknown(release):
    assert opens_at(date(2027, 1, 2), 1, release, ZoneInfo("Europe/Paris")) is None


@pytest.mark.parametrize("day", [date(2027, 3, 28), date(2027, 10, 31)])
def test_ambiguous_or_nonexistent_release_time_stays_unknown(day):
    assert opens_at(day, 0, "02:30", ZoneInfo("Europe/Paris")) is None


@pytest.mark.parametrize("key", ["sevenrooms", "resdiary"])
@pytest.mark.parametrize("clock", ["19:00garbage", "19:00Z", "19:00:30"])
async def test_clock_only_contract_does_not_truncate_malformed_times(tmp_path, key, clock):
    day = soon(2).isoformat()
    app, venue = build(tmp_path, lambda request: httpx.Response(200, json=calendar(key, day, [clock])), key)
    result = (await app.service.check_availability("local", [venue], day))["results"][0]
    assert result["status"] == "unavailable" and not result["slots"]
    assert result["coverage"] == {"dates_checked": [], "dates_not_checked": [day]}


@pytest.mark.parametrize("key", ["sevenrooms", "resdiary", "tablecheck"])
async def test_explicit_empty_day_remains_a_complete_negative(tmp_path, key):
    day = soon(2).isoformat()
    body = calendar(key, day, [])
    app, venue = build(tmp_path, lambda request: httpx.Response(200, json=body), key)
    result = (await app.service.check_availability("local", [venue], day))["results"][0]
    assert result["status"] == "none_available"
    assert result["coverage"] == {"dates_checked": [day], "dates_not_checked": []}


def test_tablecheck_explicit_offset_disambiguates_both_fold_occurrences():
    from dine_mcp.providers.tablecheck import TableCheck

    slots = {}
    for offset in ("+02:00", "+01:00"):
        assert TableCheck._add(slots, f"2027-10-31T02:30:00{offset}", ZoneInfo("Europe/Paris"), 2)
    assert list(slots) == ["2027-10-31T02:30+02:00", "2027-10-31T02:30+01:00"]


async def test_tablecheck_slot_cannot_escape_its_calendar_day(tmp_path):
    day, other = soon(2).isoformat(), soon(3).isoformat()
    body = {
        "availability_calendar": {"type": "success", "data": {day: {f"{other}T19:00:00+08:00": {"is_available": True}}}}
    }
    app, venue = build(tmp_path, lambda request: httpx.Response(200, json=body), "tablecheck")
    result = (await app.service.check_availability("local", [venue], day, other))["results"][0]
    assert result["status"] == "unavailable" and not result["slots"]
    assert result["coverage"] == {"dates_checked": [], "dates_not_checked": [day, other]}


@pytest.mark.parametrize("clock", ["1900", "19:00:30", "19:00:00.5", "19:00Z"])
def test_python_query_rejects_times_that_would_be_changed_in_provider_requests(make_app, clock):
    from dine_mcp.errors import DineError

    app = make_app()
    with pytest.raises(DineError) as error:
        app.service.parse_query(soon(2).isoformat(), None, clock, "22:00", 2, ["bookable"])
    assert error.value.code == "invalid_argument"


async def test_malformed_tablecheck_alternates_retain_known_positive_without_claiming_coverage(tmp_path):
    day = soon(2).isoformat()
    body = calendar("tablecheck", day, ["19:00"])
    body["availability_calendar"]["alternate_timeslots"] = ["unexpected"]
    app, venue = build(tmp_path, lambda request: httpx.Response(200, json=body), "tablecheck")
    result = (await app.service.check_availability("local", [venue], day))["results"][0]
    assert result["status"] == "ok" and len(result["slots"]) == 1
    assert result["coverage"] == {"dates_checked": [], "dates_not_checked": [day]}
