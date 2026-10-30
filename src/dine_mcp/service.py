"""DineService: what the MCP tools, the CLI and the Python API call.

One service per server; the calling person is passed into every method (chosen by the HTTP layer
before any tool code runs) and only ever selects budgets and logs: in this release no tool reads or
writes anything personal.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from datetime import time as dtime
from typing import Any

from . import __version__, links
from .availability import (
    MAX_DAYS,
    MAX_REQUESTS,
    Checker,
    ObservationCache,
    Planner,
    Query,
    Task,
    VenueInfoCache,
    contact_result,
    listing_result,
    signed_in_result,
)
from .controls import bounded, call_scope
from .definitions import EffectiveProvider, ProviderDefinition, Region, effective
from .errors import DineError, invalid
from .hours import closed_days
from .models import AccessMode, ProviderVenueRef, SlotKind, VenueResult, VenueStatus
from .places import Place, PlaceSourceError
from .providers.base import Provider, VenueSearch, Window
from .resolver import Resolution, Resolver
from .state import Store
from .transport import ProviderError

log = logging.getLogger("dine.service")

MAX_VENUES = 5
CONCISE_SLOTS_PER_DAY = 6


@dataclass
class Settings:
    deadline_seconds: float = 30.0


class DineService:
    def __init__(
        self,
        definitions: dict[str, ProviderDefinition],
        regions: dict[str, Region],
        providers: dict[str, Provider],
        store: Store,
        resolver: Resolver,
        overrides_view: dict[str, EffectiveProvider],
        *,
        disabled: bool = False,
        settings: Settings | None = None,
    ) -> None:
        self.definitions = definitions
        self.regions = regions
        self.providers = providers
        self.store = store
        self.resolver = resolver
        self.effective = overrides_view
        self.disabled = disabled
        self.settings = settings or Settings()
        self.cache = ObservationCache()
        self.venue_info = VenueInfoCache()
        self.planner = Planner(self.cache, self.venue_info)
        self._check_lock = asyncio.Lock()
        self._canaries: dict[str, tuple[float, dict[str, Any]]] = {}

    # --- venues ----------------------------------------------------------------------------------------

    def _guard(self) -> None:
        if self.disabled:
            raise DineError("disabled")

    @bounded
    async def find_venue(
        self,
        person: str,
        name: str | None = None,
        area: str | None = None,
        country: str | None = None,
        url: str | None = None,
        google_place_id: str | None = None,
        limit: int = 5,
    ) -> dict[str, Any]:
        self._guard()
        if not 1 <= limit <= 10:
            raise invalid("limit must be between 1 and 10")
        given = [x for x in (name, url, google_place_id) if x]
        if len(given) != 1:
            raise invalid("Give exactly one of name, url or google_place_id")
        if country is not None:
            country = country.upper()
        try:
            if url:
                resolution = await self._from_url(url, person)
            elif google_place_id:
                resolution = await self.resolver.from_place_id(google_place_id, person)
            else:
                assert name is not None
                resolution = await self._search(name, area, country, limit, person)
        except PlaceSourceError as exc:
            raise DineError("place_source_unavailable", log_detail=exc.code) from None
        venues = []
        for item in resolution.venues:
            record = await self.venue_view(item["venue_id"], detailed=False, person=person)
            record["match"] = {"confidence": item["confidence"], "evidence": item["evidence"]}
            venues.append(record)
        return {
            "venues": venues,
            "candidates": [c.to_dict() for c in resolution.candidates],
            "note": resolution.note,
        }

    async def _from_url(self, url: str, person: str) -> Resolution:
        cleaned = links.clean(url)
        if cleaned is None:
            raise invalid("url must be an https link")
        ref = links.match(cleaned, self.definitions)
        if ref is None:
            return Resolution([], [], "This link is not a booking page of a supported provider.")
        provider = self.providers.get(ref.provider)
        if provider is None or not self.effective[ref.provider].enabled:
            return Resolution([], [], f"{self.definitions[ref.provider].name} is not enabled on this server.")
        try:
            venue = self.venue_info.get(ref) or await provider.get_venue(ref, person)
        except ProviderError as exc:
            if exc.status is VenueStatus.UNKNOWN_VENUE:
                return Resolution([], [], "The provider has no venue at this link.")
            return Resolution([], [], f"The provider could not be asked now ({exc.status.value}); retry later.")
        self.venue_info.put(venue)
        venue_id = self.resolver.save_from_provider(venue)
        return Resolution([{"venue_id": venue_id, "confidence": 1.0, "evidence": "booking link"}], [])

    async def _search(self, name: str, area: str | None, country: str | None, limit: int, person: str) -> Resolution:
        primary: Resolution | None = None
        source_error: PlaceSourceError | None = None
        try:
            primary = await self.resolver.search(name, area, country, limit, person)
            # Preserve source ambiguity. An empty result may reflect thin regional coverage.
            if primary.venues or primary.candidates:
                return primary
        except PlaceSourceError as exc:
            source_error = exc
        region = self.regions.get(country or "")
        found: dict[str, dict[str, Any]] = {}
        for key in region.providers if region else self.providers:
            provider = self.providers.get(key)
            if not isinstance(provider, VenueSearch) or not self.effective[key].enabled:
                continue
            try:
                for venue in await provider.search_venues(name, country, person):
                    if country and venue.country != country:
                        continue
                    self.venue_info.put(venue)
                    venue_id = self.resolver.save_from_provider(venue)
                    found.setdefault(
                        venue_id,
                        {
                            "venue_id": venue_id,
                            "confidence": 0.7,
                            "evidence": f"name search on {self.definitions[key].name}",
                        },
                    )
            except ProviderError:
                continue
            if len(found) >= limit:
                break
        if found:
            return Resolution(
                list(found.values())[:limit],
                [],
                "Found by bounded provider search; check the name and location. Coverage is not exhaustive.",
            )
        if primary is not None:
            return primary
        assert source_error is not None
        raise source_error

    @bounded
    async def venue_view(self, venue_id: str, *, detailed: bool, person: str = "local") -> dict[str, Any]:
        self._guard()
        record = self.store.get_venue(venue_id)
        if record is None:
            raise DineError("unknown_venue_id")
        place: Place | None = None
        if record.get("google_place_id"):
            try:
                place = await self.resolver.place(record["google_place_id"])
            except PlaceSourceError:
                place = None
        if place is not None:
            await self.resolver.enrich(place, person)
            record = self.store.get_venue(record["venue_id"]) or record
        channels = record.get("channels", [])
        view: dict[str, Any] = {
            "venue_id": record["venue_id"],
            "name": (place.name if place else None) or record.get("name") or record["venue_id"],
            "address": (place.address if place else None) or record.get("address"),
            "country": record.get("country"),
            "time_zone": (place.time_zone if place else None) or record.get("time_zone"),
            "google_place_id": record.get("google_place_id"),
            "maps_url": place.maps_url if place else None,
            "channels": [self._channel_view(c) for c in channels],
            "booking_note": record.get("rules_note"),
            "attribution": place.attribution if place else None,
        }
        if detailed:
            view["opening_hours"] = place.opening_hours if place else None
            details: list[dict[str, Any]] = []
            for channel in channels:
                provider = self.providers.get(channel.get("kind", ""))
                if provider is None or not channel.get("ref") or not self.effective[channel["kind"]].enabled:
                    continue
                ref = ProviderVenueRef(channel["kind"], channel["ref"])
                try:
                    info = self.venue_info.get(ref) or await provider.get_venue(ref, person)
                except ProviderError as exc:
                    details.append({"provider": ref.provider, "status": exc.status.value})
                    continue
                self.venue_info.put(info)
                details.append(
                    {
                        "provider": ref.provider,
                        "status": "ok",
                        "name": info.name,
                        "time_zone": info.time_zone,
                        "booking_rules": info.rules.to_dict(),
                        "experiences": list(info.experiences),
                        "booking_link": provider.booking_link(info, None, None, None).to_dict(),
                    }
                )
            view["providers"] = details
        return view

    def _channel_view(self, channel: dict[str, Any]) -> dict[str, Any]:
        kind = channel.get("kind", "")
        out = dict(channel)
        state = self.effective.get(kind)
        if state is not None:
            out["access_mode"] = state.access_mode.value if state.enabled else AccessMode.LISTING.value
            out["provider_name"] = state.definition.name
        return out

    # --- availability ----------------------------------------------------------------------------------

    def parse_query(
        self, date_from: str, date_to: str | None, time_from: str, time_to: str, party_size: int, kinds: list[str]
    ) -> Query:
        try:
            first = date.fromisoformat(date_from)
            last = date.fromisoformat(date_to) if date_to else first
        except ValueError:
            raise invalid("date_from and date_to are YYYY-MM-DD, for example 2026-11-06") from None
        if last < first:
            raise invalid("date_to is before date_from")
        if (last - first).days + 1 > MAX_DAYS:
            raise invalid(f"At most {MAX_DAYS} days per call (date_from to date_to inclusive)")
        if (first - datetime.now().date()).days > 400:
            raise invalid("date_from is more than a year ahead")
        try:
            if any(not re.fullmatch(r"[0-9]{2}:[0-9]{2}", value) for value in (time_from, time_to)):
                raise ValueError
            start, end = dtime.fromisoformat(time_from), dtime.fromisoformat(time_to)
        except ValueError:
            raise invalid("time_from and time_to are HH:MM in the venue's local time, for example 19:00") from None
        if not 1 <= party_size <= 20:
            raise invalid("party_size must be between 1 and 20")
        if start.tzinfo is not None or end.tzinfo is not None:
            raise invalid("Times are local HH:MM without a timezone offset")
        try:
            wanted = frozenset(SlotKind(k) for k in kinds)
        except ValueError:
            raise invalid("kinds are bookable, request or waitlist") from None
        if not wanted:
            raise invalid("kinds must not be empty")
        return Query(first, last, start, end, party_size, wanted)

    async def check_availability(
        self,
        person: str,
        venue_ids: list[str],
        date_from: str,
        date_to: str | None = None,
        time_from: str = "17:00",
        time_to: str = "22:30",
        party_size: int = 2,
        kinds: list[str] | None = None,
        detailed: bool = False,
    ) -> dict[str, Any]:
        self._guard()
        if not venue_ids or len(venue_ids) > MAX_VENUES:
            raise invalid(f"Give 1 to {MAX_VENUES} venue_ids")
        if len(set(venue_ids)) != len(venue_ids):
            raise invalid("venue_ids repeat a venue")
        query = self.parse_query(
            date_from, date_to, time_from, time_to, party_size, ["bookable", "request"] if kinds is None else kinds
        )
        results: list[VenueResult] = []
        tasks: list[Task] = []
        started = time.monotonic()
        cost = 0
        with call_scope(self.settings.deadline_seconds) as call:
            try:
                async with asyncio.timeout(call.remaining()):
                    for venue_id in venue_ids:
                        record = self.store.get_venue(venue_id)
                        if record is None:
                            raise DineError("unknown_venue_id", f"Unknown venue_id {venue_id[:20]}")
                        await self._plan_venue(record, tasks, results, person)
                    cost = 0
                    for task in tasks:
                        self.planner.plan(task, query)
                        cost += task.cost
                    if cost > MAX_REQUESTS:
                        raise DineError(
                            "too_broad",
                            f"This needs about {cost} provider requests; the limit is {MAX_REQUESTS} per call",
                        )
                    await Checker(self.planner, person, query, call.remaining()).run(tasks)
            except TimeoutError:
                pass
        covered = {r.venue_id for r in results} | {t.venue_id for t in tasks}
        for venue_id in venue_ids:
            if venue_id not in covered:
                results.append(
                    VenueResult(
                        venue_id,
                        venue_id,
                        "planning",
                        VenueStatus.SKIPPED_DEADLINE,
                        dates_not_checked=[d.isoformat() for d in query.dates()],
                    )
                )
        for task in tasks:
            if task.result is None:
                task.result = VenueResult(
                    task.venue_id,
                    task.venue_name,
                    task.ref.provider,
                    VenueStatus.SKIPPED_DEADLINE,
                    dates_not_checked=[d.isoformat() for d in query.dates()],
                )
        results.extend(task.result for task in tasks if task.result is not None)
        order = {venue_id: index for index, venue_id in enumerate(venue_ids)}
        results.sort(key=lambda r: order.get(r.venue_id, 99))
        log.info(
            "availability",
            extra={"venues": len(venue_ids), "requests_planned": cost, "ms": int((time.monotonic() - started) * 1000)},
        )
        per_day = None if detailed else CONCISE_SLOTS_PER_DAY
        return {
            "query": {
                "date_from": query.date_from.isoformat(),
                "date_to": query.date_to.isoformat(),
                "time_from": query.time_from.strftime("%H:%M"),
                "time_to": query.time_to.strftime("%H:%M"),
                "party_size": query.party_size,
                "kinds": sorted(k.value for k in query.kinds),
            },
            "results": [r.to_dict(max_slots_per_day=per_day) for r in results],
            "summary": self._summary(results),
        }

    async def _plan_venue(
        self, record: dict[str, Any], tasks: list[Task], results: list[VenueResult], person: str
    ) -> None:
        venue_id = record["venue_id"]
        name = record.get("name")
        time_zone = record.get("time_zone")
        place = None
        if record.get("google_place_id"):
            try:
                place = await self.resolver.place(record["google_place_id"])
            except PlaceSourceError:
                place = None
        if place is not None:
            await self.resolver.enrich(place, person)
            record = self.store.get_venue(record["venue_id"]) or record
            name = name or place.name
            time_zone = time_zone or place.time_zone
        name = name or venue_id
        contacts = [c for c in record.get("channels", []) if c.get("kind") in ("phone", "messaging", "website")]
        contact = "; ".join(str(c.get("text") or c.get("url")) for c in contacts if c.get("text") or c.get("url"))
        bookable = False
        for channel in record.get("channels", []):
            kind = channel.get("kind", "")
            if kind == "booking_page":
                results.append(listing_result(venue_id, name, kind, channel.get("url"), channel.get("text")))
                bookable = True
                continue
            state = self.effective.get(kind)
            provider = self.providers.get(kind)
            if state is None or provider is None or not channel.get("ref"):
                continue
            bookable = True
            task = Task(
                venue_id, name, provider, state, ProviderVenueRef(kind, channel["ref"]), time_zone, contact or None
            )
            if place is not None:
                task.closures = closed_days(place, self.store.place_fetched_at(place.place_id))
            if not state.enabled or state.access_mode in (AccessMode.LISTING, AccessMode.DIRECTORY):
                note = state.reason or "listing only"
                results.append(
                    listing_result(venue_id, name, kind, channel.get("url"), f"{state.definition.name} ({note})")
                )
            elif state.access_mode is AccessMode.SIGNED_IN:
                results.append(signed_in_result(task))
            else:
                tasks.append(task)
        if not bookable:
            results.append(contact_result(venue_id, name, contact or None, record.get("rules_note")))

    def _summary(self, results: list[VenueResult]) -> dict[str, Any]:
        providers: dict[str, dict[str, int]] = {}
        for result in results:
            providers.setdefault(result.channel, {}).setdefault(result.status.value, 0)
            providers[result.channel][result.status.value] += 1
        return {
            "venues_with_slots": len({r.venue_id for r in results if r.slots}),
            "by_channel": providers,
        }

    # --- status ----------------------------------------------------------------------------------------

    @bounded
    async def checked_status(self, transports: dict[str, Any], person: str) -> dict[str, Any]:
        """Explicit, budgeted guest-widget checks, sharing a durable 15-minute cooldown."""
        async with self._check_lock:
            for key, provider in self.providers.items():
                state = self.effective[key]
                ref = state.definition.canary.get("ref")
                if self.disabled or not state.enabled or state.access_mode is not AccessMode.PUBLIC or not ref:
                    continue
                if not self.store.claim_canary(key):
                    continue
                answer = {"status": "unavailable", "observed_at": datetime.now().astimezone().isoformat()}
                # Reserve the cooldown before dispatch, also on failure/cancellation.
                self._canaries[key] = (time.monotonic(), answer)
                try:
                    info = await provider.get_venue(ProviderVenueRef(key, ref), person)
                    from .availability import zone

                    tz = zone(info.time_zone)
                    day = datetime.now(tz).date() + timedelta(days=1)
                    observation = await provider.availability(info, Window((day,), dtime(18), dtime(20), 2), person)
                    answer["status"] = "ok" if day.isoformat() in observation.dates_checked else "unavailable"
                except ProviderError as exc:
                    answer["status"] = exc.status.value
                except Exception as exc:
                    answer["status"] = "unavailable"
                    log.warning("canary failed", extra={"provider": key, "error": type(exc).__name__})
        return self.status(transports)

    def status(self, transports: dict[str, Any]) -> dict[str, Any]:
        providers = []
        for key, state in sorted(self.effective.items()):
            definition = state.definition
            item: dict[str, Any] = {
                "key": key,
                "name": definition.name,
                "enabled": state.enabled,
                "access_mode": state.access_mode.value,
                "maturity": definition.maturity.value,
                "countries": list(definition.countries),
                "reason": state.reason,
            }
            transport = transports.get(key)
            if transport is not None:
                item.update(transport.budget())
            cached = self._canaries.get(key)
            if cached is not None:
                item["check"] = {**cached[1], "age_seconds": int(time.monotonic() - cached[0])}
            providers.append(item)
        return {
            "version": __version__,
            "disabled": self.disabled,
            "providers": providers,
            "regions": sorted(self.regions),
            "cache": {"observations": len(self.cache)},
            "website_discovery": {
                "enabled": self.resolver.websites is not None,
                **(self.resolver.websites.transport.budget() if self.resolver.websites is not None else {}),
            },
        }


def build_effective(definitions: dict[str, ProviderDefinition], overrides: Any) -> dict[str, EffectiveProvider]:
    return {key: effective(definition, overrides) for key, definition in definitions.items()}
