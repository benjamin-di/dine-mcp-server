"""The composition root: settings, definitions, state, transports, providers, place source, service.

The serving composition never signs in to anything and holds no provider credentials.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import httpx

from .definitions import DefinitionError, load_overrides, load_providers, load_regions
from .http_boundary import HttpPolicy
from .places import PlaceSource
from .providers import REGISTRY
from .providers.base import Provider
from .resolver import Resolver
from .service import DineService, Settings, build_effective
from .state import Store
from .transport import DEFAULT_USER_AGENT, Transport, WebsiteTransport, provider_client
from .widgets import WebsiteDiscovery


class ConfigError(Exception):
    """Bad configuration; raised at startup only."""


@dataclass
class App:
    service: DineService
    store: Store
    transports: dict[str, Transport]
    client: httpx.AsyncClient
    http_policy: HttpPolicy
    website_client: httpx.AsyncClient | None = None
    apps_enabled: bool = False

    async def aclose(self) -> None:
        try:
            await self.client.aclose()
        finally:
            try:
                if self.website_client is not None and self.website_client is not self.client:
                    await self.website_client.aclose()
            finally:
                self.store.close()


def default_state_dir(env: Mapping[str, str]) -> Path:
    if env.get("DINE_STATE_DIR"):
        return Path(env["DINE_STATE_DIR"])
    base = Path(env.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    return base / "dine-mcp"


def build_place_source(env: Mapping[str, str], state_dir: Path) -> PlaceSource | None:
    choice = (env.get("DINE_PLACES") or "google").strip()
    if choice == "none":
        return None
    if choice != "google":
        raise ConfigError("DINE_PLACES is google or none")
    from .places_google import GooglePlaces

    try:
        budget = int(env.get("DINE_GOOGLE_DAILY_BUDGET") or "200")
    except ValueError:
        raise ConfigError("DINE_GOOGLE_DAILY_BUDGET must be a whole number") from None
    return GooglePlaces(state_dir / "google", daily_budget=budget, language=env.get("DINE_LANGUAGE") or "en")


def build_app(
    env: Mapping[str, str] | None = None,
    *,
    client: httpx.AsyncClient | None = None,
    place_source: PlaceSource | None | str = "env",
    providers: dict[str, Provider] | None = None,
    website_client: httpx.AsyncClient | None = None,
) -> App:
    env = os.environ if env is None else env
    try:
        http_policy = HttpPolicy.from_env(env)
    except ValueError as exc:
        raise ConfigError(str(exc)) from None
    try:
        definitions = load_providers()
        regions = load_regions(definitions)
        overrides_path = env.get("DINE_OVERRIDES")
        overrides = load_overrides(Path(overrides_path) if overrides_path else None, set(definitions))
        effective = build_effective(definitions, overrides)
    except DefinitionError as exc:
        raise ConfigError(str(exc)) from None
    registry = providers if providers is not None else {key: cls() for key, cls in REGISTRY.items()}
    if set(registry) != set(definitions):
        raise ConfigError(f"providers {sorted(registry)} and definitions {sorted(definitions)} disagree")
    website_setting = env.get("DINE_WEBSITE_DISCOVERY", "0")
    if website_setting not in ("0", "1"):
        raise ConfigError("DINE_WEBSITE_DISCOVERY must be 0 or 1")
    website_enabled = website_setting == "1" and not overrides.disabled
    apps_setting = env.get("DINE_APPS", "0")
    if apps_setting not in ("0", "1"):
        raise ConfigError("DINE_APPS must be 0 or 1")
    if website_enabled and website_client is not None and website_client is client:
        raise ConfigError("Website discovery requires a separate HTTP client")
    state_dir = default_state_dir(env)
    store = Store(state_dir, website_discovery=website_enabled)
    http = client or provider_client()
    transports: dict[str, Transport] = {}
    for key, provider in registry.items():
        transport = Transport(
            definitions[key],
            provider.operations,
            store,
            http,
            user_agent=env.get("DINE_USER_AGENT") or DEFAULT_USER_AGENT,
        )
        provider.bind(transport)
        transports[key] = transport
    places = build_place_source(env, state_dir) if place_source == "env" else place_source
    website_http = (website_client or provider_client()) if website_enabled else None
    websites = WebsiteDiscovery(definitions, store, WebsiteTransport(store, website_http)) if website_http else None
    resolver = Resolver(definitions, regions, store, places if not isinstance(places, str) else None, websites=websites)
    try:
        deadline = float(env.get("DINE_DEADLINE_SECONDS") or "30")
    except ValueError:
        raise ConfigError("DINE_DEADLINE_SECONDS must be a number") from None
    if not 5 <= deadline <= 200:
        raise ConfigError("DINE_DEADLINE_SECONDS must be between 5 and 200")
    service = DineService(
        definitions,
        regions,
        registry,
        store,
        resolver,
        effective,
        disabled=overrides.disabled,
        settings=Settings(deadline_seconds=deadline),
    )
    return App(service, store, transports, http, http_policy, website_http, apps_setting == "1")
