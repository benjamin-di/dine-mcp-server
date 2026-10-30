"""Only explicit, dated closed days avoid provider work; uncertainty is preserved."""

from __future__ import annotations

import time
from datetime import timedelta

from conftest import FakePlaces, FakeProvider, soon

from dine_mcp.hours import closed_days
from dine_mcp.places import Place


def place(hours):
    return Place(
        "synthetic-hours",
        "Example Venue",
        time_zone="Asia/Singapore",
        opening_hours=hours,
        reservation_links=(("https://www.sevenrooms.com/reservations/example", "sevenrooms.com"),),
    )


def test_closed_days_horizon_holidays_and_malformed_periods():
    day = soon(2)
    hours = {"weekdayDescriptions": [f"{day:%A}: Closed"], "periods": []}
    assert day in closed_days(place(hours), time.time())
    old = time.time() - 8 * 86400
    assert day not in closed_days(place(hours), old)
    hours["holidays"] = [{"date": day.isoformat(), "confirmed": False}]
    assert day not in closed_days(place(hours), time.time())
    hours["holidays"] = []
    hours["periods"] = [{"open": {"day": 1}, "close": {"day": 2}}]
    assert closed_days(place(hours), time.time()) == {}


def test_overnight_opening_prevents_false_closed_day():
    day = soon(2)

    def point(day, hour):
        return {"date": {"year": day.year, "month": day.month, "day": day.day}, "hour": hour}

    hours = {
        "weekdayDescriptions": [f"{day:%A}: Closed"],
        "periods": [{"open": point(day - timedelta(days=1), 22), "close": point(day, 2)}],
    }
    assert day not in closed_days(place(hours), time.time())


async def test_explicit_closed_date_needs_no_provider_lookup(make_app):
    day = soon(2)
    provider = FakeProvider("sevenrooms", venues={"example": {day.isoformat(): ["19:00"]}})
    app = make_app(
        FakePlaces([place({"weekdayDescriptions": [f"{day:%A}: Closed"], "periods": []})]), {"sevenrooms": provider}
    )
    venue = (await app.service.find_venue("local", google_place_id="synthetic-hours"))["venues"][0]["venue_id"]
    result = (await app.service.check_availability("local", [venue], day.isoformat()))["results"][0]
    assert result["status"] == "closed" and result["coverage"]["dates_checked"] == [day.isoformat()]
    assert not provider.calls
