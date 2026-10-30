"""Availability across venues and providers: plan, budget, fan out under one deadline, cache.

1. Plan every venue × channel. Provider channels in ``public`` mode are checked; ``listing`` providers
   and unsupported booking pages answer with their link; venues with only contact channels answer
   ``contact_only``.
2. Count the provider requests the plan needs (cache hits cost nothing) before any request. More than
   ``MAX_REQUESTS`` refuses the call (``too_broad``) with a narrower example.
3. Providers run concurrently; requests to one provider run one at a time through its transport. At
   the deadline, unfinished venues answer ``skipped_deadline`` with what was covered.
4. Public observations are cached for everyone, keyed by everything that changes the provider's
   answer; nothing person-bound is cached. A repeated call reuses completed parts.
"""

from __future__ import annotations

import asyncio
import logging
import time as clock
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta, tzinfo
from datetime import time as dtime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .controls import CURRENT, PROGRESS
from .definitions import EffectiveProvider
from .errors import DineError
from .models import AccessMode, BookingLink, ProviderVenue, ProviderVenueRef, Slot, SlotKind, VenueResult, VenueStatus
from .providers.base import OBSERVER, Observation, Provider, Window
from .providers.common import unambiguous_local
from .transport import ProviderError

log = logging.getLogger("dine.availability")

MAX_REQUESTS = 20
MAX_DAYS = 7
DEFAULT_DEADLINE = 30.0
VENUE_INFO_TTL = 3600.0
CACHE_LIMIT = 5000


def zone(name: str | None) -> tzinfo | None:
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None


@dataclass
class CacheEntry:
    observed_at: float
    status: VenueStatus
    slots: list[Slot]
    note: str | None = None
    max_party: int | None = None
    complete: bool = True


CacheKey = tuple[str, str, int, str, str]


