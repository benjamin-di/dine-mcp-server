"""Provider definitions, regions and the operator's overrides: data, validated at startup.

- ``provider_definitions/<key>.toml``: what a provider is (access mode, maturity, countries, hosts, URL
  patterns, pacing, budgets, cache lifetime, terms note, credit). Behaviour, including every request a
  provider may make, is code in ``providers/<key>.py``.
- ``regions/<cc>.toml``: per country, which providers matter and in what order, plus locale, currency
  and time zones. The code never branches on country.
- The overrides file (``DINE_OVERRIDES``): the operator's only switch. It can turn everything off,
  enable an experimental provider, disable one, or lower its access mode; never raise it.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from datetime import date
from importlib import resources
from pathlib import Path
from typing import Any

from .models import AccessMode, Maturity

SCHEMA = 1
KEY = re.compile(r"[a-z][a-z0-9]{1,31}\Z")
COUNTRY = re.compile(r"[A-Z]{2}\Z")
# public > listing > directory; signed_in sits beside public (it needs an account) and lowers to listing.
_RANK = {AccessMode.PUBLIC: 3, AccessMode.SIGNED_IN: 3, AccessMode.LISTING: 2, AccessMode.DIRECTORY: 1}


class DefinitionError(Exception):
    """A definition, region or overrides file is invalid; raised at startup only."""


@dataclass(frozen=True)
class ProviderDefinition:
    key: str
    name: str
    access_mode: AccessMode
    maturity: Maturity
    countries: tuple[str, ...]
    hosts: tuple[str, ...]
    url_patterns: tuple[re.Pattern[str], ...]
    link_sources: tuple[str, ...]
    request_interval_seconds: float
    daily_request_budget: int
    person_daily_share: float
    cache_ttl_seconds: int
    terms: str
    credit: str
    canary: dict[str, Any] = field(default_factory=dict)
    last_verified: str | None = None


@dataclass(frozen=True)
class Region:
    country: str
    name: str
    time_zones: tuple[str, ...]
    currency: str
    locales: tuple[str, ...]
    providers: tuple[str, ...]
    place_source: str
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Overrides:
    disabled: bool = False
    enabled: frozenset[str] = frozenset()  # experimental providers switched on
    switched_off: frozenset[str] = frozenset()
    lowered: dict[str, AccessMode] = field(default_factory=dict)


@dataclass(frozen=True)
class EffectiveProvider:
    """A definition as the operator's overrides leave it."""

    definition: ProviderDefinition
    enabled: bool
    access_mode: AccessMode
    reason: str | None = None  # why it is off or lowered


def _require(data: dict[str, Any], name: str, kind: type | tuple[type, ...], where: str) -> Any:
    value = data.get(name)
    if not isinstance(value, kind) or isinstance(value, bool) and kind is not bool:
        raise DefinitionError(f"{where}: {name} is missing or not {getattr(kind, '__name__', kind)}")
    return value


def _strings(data: dict[str, Any], name: str, where: str, *, required: bool = True) -> tuple[str, ...]:
    value = data.get(name, None if required else [])
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise DefinitionError(f"{where}: {name} must be a list of strings")
    return tuple(value)


def parse_provider(data: dict[str, Any], where: str) -> ProviderDefinition:
    if data.get("schema") != SCHEMA:
        raise DefinitionError(f"{where}: schema must be {SCHEMA}")
    key = _require(data, "key", str, where)
    if not KEY.match(key):
        raise DefinitionError(f"{where}: key must be 2-32 lower-case letters and digits")
    try:
        mode = AccessMode(_require(data, "access_mode", str, where))
        maturity = Maturity(_require(data, "maturity", str, where))
    except ValueError as exc:
        raise DefinitionError(f"{where}: {exc}") from None
    countries = _strings(data, "countries", where)
    if not all(COUNTRY.match(c) for c in countries):
        raise DefinitionError(f"{where}: countries are ISO 3166-1 alpha-2 codes")
    hosts = _strings(data, "hosts", where, required=mode is not AccessMode.DIRECTORY)
    patterns = []
    for raw in _strings(data, "url_patterns", where, required=False):
        try:
            pattern = re.compile(raw)
        except re.error as exc:
            raise DefinitionError(f"{where}: bad url pattern {raw!r}: {exc}") from None
        if "ref" not in pattern.groupindex or not raw.startswith("^https://"):
            raise DefinitionError(f"{where}: url patterns start with ^https:// and capture (?P<ref>...)")
        patterns.append(pattern)
    interval = float(_require(data, "request_interval_seconds", (int, float), where))
    budget = _require(data, "daily_request_budget", int, where)
    share = float(_require(data, "person_daily_share", (int, float), where))
    ttl = _require(data, "cache_ttl_seconds", int, where)
    if interval < 0.5 or budget < 1 or not 0 < share <= 1 or not 30 <= ttl <= 86_400:
        raise DefinitionError(f"{where}: pacing, budget, share or cache lifetime out of range")
    canary = data.get("canary", {})
    if not isinstance(canary, dict):
        raise DefinitionError(f"{where}: canary must be a table")
    verified = data.get("last_verified")
    if verified is not None:
        try:
            if not isinstance(verified, str) or date.fromisoformat(verified).isoformat() != verified:
                raise ValueError
        except ValueError:
            raise DefinitionError(f"{where}: last_verified must be a YYYY-MM-DD string") from None
    return ProviderDefinition(
        key=key,
        name=_require(data, "name", str, where),
        access_mode=mode,
        maturity=maturity,
        countries=countries,
        hosts=hosts,
        url_patterns=tuple(patterns),
        link_sources=_strings(data, "link_sources", where, required=False),
        request_interval_seconds=interval,
        daily_request_budget=budget,
        person_daily_share=share,
        cache_ttl_seconds=ttl,
        terms=_require(data, "terms", str, where),
        credit=str(data.get("credit", "")),
        canary=canary,
        last_verified=verified,
    )


