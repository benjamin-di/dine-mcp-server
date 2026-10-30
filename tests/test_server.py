"""The MCP surface over the wire: tools, schemas, text mirrors, error envelopes, people, auth."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import FakePlaces, FakeProvider, soon
from fastmcp import Client
from test_service import amber_place

from dine_mcp.auth import AuthConfig
from dine_mcp.server import create_server

SNAPSHOT = Path(__file__).parent / "fixtures" / "tool_surface.json"
SURFACE_BUDGET = 12_000  # bytes of serialised tools/list; raise only deliberately, recording why


@pytest.fixture
def app(make_app):
    day = soon(2).isoformat()
    provider = FakeProvider("sevenrooms", venues={"amber": {day: ["19:00", "19:30"]}})
    return make_app(FakePlaces([amber_place()]), {"sevenrooms": provider})


async def tools_of(server) -> list[dict]:
    async with Client(server) as client:
        tools = await client.list_tools()
    return [t.model_dump(mode="json", exclude_none=True, by_alias=True) for t in tools]


async def test_tool_surface_matches_the_snapshot(app):
    tools = await tools_of(create_server(app, stdio=True))
    names = [t["name"] for t in tools]
    assert names == ["dine_find_venue", "dine_get_venue", "dine_check_availability", "dine_status"]
    for tool in tools:
        annotations = tool["annotations"]
        assert annotations["readOnlyHint"] is True and annotations["destructiveHint"] is False
        assert tool.get("outputSchema", {}).get("type") == "object"
    surface = json.dumps(tools, sort_keys=True, ensure_ascii=False)
    assert len(surface.encode()) <= SURFACE_BUDGET, len(surface.encode())
    if not SNAPSHOT.exists():  # first run writes it; review the diff like code
        SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT.write_text(json.dumps(tools, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    assert json.loads(SNAPSHOT.read_text()) == json.loads(surface), "tool surface changed: update the snapshot"


async def test_descriptions_carry_no_behavioural_instructions(app):
    for tool in await tools_of(create_server(app, stdio=True)):
        text = tool.get("description", "").lower()
        assert "ignore" not in text and "<" not in text


async def test_find_and_check_over_the_wire(app):
    async with Client(create_server(app, stdio=True)) as client:
        found = await client.call_tool("dine_find_venue", {"name": "Amber", "country": "SG"})
        assert not found.is_error
        venue_id = found.structured_content["venues"][0]["venue_id"]
        assert venue_id in found.content[0].text
        checked = await client.call_tool(
            "dine_check_availability", {"venue_ids": [venue_id], "date_from": soon(2).isoformat()}
        )
        assert not checked.is_error
        assert "19:00" in checked.content[0].text and "not holds" in checked.content[0].text
        status = await client.call_tool("dine_status", {})
        assert status.structured_content["providers"]


async def test_errors_are_envelopes_without_exception_text(app):
    async with Client(create_server(app, stdio=True)) as client:
        bad = await client.call_tool(
            "dine_check_availability", {"venue_ids": ["v_nope"], "date_from": "2026-01-01"}, raise_on_error=False
        )
        assert bad.is_error
        envelope = json.loads(bad.content[0].text)["error"]
        assert envelope["code"] == "unknown_venue_id" and envelope["action"] == "none"
        bad_args = await client.call_tool(
            "dine_check_availability", {"venue_ids": [], "date_from": "x"}, raise_on_error=False
        )
        envelope = json.loads(bad_args.content[0].text)["error"]
        assert envelope["code"] == "invalid_argument" and "Traceback" not in envelope["next_step"]


async def test_http_gateway_mode_requires_bearer_and_valid_person(app):
    token = "g" * 40
    auth = AuthConfig.from_env({"MCP_BEARER_TOKEN": token, "DINE_PERSON_HEADER": "X-Fleet-Person"})
    app.store.add_person("alex")
    server = create_server(app, auth=auth)
    http = server.http_app(transport="http", stateless_http=True, json_response=True)
    import httpx

    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "dine_find_venue", "arguments": {"name": "Amber", "country": "SG"}},
    }
    status = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "dine_status", "arguments": {}}}
    headers = {"accept": "application/json, text/event-stream", "mcp-protocol-version": "2025-11-25"}
    async with (
        http.router.lifespan_context(http),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=http), base_url="http://127.0.0.1") as client,
    ):
        assert (await client.get("/health")).status_code == 200
        assert (await client.post("/mcp", json=body, headers=headers)).status_code == 401
        auth_headers = {**headers, "authorization": f"Bearer {token}"}
        unknown = await client.post("/mcp", json=body, headers={**auth_headers, "x-fleet-person": "../sam"})
        assert "unknown_person" in unknown.text
        known = await client.post("/mcp", json=body, headers={**auth_headers, "x-fleet-person": "alex"})
        assert known.status_code == 200 and "unknown_person" not in known.text and "Amber" in known.text
        # Status is aggregate and non-personal: the monitor needs only the bearer.
        monitor = await client.post("/mcp", json=status, headers=auth_headers)
        assert monitor.status_code == 200 and '"providers"' in monitor.text
