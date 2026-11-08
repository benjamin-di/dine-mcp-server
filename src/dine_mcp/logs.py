"""JSON-lines logging to stderr and one log line per tool call.

Lines are flat JSON objects (``ts``, ``level``, ``service``, ``component``, ``msg`` plus the
record's extra fields), so they stay greppable in ``docker logs``. ``LOG_LEVEL`` sets the
level (default INFO). Tool arguments are never logged, only a short hash per argument.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import sys
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware
from rich.logging import RichHandler

from .controls import AUDIT, TRACE
from .identity import log_context

_RESERVED = frozenset(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str, original: logging.Formatter | None = None) -> None:
        super().__init__()
        self.service = service
        self.original = original

    def format(self, record: logging.LogRecord) -> str:
        if self.original is not None:
            rendered = self.original.format(record)
            try:
                structured = json.loads(rendered)
            except (ValueError, TypeError):
                structured = None
            retained = structured if isinstance(structured, dict) else {"msg": rendered}
            context = {"level": record.levelname, "component": record.name, **log_context()}
            for name in context:
                if name in retained:
                    displaced = name + "_"
                    while displaced in retained:
                        displaced += "_"
                    retained[displaced] = retained[name]
            return json.dumps({**retained, **context}, ensure_ascii=False)
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname,
            **log_context(),
            "component": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _RESERVED or key.startswith("_"):
                continue
            # event() carries its fields in one ``extras`` dict; flatten it.
            items = value.items() if key == "extras" and isinstance(value, dict) else [(key, value)]
            for name, item in items:
                if name in {"service", "version", "source_revision", "instance"}:
                    continue
                try:
                    json.dumps(item)
                    payload[name] = item
                except TypeError:
                    payload[name] = repr(item)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def _native_console_dispatch(console: Any, render: Any, hook: Any) -> Any:
    """Scope render provenance to this thread without replacing print dispatch."""
    from contextlib import contextmanager
    from threading import local

    @contextmanager
    def dispatch() -> Any:
        key = "_build_identity_render_dispatch"
        with console._lock:
            state = vars(console).get(key)
            if state is None:
                missing = object()
                state = {
                    "local": local(),
                    "users": 0,
                    "render": console.render,
                    "saved_render": vars(console).get("render", missing),
                    "missing": missing,
                    "hooks": console._render_hooks,
                }

                def current() -> Any:
                    stack = getattr(state["local"], "stack", ())
                    return stack[-1] if stack else None

                def dispatch_render(*args: Any, **kwargs: Any) -> Any:
                    context = current()
                    if context is None:
                        yield from state["render"](*args, **kwargs)
                        return
                    stack = state["local"].stack
                    stack.append(None)
                    try:
                        yield from context[0](*args, **kwargs)
                    finally:
                        stack.pop()

                class DispatchHook:
                    def __init__(self, original: Any) -> None:
                        self.original = original

                    def process_renderables(self, renderables: list[Any]) -> list[Any]:
                        context = current()
                        target = self.original
                        if context is not None:
                            target = context[1](target)
                        return target.process_renderables(renderables)

                class DispatchHooks:
                    def __getattr__(self, name: str) -> Any:
                        return getattr(state["hooks"], name)

                    def __getitem__(self, index: Any) -> Any:
                        items = state["hooks"][index]
                        if current() is None:
                            return items
                        if isinstance(index, slice):
                            return [DispatchHook(item) for item in items]
                        return DispatchHook(items)

                    def __len__(self) -> int:
                        return len(state["hooks"])

                    def __iter__(self) -> Any:
                        return iter(self[:])

                state["dispatch_render"] = dispatch_render
                state["dispatch_hooks"] = DispatchHooks()
                console.render = dispatch_render
                console._render_hooks = state["dispatch_hooks"]
                vars(console)[key] = state
            state["users"] += 1
            stack = getattr(state["local"], "stack", None)
            if stack is None:
                stack = state["local"].stack = []
            stack.append((render, hook))
        try:
            yield
        finally:
            with console._lock:
                stack.pop()
                state["users"] -= 1
                if not state["users"]:
                    if console.render is state["dispatch_render"]:
                        if state["saved_render"] is state["missing"]:
                            del console.render
                        else:
                            console.render = state["saved_render"]
                    if console._render_hooks is state["dispatch_hooks"]:
                        console._render_hooks = state["hooks"]
                    del vars(console)[key]

    return dispatch()


class _RichSink(logging.Handler):
    """Enrich only the rendering retained by Rich's traceback and console policy."""

    def __init__(self, original: RichHandler, service: str) -> None:
        super().__init__(original.level)
        self.original = original
        self.filters = list(original.filters)
        self.setFormatter(original.formatter)
        self.identity_formatter = JsonFormatter(service, logging.Formatter())

    def setFormatter(self, formatter: logging.Formatter | None) -> None:
        super().setFormatter(formatter)
        self.original.setFormatter(formatter)

    def emit(self, record: logging.LogRecord) -> None:
        owner = self
        original: Any = self.original
        original.acquire()
        console = original.console

        class RecordConsole:
            def __getattr__(self, name: str) -> Any:
                return getattr(console, name)

            def __setattr__(self, name: str, value: Any) -> None:
                setattr(console, name, value)

            def __delattr__(self, name: str) -> None:
                delattr(console, name)

            def print(self, *objects: Any, **kwargs: Any) -> None:
                from rich.control import Control, ControlType
                from rich.live import Live
                from rich.live_render import LiveRender
                from rich.segment import Segment

                host_renderables: set[int] = set()
                markers: dict[int, tuple[Any, bool]] = {}

                class Hook:
                    def __init__(self, original_hook: Any) -> None:
                        self.original_hook = original_hook

                    def process_renderables(self, renderables: list[Any]) -> list[Any]:
                        incoming = {id(item) for item in renderables}
                        result = self.original_hook.process_renderables(renderables)
                        if isinstance(self.original_hook, Live):
                            host_renderables.update(
                                id(item)
                                for item in result
                                if id(item) not in incoming and isinstance(item, (Control, LiveRender))
                            )
                        return result

                def marker(owned: bool) -> Any:
                    # Fresh tuple identity carries provenance through native cropping.
                    controls = [(ControlType.BELL,)]
                    token = tuple(controls)
                    markers[id(token)] = (token, owned)
                    return Segment("", None, token)

                def render(renderable: Any, options: Any = None) -> Any:
                    yield marker(id(renderable) not in host_renderables)
                    yield from console.render(renderable, options)
                    yield marker(True)

                with console:
                    # Rich owns a thread-local buffer; isolate this print from host
                    # output and restore it even if a hook or renderer raises.
                    prefix = console._buffer[:]
                    console._buffer.clear()
                    try:
                        with _native_console_dispatch(console, render, Hook):
                            console.print(*objects, **kwargs)
                        raw = console._buffer[:]
                    finally:
                        console._buffer[:] = prefix
                    groups: list[tuple[bool, list[Any]]] = []
                    owned = True
                    for segment in raw:
                        provenance = markers.get(id(segment.control))
                        if provenance is not None:
                            owned = provenance[1]
                        elif groups and groups[-1][0] == owned:
                            groups[-1][1].append(segment)
                        else:
                            groups.append((owned, [segment]))
                    for owned, segments in groups:
                        if not owned:
                            console._buffer.extend(segments)
                            continue
                        rendered = console._render_buffer(segments)
                        if not rendered:
                            continue
                        retained = logging.makeLogRecord(
                            {
                                **record.__dict__,
                                "msg": rendered.rstrip("\n"),
                                "args": (),
                                "exc_info": None,
                                "exc_text": None,
                                "stack_info": None,
                            }
                        )
                        attributed = owner.identity_formatter.format(retained)
                        try:
                            structured = json.loads(attributed)
                        except (ValueError, TypeError):
                            structured = None
                        if getattr(owner.identity_formatter, "tty", False) and not isinstance(structured, dict):
                            # These formatters declare a constant prefix
                            # for every native line.
                            native_line = (rendered.rstrip("\n").splitlines() or [""])[0]
                            attributed_line = attributed.splitlines()[0]
                            header = (
                                attributed_line[: len(attributed_line) - len(native_line)]
                                if native_line
                                else attributed_line
                            )
                            retained_segments = segments[:]
                            for index in range(len(retained_segments) - 1, -1, -1):
                                segment = retained_segments[index]
                                if segment.control:
                                    continue
                                trimmed = segment.text.rstrip("\n")
                                retained_segments[index] = Segment(trimmed, segment.style)
                                if trimmed or trimmed == segment.text:
                                    break
                            line_start = True
                            for segment in retained_segments:
                                if segment.control:
                                    console._buffer.append(segment)
                                    continue
                                for part in segment.text.splitlines(keepends=True):
                                    if line_start:
                                        console._buffer.append(Segment(header))
                                    console._buffer.append(Segment(part, segment.style))
                                    line_start = part.endswith("\n")
                            console._buffer.append(Segment.line())
                        else:
                            from rich.ansi import AnsiDecoder

                            text = AnsiDecoder().decode_line(attributed + "\n")
                            text.end = ""
                            console._buffer.extend(
                                console.render(text, console.options.update(no_wrap=True, overflow="ignore"))
                            )

        try:
            original.console = RecordConsole()
            original.emit(record)
        finally:
            original.console = console
            original.release()


