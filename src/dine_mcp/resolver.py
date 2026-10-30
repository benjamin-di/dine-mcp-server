"""Venue resolution: from a link, a Google place id or a name to a venue with its booking channels.

Order, stopping when confident:

1. a booking link, matched against provider URL patterns (never fetched);
2. a Google place id, or a place search by name and area, then the place's reservation links;
3. venue search on the region's enabled discovery providers when the place source cannot answer;
4. curated channels (``venues/*.toml``) for what neither knows (WhatsApp-only, release times).

Below the confidence threshold the answer is a list of candidates, never a guess.
"""

from __future__ import annotations

import difflib
import logging
import re
import time
import tomllib
import unicodedata
from dataclasses import dataclass, replace
from importlib import resources
from typing import Any

from . import links
from .definitions import DefinitionError, ProviderDefinition, Region
from .models import AccessMode, Channel, ProviderVenue, ProviderVenueRef
from .places import Place, PlaceSource, PlaceSourceError
from .state import PLACE_TTL, Store
from .widgets import WebsiteDiscovery

log = logging.getLogger("dine.resolver")

CONFIDENT = 0.85
MARGIN = 0.1
_NOISE = re.compile(r"\b(the|restaurant|restaurante|ristorante|bar|cafe|café|by|and|&|ltd|limited)\b")


@dataclass(frozen=True)
class Candidate:
    name: str
    google_place_id: str | None
    address: str | None
    category: str | None
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "google_place_id": self.google_place_id,
            "address": self.address,
            "category": self.category,
            "confidence": round(self.confidence, 2),
        }


@dataclass
class Resolution:
    venues: list[dict[str, Any]]
    candidates: list[Candidate]
    note: str | None = None


def normalise(name: str) -> str:
    text = unicodedata.normalize("NFKC", name).casefold()
    text = re.sub(r"[^\w\s&]", " ", text)
    text = _NOISE.sub(" ", text)
    return " ".join(text.split())


def similarity(query: str, name: str) -> float:
    a, b = normalise(query), normalise(name)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    if (a in b or b in a) and min(len(a), len(b)) >= 3:
        ratio = max(ratio, 0.9)
    return ratio


