"""Offline acceptance of roadmap features through real service and MCP boundaries."""

from __future__ import annotations

import pytest
from conftest import FakePlaces, FakeProvider, soon
from fastmcp import Client

from dine_mcp.models import BookingRules
from dine_mcp.places import Place
from dine_mcp.server import create_server


@pytest.mark.parametrize("cached", [False, True])
async def test_minimum_party_is_not_sold_out_and_makes_no_calendar_read(make_app, monkeypatch, cached):
    provider = FakeProvider("sevenrooms", venues={"synthetic": {}}, rules=BookingRules(min_party=4))
    app = make_app(
        FakePlaces(
            [
                Place(
                    "ChIJ_synthetic_minimum_000",
                    "Synthetic",
                    phone="+00 0000 0000",
                    reservation_links=(("https://www.sevenrooms.com/reservations/synthetic", "SevenRooms"),),
                )
            ]
        ),
        {"sevenrooms": provider},
    )
    async with Client(create_server(app, stdio=True)) as client:
        found = await client.call_tool("dine_find_venue", {"google_place_id": "ChIJ_synthetic_minimum_000"})
        venue = found.structured_content["venues"][0]["venue_id"]
        if cached:
            await client.call_tool("dine_get_venue", {"venue_id": venue})
            monkeypatch.setattr(provider, "cost", lambda *args: 25)
        provider.calls.clear()
        response = await client.call_tool(
            "dine_check_availability", {"venue_ids": [venue], "date_from": soon(2).isoformat(), "party_size": 2}
        )
    result = response.structured_content["results"][0]
    assert result["status"] == "party_too_small" and result["min_party"] == 4
    assert result["booking_link"] and result["contact"] and not result["slots"]
    assert "party_too_small" in response.content[0].text
    assert provider.calls == ([] if cached else ["venue:synthetic"])


async def test_empty_kinds_refused_at_mcp_boundary(make_app):
    app = make_app(None)
    async with Client(create_server(app, stdio=True)) as client:
        result = await client.call_tool(
            "dine_check_availability",
            {"venue_ids": ["v_synthetic"], "date_from": soon(2).isoformat(), "kinds": []},
            raise_on_error=False,
        )
    assert result.is_error and "invalid_argument" in result.content[0].text


async def test_optional_app_metadata_resource_and_text_fallback(make_app):
    async with Client(create_server(make_app(env={"DINE_APPS": "1"}), stdio=True)) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
        assert tools["dine_check_availability"].meta["ui"]["resourceUri"] == "ui://dine/availability.html"
        listed = await client.list_resources()
        assert str(listed[0].uri) == "ui://dine/availability.html"
        resource = (await client.read_resource("ui://dine/availability.html"))[0]
        assert resource.mime_type == "text/html;profile=mcp-app"
        assert "Table availability" in resource.text
        assert "script src=" not in resource.text and "iframe" not in resource.text[:3000]
    async with Client(create_server(make_app(env={"DINE_APPS": "0"}), stdio=True)) as client:
        assert await client.list_resources() == []
        tools = {tool.name: tool for tool in await client.list_tools()}
        assert not (tools["dine_check_availability"].meta or {}).get("ui")


def test_packaged_ui_matches_reviewed_source():
    import hashlib
    from pathlib import Path

    root = Path(__file__).parents[1]
    digest = hashlib.sha256()
    for name in (
        "app.js",
        "render.js",
        "template.html",
        "package.json",
        "package-lock.json",
        "build.mjs",
        "licenses.mjs",
    ):
        digest.update((root / "tools/ui" / name).read_bytes())
    html = (root / "src/dine_mcp/ui/availability.html").read_text()
    assert html.startswith(f"<!-- source-sha256: {digest.hexdigest()} -->")
