"""HTTP authority validation and a bounded request body before JSON parsing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from fastmcp import FastMCP
from starlette.responses import JSONResponse


@dataclass(frozen=True)
class HttpPolicy:
    hosts: tuple[str, ...] = ("localhost", "127.0.0.1", "[::1]")
    origins: tuple[str, ...] = ()
    max_body: int = 1_000_000

    @classmethod
    def from_env(cls, env: Any) -> HttpPolicy:
        hosts = tuple(
            h.strip() for h in env.get("DINE_HTTP_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]").split(",") if h.strip()
        )
        origins = tuple(o.strip() for o in env.get("DINE_HTTP_ALLOWED_ORIGINS", "").split(",") if o.strip())
        if not hosts or any(any(c in h for c in "/*@?#\\") for h in hosts):
            raise ValueError("DINE_HTTP_ALLOWED_HOSTS needs explicit host names (no wildcard or URL)")
        for origin in origins:
            parsed = urlsplit(origin)
            if (
                parsed.scheme not in ("http", "https")
                or not parsed.netloc
                or parsed.path
                or parsed.query
                or parsed.fragment
                or parsed.username
            ):
                raise ValueError("DINE_HTTP_ALLOWED_ORIGINS needs exact HTTP origins")
        return cls(hosts, origins)


class RequestBoundary:
    def __init__(self, app: Any, policy: HttpPolicy) -> None:
        self.app, self.policy = app, policy

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        pairs = scope.get("headers", [])
        if any(sum(1 for key, _ in pairs if key == name) > 1 for name in (b"host", b"origin", b"content-length")):
            await JSONResponse({"error": "ambiguous_headers"}, status_code=400)(scope, receive, send)
            return
        headers = dict(pairs)
        host = headers.get(b"host", b"").decode("latin1").lower()
        # Do not infer trust from bind address, forwarded headers or the incoming origin.
        allowed = {h.lower() for h in self.policy.hosts}
        try:
            authority = urlsplit("//" + host)
            name = authority.hostname or ""
            if ":" in name:
                name = f"[{name}]"
            port = authority.port
            valid_host = (
                not authority.username
                and not authority.password
                and not authority.path
                and not authority.query
                and not authority.fragment
                and host == name + (f":{port}" if port is not None else "")
            )
        except ValueError:
            valid_host = False
            name = ""
        origin = headers.get(b"origin", b"").decode("latin1")
        valid_origin = (
            not origin or origin in self.policy.origins or origin == f"{scope.get('scheme', 'http')}://{host}"
        )
        if not host or not valid_host or not (host in allowed or name in allowed) or not valid_origin:
            await JSONResponse({"error": "untrusted_host_or_origin"}, status_code=403)(scope, receive, send)
            return
        if b"content-length" in headers:
            length = headers[b"content-length"]
            if not length.isdigit():
                await JSONResponse({"error": "invalid_content_length"}, status_code=400)(scope, receive, send)
                return
            if len(length) > 12 or int(length) > self.policy.max_body:
                await JSONResponse({"error": "request_too_large"}, status_code=413)(scope, receive, send)
                return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > self.policy.max_body:
                await JSONResponse({"error": "request_too_large"}, status_code=413)(scope, receive, send)
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break
        delivered = False

        async def replay() -> Any:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


class DineMCP(FastMCP):
    def __init__(self, *args: Any, http_policy: HttpPolicy, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.http_policy = http_policy

    def http_app(self, *args: Any, **kwargs: Any) -> Any:
        kwargs.update(
            host_origin_protection=True, allowed_hosts=self.http_policy.hosts, allowed_origins=self.http_policy.origins
        )
        app = super().http_app(*args, **kwargs)
        app.add_middleware(RequestBoundary, policy=self.http_policy)
        return app
