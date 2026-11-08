"""Artifact identity across publisher, installed resource, logs and real MCP stdio."""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import zipfile
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastmcp import Client

from dine_mcp import __version__, logs
from dine_mcp.identity import BUILD, BuildIdentity, load, log_context, render_app
from dine_mcp.server import create_server

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("embed_dine_identity", ROOT / "scripts/embed-build-identity.py")
assert spec and spec.loader
embedder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(embedder)


@pytest.mark.parametrize("length", [40, 64])
def test_publisher_embeds_normalized_identity(tmp_path, length):
    (tmp_path / "src/dine_mcp").mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text(f'[project]\nversion = "{__version__}"\n')
    embedder.embed(tmp_path, "v" + __version__, "A" * length)
    artifact = tmp_path / "src/dine_mcp/build_identity.json"
    assert load(artifact) == BuildIdentity("dine", __version__, "a" * length)


@pytest.mark.parametrize(
    ("release", "revision"),
    [
        ("", "a" * 40),
        ("sha-deadbeef", "a" * 40),
        ("code-qualification", "a" * 40),
        (__version__, "unknown"),
        (__version__, "a" * 12),
        (__version__, "x" * 40),
    ],
)
def test_publisher_refuses_missing_or_conflicting_identity(tmp_path, release, revision):
    (tmp_path / "src/dine_mcp").mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text(f'[project]\nversion = "{__version__}"\n')
    with pytest.raises(ValueError):
        embedder.embed(tmp_path, release, revision)
    assert not (tmp_path / "src/dine_mcp/build_identity.json").exists()


@pytest.mark.parametrize(
    "change",
    [
        {"service": "other"},
        {"version": "wrong"},
        {"source_revision": "unknown"},
        {"source_revision": None},
        {"instance": "not-artifact-metadata"},
    ],
)
def test_installed_identity_rejects_invalid_metadata(tmp_path, change):
    artifact = tmp_path / "identity.json"
    artifact.write_text(json.dumps({"service": "dine", "version": __version__, "source_revision": "a" * 40, **change}))
    with pytest.raises(ValueError):
        load(artifact)


def test_source_development_identity_cannot_be_relabelled_by_environment(tmp_path, monkeypatch):
    for name in ("VERSION", "VCS_REF", "SOURCE_REVISION", "DINE_VERSION"):
        monkeypatch.setenv(name, "forged")
    assert load(tmp_path / "missing.json") == BuildIdentity("dine", __version__, "unknown")


