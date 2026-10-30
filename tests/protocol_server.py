"""Loopback-only synthetic server for protocol qualification; never used by the package."""

from __future__ import annotations

import argparse
import tempfile

import httpx
from conftest import FakePlaces, FakeProvider, soon

from dine_mcp.composition import build_app
from dine_mcp.definitions import load_providers
from dine_mcp.places import Place
from dine_mcp.server import HTTP_OPTIONS, create_server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="dine-protocol-") as state:
        providers = {key: FakeProvider(key) for key in load_providers()}
        providers["sevenrooms"].venues["example"] = {soon(2).isoformat(): ["19:00"]}
        places = FakePlaces(
            [
                Place(
                    "synthetic-place",
                    "Example Venue",
                    time_zone="Asia/Singapore",
                    reservation_links=(("https://www.sevenrooms.com/reservations/example", "sevenrooms.com"),),
                )
            ]
        )
        app = build_app(
            {"DINE_STATE_DIR": state},
            providers=providers,
            place_source=places,
            client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(599))),
        )
        server = create_server(app, stdio=True)
        if args.port:
            server.run(transport="http", host="127.0.0.1", port=args.port, show_banner=False, **HTTP_OPTIONS)
        else:
            server.run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
