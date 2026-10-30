"""The only module that talks to booking providers.

Every request names one of its provider's pinned operations (method, host, path); anything else is
refused before it leaves. Redirects are followed by hand and each hop is checked again. Per provider:
one request at a time, a minimum interval between requests, a UTC daily budget shared by everyone
behind this server's address plus each person's share of it, and a breaker that survives restarts.
A challenge or bot block opens the breaker; it is never retried, solved or worked around.
"""

from __future__ import annotations

import asyncio
import email.utils
import ipaddress
import logging
import re
import socket
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import httpx

from .controls import CURRENT, TRACE
from .definitions import ProviderDefinition, load_providers
from .links import TRACKING, host_allowed
from .models import AccessMode, Maturity, VenueStatus
from .state import ALL, Store

log = logging.getLogger("dine.transport")

MAX_BODY = 8_000_000  # SevenRooms answers up to a few MB for three days at a busy venue
MAX_REDIRECTS = 3
TIMEOUT = httpx.Timeout(15.0, connect=10.0)
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)
# Bodies that mean "a bot check answered instead of the provider".
CHALLENGE = re.compile(
    rb"(cf-chl|challenge-platform|captcha-delivery|datadome|px-captcha|_incapsula_resource|"
    rb"Attention Required! \| Cloudflare|Just a moment\.\.\.|Access Denied)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Operation:
    """A request a provider adapter may make. Reads only: POST is allowed solely for read endpoints
    that take their query as a body, and must say why."""

    name: str
    method: str
    host: str
    path: re.Pattern[str]
    post_reason: str | None = None

    def __post_init__(self) -> None:
        if self.method not in ("GET", "POST"):
            raise ValueError(f"{self.name}: only GET and POST are allowed")
        if self.method == "POST" and not self.post_reason:
            raise ValueError(f"{self.name}: a POST operation must state why it is a read")


class ProviderError(Exception):
    """A provider-level outcome that ends this venue's check; never a whole-call error."""

    def __init__(self, status: VenueStatus, log_detail: str = "", *, retry_at: float | None = None) -> None:
        super().__init__(status.value)
        self.status = status
        self.log_detail = log_detail
        self.retry_at = retry_at


class Transport:
    """Requests for one provider."""

    def __init__(
        self,
        definition: ProviderDefinition,
        operations: dict[str, Operation],
        store: Store,
        client: httpx.AsyncClient,
        *,
        user_agent: str = DEFAULT_USER_AGENT,
        clock: Any = time.monotonic,
        sleep: Any = asyncio.sleep,
        max_body: int | None = None,
        timeout: httpx.Timeout = TIMEOUT,
        credential_free: bool = False,
    ) -> None:
        for op in operations.values():
            if op.host not in definition.hosts:
                raise ValueError(f"{definition.key}.{op.name}: host {op.host} is not in the definition's hosts")
        self.definition = definition
        self.operations = operations
        self.store = store
        self.client = client
        self.user_agent = user_agent
        self._turns = FairQueue()
        self._last = 0.0
        self._clock = clock
        self._sleep = sleep
        self.max_body = MAX_BODY if max_body is None else max_body
        self.timeout = timeout
        self.credential_free = credential_free

    @property
    def key(self) -> str:
        return self.definition.key

    def _check(self, op: Operation, method: str, url: str) -> None:
        parts = urlsplit(url)
        if (
            parts.scheme != "https"
            or method != op.method
            or (parts.hostname or "").lower() != op.host
            or parts.username is not None
            or parts.password is not None
            or parts.port not in (None, 443)
            or not op.path.fullmatch(parts.path or "/")
        ):
            raise ProviderError(VenueStatus.UNAVAILABLE, f"{self.key}.{op.name}: request outside its operation")

    def person_limit(self) -> int:
        return max(1, int(self.definition.daily_request_budget * self.definition.person_daily_share))

    async def request(
        self,
        operation: str,
        url: str,
        person: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        headers: dict[str, str] | None = None,
        accept: tuple[int, ...] = (),
    ) -> httpx.Response:
        """One request through the operation's checks. ``accept`` lists error statuses the adapter
        interprets itself (e.g. a 500 meaning "no such venue")."""
        op = self.operations[operation]
        async with self._turns.turn(person):
            return await self._send(op, url, params, json_body, headers or {}, accept, person)

    async def _admit(self, person: str) -> None:
        breaker = self.store.breaker(self.key)
        if breaker.is_open():
            raise ProviderError(VenueStatus.BLOCKED, "breaker open", retry_at=breaker.open_until)
        wait = self.definition.request_interval_seconds - (self._clock() - self._last)
        if wait > 0:
            await self._sleep(wait)
        # A different process may have opened the persisted breaker while we waited.
        breaker = self.store.breaker(self.key)
        if breaker.is_open():
            raise ProviderError(VenueStatus.BLOCKED, "breaker open", retry_at=breaker.open_until)
        call = CURRENT.get()
        if call is not None:
            call.reserve()
        if not self.store.spend(self.key, person, self.definition.daily_request_budget, self.person_limit()):
            raise ProviderError(VenueStatus.RATE_LIMITED, "daily budget spent")

    async def _send(
        self,
        op: Operation,
        url: str,
        params: dict[str, Any] | None,
        json_body: Any,
        headers: dict[str, str],
        accept: tuple[int, ...] = (),
        person: str = "local",
    ) -> httpx.Response:
        method = op.method
        request_headers = {"User-Agent": self.user_agent, "Accept-Language": "en", "Accept-Encoding": "gzip", **headers}
        if TRACE.get() and not self.credential_free:
            request_headers["traceparent"] = str(TRACE.get())
        if self.credential_free:
            request_headers.update({"Cookie": "", "Authorization": "", "Referer": "", "traceparent": ""})
        for _hop in range(MAX_REDIRECTS + 1):
            self._check(op, method, str(httpx.URL(url, params=params)) if params else url)
            await self._admit(person)
            try:
                async with self.client.stream(
                    method,
                    url,
                    params=params,
                    json=json_body if method == "POST" else None,
                    headers=request_headers,
                    follow_redirects=False,
                    timeout=self.timeout,
                    **({"auth": None} if self.credential_free else {}),
                ) as streamed:
                    chunks = bytearray()
                    async for chunk in streamed.aiter_bytes():
                        if len(chunks) + len(chunk) > self.max_body:
                            raise ProviderError(VenueStatus.UNAVAILABLE, "response too large")
                        chunks.extend(chunk)
                    # aiter_bytes already decoded the wire representation. Retaining its
                    # encoding would make Response decode the plaintext a second time.
                    decoded_headers = streamed.headers.copy()
                    for header in ("content-encoding", "content-length", "transfer-encoding"):
                        decoded_headers.pop(header, None)
                    response = httpx.Response(
                        streamed.status_code, headers=decoded_headers, content=bytes(chunks), request=streamed.request
                    )
            except (httpx.HTTPError, OSError, TimeoutError) as exc:
                raise ProviderError(VenueStatus.UNAVAILABLE, type(exc).__name__) from None
            finally:
                self._last = self._clock()
            self._classify(response, accept)
            if response.is_redirect:
                location = response.headers.get("location", "")
                url = str(response.url.join(location))
                params = None
                if method == "POST":
                    raise ProviderError(VenueStatus.UNAVAILABLE, "redirect on POST")
                continue
            return self._classify(response, accept)
        raise ProviderError(VenueStatus.UNAVAILABLE, "too many redirects")

    def _classify(self, response: httpx.Response, accept: tuple[int, ...] = ()) -> httpx.Response:
        status = response.status_code
        head = response.content[:20_000]
        if response.headers.get("cf-mitigated"):
            breaker = self.store.trip(self.key, "challenge (cf-mitigated)")
            raise ProviderError(VenueStatus.BLOCKED, "cf-mitigated", retry_at=breaker.open_until)
        if status == 429:
            breaker = self.store.trip(self.key, "429", minimum=retry_after(response.headers.get("retry-after")))
            raise ProviderError(VenueStatus.RATE_LIMITED, "429", retry_at=breaker.open_until)
        if (
            status in (401, 403)
            or (status >= 400 and CHALLENGE.search(head))
            or (response.headers.get("content-type", "").startswith("text/html") and CHALLENGE.search(head))
        ):
            breaker = self.store.trip(self.key, f"challenge or refusal ({status})")
            log.warning("provider pushed back", extra={"provider": self.key, "status": status})
            raise ProviderError(VenueStatus.BLOCKED, f"pushback {status}", retry_at=breaker.open_until)
        if status >= 500 and status not in accept:
            raise ProviderError(VenueStatus.UNAVAILABLE, f"http {status}")
        return response

    def budget(self) -> dict[str, Any]:
        breaker = self.store.breaker(self.key)
        return {
            "daily_request_budget": self.definition.daily_request_budget,
            "used_today": self.store.used(self.key, ALL),
            "breaker_open_until": _iso(breaker.open_until) if breaker.is_open() else None,
            "breaker_reason": breaker.reason if breaker.is_open() else None,
        }


WEBSITE_BODY_LIMIT = 512_000
WEBSITE_POLICY = ProviderDefinition(
    key="websites",
    name="Venue websites",
    access_mode=AccessMode.DIRECTORY,
    maturity=Maturity.EXPERIMENTAL,
    countries=(),
    hosts=(),
    url_patterns=(),
    link_sources=(),
    request_interval_seconds=1.0,
    daily_request_budget=50,
    person_daily_share=0.5,
    cache_ttl_seconds=86_400,
    terms="Opt-in reads of the place source's venue website only.",
    credit="",
)


def website_url(value: str) -> str | None:
    """Normalize a source-owned website, refusing credentials, IPs and ambiguous authorities.

    Tracking queries and fragments are discarded. Functional queries are refused rather than
    fetching a different page or dispatching action tokens. Public DNS enforcement and connection
    pinning still apply to the normalized hostname.
    """
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) <= 32 for c in value) or "\\" in value:
        return None
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").encode("idna").decode("ascii").lower()
        if parts.scheme != "https" or parts.username is not None or parts.password is not None or parts.port:
            return None
        # Do not fetch a different page by dropping a functional query, or dispatch action tokens.
        for key, _ in parse_qsl(parts.query, keep_blank_values=True, max_num_fields=32):
            if key.lower() not in TRACKING and not key.lower().startswith("utm_"):
                return None
        labels = host.split(".")
        if labels[-1].isdigit():
            return None
        if len(labels) < 2 or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels):
            return None
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            return None
        if host.endswith((".localhost", ".local", ".internal")):
            return None
        return str(httpx.URL(value).copy_with(host=host, query=None, fragment=None))
    except (ValueError, UnicodeError, httpx.InvalidURL):
        return None