def parse_region(data: dict[str, Any], where: str) -> Region:
    if data.get("schema") != SCHEMA:
        raise DefinitionError(f"{where}: schema must be {SCHEMA}")
    country = _require(data, "country", str, where)
    if not COUNTRY.match(country):
        raise DefinitionError(f"{where}: country is an ISO 3166-1 alpha-2 code")
    return Region(
        country=country,
        name=_require(data, "name", str, where),
        time_zones=_strings(data, "time_zones", where),
        currency=_require(data, "currency", str, where),
        locales=_strings(data, "locales", where),
        providers=_strings(data, "providers", where, required=False),
        place_source=str(data.get("place_source", "google")),
        notes=_strings(data, "notes", where, required=False),
    )


def parse_overrides(data: dict[str, Any], known: set[str], where: str) -> Overrides:
    disabled = data.get("disabled", False)
    if not isinstance(disabled, bool):
        raise DefinitionError(f"{where}: disabled must be true or false")
    enabled: set[str] = set()
    off: set[str] = set()
    lowered: dict[str, AccessMode] = {}
    providers = data.get("providers", {})
    if not isinstance(providers, dict):
        raise DefinitionError(f"{where}: providers must be a table")
    unknown = set(data) - {"disabled", "providers"}
    if unknown:
        raise DefinitionError(f"{where}: unknown settings {sorted(unknown)}")
    for key, table in providers.items():
        if key not in known:
            raise DefinitionError(f"{where}: unknown provider {key!r}")
        if not isinstance(table, dict) or set(table) - {"enabled", "access_mode"}:
            raise DefinitionError(f"{where}: providers.{key} takes only enabled and access_mode")
        if "enabled" in table:
            if not isinstance(table["enabled"], bool):
                raise DefinitionError(f"{where}: providers.{key}.enabled must be true or false")
            (enabled if table["enabled"] else off).add(key)
        if "access_mode" in table:
            try:
                lowered[key] = AccessMode(table["access_mode"])
            except ValueError:
                raise DefinitionError(f"{where}: providers.{key}.access_mode is not a mode") from None
    return Overrides(disabled, frozenset(enabled), frozenset(off), lowered)


def _toml_files(package: str) -> list[tuple[str, dict[str, Any]]]:
    out = []
    folder = resources.files("dine_mcp") / package
    for entry in sorted(folder.iterdir(), key=lambda e: e.name):
        if entry.name.endswith(".toml"):
            try:
                out.append((f"{package}/{entry.name}", tomllib.loads(entry.read_text(encoding="utf-8"))))
            except tomllib.TOMLDecodeError as exc:
                raise DefinitionError(f"{package}/{entry.name}: {exc}") from None
    return out


def load_providers() -> dict[str, ProviderDefinition]:
    out: dict[str, ProviderDefinition] = {}
    for where, data in _toml_files("provider_definitions"):
        definition = parse_provider(data, where)
        if Path(where).stem != definition.key:
            raise DefinitionError(f"{where}: file name must be the key {definition.key!r}")
        out[definition.key] = definition
    return out


def load_regions(providers: dict[str, ProviderDefinition]) -> dict[str, Region]:
    out: dict[str, Region] = {}
    for where, data in _toml_files("regions"):
        region = parse_region(data, where)
        if Path(where).stem != region.country.lower():
            raise DefinitionError(f"{where}: file name must be the country code in lower case")
        missing = [p for p in region.providers if p not in providers]
        if missing:
            raise DefinitionError(f"{where}: unknown providers {missing}")
        out[region.country] = region
    return out


def load_overrides(path: Path | None, known: set[str]) -> Overrides:
    if path is None:
        return Overrides()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise DefinitionError(f"overrides file {path}: {exc.strerror}") from None
    except tomllib.TOMLDecodeError as exc:
        raise DefinitionError(f"overrides file {path}: {exc}") from None
    return parse_overrides(data, known, str(path))


def effective(definition: ProviderDefinition, overrides: Overrides) -> EffectiveProvider:
    key = definition.key
    if overrides.disabled:
        return EffectiveProvider(definition, False, definition.access_mode, "all providers are switched off")
    if key in overrides.switched_off:
        return EffectiveProvider(definition, False, definition.access_mode, "switched off by the operator")
    if definition.maturity is Maturity.EXPERIMENTAL and key not in overrides.enabled:
        return EffectiveProvider(definition, False, definition.access_mode, "experimental; not enabled")
    mode = definition.access_mode
    lowered = overrides.lowered.get(key)
    if lowered is not None:
        if _RANK[lowered] > _RANK[mode] or (lowered is AccessMode.SIGNED_IN and mode is AccessMode.PUBLIC):
            raise DefinitionError(f"overrides: {key} cannot be raised from {mode.value} to {lowered.value}")
        if lowered is not mode:
            return EffectiveProvider(definition, True, lowered, "lowered by the operator")
    return EffectiveProvider(definition, True, mode)
