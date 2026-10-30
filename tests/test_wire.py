"""Exercise subprocess stdio and TCP HTTP using only the synthetic service world."""

from __future__ import annotations

import asyncio
import os
import socket
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from conftest import soon
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

SERVER = str(Path(__file__).with_name("protocol_server.py"))


@asynccontextmanager
async def http_server():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        SERVER,
        "--port",
        str(port),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        async with asyncio.timeout(20), httpx.AsyncClient() as probe:
            while True:
                if process.returncode is not None:
                    raise RuntimeError("synthetic HTTP server exited before readiness")
                try:
                    if (await probe.get(url + "/health")).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.05)
        yield url
    finally:
        if process.returncode is None:
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), 5)
        except TimeoutError:
            process.kill()
            await process.wait()


async def exercise(client, *, notifications=True):
    async with client:
        assert len(await client.list_tools()) == 4
        found = await client.call_tool("dine_find_venue", {"name": "Example Venue"})
        venue = found.structured_content["venues"][0]["venue_id"]
        progress = []

        async def on_progress(value, total, message):
            progress.append(value)

        result = await client.call_tool(
            "dine_check_availability",
            {"venue_ids": [venue], "date_from": soon(2).isoformat()},
            progress_handler=on_progress,
        )
        assert result.structured_content["results"][0]["slots"][0]["start"][11:16] == "19:00"
        # Stateless JSON HTTP carries the response only; stdio can carry notifications.
        assert progress == ([1] if notifications else [])
        invalid = await client.call_tool("dine_get_venue", {"venue_id": "v_missing"}, raise_on_error=False)
        assert invalid.is_error and "unknown_venue_id" in invalid.content[0].text


async def test_real_stdio():
    await exercise(Client(StdioTransport(command=sys.executable, args=[SERVER], env=dict(os.environ))))


async def test_real_http():
    async with http_server() as url:
        await exercise(Client(url + "/mcp"), notifications=False)
