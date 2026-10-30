"""The data model: closed, validated dataclasses shared by providers, resolver and service.

Times are ISO 8601 with the venue's offset; dates are the venue's local dates. ``to_dict`` gives the
JSON shape tools return (open: clients must ignore keys they do not know).
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class AccessMode(StrEnum):
    PUBLIC = "public"  # availability from the guest widget without an account
    SIGNED_IN = "signed_in"  # availability needs the person's own diner account
    LISTING = "listing"  # venue facts and a booking link; no availability
    DIRECTORY = "directory"  # no booking system: contact channel only


class Maturity(StrEnum):
    EXPERIMENTAL = "experimental"
    BETA = "beta"
    STABLE = "stable"


class SlotKind(StrEnum):
    BOOKABLE = "bookable"
    REQUEST = "request"
    WAITLIST = "waitlist"


class VenueStatus(StrEnum):
    OK = "ok"
    NONE_AVAILABLE = "none_available"
    NOT_YET_OPEN = "not_yet_open"
    PARTY_TOO_SMALL = "party_too_small"
    PARTY_TOO_LARGE = "party_too_large"
    CLOSED = "closed"
    CONTACT_ONLY = "contact_only"
    NOT_CONNECTED = "not_connected"
    BLOCKED = "blocked"
    RATE_LIMITED = "rate_limited"
    UNAVAILABLE = "unavailable"
    UNSUPPORTED = "unsupported"
    UNKNOWN_VENUE = "unknown_venue"
    SKIPPED_DEADLINE = "skipped_deadline"


@dataclass(frozen=True)
class ProviderVenueRef:
    """A venue on one provider: the provider key and the provider's own id for it."""

    provider: str
    ref: str

    @property
    def alias(self) -> str:
        return f"{self.provider}:{self.ref}"


@dataclass(frozen=True)
class Money:
    minor: int  # in the currency's minor unit (cents); JPY has none, so yen
    currency: str
    display: str

    def to_dict(self) -> dict[str, Any]:
        return {"minor": self.minor, "currency": self.currency, "display": self.display}


@dataclass(frozen=True)
class BookingLink:
    url: str
    prefills: tuple[str, ...] = ()  # which of "date", "time", "party_size" the link carries

    def to_dict(self) -> dict[str, Any]:
        return {"url": self.url, "prefills": list(self.prefills)}


@dataclass(frozen=True)
class Slot:
    start: str  # ISO 8601 with the venue's offset
    party_size: int
    kind: SlotKind = SlotKind.BOOKABLE
    area: str | None = None
    experience: str | None = None
    price: Money | None = None
    requires_card: bool | None = None
    booking_link: BookingLink | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": self.start,
            "party_size": self.party_size,
            "kind": self.kind.value,
            "area": self.area,
            "experience": self.experience,
            "price": self.price.to_dict() if self.price else None,
            "requires_card": self.requires_card,
            "booking_link": self.booking_link.to_dict() if self.booking_link else None,
        }


@dataclass(frozen=True)
class BookingRules:
    """What the provider publishes about booking; every field may be unknown (None)."""

    window_days: int | None = None
    release_time: str | None = None  # local HH:MM when new dates open
    min_party: int | None = None
    max_party: int | None = None
    requires_card: bool | None = None
    cancellation: str | None = None
    source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass(frozen=True)
class ProviderVenue:
    """A venue as one provider describes it (no Google data)."""

    ref: ProviderVenueRef
    name: str
    time_zone: str | None = None
    country: str | None = None
    locality: str | None = None
    address: str | None = None
    rules: BookingRules = field(default_factory=BookingRules)
    experiences: tuple[str, ...] = ()
    booking_url: str | None = None
    currency: str | None = None
    extra: dict[str, Any] = field(default_factory=dict, compare=False)  # provider-specific ids (e.g. shop id)


@dataclass(frozen=True)
class Channel:
    """One way to book: a provider (with its ref) or a contact channel (phone, messaging, website)."""

    kind: str  # a provider key, or "phone" / "messaging" / "website"
    access_mode: AccessMode
    ref: str | None = None
    url: str | None = None
    text: str | None = None  # phone number or contact text
    source: str | None = None  # where this channel came from: "google", "provider", "curated", "link"
    verified: bool = False  # a booking page on the provider's own host

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "access_mode": self.access_mode.value,
            "ref": self.ref,
            "url": self.url,
            "text": self.text,
            "source": self.source,
            "verified": self.verified,
        }


@dataclass
class VenueResult:
    """Availability of one venue through one channel."""

    venue_id: str
    venue_name: str
    channel: str
    status: VenueStatus
    time_zone: str | None = None
    slots: list[Slot] = field(default_factory=list)
    dates_checked: list[str] = field(default_factory=list)
    dates_not_checked: list[str] = field(default_factory=list)
    observed_at: str | None = None
    served_from: str | None = None  # "live" or "cache"
    booking_link: BookingLink | None = None
    opens_at: str | None = None  # not_yet_open: when the requested dates open, if known
    min_party: int | None = None  # party_too_small
    max_party: int | None = None  # party_too_large
    contact: str | None = None  # contact_only / party_too_large: phone or website
    note: str | None = None

    def to_dict(self, *, max_slots_per_day: int | None = None) -> dict[str, Any]:
        slots = self.slots
        omitted = 0
        if max_slots_per_day is not None:
            kept: list[Slot] = []
            per_day: dict[str, int] = {}
            for slot in slots:
                day = slot.start[:10]
                per_day[day] = per_day.get(day, 0) + 1
                if per_day[day] <= max_slots_per_day:
                    kept.append(slot)
            omitted = len(slots) - len(kept)
            slots = kept
        return {
            "venue_id": self.venue_id,
            "venue_name": self.venue_name,
            "channel": self.channel,
            "status": self.status.value,
            "time_zone": self.time_zone,
            "slots": [slot.to_dict() for slot in slots],
            "slots_omitted": omitted,
            "coverage": {"dates_checked": self.dates_checked, "dates_not_checked": self.dates_not_checked},
            "observed_at": self.observed_at,
            "served_from": self.served_from,
            "booking_link": self.booking_link.to_dict() if self.booking_link else None,
            "opens_at": self.opens_at,
            "min_party": self.min_party,
            "max_party": self.max_party,
            "contact": self.contact,
            "note": self.note,
        }
