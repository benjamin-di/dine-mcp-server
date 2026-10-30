"""Run pinned Inspector and generic conformance checks against the synthetic application.

npm ci --prefix tools/qualification
uv run python scripts/qualify-protocol.py
Reports stay in .artifacts/protocol/; no booking provider or Google is contacted.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from test_wire import SERVER, http_server  # noqa: E402

SCENARIOS = ("server-initialize", "ping", "tools-list", "logging-set-level", "dns-rebinding-protection")


async def run(name: str, *command: str) -> None:
    reports = ROOT / ".artifacts" / "protocol"
    reports.mkdir(parents=True, exist_ok=True)
    process = await asyncio.create_subprocess_exec(
        *command, cwd=ROOT, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), 90)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise RuntimeError(f"{name}: qualification exceeded 90 seconds") from None
    (reports / f"{name}.log").write_bytes(output)
    print(f"{name}: {'passed' if process.returncode == 0 else 'FAILED'}", flush=True)
    if process.returncode:
        print(output.decode(errors="replace")[-4000:])
        raise RuntimeError(f"{name}: see .artifacts/protocol/{name}.log")


async def main() -> None:
    bins = ROOT / "tools" / "qualification" / "node_modules" / ".bin"
    if not (bins / "conformance").exists():
        raise SystemExit("Run npm ci --prefix tools/qualification first (Node 22+).")
    baseline = ROOT / "tools" / "qualification" / "expected-failures.yaml"
    await run(
        "inspector-stdio",
        str(bins / "mcp-inspector"),
        "--cli",
        sys.executable,
        SERVER,
        "--method",
        "tools/list",
        "--protocol-era",
        "modern",
        "--format",
        "json",
    )
    async with http_server() as url:
        await run(
            "inspector-http",
            str(bins / "mcp-inspector"),
            "--cli",
            url + "/mcp",
            "--method",
            "tools/call",
            "--tool-name",
            "dine_status",
            "--protocol-era",
            "modern",
            "--format",
            "json",
        )
        for scenario in SCENARIOS:
            await run(
                scenario,
                str(bins / "conformance"),
                "server",
                "--url",
                url + "/mcp",
                "--scenario",
                scenario,
                "--expected-failures",
                str(baseline),
                "--output-dir",
                str(ROOT / ".artifacts" / "protocol" / scenario),
            )
    (ROOT / ".artifacts" / "protocol" / "summary.json").write_text(
        json.dumps({"scenarios": SCENARIOS, "expected_failures": [], "passed": True}, indent=2) + "\n"
    )


if __name__ == "__main__":
    asyncio.run(main())
