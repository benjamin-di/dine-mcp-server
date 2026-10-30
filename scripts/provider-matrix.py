"""Generate the README's provider table; --check refuses documentation drift."""

from __future__ import annotations

import argparse
from pathlib import Path

from dine_mcp.definitions import load_providers

START = "<!-- providers:start -->"
END = "<!-- providers:end -->"
ROOT = Path(__file__).resolve().parents[1]


def render() -> str:
    rows = ["| Provider | Countries | Mode | Maturity | Last verified live |", "|---|---|---|---|---|"]
    for provider in sorted(load_providers().values(), key=lambda p: p.name):
        rows.append(
            f"| {provider.name} | {', '.join(provider.countries)} | {provider.access_mode.value} | "
            f"{provider.maturity.value} | {provider.last_verified or 'unverified'} |"
        )
    return "\n".join(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    path = ROOT / "README.md"
    before = path.read_text()
    prefix, rest = before.split(START, 1)
    _, suffix = rest.split(END, 1)
    after = prefix + START + "\n" + render() + "\n" + END + suffix
    if args.check:
        if before != after:
            raise SystemExit("Provider matrix differs: run uv run python scripts/provider-matrix.py")
    else:
        path.write_text(after)


if __name__ == "__main__":
    main()
