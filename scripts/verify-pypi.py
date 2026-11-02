#!/usr/bin/env python3
"""Require anonymous PyPI metadata to match the exact tested distributions."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import urllib.request
from pathlib import Path
from typing import Any


def distribution_hashes(folder: Path, version: str) -> dict[str, str]:
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError("A stable release version is required")
    names = {f"dine_mcp-{version}-py3-none-any.whl", f"dine_mcp-{version}.tar.gz"}
    actual = {p.name for p in folder.iterdir() if p.name.endswith((".whl", ".tar.gz"))}
    if actual != names or any((folder / name).is_symlink() for name in names):
        raise ValueError("Expected exactly this release's wheel and source distribution")
    return {name: hashlib.sha256((folder / name).read_bytes()).hexdigest() for name in sorted(names)}


def verify(metadata: Any, version: str, expected: dict[str, str]) -> None:
    if not isinstance(metadata, dict) or not isinstance(metadata.get("info"), dict):
        raise ValueError("Invalid PyPI metadata")
    if metadata["info"].get("version") != version or metadata["info"].get("name") != "dine-mcp":
        raise ValueError("PyPI package identity mismatch")
    rows = metadata.get("urls")
    if not isinstance(rows, list):
        raise ValueError("Missing PyPI file metadata")
    if any(not isinstance(row, dict) or not isinstance(row.get("filename"), str) for row in rows):
        raise ValueError("Malformed PyPI file metadata")
    if len(rows) != len(expected) or {row["filename"] for row in rows} != set(expected):
        raise ValueError("Published file set differs from the tested distributions")
    for name, digest in expected.items():
        matches = [row for row in rows if isinstance(row, dict) and row.get("filename") == name]
        if len(matches) != 1:
            raise ValueError("Missing or duplicate PyPI distribution")
        row = matches[0]
        if row.get("yanked") is not False or not isinstance(row.get("digests"), dict):
            raise ValueError("Unusable PyPI distribution")
        if row["digests"].get("sha256") != digest:
            raise ValueError("Published distribution differs from the tested bytes")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    expected = distribution_hashes(args.dist, args.version)
    url = f"https://pypi.org/pypi/dine-mcp/{args.version}/json"
    with urllib.request.urlopen(url, timeout=20) as response:
        body = response.read(2_000_001)
    if len(body) > 2_000_000:
        raise ValueError("PyPI metadata exceeded its limit")
    verify(json.loads(body), args.version, expected)
    print("Verified anonymous PyPI hashes for both tested distributions")


if __name__ == "__main__":
    main()
