"""The safety boundary: what may reach the network, and that nothing writes.

A failure here is a design change to review, never a test to update casually.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from dine_mcp.definitions import load_providers
from dine_mcp.providers import REGISTRY
from dine_mcp.transport import WEBSITE_BODY_LIMIT, WEBSITE_POLICY, Operation

SRC = Path(__file__).parents[1] / "src" / "dine_mcp"
NETWORK = {"httpx", "requests", "urllib.request", "aiohttp", "socket", "http.client", "curl_cffi", "playwright"}
# Modules allowed to import network code: the transport, the composition root that builds its client,
# the CLI's local health probe, and the Google place source (through maps-mcp's library).
ALLOWED = {"transport.py", "composition.py", "cli.py", "places_google.py"}
WRITE_WORDS = re.compile(r"(book|confirm|cancel|checkout|pay|hold|lock|submit|create|update|delete)", re.I)


def imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


def test_only_allowed_modules_import_network_code():
    for path in SRC.rglob("*.py"):
        reached = {name for name in imports(path) if name.split(".")[0] in {n.split(".")[0] for n in NETWORK}}
        reached = {name for name in reached if any(name == n or name.startswith(n + ".") for n in NETWORK)}
        if reached:
            assert path.name in ALLOWED, f"{path.relative_to(SRC)} imports {sorted(reached)}"


def test_providers_reach_the_network_only_through_their_transport():
    for path in (SRC / "providers").glob("*.py"):
        assert not imports(path) & NETWORK, path.name


def test_registry_matches_definitions():
    assert set(REGISTRY) == set(load_providers())


@pytest.mark.parametrize("key", sorted(REGISTRY))
def test_provider_operations_are_reads_on_their_own_hosts(key):
    provider = REGISTRY[key]()
    definition = load_providers()[key]
    assert provider.operations, key
    for name, op in provider.operations.items():
        assert isinstance(op, Operation) and op.name == name
        assert op.host in definition.hosts
        assert op.method in ("GET", "POST")
        if op.method == "POST":
            assert op.post_reason, f"{key}.{name} must say why a POST is a read"
        # No booking, payment, profile or messaging endpoint, by name or by path.
        assert not WRITE_WORDS.search(op.name), f"{key}.{name}"
        assert not WRITE_WORDS.search(op.path.pattern), f"{key}.{name}: {op.path.pattern}"


def test_operation_rejects_unexplained_post():
    with pytest.raises(ValueError):
        Operation("x", "POST", "h", re.compile("/"))
    with pytest.raises(ValueError):
        Operation("x", "DELETE", "h", re.compile("/"))


def test_no_tool_argument_reaches_a_fetch():
    """The model's url is only pattern-matched: the service never passes it to a transport."""
    service = (SRC / "service.py").read_text(encoding="utf-8")
    assert "request(" not in service
    assert "links.match(" in service and "links.clean(" in service


def test_website_discovery_is_a_separate_bounded_read_policy():
    assert WEBSITE_BODY_LIMIT == 512_000
    assert WEBSITE_POLICY.hosts == ()  # selected only from a current place-source website
    assert WEBSITE_POLICY.daily_request_budget == 50 and WEBSITE_POLICY.person_daily_share == 0.5
    assert WEBSITE_POLICY.request_interval_seconds >= 1
    # Neither new tool inputs nor another network-importing module enable discovery.
    assert not imports(SRC / "widgets.py") & NETWORK
