"""Command line: serve (default), operator commands, and the same reads the MCP tools offer.

dine-mcp [--transport http|stdio] [--host H] [--port P]      serve (HTTP needs a bearer)
dine-mcp find "Harbour Grill" --area "Raffles Place" --country SG   find a venue
dine-mcp check v_abc --date 2026-11-06 --party 2             check availability
dine-mcp status | health
dine-mcp people add|remove|list [ID]                         the roster (gateway mode)
dine-mcp users add ID --file users.toml                      standalone users (prints the token once)
dine-mcp overrides check FILE                                validate an overrides file
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Any

from . import __version__

COMMANDS = ("find", "check", "status", "health", "people", "users", "overrides")


def _print(data: Any) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def _serve_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dine-mcp", description="Restaurant availability MCP server.")
    parser.add_argument("--version", action="version", version=f"dine-mcp {__version__}")
    parser.add_argument("--transport", choices=("http", "stdio"), default="http")
    parser.add_argument("--host", default=os.environ.get("DINE_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("DINE_PORT", "8000")))
    return parser


def _command_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dine-mcp")
    sub = parser.add_subparsers(dest="command", required=True)
    find = sub.add_parser("find", help="find a venue")
    find.add_argument("name", nargs="?")
    find.add_argument("--area")
    find.add_argument("--country")
    find.add_argument("--url")
    find.add_argument("--place-id")
    find.add_argument("--limit", type=int, default=5)
    check = sub.add_parser("check", help="check availability")
    check.add_argument("venue_ids", nargs="+")
    check.add_argument("--date", required=True)
    check.add_argument("--to")
    check.add_argument("--from-time", default="17:00")
    check.add_argument("--to-time", default="22:30")
    check.add_argument("--party", type=int, default=2)
    check.add_argument("--detailed", action="store_true")
    status = sub.add_parser("status", help="providers, budgets, breakers")
    status.add_argument("--check", action="store_true", help="budgeted canaries with a durable 15-minute cooldown")
    health = sub.add_parser("health", help="probe the local server's /health")
    health.add_argument("--port", type=int, default=int(os.environ.get("DINE_PORT", "8000")))
    people = sub.add_parser("people", help="the roster (gateway mode)")
    people.add_argument("action", choices=("add", "remove", "list"))
    people.add_argument("person", nargs="?")
    people.add_argument("--name")
    users = sub.add_parser("users", help="standalone users file")
    users.add_argument("action", choices=("add",))
    users.add_argument("person")
    users.add_argument("--file", required=True)
    overrides = sub.add_parser("overrides", help="validate an overrides file")
    overrides.add_argument("action", choices=("check",))
    overrides.add_argument("file")
    return parser


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in COMMANDS:
        args = _serve_parser().parse_args(argv)
        from .server import serve

        serve(args.transport, args.host, args.port)
        return
    args = _command_parser().parse_args(argv)
    try:
        code = run(args)
    except Exception as exc:  # operator-facing: name the problem, no traceback
        print(f"dine-mcp: {type(exc).__name__}: {exc}", file=sys.stderr)
        code = 1
    raise SystemExit(code)


def run(args: argparse.Namespace) -> int:
    if args.command == "health":
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{args.port}/health", timeout=4) as response:
                return 0 if response.status == 200 else 1
        except OSError:
            return 1
    if args.command == "users":
        from .auth import add_user

        token = add_user(Path(args.file), args.person)
        print(f"Added {args.person}. Their token (shown once): {token}")
        return 0
    if args.command == "overrides":
        from .definitions import effective, load_overrides, load_providers

        definitions = load_providers()
        overrides = load_overrides(Path(args.file), set(definitions))
        for key, definition in sorted(definitions.items()):
            state = effective(definition, overrides)
            print(
                f"{key}: {'on' if state.enabled else 'off'}, {state.access_mode.value}"
                + (f" ({state.reason})" if state.reason else "")
            )
        return 0
    from .composition import build_app

    app = build_app()
    try:
        if args.command == "people":
            if args.action == "list":
                print("\n".join(app.store.people()))
                return 0
            if not args.person:
                print("dine-mcp: give the person id", file=sys.stderr)
                return 2
            if args.action == "add":
                print("added" if app.store.add_person(args.person, args.name) else "already on the roster")
            else:
                print("removed" if app.store.remove_person(args.person) else "not on the roster")
            return 0
        if args.command == "status":
            if args.check:
                return asyncio.run(_reads(app, args))
            _print(app.service.status(app.transports))
            return 0
        return asyncio.run(_reads(app, args))
    finally:
        app.store.close()


async def _reads(app: Any, args: argparse.Namespace) -> int:
    from .errors import DineError

    try:
        if args.command == "status":
            result = await app.service.checked_status(app.transports, "monitor")
        elif args.command == "find":
            result = await app.service.find_venue(
                "local", args.name, args.area, args.country, args.url, args.place_id, args.limit
            )
        else:
            result = await app.service.check_availability(
                "local",
                args.venue_ids,
                args.date,
                args.to,
                args.from_time,
                args.to_time,
                args.party,
                detailed=args.detailed,
            )
    except DineError as exc:
        _print({"error": exc.payload()})
        return 1
    finally:
        await app.aclose()
    _print(result)
    return 0
