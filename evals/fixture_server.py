"""dine-mcp over stdio on the synthetic world (evals/catalog.py). Juniper's provider is blocked.

python evals/fixture_server.py            # used by evals/run.py through an MCP config
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import httpx  # noqa: E402
from catalog import curated, places, providers  # noqa: E402

from dine_mcp.composition import build_app  # noqa: E402
from dine_mcp.server import create_server  # noqa: E402


def serve(state: str) -> None:
    fakes = providers()
    refuse = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(599)))
    app = build_app(
        {"DINE_STATE_DIR": state},
        client=refuse,
        place_source=places(),
        providers=fakes,
    )
    app.service.resolver.curated = curated()
    create_server(app, stdio=True).run(transport="stdio", show_banner=False)


def main() -> None:
    state = os.environ.get("DINE_EVAL_STATE")
    if state:
        serve(state)
    else:
        with tempfile.TemporaryDirectory(prefix="dine-eval-") as temporary:
            serve(temporary)


if __name__ == "__main__":
    main()
