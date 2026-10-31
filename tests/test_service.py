"""Resolution and availability through the service, with fake providers and a fake place source."""

from __future__ import annotations

import pytest
from conftest import FakePlaces, FakeProvider, soon

from dine_mcp.errors import DineError
from dine_mcp.models import BookingRules, VenueStatus
from dine_mcp.places import Place


def amber_place(links=(("https://www.sevenrooms.com/reservations/amber?src=g&rwg_token=x", "sevenrooms.com"),)):
    return Place(
        "ChIJ_amber_0000000000000",
        "Amber",
        address="15 Telok Ayer Street Raffles Place, Singapore",
        time_zone="Asia/Singapore",
        phone="+65 0000 0000",
        website="https://www.example.com/amber?utm_source=google",
        reservation_links=tuple(links),
    )


@pytest.fixture
def sevenrooms():
    day = soon(2).isoformat()
    return FakeProvider("sevenrooms", venues={"amber": {day: ["18:00", "18:30", "21:15", "23:30"]}})


async def test_find_by_name_resolves_links_and_contacts(make_app, sevenrooms):
    places = FakePlaces([amber_place()])
    app = make_app(places, {"sevenrooms": sevenrooms})
    result = await app.service.find_venue("alex", name="Amber", area="Raffles Place", country="sg")
    assert len(result["venues"]) == 1 and not result["candidates"]
    venue = result["venues"][0]
    kinds = [c["kind"] for c in venue["channels"]]
    assert kinds == ["sevenrooms", "phone", "website"]
    seven = venue["channels"][0]
    assert seven["ref"] == "amber" and seven["verified"] and "rwg_token" not in seven["url"]
    assert "utm_source" not in venue["channels"][2]["url"]
    assert venue["match"]["confidence"] == 1.0
    # The same place again resolves to the same venue id, from the cache (no new place request).
    calls = len(places.calls)
    again = await app.service.find_venue("alex", google_place_id=amber_place().place_id)
    assert again["venues"][0]["venue_id"] == venue["venue_id"]
    assert len(places.calls) == calls


async def test_ambiguous_names_return_candidates_not_guesses(make_app):
    places = FakePlaces(
        [
            Place("ChIJ_a_000000000000000000", "Amber Kitchen"),
            Place("ChIJ_b_000000000000000000", "Amber Lounge"),
        ]
    )
    app = make_app(places)
    result = await app.service.find_venue("alex", name="Amber")
    assert result["venues"] == [] and len(result["candidates"]) == 2
    assert result["note"]


async def test_unsupported_booking_link_is_listed_unverified(make_app):
    place = amber_place(links=(("https://www.opentable.example/r/amber?ref=1", "opentable.example"),))
    app = make_app(FakePlaces([place]))
    venue = (await app.service.find_venue("alex", google_place_id=place.place_id))["venues"][0]
    page = venue["channels"][0]
    assert page["kind"] == "booking_page" and not page["verified"] and page["access_mode"] == "listing"


async def test_url_is_matched_never_fetched(make_app, sevenrooms):
    app = make_app(FakePlaces([]), {"sevenrooms": sevenrooms})
    result = await app.service.find_venue("alex", url="https://www.sevenrooms.com/reservations/amber")
    assert result["venues"][0]["channels"][0]["ref"] == "amber"
    assert sevenrooms.calls == ["venue:amber"]
    other = await app.service.find_venue("alex", url="https://attacker.example/?q=secret")
    assert other["venues"] == [] and "not a booking page" in other["note"]


async def test_find_requires_exactly_one_identifier(make_app):
    app = make_app(FakePlaces([]))
    with pytest.raises(DineError):
        await app.service.find_venue("alex")
    with pytest.raises(DineError):
        await app.service.find_venue("alex", name="Amber", url="https://www.sevenrooms.com/reservations/amber")


async def test_place_source_failure_is_a_typed_error(make_app):
    app = make_app(FakePlaces(fail=True))
    with pytest.raises(DineError) as raised:
        await app.service.find_venue("alex", name="Amber")
    assert raised.value.code == "place_source_unavailable"


async def _amber(app, sevenrooms_place=None):
    place = sevenrooms_place or amber_place()
    app.service.resolver.places = FakePlaces([place])
    return (await app.service.find_venue("alex", google_place_id=place.place_id))["venues"][0]["venue_id"]


async def test_availability_filters_window_and_caches(make_app, sevenrooms):
    app = make_app(None, {"sevenrooms": sevenrooms})
    venue_id = await _amber(app)
    day = soon(2).isoformat()
    result = await app.service.check_availability("alex", [venue_id], day, time_from="18:00", time_to="22:00")
    seven = [r for r in result["results"] if r["channel"] == "sevenrooms"][0]
    assert seven["status"] == "ok"
    assert [s["start"][11:16] for s in seven["slots"]] == ["18:00", "18:30", "21:15"]
    assert seven["served_from"] == "live" and seven["coverage"]["dates_checked"] == [day]
    assert seven["slots"][0]["start"].endswith("+08:00")
    calls = list(sevenrooms.calls)
    again = await app.service.check_availability("bob", [venue_id], day, time_from="21:00", time_to="23:59")
    assert sevenrooms.calls == calls  # served from the shared cache
    assert [s["start"][11:16] for s in again["results"][0]["slots"]] == ["21:15", "23:30"]
    assert again["results"][0]["served_from"] == "cache"


async def test_window_across_midnight(make_app, sevenrooms):
    app = make_app(None, {"sevenrooms": sevenrooms})
    venue_id = await _amber(app)
    result = await app.service.check_availability(
        "alex", [venue_id], soon(2).isoformat(), time_from="23:00", time_to="01:00"
    )
    assert [s["start"][11:16] for s in result["results"][0]["slots"]] == ["23:30"]


