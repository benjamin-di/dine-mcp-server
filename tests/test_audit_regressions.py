"""Offline regressions for planning, provenance and adapter failure boundaries."""

from __future__ import annotations

import time
from zoneinfo import ZoneInfo

import httpx
import pytest
from conftest import FakePlaces, FakeProvider, soon
from test_hardening import build

from dine_mcp.availability import VenueInfoCache
from dine_mcp.errors import DineError
from dine_mcp.links import clean
from dine_mcp.models import BookingRules, ProviderVenue, ProviderVenueRef, VenueStatus
from dine_mcp.places import Place
from dine_mcp.providers.base import Observation
from dine_mcp.providers.tablecheck import TableCheck

PLACE_ID = "ChIJ_synthetic_audit_00000"
URL = "https://www.sevenrooms.com/reservations/synthetic"


def place(**kwargs):
    return Place(PLACE_ID, "Synthetic", reservation_links=((URL, "SevenRooms"),), **kwargs)


async def resolve(app):
    return (await app.service.find_venue("local", google_place_id=PLACE_ID))["venues"][0]["venue_id"]


async def test_empty_tablecheck_window_preserves_release_warning_live_and_cached(tmp_path):
    day = soon(2).isoformat()
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "availability_calendar": {
                    "type": "success",
                    "data": {day: {}},
                    "time_zone": "Asia/Singapore",
                }
            },
        )

    app, venue = build(tmp_path, handler, "tablecheck")
    for source in ("live", "cache"):
        result = (
            await app.service.check_availability(
                "local",
                [venue],
                day,
                time_from="19:00",
                time_to="19:00",
            )
        )["results"][0]
        assert result["status"] == "none_available"
        assert "unreleased" in (result["note"] or "")
        assert result["served_from"] == source
    assert len(calls) == 1


async def test_equal_time_tablecheck_uses_one_request(tmp_path):
    day = soon(2).isoformat()
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "availability_calendar": {
                    "type": "success",
                    "data": {day: {f"{day}T19:00:00+08:00": {"is_available": True}}},
                }
            },
        )

    app, venue = build(tmp_path, handler, "tablecheck")
    result = (
        await app.service.check_availability(
            "local",
            [venue],
            day,
            time_from="19:00",
            time_to="19:00",
        )
    )["results"][0]
    assert result["status"] == "ok" and len(result["slots"]) == 1
    assert len(calls) == 1


@pytest.mark.parametrize(
    "rules,party,status",
    [
        (BookingRules(max_party=2), 9, "party_too_large"),
        (BookingRules(window_days=1), 2, "not_yet_open"),
    ],
)
async def test_cached_rules_prevent_false_too_broad(make_app, monkeypatch, rules, party, status):
    provider = FakeProvider("sevenrooms", venues={"synthetic": {}}, rules=rules)
    monkeypatch.setattr(provider, "cost", lambda *args: 25)
    app = make_app(FakePlaces([place()]), {"sevenrooms": provider})
    venue = await resolve(app)
    info = await provider.get_venue(ProviderVenueRef("sevenrooms", "synthetic"), "local")
    app.service.venue_info.put(info)
    provider.calls.clear()
    result = (await app.service.check_availability("local", [venue], soon(3).isoformat(), party_size=party))["results"][
        0
    ]
    assert result["status"] == status
    assert not provider.calls


@pytest.mark.parametrize("expired", [False, True])
async def test_verified_provider_channel_survives_place_refresh_or_expiry(make_app, expired):
    provider = FakeProvider("sevenrooms", venues={"synthetic": {soon(2).isoformat(): ["19:00"]}})
    source = FakePlaces([place()])
    app = make_app(source, {"sevenrooms": provider})
    venue = await resolve(app)
    if expired:
        with app.store._tx() as db:
            db.execute("UPDATE place_cache SET fetched_at=0")
        record = app.store.get_venue(venue)
        for channel in record["channels"]:
            channel["expires_at"] = 0
        app.store.save_venue(record, [])
        source.fail = True
    found = (await app.service.find_venue("local", url=URL))["venues"][0]
    assert found["venue_id"] == venue
    assert any(c["source"] == "provider" and c["ref"] == "synthetic" for c in found["channels"])
    result = (await app.service.check_availability("local", [venue], soon(2).isoformat()))["results"][0]
    assert result["status"] == "ok"