def load_curated() -> dict[str, dict[str, Any]]:
    """Curated channel facts keyed by Google place id (public facts from primary sources only)."""
    out: dict[str, dict[str, Any]] = {}
    folder = resources.files("dine_mcp") / "venues"
    for entry in sorted(folder.iterdir(), key=lambda e: e.name):
        if not entry.name.endswith(".toml"):
            continue
        try:
            data = tomllib.loads(entry.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise DefinitionError(f"venues/{entry.name}: {exc}") from None
        for venue in data.get("venue", []):
            place_id = venue.get("google_place_id")
            if not isinstance(place_id, str) or not place_id or not isinstance(venue.get("source"), str):
                raise DefinitionError(f"venues/{entry.name}: each venue needs google_place_id and source")
            out[place_id] = venue
    return out


class Resolver:
    def __init__(
        self,
        definitions: dict[str, ProviderDefinition],
        regions: dict[str, Region],
        store: Store,
        place_source: PlaceSource | None,
        curated: dict[str, dict[str, Any]] | None = None,
        websites: WebsiteDiscovery | None = None,
    ) -> None:
        self.definitions = definitions
        self.regions = regions
        self.store = store
        self.places = place_source
        self.curated = curated if curated is not None else load_curated()
        self.websites = websites

    # --- channels -------------------------------------------------------------------------------------

    def channel_for_link(self, url: str, source: str, provider_name: str = "") -> Channel | None:
        cleaned = links.clean(url)
        if cleaned is None:
            return None
        ref = links.match(cleaned, self.definitions)
        if ref is not None:
            definition = self.definitions[ref.provider]
            return Channel(ref.provider, definition.access_mode, ref=ref.ref, url=cleaned, source=source, verified=True)
        # A provider we do not support (yet): a booking page the person can open, marked unverified.
        return Channel(
            "booking_page", AccessMode.LISTING, url=cleaned, text=provider_name or links.host_of(cleaned), source=source
        )

    def channels_for_place(self, place: Place) -> list[Channel]:
        channels: list[Channel] = []
        seen: set[str] = set()
        for uri, provider in place.reservation_links:
            channel = self.channel_for_link(uri, "google", provider)
            if channel is not None and (channel.url or "") not in seen:
                seen.add(channel.url or "")
                channels.append(channel)
        curated = self.curated.get(place.place_id, {})
        for item in curated.get("channels", []):
            kind = item.get("kind")
            if kind in ("phone", "messaging", "website"):
                channels.append(
                    Channel(kind, AccessMode.DIRECTORY, url=item.get("url"), text=item.get("text"), source="curated")
                )
            elif kind == "link" and item.get("url"):
                channel = self.channel_for_link(item["url"], "curated")
                if channel is not None and (channel.url or "") not in seen:
                    seen.add(channel.url or "")
                    channels.append(channel)
        if place.phone:
            channels.append(Channel("phone", AccessMode.DIRECTORY, text=place.phone, source="google"))
        if place.website:
            website = links.clean(place.website) or place.website
            channels.append(Channel("website", AccessMode.DIRECTORY, url=website, source="google"))
        return channels

    # --- records -----------------------------------------------------------------------------------------

    def save_from_place(self, place: Place, extra: list[Channel] | None = None) -> str:
        channels = self.channels_for_place(place)
        aliases = [f"google:{place.place_id}"] + [ProviderVenueRef(c.kind, c.ref).alias for c in channels if c.ref]
        existing = {venue_id for alias in aliases if (venue_id := self.store.venue_for_alias(alias)) is not None}
        # Provider-verified facts have their own provenance. A place refresh must not erase them
        # or turn them back into Google facts that disappear when the place cache expires.
        provider_facts: dict[str, Any] = {}
        provider_channels: list[Channel] = []
        for venue_id in sorted(existing):
            record = self.store.get_venue(venue_id) or {}
            provider_facts.update(
                {key: record[key] for key in ("name", "country", "time_zone", "address") if key in record}
            )
            for stored in record.get("channels", []):
                if stored.get("source") == "provider" and stored.get("ref"):
                    provider_channels.append(
                        Channel(
                            stored["kind"],
                            AccessMode(stored["access_mode"]),
                            ref=stored["ref"],
                            url=stored.get("url"),
                            source="provider",
                            verified=True,
                        )
                    )
        for verified_channel in provider_channels:
            channels = [c for c in channels if (c.kind, c.ref) != (verified_channel.kind, verified_channel.ref)]
            channels.insert(0, verified_channel)
        website = self.websites.cached(place) if self.websites is not None else None
        if website is not None and not any(c.ref or c.kind == "booking_page" for c in channels):
            for url in website["urls"]:
                channel = self.channel_for_link(url, "website")
                if channel is not None:
                    # A site's widget is a lead, not independent verification of venue identity.
                    channels.insert(0, replace(channel, verified=False))
        for channel in extra or []:
            if all((c.kind, c.ref) != (channel.kind, channel.ref) for c in channels):
                channels.insert(0, channel)
        # Provisional website associations must expire, never become permanent venue aliases.
        refs = [ProviderVenueRef(c.kind, c.ref) for c in channels if c.ref and c.source != "website"]
        cached_at = self.store.place_fetched_at(place.place_id) or time.time()
        serialised = [c.to_dict() for c in channels]
        for stored_channel in serialised:
            if stored_channel.get("source") == "google":
                stored_channel["expires_at"] = cached_at + PLACE_TTL
            elif stored_channel.get("source") == "website" and website is not None:
                stored_channel["expires_at"] = min(website["expires_at"], cached_at + PLACE_TTL)
        record = {
            **provider_facts,
            "google_place_id": place.place_id,
            "channels": serialised,
            "rules_note": self.curated.get(place.place_id, {}).get("booking_note"),
        }
        aliases = [f"google:{place.place_id}", *(ref.alias for ref in refs)]
        venue_id = self.store.save_venue(record, aliases)
        for other in existing - {venue_id}:
            if other:
                self.store.merge_venues(venue_id, other)
        return venue_id

    async def enrich(self, place: Place, person: str) -> str:
        if self.websites is not None and not any(
            c.ref or c.kind == "booking_page" for c in self.channels_for_place(place)
        ):
            await self.websites.discover(place, person)
        return self.save_from_place(place)

    def save_from_provider(self, venue: ProviderVenue) -> str:
        definition = self.definitions[venue.ref.provider]
        channel = Channel(
            venue.ref.provider,
            definition.access_mode,
            ref=venue.ref.ref,
            url=venue.booking_url,
            source="provider",
            verified=True,
        )
        existing_id = self.store.venue_for_alias(venue.ref.alias)
        record = self.store.get_venue(existing_id) if existing_id else None
        if record is None:
            record = {
                "google_place_id": None,
            }
        record.update(
            {"name": venue.name, "country": venue.country, "time_zone": venue.time_zone, "address": venue.address}
        )
        record["channels"] = [channel.to_dict()] + [
            c for c in record.get("channels", []) if (c.get("kind"), c.get("ref")) != (channel.kind, channel.ref)
        ]
        return self.store.save_venue(record, [venue.ref.alias])

    # --- resolution ----------------------------------------------------------------------------------------

    async def place(self, place_id: str) -> Place | None:
        cached = self.store.cached_place(place_id)
        if cached is not None:
            return Place.from_cache(cached)
        if self.places is None:
            raise PlaceSourceError("no_place_source")
        place = await self.places.get(place_id)
        if place is not None:
            self.store.cache_place(place.place_id, place.to_cache())
        return place

    async def from_place_id(self, place_id: str, person: str = "local") -> Resolution:
        place = await self.place(place_id)
        if place is None:
            return Resolution([], [], "No place has this google_place_id; search by name instead.")
        return Resolution(
            [{"venue_id": await self.enrich(place, person), "confidence": 1.0, "evidence": "place id"}], []
        )

    async def search(
        self, name: str, area: str | None, country: str | None, limit: int, person: str = "local"
    ) -> Resolution:
        region = self.regions.get(country or "")
        text = ", ".join(part for part in (name, area, region.name if region else country) if part)
        if self.places is None:
            raise PlaceSourceError("no_place_source")
        places = await self.places.search(text, limit=max(limit, 3))
        scored = sorted(((similarity(name, p.name), p) for p in places if p.place_id), key=lambda x: -x[0])
        if not scored:
            return Resolution([], [], "No place matches; check the spelling or add the area.")
        best_score, best = scored[0]
        runner_up = scored[1][0] if len(scored) > 1 else 0.0
        resolved: list[dict[str, Any]] = []
        candidates = scored
        if best_score >= CONFIDENT and best_score - runner_up >= MARGIN:
            # Search rows are sparse even when they have a booking link. Keep fresh details
            # and their original TTL, or fetch details before falling back to the search row.
            cached = self.store.cached_place(best.place_id)
            if cached is not None and best.reservation_links:
                cached["reservation_links"] = [list(link) for link in best.reservation_links]
                self.store.cache_place(best.place_id, cached, fetched_at=self.store.place_fetched_at(best.place_id))
            try:
                full = await self.place(best.place_id)
            except PlaceSourceError:
                full = None
            if full is None:
                full = best
                self.store.cache_place(best.place_id, best.to_cache())
            resolved.append(
                {
                    "venue_id": await self.enrich(full, person),
                    "confidence": round(best_score, 2),
                    "evidence": "name match on Google Maps",
                }
            )
            candidates = scored[1:]
        return Resolution(
            resolved,
            [Candidate(p.name, p.place_id, p.address, p.category, score) for score, p in candidates[:limit]],
            None if resolved else "Several places match; call again with the right google_place_id.",
        )