def setup(service: str) -> None:
    """Send every log line to stderr as JSON (stdout stays free for the stdio transport)."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter(service))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    level = os.environ.get("LOG_LEVEL", "INFO").strip().upper() or "INFO"
    if level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        level = "INFO"
    root.setLevel(level)
    # Keep independent SDK sinks' thresholds, filters and destinations.
    replacements: dict[logging.Handler, logging.Handler] = {}
    for name, logger in logging.Logger.manager.loggerDict.copy().items():
        if not isinstance(logger, logging.Logger) or name.split(".")[0] not in {
            "dine_mcp",
            "fastmcp",
            "mcp",
            "uvicorn",
        }:
            continue
        for index, existing in enumerate(logger.handlers):
            target = replacements.get(existing, existing)
            if isinstance(target, RichHandler):
                replacements[existing] = target = _RichSink(target, service)
            logger.handlers[index] = target
            if not isinstance(target, _RichSink) and not isinstance(target.formatter, JsonFormatter):
                target.setFormatter(JsonFormatter(service, target.formatter or logging.Formatter()))
    # Libraries log what they request (venue names, coordinates, ids) at INFO; tool
    # arguments are not logged, so it speaks only to warn.
    for noisy in ("uvicorn.access", "httpx", "httpcore", "maps_mcp"):
        logging.getLogger(noisy).setLevel(max(logging.WARNING, root.level))


def event(logger: logging.Logger, message: str, **fields: Any) -> None:
    """One structured line; the fields become flat keys."""
    logger.info(message, extra={"extras": fields})


# A per-process key: the short hashes correlate calls within one run, and a dictionary of
# likely values (coordinates, place names) cannot reverse them.
_HASH_KEY = os.urandom(16)


def person_hash(person: str) -> str:
    """A short keyed hash of a person id: correlates one person's calls within a run, reveals nothing."""
    return hmac.new(_HASH_KEY, person.encode("utf-8"), hashlib.sha256).hexdigest()[:10]