def test_embedded_identity_is_shipped_in_the_built_wheel(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    for name in ("pyproject.toml", "README.md", "LICENSE", "NOTICE", ".gitignore"):
        shutil.copyfile(ROOT / name, project / name)
    shutil.copytree(ROOT / "src", project / "src", ignore=shutil.ignore_patterns("__pycache__"))
    embedder.embed(project, __version__, "a" * 40)
    subprocess.run(
        ["uv", "build", "--offline", "--no-sources", "--wheel", "--out-dir", str(tmp_path / "dist")],
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    with zipfile.ZipFile(next((tmp_path / "dist").glob("*.whl"))) as wheel:
        assert json.loads(wheel.read("dine_mcp/build_identity.json")) == {
            "service": "dine",
            "version": __version__,
            "source_revision": "a" * 40,
        }


@pytest.mark.parametrize("change", [{}, {"source_revision": "unknown"}, {"version": "wrong"}, {"service": "other"}])
def test_image_smoke_executes_its_metadata_assertion(monkeypatch, change):
    from dine_mcp import identity, server

    # Execute the actual installed-image entrypoint, including the missing-stamp
    # negative, without replacing this acceptance condition with a mock result.
    match = re.search(r"-c '([^']+)'", (ROOT / "scripts/smoke-image.sh").read_text())
    assert match
    monkeypatch.setattr(
        identity,
        "BUILD",
        BuildIdentity(
            **{
                "service": "dine",
                "version": __version__,
                "source_revision": "a" * 40,
                **change,
            }
        ),
    )
    monkeypatch.setattr(server, "create_server", lambda **kwargs: SimpleNamespace(name="dine"))
    monkeypatch.setattr(sys, "argv", ["-c", __version__, "a" * 40])
    code = compile(match[1], "installed-image-smoke", "exec")
    if change:
        with pytest.raises(AssertionError):
            exec(code, {})
    else:
        exec(code, {})


async def test_real_app_resource_uses_installed_identity(make_app):
    async with Client(create_server(make_app(env={"DINE_APPS": "1"}), stdio=True)) as client:
        resource = (await client.read_resource("ui://dine/availability.html"))[0]
    assert f"Dine v{BUILD.version} · {BUILD.source_revision[:12]}" in resource.text
    assert f'title="Source revision: {BUILD.source_revision}"' in resource.text
    assert f"Source revision <code>{BUILD.source_revision}</code>" in resource.text
    assert f'<meta name="dine-version" content="{BUILD.version}">' in resource.text
    assert "__DINE_" not in resource.text


def test_app_identity_escapes_attribute_and_text_contexts():
    template = '<meta content="__DINE_VERSION__"><footer title="__DINE_REVISION_FULL__">__DINE_REVISION__</footer>'
    rendered = render_app(template, BuildIdentity("dine", '"></meta><script>', '"><script>'))
    assert "<script>" not in rendered and "</meta>" not in rendered
    assert "&quot;&gt;&lt;" in rendered


def test_json_identity_is_authoritative_and_preserves_event_fields():
    record = logging.LogRecord("dine.worker", logging.DEBUG, "", 0, "first\nsecond", (), None)
    record.extras = {"version": "forged", "instance": "forged", "duration_ms": 8}
    record.service = "forged"
    payload = json.loads(logs.JsonFormatter("dine").format(record))
    assert {key: payload[key] for key in log_context()} == log_context()
    assert payload["duration_ms"] == 8 and payload["level"] == "DEBUG" and payload["msg"] == "first\nsecond"


def test_sdk_sink_retains_filtering_and_severity():
    names = ("", "fastmcp", "uvicorn", "uvicorn.error", "uvicorn.access", "httpx", "httpcore", "maps_mcp")
    snapshots = [
        (
            logging.getLogger(name),
            list(logging.getLogger(name).handlers),
            logging.getLogger(name).level,
            logging.getLogger(name).propagate,
        )
        for name in names
    ]
    formats = [(handler, handler.formatter) for _, handlers, _, _ in snapshots for handler in handlers]
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setLevel(logging.ERROR)
    handler.addFilter(lambda record: record.getMessage() != "discard")
    sdk = logging.getLogger("fastmcp")
    sdk.handlers = [handler]
    sdk.setLevel(logging.DEBUG)
    sdk.propagate = False
    try:
        logs.setup("dine")
        sdk.debug("too quiet")
        sdk.error("discard")
        sdk.error("retained")
        [payload] = [json.loads(line) for line in stream.getvalue().splitlines()]
        assert payload["level"] == "ERROR" and payload["msg"] == "retained"
        assert {key: payload[key] for key in log_context()} == log_context()
    finally:
        for logger, handlers, level, propagate in snapshots:
            logger.handlers, logger.level, logger.propagate = handlers, level, propagate
        for existing, formatter in formats:
            existing.setFormatter(formatter)


def test_isolated_child_formatter_redaction_and_typed_fields_remain_authoritative():
    class Closed(logging.Formatter):
        def format(self, record):
            return json.dumps(
                {
                    "msg": "sanitized",
                    "count": 7,
                    "flag": False,
                    "service": {"previous": True},
                    "level": "DEBUG",
                    "component": "captured",
                    "level_": 9,
                }
            )

    logger = logging.getLogger("fastmcp.synthetic.identity")
    previous = list(logger.handlers), logger.level, logger.propagate
    root = logging.getLogger()
    root_state = list(root.handlers), root.level
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(Closed())
    logger.handlers, logger.level, logger.propagate = [handler], logging.DEBUG, False
    try:
        logs.setup("dine")
        logger.error("private raw message", extra={"private_extra": "omitted"})
        event = json.loads(stream.getvalue())
        assert event["msg"] == "sanitized" and event["count"] == 7 and event["flag"] is False
        assert event["service_"] == {"previous": True}
        assert event["level"] == "ERROR" and event["component"] == logger.name
        assert event["level_"] == 9 and event["level__"] == "DEBUG" and event["component_"] == "captured"
        assert {key: event[key] for key in log_context()} == log_context()
        assert "private" not in stream.getvalue() and "omitted" not in stream.getvalue()
    finally:
        logger.handlers, logger.level, logger.propagate = previous
        root.handlers, root.level = root_state


@pytest.mark.parametrize("protocol", ["2025-03-26", "2025-11-25"])
def test_real_stdio_has_native_version_and_only_protocol_stdout(tmp_path, protocol):
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": protocol,
            "capabilities": {},
            "clientInfo": {"name": "synthetic", "version": "1"},
        },
    }
    result = subprocess.run(
        [sys.executable, "-m", "dine_mcp.server", "--transport", "stdio"],
        input=json.dumps(request) + "\n",
        text=True,
        capture_output=True,
        timeout=30,
        env={"PATH": os.environ["PATH"], "DINE_STATE_DIR": str(tmp_path), "DINE_PLACES": "none", "LOG_LEVEL": "DEBUG"},
    )
    assert result.returncode == 0, result.stderr
    response = json.loads(result.stdout)
    assert response["result"]["serverInfo"]["version"] == BUILD.version
    records = [json.loads(line) for line in result.stderr.splitlines()]
    assert records
    assert all(
        record["version"] == BUILD.version and record["source_revision"] == BUILD.source_revision for record in records
    )


def test_missing_bearer_startup_error_has_build_identity(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "dine_mcp.server"],
        text=True,
        capture_output=True,
        timeout=30,
        env={"PATH": os.environ["PATH"], "DINE_STATE_DIR": str(tmp_path), "DINE_PLACES": "none"},
    )
    assert result.returncode == 1 and not result.stdout
    [record] = [json.loads(line) for line in result.stderr.splitlines()]
    assert record["level"] == "ERROR" and record["version"] == BUILD.version
    assert "MCP_BEARER_TOKEN" in record["msg"]