def test_venue_metadata_cache_expires_and_bounds_unique_entries(monkeypatch):
    import dine_mcp.availability as availability

    monkeypatch.setattr(availability, "CACHE_LIMIT", 10)
    now = [0.0]
    monkeypatch.setattr(availability.clock, "monotonic", lambda: now[0])
    cache = VenueInfoCache()
    for i in range(30):
        cache.put(ProviderVenue(ProviderVenueRef("sevenrooms", f"synthetic-{i}"), "Synthetic"))
    assert len(cache._items) <= 10
    assert cache.get(ProviderVenueRef("sevenrooms", "synthetic-29")) is not None
    now[0] = availability.VENUE_INFO_TTL + 1
    assert cache.get(ProviderVenueRef("sevenrooms", "synthetic-29")) is None
    cache.put(ProviderVenue(ProviderVenueRef("sevenrooms", "fresh"), "Synthetic"))
    assert len(cache._items) == 1


async def test_metadata_policy_error_is_not_hidden_as_adapter_failure(make_app, monkeypatch):
    provider = FakeProvider("sevenrooms", venues={"synthetic": {}})

    async def capped(*args):
        raise DineError("too_broad")

    monkeypatch.setattr(provider, "get_venue", capped)
    app = make_app(FakePlaces([place()]), {"sevenrooms": provider})
    venue = await resolve(app)
    with pytest.raises(DineError, match="too_broad"):
        await app.service.check_availability("local", [venue], soon(2).isoformat())


async def test_booking_link_adapter_failure_is_isolated_to_venue(make_app, monkeypatch):
    provider = FakeProvider("sevenrooms", venues={"broken": {}, "healthy": {soon(2).isoformat(): ["19:00"]}})
    original = provider.booking_link

    def broken_link(info, *args):
        if info.ref.ref == "broken":
            raise ValueError("synthetic adapter failure")
        return original(info, *args)

    monkeypatch.setattr(provider, "booking_link", broken_link)
    app = make_app(None, {"sevenrooms": provider})
    ids = [
        app.store.save_venue({"channels": [{"kind": "sevenrooms", "ref": ref}]}, [f"sevenrooms:{ref}"])
        for ref in ("broken", "healthy")
    ]
    result = await app.service.check_availability("local", ids, soon(2).isoformat())
    assert [r["status"] for r in result["results"]] == ["unavailable", "ok"]


@pytest.mark.parametrize(
    "authority", ["@www.sevenrooms.com", "www.sevenrooms.com:0", "www.sevenrooms.com:", "www.seven\trooms.com"]
)
def test_booking_url_rejects_ambiguous_authority(authority):
    assert clean(f"https://{authority}/reservations/synthetic") is None


