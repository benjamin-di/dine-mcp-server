"""Run the shipped acceptance client, including negative behavior and asset controls."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from dine_mcp.composition import build_app
from dine_mcp.server import create_server

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("image_smoke", ROOT / "scripts/smoke.py")
assert spec and spec.loader
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


@pytest.mark.parametrize("broken", [None, "find_venue", "venue_view", "check_availability", "app-resource"])
async def test_real_client_acceptance_detects_broken_domain_reads_and_app_resource(tmp_path, monkeypatch, broken):
    expected = json.loads((ROOT / "surface.json").read_text())
    profile = expected["profiles"][0]
    keys = next(item["keys"] for item in expected["configuration"] if item["name"] == "providers")
    smoke.prepare(tmp_path)
    app = build_app({"DINE_STATE_DIR": str(tmp_path), "DINE_PLACES": "none", "DINE_APPS": "1"})

    async def fail(*args, **kwargs):
        raise RuntimeError("synthetic acceptance failure")

    try:
        if broken and broken != "app-resource":
            monkeypatch.setattr(app.service, broken, fail)
        async with Client(create_server(app, stdio=True)) as client:
            if broken == "app-resource":
                monkeypatch.setattr(client, "read_resource", fail)
            if broken:
                with pytest.raises((RuntimeError, ToolError)):
                    await smoke.verify(client, profile, keys, apps=True)
            else:
                await smoke.verify(client, profile, keys, apps=True)
    finally:
        await app.aclose()