class ObservationCache:
    """Shared cache of public provider observations, per (provider, ref, party size, date)."""

    def __init__(self, now: Any = clock.time) -> None:
        self._entries: dict[CacheKey, CacheEntry] = {}
        self._now = now

    def get(self, key: CacheKey, ttl: float) -> CacheEntry | None:
        entry = self._entries.get(key)
        if entry is None or not entry.complete or self._now() - entry.observed_at > ttl:
            return None
        return entry

    def stale(self, key: CacheKey, max_age: float) -> CacheEntry | None:
        entry = self._entries.get(key)
        return entry if entry is not None and self._now() - entry.observed_at <= max_age else None

    def put(self, key: CacheKey, entry: CacheEntry) -> None:
        if len(self._entries) >= CACHE_LIMIT:
            for old in sorted(self._entries, key=lambda k: self._entries[k].observed_at)[: CACHE_LIMIT // 10]:
                del self._entries[old]
        self._entries[key] = entry

    def __len__(self) -> int:
        return len(self._entries)


@dataclass
class Query:
    date_from: date
    date_to: date
    time_from: dtime
    time_to: dtime
    party_size: int
    kinds: frozenset[SlotKind]

    def dates(self) -> list[date]:
        extra = int(self.time_from > self.time_to)
        return [self.date_from + timedelta(days=n) for n in range((self.date_to - self.date_from).days + 1 + extra)]

    def in_window(self, at: dtime) -> bool:
        if self.time_from <= self.time_to:
            return self.time_from <= at <= self.time_to
        return at >= self.time_from or at <= self.time_to


@dataclass
class Task:
    """One provider venue to check."""

    venue_id: str
    venue_name: str
    provider: Provider
    effective: EffectiveProvider
    ref: ProviderVenueRef
    time_zone: str | None
    contact: str | None
    result: VenueResult | None = None
    fetch_dates: list[date] = field(default_factory=list)
    cost: int = 0
    closures: dict[date, float] = field(default_factory=dict)


class VenueInfoCache:
    def __init__(self) -> None:
        self._items: dict[str, tuple[float, ProviderVenue]] = {}

    def get(self, ref: ProviderVenueRef) -> ProviderVenue | None:
        item = self._items.get(ref.alias)
        if item and clock.monotonic() - item[0] < VENUE_INFO_TTL:
            return item[1]
        self._items.pop(ref.alias, None)
        return None

    def put(self, venue: ProviderVenue) -> None:
        now = clock.monotonic()
        self._items = {key: item for key, item in self._items.items() if now - item[0] < VENUE_INFO_TTL}
        if venue.ref.alias not in self._items and len(self._items) >= CACHE_LIMIT:
            del self._items[min(self._items, key=lambda key: self._items[key][0])]
        self._items[venue.ref.alias] = (now, venue)


def iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def local_today(tz: tzinfo | None) -> date:
    return datetime.now(tz or UTC).date()


def filter_slots(slots: list[Slot], query: Query) -> list[Slot]:
    out = []
    for slot in slots:
        try:
            at = datetime.fromisoformat(slot.start).time()
        except ValueError:
            continue
        slot_day = date.fromisoformat(slot.start[:10])
        service_day = slot_day
        if query.time_from > query.time_to and at <= query.time_to:
            service_day -= timedelta(days=1)
        if query.date_from <= service_day <= query.date_to and slot.kind in query.kinds and query.in_window(at):
            out.append(slot)
    return out


def opens_at(first: date, rules_window: int | None, release: str | None, tz: tzinfo | None) -> str | None:
    """When ``first`` becomes bookable, from the booking window and release time, if both are known."""
    if rules_window is None:
        return None
    day = first - timedelta(days=rules_window)
    hour, minute = 0, 0
    if release:
        try:
            hour, minute = (int(part) for part in release.split(":", 1))
        except ValueError:
            return None
    try:
        at = dtime(hour, minute)
    except ValueError:
        return None
    return unambiguous_local(day, at, tz or UTC)


class Planner:
    """Decides, per provider task, which dates still need a request, and what it costs."""

    def __init__(self, cache: ObservationCache, venue_info: VenueInfoCache) -> None:
        self.cache = cache
        self.venue_info = venue_info

    def key(self, task: Task, query: Query, day: date) -> CacheKey:
        coverage = "day"
        if getattr(task.provider, "windowed_availability", False):
            coverage = f"{query.time_from.isoformat()}/{query.time_to.isoformat()}"
        return (task.ref.provider, task.ref.ref, query.party_size, day.isoformat(), coverage)

    def plan(self, task: Task, query: Query) -> None:
        info = self.venue_info.get(task.ref)
        tz = zone((info.time_zone if info else None) or task.time_zone)
        today = local_today(tz)
        ttl = float(task.effective.definition.cache_ttl_seconds)
        task.fetch_dates = []
        task.cost = 0
        if info and info.rules.min_party is not None and query.party_size < info.rules.min_party:
            return
        if info and info.rules.max_party is not None and query.party_size > info.rules.max_party:
            return
        for day in query.dates():
            if day < today or day in task.closures:
                continue
            if info and info.rules.window_days is not None and day > today + timedelta(days=info.rules.window_days):
                continue
            if self.cache.get(self.key(task, query, day), ttl) is None:
                task.fetch_dates.append(day)
        if not task.fetch_dates:
            task.cost = 0
            return
        window = Window(tuple(task.fetch_dates), query.time_from, query.time_to, query.party_size)
        task.cost = task.provider.cost(task.ref, window) + (0 if info else 1)


class Checker:
    """Runs the planned tasks and assembles one VenueResult per task."""

    def __init__(self, planner: Planner, person: str, query: Query, deadline: float = DEFAULT_DEADLINE) -> None:
        self.planner = planner
        self.person = person
        self.query = query
        self.deadline = deadline

    async def run(self, tasks: list[Task]) -> None:
        by_provider: dict[str, list[Task]] = {}
        for task in tasks:
            by_provider.setdefault(task.ref.provider, []).append(task)

        async def provider_run(items: list[Task]) -> None:
            for task in items:
                try:
                    await self.check(task)
                except (DineError, asyncio.CancelledError):
                    raise
                except TimeoutError as exc:
                    call = CURRENT.get()
                    if call is not None and call.remaining() <= 0:
                        task.result = self.expired(task)
                        return
                    task.result = self.crashed(task, exc)
                except Exception as exc:
                    task.result = self.crashed(task, exc)
                progress = PROGRESS.get()
                if progress is not None:
                    await progress(sum(t.result is not None for t in tasks), len(tasks), "Venue check completed")

        jobs = [asyncio.create_task(provider_run(items)) for items in by_provider.values()]
        if not jobs:
            return
        done: set[asyncio.Task[None]] = set()
        try:
            done, _ = await asyncio.wait(jobs, timeout=self.deadline)
        finally:
            pending = [job for job in jobs if not job.done()]
            for job in pending:
                job.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        for job in done:
            error = job.exception()
            if error is not None:
                raise error
        for task in tasks:
            if task.result is None:
                task.result = self.base(task, VenueStatus.SKIPPED_DEADLINE)
                task.result.dates_not_checked = [d.isoformat() for d in self.query.dates()]
                task.result.note = "Not checked within the time limit; call again for this venue."

    def base(self, task: Task, status: VenueStatus) -> VenueResult:
        return VenueResult(
            venue_id=task.venue_id,
            venue_name=task.venue_name,
            channel=task.ref.provider,
            status=status,
            time_zone=task.time_zone,
        )

    async def venue(self, task: Task) -> ProviderVenue:
        info = self.planner.venue_info.get(task.ref)
        if info is None:
            info = await task.provider.get_venue(task.ref, self.person)
            self.planner.venue_info.put(info)
        return info

    async def check(self, task: Task) -> None:
        query = self.query
        ttl = float(task.effective.definition.cache_ttl_seconds)
        if all(day in task.closures for day in query.dates()):
            task.result = self.base(task, VenueStatus.CLOSED)
            task.result.dates_checked = [d.isoformat() for d in query.dates()]
            task.result.observed_at = iso(min(task.closures[d] for d in query.dates()))
            task.result.served_from = "cache"
            task.result.note = "The place source explicitly lists these dates as closed."
            return
        try:
            info = await self.venue(task)
        except ProviderError as exc:
            task.result = self.failed(task, exc, None)
            return
        except asyncio.CancelledError:
            raise
        except (DineError, TimeoutError):
            raise
        except Exception as exc:  # an adapter bug never fails the whole call
            task.result = self.crashed(task, exc)
            return
        task.time_zone = info.time_zone or task.time_zone
        if info.name and task.venue_name.startswith("v_"):
            task.venue_name = info.name
        tz = zone(task.time_zone)
        today = local_today(tz)
        rules = info.rules
        link = task.provider.booking_link(info, query.date_from, query.time_from, query.party_size)
        result = self.base(task, VenueStatus.OK)
        result.time_zone = task.time_zone
        result.booking_link = link if link else None
        if rules.min_party is not None and query.party_size < rules.min_party:
            result.status = VenueStatus.PARTY_TOO_SMALL
            result.min_party = rules.min_party
            result.contact = task.contact
            result.note = "Below the smallest party this venue books online; contact the venue."
            task.result = result
            return
        if rules.max_party is not None and query.party_size > rules.max_party:
            result.status = VenueStatus.PARTY_TOO_LARGE
            result.max_party = rules.max_party
            result.contact = task.contact
            result.note = "Above the largest party this venue books online; contact the venue."
            task.result = result
            return
        past = [d for d in query.dates() if d < today]
        closed = [d for d in query.dates() if d >= today and d in task.closures]
        bookable = [d for d in query.dates() if d >= today and d not in task.closures]
        beyond = []
        if rules.window_days is not None:
            last_open = today + timedelta(days=rules.window_days)
            beyond = [d for d in bookable if d > last_open]
            bookable = [d for d in bookable if d <= last_open]
        result.dates_not_checked = [d.isoformat() for d in past + beyond]
        result.dates_checked = [d.isoformat() for d in closed]
        if closed:
            result.observed_at = iso(min(task.closures[d] for d in closed))
            result.served_from = "cache"
        if not bookable:
            result.status = (
                VenueStatus.NOT_YET_OPEN if beyond else VenueStatus.CLOSED if closed else VenueStatus.NONE_AVAILABLE
            )
            if beyond:
                result.opens_at = opens_at(beyond[0], rules.window_days, rules.release_time, tz)
                result.note = "These dates are not open for booking yet."
            else:
                result.note = "All requested dates are in the past."
            task.result = result
            return
        missing = [d for d in bookable if self.planner.cache.get(self.planner.key(task, query, d), ttl) is None]
        observed_at = clock.time()
        served = "cache"
        if missing:
            window = Window(tuple(missing), query.time_from, query.time_to, query.party_size)
            published: set[str] = set()

            def remember(part: Observation) -> None:
                self.store_observation(task, part, missing, clock.time())
                published.update(part.dates_checked)

            token = OBSERVER.set(remember)
            try:
                # A fresh place-source zone can fill a provider gap for this check only.
                observation = await task.provider.availability(
                    replace(info, time_zone=task.time_zone), window, self.person
                )
            except ProviderError as exc:
                task.result = self.failed(task, exc, result)
                return
            except asyncio.CancelledError:
                task.result = self.expired(task, result)
                raise
            except (DineError, TimeoutError):
                raise
            except Exception as exc:
                log.error(
                    "provider availability crashed", extra={"provider": task.ref.provider, "error": type(exc).__name__}
                )
                result.status = VenueStatus.UNAVAILABLE
                result.dates_not_checked = [d.isoformat() for d in query.dates()]
                self.serve_stale(task, result)
                task.result = result
                return
            finally:
                OBSERVER.reset(token)
            self.store_observation(
                task, observation, [d for d in missing if d.isoformat() not in published], clock.time()
            )
            served = "live"
        slots: list[Slot] = []
        statuses: set[VenueStatus] = {VenueStatus.CLOSED} if closed else set()
        notes: list[str] = []
        oldest = min([observed_at] + [task.closures[d] for d in closed])
        for day in bookable:
            entry = self.planner.cache.get(self.planner.key(task, query, day), ttl)
            if entry is None:
                result.dates_not_checked.append(day.isoformat())
                partial = self.planner.cache.stale(self.planner.key(task, query, day), ttl)
                if partial is not None and not partial.complete:
                    oldest = min(oldest, partial.observed_at)
                    slots.extend(filter_slots(partial.slots, query))
                continue
            oldest = min(oldest, entry.observed_at)
            result.dates_checked.append(day.isoformat())
            statuses.add(entry.status)
            slots.extend(filter_slots(entry.slots, query))
            if entry.note:
                notes.append(entry.note)
            if entry.max_party is not None:
                result.max_party = entry.max_party
        result.slots = sorted(slots, key=lambda s: s.start)
        result.observed_at = iso(oldest)
        result.served_from = served
        if beyond and not result.slots:
            result.opens_at = opens_at(beyond[0], rules.window_days, rules.release_time, tz)
        result.status = self.summarise(statuses, bool(result.slots), bool(beyond))
        if not result.dates_checked and not result.slots:
            result.status = VenueStatus.UNAVAILABLE
            result.observed_at = None
            result.served_from = None
        if any(d.isoformat() in result.dates_not_checked for d in bookable):
            notes.insert(0, "Some requested dates were not observed; missing coverage is unknown.")
            if not result.slots:
                result.status = VenueStatus.UNAVAILABLE
        if result.status in (VenueStatus.PARTY_TOO_SMALL, VenueStatus.PARTY_TOO_LARGE):
            result.contact = task.contact
        if notes:
            result.note = "; ".join(dict.fromkeys(notes))[:300]
        task.result = result

    def summarise(self, statuses: set[VenueStatus], any_slots: bool, beyond: bool) -> VenueStatus:
        if any_slots:
            return VenueStatus.OK
        for status in (VenueStatus.PARTY_TOO_SMALL, VenueStatus.PARTY_TOO_LARGE, VenueStatus.UNKNOWN_VENUE):
            if status in statuses:
                return status
        if statuses and statuses <= {VenueStatus.CLOSED}:
            return VenueStatus.CLOSED
        if beyond and statuses <= {VenueStatus.NOT_YET_OPEN, VenueStatus.NONE_AVAILABLE, VenueStatus.CLOSED}:
            return VenueStatus.NOT_YET_OPEN if VenueStatus.NOT_YET_OPEN in statuses else VenueStatus.NONE_AVAILABLE
        if VenueStatus.NOT_YET_OPEN in statuses:
            return VenueStatus.NOT_YET_OPEN
        return VenueStatus.NONE_AVAILABLE

    def store_observation(self, task: Task, observation: Observation, days: list[date], observed_at: float) -> None:
        by_day: dict[str, list[Slot]] = {}
        for slot in observation.slots:
            by_day.setdefault(slot.start[:10], []).append(slot)
        checked = set(observation.dates_checked)
        for day in days:
            iso_day = day.isoformat()
            if iso_day not in checked:
                continue
            day_slots = by_day.get(iso_day, [])
            status = VenueStatus.OK if day_slots else observation.day_status.get(iso_day, observation.status)
            if status is VenueStatus.OK and not day_slots:
                status = VenueStatus.NONE_AVAILABLE
            self.planner.cache.put(
                self.planner.key(task, self.query, day),
                CacheEntry(
                    observed_at, status, day_slots, observation.note, observation.max_party, observation.complete
                ),
            )

    def expired(self, task: Task, result: VenueResult | None = None) -> VenueResult:
        out = result or self.base(task, VenueStatus.SKIPPED_DEADLINE)
        out.status = VenueStatus.SKIPPED_DEADLINE
        out.dates_not_checked = [d.isoformat() for d in self.query.dates()]
        self.serve_stale(task, out)
        return out

    def crashed(self, task: Task, exc: Exception) -> VenueResult:
        log.error("provider check crashed", extra={"provider": task.ref.provider, "error": type(exc).__name__})
        out = self.base(task, VenueStatus.UNAVAILABLE)
        out.dates_not_checked = [d.isoformat() for d in self.query.dates()]
        self.serve_stale(task, out, max_age=float(task.effective.definition.cache_ttl_seconds))
        return out

    def failed(self, task: Task, exc: ProviderError, result: VenueResult | None) -> VenueResult:
        out = result or self.base(task, exc.status)
        out.status = exc.status
        out.dates_not_checked = [d.isoformat() for d in self.query.dates()]
        if exc.status in (VenueStatus.BLOCKED, VenueStatus.RATE_LIMITED):
            out.note = "The provider asked us to slow down; not checked now" + (
                f" (retry after {iso(exc.retry_at)})" if exc.retry_at else ""
            )
            self.serve_stale(task, out)
        elif exc.status is VenueStatus.UNAVAILABLE:
            self.serve_stale(task, out, max_age=float(task.effective.definition.cache_ttl_seconds))
        elif exc.status is VenueStatus.UNKNOWN_VENUE:
            out.note = "The provider no longer knows this venue."
        log.info(
            "provider status",
            extra={"provider": task.ref.provider, "status": exc.status.value, "detail": exc.log_detail},
        )
        return out

    def serve_stale(self, task: Task, out: VenueResult, max_age: float = 600) -> None:
        """While the provider pushes back, results up to 10 minutes old are shown, labelled as such."""
        slots: list[Slot] = []
        closures = {d: at for d, at in task.closures.items() if d in self.query.dates()}
        oldest: float | None = min(closures.values()) if closures else None
        checked = [d.isoformat() for d in closures]
        for day in self.query.dates():
            entry = self.planner.cache.stale(self.planner.key(task, self.query, day), max_age)
            if entry is None:
                continue
            if entry.complete:
                checked.append(day.isoformat())
            oldest = entry.observed_at if oldest is None else min(oldest, entry.observed_at)
            slots.extend(filter_slots(entry.slots, self.query))
        if oldest is not None:
            out.slots = sorted(slots, key=lambda s: s.start)
            out.dates_checked = checked
            out.dates_not_checked = [d for d in out.dates_not_checked if d not in checked]
            out.observed_at = iso(oldest)
            out.served_from = "cache"


def listing_result(venue_id: str, name: str, channel: str, url: str | None, text: str | None) -> VenueResult:
    return VenueResult(
        venue_id=venue_id,
        venue_name=name,
        channel=channel,
        status=VenueStatus.UNSUPPORTED,
        booking_link=BookingLink(url) if url else None,
        note=f"Availability is not checked for {text or channel}; book through the link.",
    )


def contact_result(venue_id: str, name: str, contact: str | None, note: str | None = None) -> VenueResult:
    return VenueResult(
        venue_id=venue_id,
        venue_name=name,
        channel="contact",
        status=VenueStatus.CONTACT_ONLY,
        contact=contact,
        note=note or "No online booking system is known for this venue; contact it directly.",
    )


def signed_in_result(task: Task) -> VenueResult:
    return replace(
        VenueResult(task.venue_id, task.venue_name, task.ref.provider, VenueStatus.NOT_CONNECTED),
        note="This provider requires signed-in availability, which this server does not support; use its booking page.",
    )


def mode_allows_check(mode: AccessMode) -> bool:
    return mode is AccessMode.PUBLIC
