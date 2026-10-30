"""What a provider adapter is.

Required: ``Provider``. Optional capabilities, checked with ``isinstance``: ``Calendar`` (one request
answers a range of dates) and ``VenueSearch`` (find venues by name). ``Bookable`` is a reserved name
and deliberately not defined: dine-mcp checks availability and never books.
"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import date, time
from typing import Protocol, runtime_checkable

from ..definitions import ProviderDefinition
from ..models import BookingLink, ProviderVenue, ProviderVenueRef, Slot, VenueStatus
from ..transport import Operation, Transport


@dataclass(frozen=True)
class Window:
    """What to check, in the venue's local time."""

    dates: tuple[date, ...]
    time_from: time
    time_to: time  # < time_from crosses midnight; equality means one local time
    party_size: int

    def contains(self, at: time) -> bool:
        if self.time_from <= self.time_to:
            return self.time_from <= at <= self.time_to
        return at >= self.time_from or at <= self.time_to


@dataclass
class Observation:
    """One provider's answer for one venue: slots per date, or a status that ends the check."""

    status: VenueStatus  # for checked dates without slots and without their own entry in day_status
    slots: list[Slot]
    dates_checked: list[str]
    note: str | None = None
    max_party: int | None = None
    day_status: dict[str, VenueStatus] = field(default_factory=dict)  # e.g. {"2026-11-08": CLOSED}

    complete: bool = True  # false while a time-window batch is only partly observed


OBSERVER: ContextVar[Callable[[Observation], None] | None] = ContextVar("dine_observer", default=None)


def publish(observation: Observation) -> None:
    """Retain a completed read before the adapter performs its next request."""
    observer = OBSERVER.get()
    if observer is not None:
        observer(observation)


@runtime_checkable
class Provider(Protocol):
    definition: ProviderDefinition
    operations: dict[str, Operation]

    def bind(self, transport: Transport) -> None:
        """Receive the transport (the only way to reach the network)."""

    async def get_venue(self, ref: ProviderVenueRef, person: str) -> ProviderVenue:
        """The provider's description of a venue; ProviderError(UNKNOWN_VENUE) if it has none."""

    def cost(self, ref: ProviderVenueRef, window: Window) -> int:
        """Provider requests ``availability`` will make for this window (computed before any request)."""

    async def availability(self, venue: ProviderVenue, window: Window, person: str) -> Observation:
        """Slots within the window. Must not make more requests than ``cost`` said."""

    def booking_link(self, venue: ProviderVenue, day: date | None, at: time | None, party: int | None) -> BookingLink:
        """The provider's own booking page, prefilled where it supports that."""


@runtime_checkable
class VenueSearch(Protocol):
    async def search_venues(self, name: str, country: str | None, person: str) -> list[ProviderVenue]:
        """Venues on this provider matching a name (a few at most)."""
