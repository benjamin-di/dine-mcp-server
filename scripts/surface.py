"""Extract public discovery/parser declarations; check never writes a baseline."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from fastmcp import Client

from dine_mcp.cli import _command_parser, _serve_parser
from dine_mcp.composition import build_app
from dine_mcp.http_boundary import HttpPolicy
from dine_mcp.server import create_server

ROOT = Path(__file__).resolve().parents[1]
SECTIONS = (
    "tools",
    "prompts",
    "resources",
    "resource_templates",
    "instructions",
    "cli",
    "environment",
    "configuration",
    "profiles",
)


def parser_surface(parser: argparse.ArgumentParser, prefix: str = "dine-mcp") -> list[dict]:
    result = []
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, child in action.choices.items():
                result.extend(parser_surface(child, prefix + " " + name))
            continue
        item = {
            "name": prefix + " " + "/".join(action.option_strings or [action.dest]),
            "required": action.required,
            "nargs": action.nargs,
            "default": action.default,
            "type": getattr(action.type, "__name__", "string"),
            "action": type(action).__name__,
        }
        if action.help is not None:
            item["description"] = action.help
        if action.choices is not None:
            item["choices"] = list(action.choices)
        result.append(item)
    return result


async def discovery(app) -> dict:
    async with Client(create_server(app, stdio=True)) as client:
        result = {}
        for section, method in [
            ("tools", client.list_tools),
            ("prompts", client.list_prompts),
            ("resources", client.list_resources),
            ("resource_templates", client.list_resource_templates),
        ]:
            items = [value.model_dump(mode="json", exclude_none=True, by_alias=True) for value in await method()]
            # Discovery resources identify themselves by URI; retain that identity as the common name.
            for item in items:
                item.setdefault("name", item.get("uri", item.get("uriTemplate")))
            result[section] = sorted(items, key=lambda item: item["name"])
        result["instructions"] = [{"name": "server", "text": client.instructions}]
        return result


async def extract() -> dict:
    with tempfile.TemporaryDirectory(prefix="interface-") as directory:
        app = build_app({"DINE_STATE_DIR": directory, "DINE_PLACES": "none"})
        try:
            value = await discovery(app)
            app.apps_enabled = True
            apps = await discovery(app)
            provider_keys = sorted(item["key"] for item in app.service.status(app.transports)["providers"])
        finally:
            await app.aclose()
    # Parser defaults are evaluated against an empty mapping, never the operator's environment.
    with patch.dict(os.environ, {}, clear=True):
        actions = {}
        for item in parser_surface(_serve_parser()) + parser_surface(_command_parser()):
            name = item["name"]
            if name in actions and actions[name] != item:
                raise ValueError("Conflicting CLI declarations: " + name)
            actions[name] = item
        value["cli"] = [actions[name] for name in sorted(actions)]
    value.update(
        schema=1,
        environment=[
            {
                "name": "DINE_HOST",
                "type": "string",
                "default": "127.0.0.1",
                "secret": False,
                "precedence": "CLI --host overrides environment",
            },
            {
                "name": "DINE_PORT",
                "type": "integer",
                "default": 8000,
                "secret": False,
                "precedence": "CLI --port overrides environment",
            },
        ],
        configuration=[
            {"name": "http-policy", "defaults": asdict(HttpPolicy())},
            {"name": "providers", "keys": provider_keys},
        ],
        profiles=[{"name": "apps", "condition": "DINE_APPS=1", **apps}],
        coverage={section: "complete" for section in SECTIONS},
    )
    value["coverage"]["environment"] = "partial"
    value["coverage"]["configuration"] = "partial"
    # JSON round trip canonicalizes tuples from dataclass defaults.
    return json.loads(json.dumps(value))


def reference(value: dict) -> str:
    lines = [
        "# Interface reference",
        "",
        "Generated from the application interface declarations. Do not edit by hand.",
        "",
    ]
    for section in SECTIONS:
        lines += ["## " + section.capitalize(), "", "Coverage: " + value["coverage"][section] + ".", ""]
        for item in value[section]:
            lines += ["### `" + item["name"] + "`", ""]
            if item.get("description"):
                lines += [item["description"], ""]
            details = {key: content for key, content in item.items() if key not in ("name", "description")}
            if details:
                lines += ["```json", json.dumps(details, indent=2, sort_keys=True, ensure_ascii=False), "```", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("check", "update", "extract"))
    args = parser.parse_args()
    value = asyncio.run(extract())
    text = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if args.mode == "extract":
        print(text, end="")
        return 0
    snapshot, docs = ROOT / "surface.json", ROOT / "docs/reference.md"
    if args.mode == "update":
        snapshot.write_text(text)
        docs.parent.mkdir(parents=True, exist_ok=True)
        docs.write_text(reference(value))
        return 0
    if (
        not snapshot.exists()
        or not docs.exists()
        or snapshot.read_text() != text
        or docs.read_text() != reference(value)
    ):
        parser.exit(1, "Interface/reference drift: run python scripts/surface.py update and review the diff\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