class WebsiteTransport(Transport):
    """The explicit dynamic-host exception: one place-source website, never a tool URL.

    Each read pins GET and the validated source hostname. Every redirect must remain on that
    HTTPS host. A separate client isolates its cookies from provider traffic; no credentials,
    referer or request trace are sent. The global budget, breaker and fair queue bound all sites.
    """

    def __init__(self, store: Store, client: httpx.AsyncClient, **kwargs: Any) -> None:
        self.provider_hosts = tuple(host for definition in load_providers().values() for host in definition.hosts)
        super().__init__(
            WEBSITE_POLICY,
            {},
            store,
            client,
            max_body=WEBSITE_BODY_LIMIT,
            timeout=httpx.Timeout(4.0, connect=3.0),
            credential_free=True,
            **kwargs,
        )

    def _check(self, op: Operation, method: str, url: str) -> None:
        super()._check(op, method, url)
        if urlsplit(url).query:
            raise ProviderError(VenueStatus.UNAVAILABLE, "website redirect query refused")

    async def read(self, source_url: str, person: str) -> tuple[str, str]:
        url = website_url(source_url)
        if url is None or host_allowed(url, self.provider_hosts):
            raise ProviderError(VenueStatus.UNAVAILABLE, "unsafe website URL")
        op = Operation("website_page", "GET", str(urlsplit(url).hostname), re.compile(r"/.*"))
        async with self._turns.turn(person):
            self.client.cookies.clear()
            try:
                response = await self._send(
                    op, url, None, None, {"Accept": "text/html, application/xhtml+xml"}, (), person
                )
            except (ValueError, httpx.InvalidURL):
                raise ProviderError(VenueStatus.UNAVAILABLE, "invalid website redirect") from None
            finally:
                self.client.cookies.clear()
        media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if response.status_code != 200 or media_type not in ("text/html", "application/xhtml+xml"):
            raise ProviderError(VenueStatus.UNAVAILABLE, "website did not return HTML")
        return response.text, str(response.url)


