"""Distribution contracts: documented tools and synthetic fixture privacy."""

from __future__ import annotations

import json
import re
from pathlib import Path

from fastmcp import Client

from dine_mcp.server import create_server

ROOT = Path(__file__).resolve().parents[1]


async def test_readme_tools_match_the_surface(make_app):
    async with Client(create_server(make_app(), stdio=True)) as client:
        names = {tool.name for tool in await client.list_tools()}
    readme = (ROOT / "README.md").read_text()
    documented = set(re.findall(r"\| `(?P<name>dine_[a-z_]+)\(", readme))
    assert names == documented


def test_fixtures_have_no_contact_records_credentials_or_private_references():
    forbidden = re.compile(r"op://|BEGIN (?:RSA |OPENSSH )?PRIVATE KEY|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", re.I)
    sensitive_keys = {"authorization", "cookie", "password", "access_token", "refresh_token", "email", "phone"}

    def walk(value):
        if isinstance(value, dict):
            assert not sensitive_keys.intersection(str(key).lower() for key in value)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    for path in (ROOT / "tests" / "fixtures").rglob("*"):
        if not path.is_file():
            continue
        raw = path.read_text()
        assert not forbidden.search(raw), path
        if path.suffix == ".json" and path.name != "tool_surface.json":
            walk(json.loads(raw))
