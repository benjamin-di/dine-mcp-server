"""The Google Maps place source, through maps-mcp's library interface (``maps_mcp.places``).

maps-mcp reads the signed-out google.com/maps web page with its own pacing, daily budget, breaker
and cache, in its own state directory. Only place ids leave this module for long-term storage.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .places import Place, PlaceSourceError, place_from_maps


class GooglePlaces:
    name = "google"

    def __init__(self, state_dir: Path, *, daily_budget: int = 200, language: str = "en") -> None:
        self.state_dir = state_dir
        self.daily_budget = daily_budget
        self.language = language
        self._client: Any = None

    def _maps(self) -> Any:
        if self._client is None:
            from maps_mcp.places import PlacesClient

            self.state_dir.mkdir(parents=True, exist_ok=True)
            self._client = PlacesClient(
                state_dir=self.state_dir, daily_request_budget=self.daily_budget, language=self.language
            )
        return self._client

    async def search(self, text: str, limit: int) -> list[Place]:
        from maps_mcp.places import MapsError

        try:
            found = await self._maps().search_places(text[:200], page_size=max(1, min(limit, 20)))
        except MapsError as exc:
            raise PlaceSourceError(exc.code) from None
        return [place_from_maps(item) for item in found.get("places") or [] if isinstance(item, dict)]

    async def get(self, place_id: str) -> Place | None:
        from maps_mcp.places import MapsError

        try:
            found = await self._maps().get_place(place_id)
        except MapsError as exc:
            if exc.code in ("place_not_found", "invalid_argument"):
                return None
            raise PlaceSourceError(exc.code) from None
        return place_from_maps(found) if found.get("id") else None

    def status(self) -> dict[str, Any] | None:
        return self._client.status() if self._client is not None else None
