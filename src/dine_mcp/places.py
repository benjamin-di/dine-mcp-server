"""Where venues come from before a provider is known: a place source (Google Maps through maps-mcp).

A place is identity and contact facts: a stable place id, name, address, coordinates, time zone,
phone, website, opening hours and, most usefully, the reservation links Google shows ("Reserve a
table"), which usually name the booking provider and the venue on it. dine-mcp asks a place source
only to resolve a venue, never per availability check.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class Place:
    place_id: str
    name: str
    address: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    time_zone: str | None = None
    phone: str | None = None
    website: str | None = None
    category: str | None = None
    reservation_links: tuple[tuple[str, str], ...] = ()  # (uri, provider as the source names it)
    opening_hours: dict[str, Any] | None = None
    maps_url: str | None = None
    attribution: str = "Map data © Google"
    extra: dict[str, Any] = field(default_factory=dict)

    def to_cache(self) -> dict[str, Any]:
        return {
            "place_id": self.place_id,
            "name": self.name,
            "address": self.address,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "time_zone": self.time_zone,
            "phone": self.phone,
            "website": self.website,
            "category": self.category,
            "reservation_links": [list(link) for link in self.reservation_links],
            "opening_hours": self.opening_hours,
            "maps_url": self.maps_url,
        }

    @classmethod
    def from_cache(cls, data: dict[str, Any]) -> Place:
        return cls(
            place_id=data["place_id"],
            name=data["name"],
            address=data.get("address"),
            latitude=data.get("latitude"),
            longitude=data.get("longitude"),
            time_zone=data.get("time_zone"),
            phone=data.get("phone"),
            website=data.get("website"),
            category=data.get("category"),
            reservation_links=tuple((str(u), str(p)) for u, p in data.get("reservation_links") or []),
            opening_hours=data.get("opening_hours"),
            maps_url=data.get("maps_url"),
        )


class PlaceSourceError(Exception):
    """The place source could not answer (blocked, budget, format change, network)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class PlaceSource(Protocol):
    name: str

    async def search(self, text: str, limit: int) -> list[Place]:
        """Places matching a free-text query ("Harbour Grill, Raffles Place, Singapore"); may omit reservation links."""

    async def get(self, place_id: str) -> Place | None:
        """One place in full, with reservation links; None when the id is unknown."""


def place_from_maps(data: dict[str, Any]) -> Place:
    """A Place from maps-mcp's Places-API-shaped dict."""
    name = _text(data.get("displayName"))
    location = data.get("location") or {}
    links = tuple(
        (str(link.get("uri")), str(link.get("provider") or ""))
        for link in data.get("reservationLinks") or []
        if isinstance(link, dict) and link.get("uri")
    )
    return Place(
        place_id=str(data.get("id") or ""),
        name=str(name or ""),
        address=data.get("formattedAddress"),
        latitude=location.get("latitude"),
        longitude=location.get("longitude"),
        time_zone=(data.get("timeZone") or {}).get("id") if isinstance(data.get("timeZone"), dict) else None,
        phone=data.get("internationalPhoneNumber") or data.get("nationalPhoneNumber"),
        website=data.get("websiteUri"),
        category=_text(data.get("primaryTypeDisplayName")),
        reservation_links=links,
        opening_hours=data.get("currentOpeningHours"),
        maps_url=data.get("googleMapsUri"),
    )


def _text(value: Any) -> str | None:
    """Google's {text, languageCode} objects (or a plain string) as text."""
    if isinstance(value, dict):
        value = value.get("text")
    return value if isinstance(value, str) and value else None