@pytest.mark.parametrize("hours", [["invalid"], {"holidays": ["invalid"]}, {"periods": [None]}])
async def test_malformed_hours_remain_unknown_and_provider_is_checked(make_app, hours):
    provider = FakeProvider("sevenrooms", venues={"synthetic": {soon(2).isoformat(): ["19:00"]}})
    app = make_app(FakePlaces([place(opening_hours=hours, time_zone="Asia/Singapore")]), {"sevenrooms": provider})
    venue = await resolve(app)
    result = (await app.service.check_availability("local", [venue], soon(2).isoformat()))["results"][0]
    assert result["status"] == "ok"


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="machine-zone regression uses POSIX tzset")
def test_tablecheck_naive_times_use_venue_zone_not_machine_zone(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    try:
        slots = {}
        tz = ZoneInfo("Asia/Tokyo")
        TableCheck._add(slots, "2027-01-02T19:00:00", tz, 2)
        TableCheck._add(slots, "2027-01-02T10:00:00Z", tz, 2)
        assert list(slots) == ["2027-01-02T19:00+09:00"]
    finally:
        monkeypatch.undo()
        time.tzset()


async def test_search_does_not_replace_fresh_place_details_with_sparse_row(make_app):
    rich = place(address="Synthetic address", time_zone="Asia/Tokyo", opening_hours={"periods": []})
    source = FakePlaces([place()])
    app = make_app(source)
    app.store.cache_place(PLACE_ID, rich.to_cache())
    before = app.store.place_fetched_at(PLACE_ID)
    result = await app.service.find_venue("local", name="Synthetic")
    assert result["venues"][0]["address"] == "Synthetic address"
    assert app.store.cached_place(PLACE_ID)["time_zone"] == "Asia/Tokyo"
    assert app.store.place_fetched_at(PLACE_ID) == before


async def test_search_fetches_detail_even_when_search_row_has_booking_link(make_app):
    rich = place(address="Synthetic address", time_zone="Asia/Tokyo")
    source = FakePlaces([rich])

    async def sparse(*args, **kwargs):
        return [place()]

    source.search = sparse
    app = make_app(source)
    result = await app.service.find_venue("local", name="Synthetic")
    assert result["venues"][0]["address"] == rich.address
    assert source.calls == [f"get:{PLACE_ID}"]


async def test_explicit_empty_kinds_are_invalid(make_app):
    app = make_app(FakePlaces([place()]))
    venue = await resolve(app)
    with pytest.raises(DineError) as error:
        await app.service.check_availability("local", [venue], soon(2).isoformat(), kinds=[])
    assert error.value.code == "invalid_argument"


async def test_coverage_warning_and_provider_note_both_survive(make_app, monkeypatch):
    provider = FakeProvider("sevenrooms", venues={"synthetic": {}})
    day = soon(2).isoformat()

    async def partial(*args):
        return Observation(VenueStatus.NONE_AVAILABLE, [], [day], note="Synthetic provider caveat")

    monkeypatch.setattr(provider, "availability", partial)
    app = make_app(FakePlaces([place()]), {"sevenrooms": provider})
    venue = await resolve(app)
    result = (await app.service.check_availability("local", [venue], day, soon(3).isoformat()))["results"][0]
    assert "unknown" in result["note"] and "Synthetic provider caveat" in result["note"]


def test_standalone_users_reject_shared_token_identity(tmp_path):
    from dine_mcp.auth import AuthConfig, AuthError, digest

    path = tmp_path / "users.toml"
    hashed = digest("synthetic-token" * 4)
    path.write_text(f'[users.first]\ntoken_sha256 = "{hashed}"\n[users.second]\ntoken_sha256 = "{hashed}"\n')
    with pytest.raises(AuthError):
        AuthConfig.from_env({"DINE_USERS_FILE": str(path)})


def test_concurrent_standalone_user_additions_preserve_every_token(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    import dine_mcp.auth as auth

    path = tmp_path / "users.toml"
    auth.add_user(path, "seed")
    original = auth.load_users

    def delayed_read(path):
        users = original(path)
        time.sleep(0.02)
        return users

    monkeypatch.setattr(auth, "load_users", delayed_read)
    with ThreadPoolExecutor(max_workers=6) as pool:
        tokens = list(pool.map(lambda n: auth.add_user(path, f"synthetic-{n}"), range(6)))
    saved = original(path)
    assert len(saved) == 7
    assert all(saved[f"synthetic-{n}"] == auth.digest(token) for n, token in enumerate(tokens))
    assert all(token not in path.read_text() for token in tokens)
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("key", ["sevenrooms", "resdiary"])
async def test_unknown_provider_timezone_refuses_ambiguous_slots(tmp_path, key):
    from datetime import time as dtime

    from dine_mcp.providers.base import Window
    from dine_mcp.transport import ProviderError

    calls = []

    def handler(request):
        calls.append(request)
        day = soon(2).isoformat()
        if key == "sevenrooms":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "availability": {
                            day: [{"times": [{"time": "19:00", "type": "book", "access_persistent_id": "synthetic"}]}],
                        }
                    }
                },
            )
        return httpx.Response(200, json=[{"Date": day, "AvailableTimes": [{"TimeSlot": "19:00"}]}])

    app, _ = build(tmp_path, handler, key)
    venue = ProviderVenue(ProviderVenueRef(key, "synthetic"), "Synthetic", time_zone=None)
    with pytest.raises(ProviderError) as error:
        await app.service.providers[key].availability(venue, Window((soon(2),), dtime(18), dtime(20), 2), "local")
    assert error.value.status == VenueStatus.UNAVAILABLE
    assert not calls


