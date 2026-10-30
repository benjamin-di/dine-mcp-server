"""Operator scheduler entry point. Emit a bounded status report; never register a schedule.

DINE_STATE_DIR=/private/persistent/state uv run python scripts/drift-report.py
Each eligible provider uses ordinary pacing/budgets and the durable 15-minute cooldown.
"""

from __future__ import annotations

import asyncio
import json
import os

from dine_mcp.composition import build_app


async def main() -> int:
    if not os.environ.get("DINE_STATE_DIR"):
        raise SystemExit("Set DINE_STATE_DIR to persistent monitoring state")
    app = build_app({**os.environ, "DINE_PLACES": "none"})
    try:
        report = await app.service.checked_status(app.transports, "monitor")
        print(json.dumps(report, separators=(",", ":")))
        eligible = [p for p in report["providers"] if p["enabled"] and p["access_mode"] == "public"]
        if any(p.get("check", {}).get("status") not in (None, "ok") for p in eligible):
            return 1
        return 2 if any("check" not in p for p in eligible) else 0
    finally:
        await app.client.aclose()
        app.store.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
