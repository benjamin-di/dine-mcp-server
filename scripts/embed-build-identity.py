#!/usr/bin/env python3
"""Validate publisher inputs before embedding the identity in the installed package."""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path


def embed(root: Path, version: str, revision: str) -> None:
    installed = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    release = version.removeprefix("v")
    if release != installed:
        raise ValueError("VERSION must match the application version in pyproject.toml")
    if not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", revision):
        raise ValueError("VCS_REF must be a full hexadecimal source revision")
    (root / "src/dine_mcp/build_identity.json").write_text(
        json.dumps({"service": "dine", "version": release, "source_revision": revision.lower()}) + "\n"
    )


if __name__ == "__main__":
    embed(Path.cwd(), *sys.argv[1:])