async def test_fresh_place_timezone_fills_metadata_gap_without_polluting_cache(tmp_path):

    from test_hardening import available

    app, _ = build(tmp_path, lambda request: httpx.Response(200, json=available(soon(2).isoformat())))
    ref = ProviderVenueRef("sevenrooms", "synthetic")
    app.service.venue_info.put(ProviderVenue(ref, "Synthetic", time_zone=None))
    app.service.resolver.places = FakePlaces([place(time_zone="Asia/Tokyo")])
    venue = await resolve(app)
    result = (await app.service.check_availability("local", [venue], soon(2).isoformat()))["results"][0]
    assert result["status"] == "ok" and result["slots"][0]["start"].endswith("+09:00")
    assert app.service.venue_info.get(ref).time_zone is None


def test_users_limit_refuses_before_replacing_valid_file(tmp_path):
    from dine_mcp.auth import AuthError, add_user, load_users

    path = tmp_path / "users.toml"
    text = "".join(f'[users."synthetic-{n}"]\ntoken_sha256 = "{n:064x}"\n' for n in range(1000))
    path.write_text(text)
    with pytest.raises(AuthError):
        add_user(path, "overflow")
    assert path.read_text() == text and len(load_users(path)) == 1000


async def test_metadata_crash_keeps_cached_slots_and_reports_coverage(make_app, monkeypatch):
    provider = FakeProvider("sevenrooms", venues={"synthetic": {soon(2).isoformat(): ["19:00"]}})
    app = make_app(FakePlaces([place()]), {"sevenrooms": provider})
    venue = await resolve(app)
    await app.service.check_availability("local", [venue], soon(2).isoformat())
    app.service.venue_info._items.clear()

    async def crashed(*args):
        raise ValueError("synthetic metadata failure")

    monkeypatch.setattr(provider, "get_venue", crashed)
    result = (await app.service.check_availability("local", [venue], soon(2).isoformat(), soon(3).isoformat()))[
        "results"
    ][0]
    assert result["status"] == "unavailable" and result["served_from"] == "cache"
    assert len(result["slots"]) == 1
    assert result["coverage"] == {"dates_checked": [soon(2).isoformat()], "dates_not_checked": [soon(3).isoformat()]}


@pytest.mark.parametrize("stage", ["metadata", "availability"])
async def test_dispatch_deadline_expiry_is_not_an_adapter_failure(make_app, monkeypatch, stage):

    from dine_mcp.controls import CURRENT
    from dine_mcp.models import Slot, SlotKind
    from dine_mcp.providers.base import publish

    provider = FakeProvider("sevenrooms", venues={"synthetic": {}})
    day = soon(2).isoformat()

    async def expired(*args):
        if stage == "availability":
            slot = Slot(f"{day}T19:00+08:00", 2, SlotKind.BOOKABLE)
            publish(Observation(VenueStatus.OK, [slot], [day]))
        call = CURRENT.get()
        call.deadline = time.monotonic() - 1
        call.reserve()

    monkeypatch.setattr(provider, "get_venue" if stage == "metadata" else "availability", expired)
    app = make_app(FakePlaces([place()]), {"sevenrooms": provider})
    venue = await resolve(app)
    result = (await app.service.check_availability("local", [venue], day, soon(3).isoformat()))["results"][0]
    assert result["status"] == "skipped_deadline"
    if stage == "availability":
        assert result["served_from"] == "cache" and len(result["slots"]) == 1
        assert result["coverage"] == {"dates_checked": [day], "dates_not_checked": [soon(3).isoformat()]}
    else:
        assert result["coverage"]["dates_not_checked"] == [day, soon(3).isoformat()]