def _iso(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def retry_after(value: str | None, now: float | None = None) -> float:
    """Provider delay, bounded to a day; malformed or past values never weaken the breaker."""
    if not value:
        return 0
    try:
        seconds = float(value)
    except ValueError:
        try:
            at = email.utils.parsedate_to_datetime(value)
            seconds = at.replace(tzinfo=at.tzinfo or UTC).timestamp() - (now if now is not None else time.time())
        except (ValueError, TypeError, OverflowError):
            return 0
    if not seconds > 0:
        return 0
    return min(seconds, 86400)


class PublicNetworkBackend:
    """Resolve once per connection, refuse non-public destinations, and connect to the vetted IP.

    httpcore retains the original origin for TLS SNI/certificate validation. The small httpx pool
    hook is covered by a construction test and kept here at the network boundary.
    """

    def __init__(self, backend: Any, resolve: Any = None) -> None:
        self.backend = backend
        self.resolve = resolve

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> Any:
        async with asyncio.timeout(timeout):
            resolver = self.resolve or asyncio.get_running_loop().getaddrinfo
            addresses = await resolver(host, port, type=socket.SOCK_STREAM)
            ips = list(dict.fromkeys(str(row[4][0]) for row in addresses))
            if not ips or any(not public_address(ip) for ip in ips):
                raise httpx.ConnectError("non-public provider destination")
            return await self.backend.connect_tcp(
                ips[0], port, timeout=timeout, local_address=local_address, socket_options=socket_options
            )

    async def connect_unix_socket(self, *args: Any, **kwargs: Any) -> Any:
        raise OSError("unix sockets are not provider destinations")

    async def sleep(self, seconds: float) -> None:
        await self.backend.sleep(seconds)


def public_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        return address.is_global and not address.is_multicast
    except ValueError:
        return False


def provider_client() -> httpx.AsyncClient:
    transport = httpx.AsyncHTTPTransport(retries=0, trust_env=False)
    pool: Any = transport._pool
    pool._network_backend = PublicNetworkBackend(pool._network_backend)
    return httpx.AsyncClient(transport=transport, trust_env=False)


class FairQueue:
    """FIFO within each person, round-robin across people; cancellation releases a turn."""

    def __init__(self) -> None:
        from collections import OrderedDict

        self.queues: Any = OrderedDict()
        self.active: asyncio.Future[None] | None = None

    def advance(self) -> None:
        if self.active is not None:
            return
        while self.queues:
            person = next(iter(self.queues))
            queue = self.queues[person]
            future = queue.pop(0)
            if queue:
                self.queues.move_to_end(person)
            else:
                del self.queues[person]
            if not future.cancelled():
                self.active = future
                future.set_result(None)
                return

    @asynccontextmanager
    async def turn(self, person: str) -> Any:
        if sum(len(q) for q in self.queues.values()) >= 128:
            raise ProviderError(VenueStatus.RATE_LIMITED, "provider queue full")
        future = asyncio.get_running_loop().create_future()
        self.queues.setdefault(person, []).append(future)
        self.advance()
        try:
            await future
            yield
        finally:
            if self.active is future:
                self.active = None
            elif person in self.queues and future in self.queues[person]:
                self.queues[person].remove(future)
                if not self.queues[person]:
                    del self.queues[person]
            self.advance()