async def test_not_yet_open_differs_from_none_available(make_app, sevenrooms):
    sevenrooms.rules = BookingRules(window_days=1, release_time="10:00")
    app = make_app(None, {"sevenrooms": sevenrooms})
    venue_id = await _amber(app)
    result = await app.service.check_availability("alex", [venue_id], soon(5).isoformat())
    seven = result["results"][0]
    assert seven["status"] == "not_yet_open" and seven["opens_at"].endswith("10:00+08:00")
    assert not any(c.startswith("avail") for c in sevenrooms.calls)


async def test_party_too_large_names_the_contact(make_app, sevenrooms):
    sevenrooms.rules = BookingRules(max_party=6)
    app = make_app(None, {"sevenrooms": sevenrooms})
    venue_id = await _amber(app)
    result = await app.service.check_availability("alex", [venue_id], soon(2).isoformat(), party_size=8)
    seven = result["results"][0]
    assert seven["status"] == "party_too_large" and seven["max_party"] == 6
    assert "+65" in seven["contact"]


async def test_contact_only_venue(make_app):
    place = amber_place(links=())
    app = make_app(None)
    venue_id = await _amber(app, place)
    result = await app.service.check_availability("alex", [venue_id], soon(2).isoformat())
    assert [r["status"] for r in result["results"]] == ["contact_only"]
    assert "+65" in result["results"][0]["contact"]


async def test_provider_pushback_is_a_venue_status_not_a_call_error(make_app, sevenrooms):
    sevenrooms.fail = VenueStatus.BLOCKED
    app = make_app(None, {"sevenrooms": sevenrooms})
    venue_id = await _amber(app)
    result = await app.service.check_availability("alex", [venue_id], soon(2).isoformat())
    assert result["results"][0]["status"] == "blocked"
    assert result["results"][0]["coverage"]["dates_not_checked"]


@pytest.mark.parametrize("status", [VenueStatus.UNAVAILABLE, VenueStatus.UNKNOWN_VENUE])
async def test_provider_failure_note_does_not_claim_venue_disappeared(make_app, sevenrooms, status):
    sevenrooms.fail = status
    app = make_app(None, {"sevenrooms": sevenrooms})
    venue_id = await _amber(app)
    result = (await app.service.check_availability("alex", [venue_id], soon(2).isoformat()))["results"][0]
    assert result["status"] == status.value
    assert "no longer" not in result["note"]
    assert result["note"] == (
        "The provider request failed; availability could not be verified."
        if status is VenueStatus.UNAVAILABLE
        else "The provider did not recognize this venue reference."
    )
    assert not result["coverage"]["dates_checked"] and not result["slots"]
    assert result["coverage"]["dates_not_checked"] == [soon(2).isoformat()]
    assert result["observed_at"] is None and result["served_from"] is None
    assert result["booking_link"]


async def test_too_broad_is_refused_before_any_request(make_app, sevenrooms):
    sevenrooms.calendar = False
    app = make_app(None, {"sevenrooms": sevenrooms})
    ids = []
    for n in range(4):
        sevenrooms.venues[f"v{n}"] = {}
        place = Place(
            f"ChIJ_v{n}_00000000000000000",
            f"Venue {n}",
            time_zone="Asia/Singapore",
            reservation_links=((f"https://www.sevenrooms.com/reservations/v{n}", "sevenrooms.com"),),
        )
        ids.append(await _amber(app, place))
    with pytest.raises(DineError) as raised:
        await app.service.check_availability("alex", ids, soon(1).isoformat(), soon(7).isoformat())
    assert raised.value.code == "too_broad"
    assert not sevenrooms.calls


@pytest.mark.parametrize(
    "args",
    [
        {"date_from": "2026-13-01"},
        {"date_from": "2026-11-10", "date_to": "2026-11-01"},
        {"date_from": "2026-11-01", "date_to": "2026-11-09"},
        {"date_from": "2026-11-01", "time_from": "7pm"},
        {"date_from": "2026-11-01", "kinds": ["walk-in"]},
    ],
)
async def test_bad_queries_are_invalid_arguments(make_app, args):
    app = make_app(None)
    venue_id = await _amber(app, amber_place(links=()))
    with pytest.raises(DineError) as raised:
        await app.service.check_availability("alex", [venue_id], **args)
    assert raised.value.code == "invalid_argument"


async def test_unknown_venue_id(make_app):
    app = make_app(None)
    with pytest.raises(DineError) as raised:
        await app.service.check_availability("alex", ["v_nope"], soon(1).isoformat())
    assert raised.value.code == "unknown_venue_id"


async def test_disabled_server_refuses(make_app, tmp_path):
    overrides = tmp_path / "overrides.toml"
    overrides.write_text("disabled = true\n")
    app = make_app(None, env={"DINE_OVERRIDES": str(overrides)})
    with pytest.raises(DineError) as raised:
        await app.service.find_venue("alex", name="Amber")
    assert raised.value.code == "disabled"


async def test_lowered_provider_answers_with_its_link(make_app, sevenrooms, tmp_path):
    overrides = tmp_path / "overrides.toml"
    overrides.write_text('[providers.sevenrooms]\naccess_mode = "listing"\n')
    app = make_app(None, {"sevenrooms": sevenrooms}, env={"DINE_OVERRIDES": str(overrides)})
    venue_id = await _amber(app)
    result = await app.service.check_availability("alex", [venue_id], soon(2).isoformat())
    seven = result["results"][0]
    assert seven["status"] == "unsupported" and seven["booking_link"]["url"].startswith("https://www.sevenrooms.com/")
    assert not any(c.startswith("avail") for c in sevenrooms.calls)