def _argument_hashes(arguments: dict[str, Any]) -> dict[str, str]:
    """A short keyed hash per argument: enough to correlate calls, never the value."""
    return {
        name: hmac.new(
            _HASH_KEY, json.dumps(value, default=repr, ensure_ascii=False).encode("utf-8", "replace"), hashlib.sha256
        ).hexdigest()[:8]
        for name, value in arguments.items()
    }


class ToolCallLog(Middleware):
    """One line when a tool starts and one when it ends (status, duration_ms).

    A ToolError the tool raised itself is its classified answer: one line at the error's own
    ``log_level`` (capped at WARNING), without its text, which may echo the input. A ToolError
    wrapping a crash (``__cause__``) or any other exception logs ERROR with the traceback.
    """

    def __init__(self) -> None:
        self.log = logging.getLogger("mcp")

    async def on_call_tool(self, context: Any, call_next: Any) -> Any:
        tool = getattr(context.message, "name", "<unknown>")
        arguments = getattr(context.message, "arguments", {}) or {}
        fields: dict[str, Any] = {"tool": tool, "req_id": uuid.uuid4().hex[:8]}
        started = time.monotonic()
        meta = getattr(context.message, "meta", None)
        if meta is not None and hasattr(meta, "model_dump"):
            meta = meta.model_dump(by_alias=True)
        trace = (meta or {}).get("traceparent") if isinstance(meta, dict) else None
        if (
            not isinstance(trace, str)
            or not re.fullmatch(r"00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}", trace)
            or trace[3:35] == "0" * 32
            or trace[36:52] == "0" * 16
        ):
            trace = None
        trace_token = TRACE.set(trace)
        if trace is not None:
            fields["traceparent"] = trace
        attribution: dict[str, Any] = {"person": None, "outcome": "ok"}
        audit_token = AUDIT.set(attribution)
        self.log.info("tool start", extra={**fields, "state": "start", "params": _argument_hashes(arguments)})

        def end(status: str, **extra: Any) -> dict[str, Any]:
            return {
                **fields,
                "state": "end",
                "status": status,
                **attribution,
                "duration_ms": _ms(started),
                **extra,
            }

        try:
            try:
                result = await call_next(context)
            except ToolError as exc:
                if exc.__cause__ is not None:
                    self.log.exception("tool error", extra=end("error"))
                    raise
                level = min(getattr(exc, "log_level", None) or logging.WARNING, logging.WARNING)
                self.log.log(level, "tool error", extra=end("error", error_type=type(exc).__name__))
                raise
            except asyncio.CancelledError:
                attribution["outcome"] = "cancelled"
                self.log.info("tool cancelled", extra=end("cancelled"))
                raise
            except Exception:
                attribution["outcome"] = "internal"
                self.log.exception("tool error", extra=end("error"))
                raise
            failed = bool(getattr(result, "is_error", False))
            self.log.log(
                logging.WARNING if failed else logging.INFO,
                "tool error" if failed else "tool ok",
                extra=end("error" if failed else "ok"),
            )
            return result
        finally:
            TRACE.reset(trace_token)
            AUDIT.reset(audit_token)


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def diagnostic(message: str) -> None:
    record = logging.LogRecord("dine", logging.ERROR, "", 0, message, (), None)
    print(JsonFormatter("dine").format(record), file=sys.stderr)
