"""Offline regressions for metadata reads on otherwise cached availability."""

import pytest
from conftest import FakeProvider, soon

from dine_mcp.errors import DineError


async def test_cached_calendar_metadata_is_counted_before_admitting_the_call(make_app, monkeypatch):
    day = soon(2).isoformat()
    provider = FakeProvider("sevenrooms", venues={"cached": {day: ["19:00"]}, "new": {}})
    app = make_app(None, {"sevenrooms": provider})
    cached, new = [
        app.store.save_venue({"channels": [{"kind": "sevenrooms", "ref": ref}]}, [f"sevenrooms:{ref}"])
        for ref in ("cached", "new")
    ]
    await app.service.check_availability("local", [cached], day)
    # Metadata can expire while a recently refreshed calendar observation is fresh.
    app.service.venue_info._items.clear()
    provider.calls.clear()
    monkeypatch.setattr(provider, "cost", lambda ref, window: 19)

    with pytest.raises(DineError) as error:
        await app.service.check_availability("local", [cached, new], day)

    assert error.value.code == "too_broad"
    assert not provider.calls  # 19 calendar reads plus two metadata reads exceed 20.


async def test_cached_calendar_rechecks_metadata_without_fetching_the_calendar(make_app):
    day = soon(2).isoformat()
    provider = FakeProvider("sevenrooms", venues={"cached": {day: ["19:00"]}})
    app = make_app(None, {"sevenrooms": provider})
    venue = app.store.save_venue({"channels": [{"kind": "sevenrooms", "ref": "cached"}]}, ["sevenrooms:cached"])
    await app.service.check_availability("local", [venue], day)
    app.service.venue_info._items.clear()
    provider.calls.clear()

    result = (await app.service.check_availability("local", [venue], day))["results"][0]

    assert provider.calls == ["venue:cached"]
    assert result["status"] == "ok" and result["served_from"] == "cache"
    assert result["coverage"] == {"dates_checked": [day], "dates_not_checked": []}
