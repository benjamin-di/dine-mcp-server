"""External client acceptance for the installed application and disposable state."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from dine_mcp.identity import BUILD
from dine_mcp.places import Place
from dine_mcp.state import Store

PLACE_ID = "ChIJ_smoke_000000000000000"


def prepare(state: Path) -> None:
    store = Store(state)
    try:
        place = Place(PLACE_ID, "Synthetic Venue", time_zone="UTC", website="https://example.invalid/fixture")
        store.cache_place(PLACE_ID, place.to_cache())
    finally:
        store.close()


async def verify(client, expected, provider_keys, apps=False):
    assert client.instructions == expected["instructions"][0]["text"], "instructions differ from source"
    for section, method in [
        ("tools", client.list_tools),
        ("prompts", client.list_prompts),
        ("resources", client.list_resources),
        ("resource_templates", client.list_resource_templates),
    ]:
        observed = [value.model_dump(mode="json", exclude_none=True, by_alias=True) for value in await method()]
        for item in observed:
            item.setdefault("name", item.get("uri", item.get("uriTemplate")))
        assert sorted(observed, key=lambda item: item["name"]) == expected[section], section + " differs from source"
    status = await client.call_tool("dine_status", {})
    assert not status.is_error
    assert sorted(item["key"] for item in status.structured_content["providers"]) == provider_keys
    found = await client.call_tool("dine_find_venue", {"google_place_id": PLACE_ID})
    assert not found.is_error and len(found.structured_content["venues"]) == 1
    venue = found.structured_content["venues"][0]
    assert venue["name"] == "Synthetic Venue"
    detail = await client.call_tool("dine_get_venue", {"venue_id": venue["venue_id"]})
    assert not detail.is_error and detail.structured_content["venue_id"] == venue["venue_id"]
    day = (datetime.now(UTC) + timedelta(days=2)).date().isoformat()
    available = await client.call_tool("dine_check_availability", {"venue_ids": [venue["venue_id"]], "date_from": day})
    assert not available.is_error
    result = available.structured_content
    assert result["query"]["party_size"] == 2 and result["query"]["time_from"] == "17:00"
    assert result["results"][0]["status"] == "contact_only" and not result["results"][0]["slots"]
    invalid = await client.call_tool(
        "dine_check_availability", {"venue_ids": [], "date_from": "invalid"}, raise_on_error=False
    )
    assert invalid.is_error and json.loads(invalid.content[0].text)["error"]["code"] == "invalid_argument"
    if apps:
        resource = (await client.read_resource("ui://dine/availability.html"))[0]
        assert resource.mime_type == "text/html;profile=mcp-app" and "Table availability" in resource.text
        assert f"Dine v{BUILD.version} · {BUILD.source_revision[:12]}" in resource.text
        assert f'title="Source revision: {BUILD.source_revision}"' in resource.text
        assert "__DINE_" not in resource.text


async def main():
    expected = json.loads(os.environ["DINE_EXPECTED_SURFACE"])
    provider_keys = next(item["keys"] for item in expected["configuration"] if item["name"] == "providers")
    state = Path(os.environ.get("DINE_SMOKE_STATE", "/state"))
    prepare(state)
    for apps in (False, True):
        profile = next(item for item in expected["profiles"] if item["name"] == "apps") if apps else expected
        async with Client(
            StdioTransport(
                command="dine-mcp",
                args=["--transport", "stdio"],
                env={"DINE_STATE_DIR": str(state), "DINE_PLACES": "none", "DINE_APPS": "1" if apps else "0"},
            )
        ) as client:
            await verify(client, profile, provider_keys, apps)
    help_result = subprocess.run(["dine-mcp", "find", "--help"], capture_output=True, text=True, timeout=15)
    assert help_result.returncode == 0 and "--limit" in help_result.stdout
    invalid = subprocess.run(["dine-mcp", "--port", "invalid"], capture_output=True, text=True, timeout=15)
    assert invalid.returncode == 2 and "invalid int value" in invalid.stderr


if __name__ == "__main__":
    asyncio.run(main())
    print("Packaged discovery, synthetic domain behavior, App resource and CLI passed")
