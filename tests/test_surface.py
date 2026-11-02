"""The adopted interface gate fails without rewriting reviewable baselines."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("surface_script", ROOT / "scripts/surface.py")
assert spec and spec.loader
surface = importlib.util.module_from_spec(spec)
spec.loader.exec_module(surface)


def test_missing_and_stale_surface_or_reference_refuse_without_writes(tmp_path, monkeypatch):
    value = json.loads((ROOT / "surface.json").read_text())

    async def extract():
        return value

    monkeypatch.setattr(surface, "ROOT", tmp_path)
    monkeypatch.setattr(surface, "extract", extract)
    monkeypatch.setattr(sys, "argv", ["surface.py", "check"])
    with pytest.raises(SystemExit, match="1"):
        surface.main()
    assert not (tmp_path / "surface.json").exists()
    monkeypatch.setattr(sys, "argv", ["surface.py", "update"])
    assert surface.main() == 0
    before = (tmp_path / "surface.json").read_bytes()
    monkeypatch.setattr(sys, "argv", ["surface.py", "check"])
    assert surface.main() == 0
    (tmp_path / "docs/reference.md").write_text("stale reference")
    with pytest.raises(SystemExit, match="1"):
        surface.main()
    assert (tmp_path / "surface.json").read_bytes() == before
    value["tools"][0]["inputSchema"]["properties"]["changed_argument"] = {"type": "string"}
    with pytest.raises(SystemExit, match="1"):
        surface.main()
    assert (tmp_path / "surface.json").read_bytes() == before


async def test_extraction_never_reads_operator_environment_values(monkeypatch):
    monkeypatch.setenv("DINE_HOST", "synthetic-operator-only")
    monkeypatch.setenv("DINE_PORT", "12345")
    monkeypatch.setenv("MCP_BEARER_TOKEN", "synthetic-operator-secret")
    value = await surface.extract()
    text = json.dumps(value)
    assert "synthetic-operator" not in text
    assert next(item for item in value["cli"] if item["name"] == "dine-mcp --port")["default"] == 8000
    assert any(item["name"] == "dine-mcp find --limit" for item in value["cli"])
    assert not any("dine-mcp commands" in item["name"] for item in value["cli"])
    assert len({item["name"] for item in value["cli"]}) == len(value["cli"])
    assert value["coverage"]["environment"] == "partial"
    assert value["instructions"][0]["text"]


async def test_runtime_instruction_drift_is_discovered_in_each_profile(monkeypatch):
    before = await surface.extract()
    original = surface.create_server

    def changed(app, **kwargs):
        server = original(app, **kwargs)
        server.instructions = "Changed runtime instructions"
        return server

    monkeypatch.setattr(surface, "create_server", changed)
    after = await surface.extract()
    assert after["instructions"] != before["instructions"]
    assert after["profiles"][0]["instructions"] != before["profiles"][0]["instructions"]
