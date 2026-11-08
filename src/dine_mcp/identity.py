"""Offline identity of the installed artifact, separate from its running process."""

from __future__ import annotations

import html
import json
import os
import re
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from uuid import uuid4

from . import __version__


@dataclass(frozen=True)
class BuildIdentity:
    service: str
    version: str
    source_revision: str


def load(path: Path | None = None) -> BuildIdentity:
    try:
        installed = version("dine-mcp")
    except PackageNotFoundError:
        installed = __version__
    artifact = path if path is not None else Path(__file__).with_name("build_identity.json")
    try:
        data = json.loads(artifact.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return BuildIdentity("dine", installed, "unknown")
    if (
        not isinstance(data, dict)
        or set(data) != {"service", "version", "source_revision"}
        or any(not isinstance(value, str) for value in data.values())
        or data["service"] != "dine"
        or data["version"] != installed
        or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", data["source_revision"])
    ):
        raise ValueError("Invalid embedded application build identity")
    return BuildIdentity(**data)


BUILD = load()


_INSTANCE = uuid4().hex


def _after_fork() -> None:
    global _INSTANCE
    _INSTANCE = uuid4().hex


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork)


def log_context() -> dict[str, str]:
    return {
        "service": BUILD.service,
        "version": BUILD.version,
        "source_revision": BUILD.source_revision,
        "instance": _INSTANCE,
    }


def render_app(template: str, build: BuildIdentity = BUILD) -> str:
    replacements = {
        "__DINE_VERSION__": html.escape(build.version, quote=True),
        "__DINE_REVISION__": html.escape(build.source_revision[:12], quote=True),
        "__DINE_REVISION_FULL__": html.escape(build.source_revision, quote=True),
    }
    return re.sub(r"__DINE_(?:VERSION|REVISION|REVISION_FULL)__", lambda match: replacements[match[0]], template)
