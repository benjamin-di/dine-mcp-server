"""Durable state in one SQLite file under the state directory.

- ``people``: first-contact roster records; admission belongs to the authenticating gateway.
- ``venues`` / ``aliases``: venue ids are minted once and never re-derived; every way of naming a venue
  (a Google place id, a provider ref, a merged id) is an alias pointing at the current id.
- ``place_cache``: Google place details, kept at most ``PLACE_TTL``; the only Google field stored for
  longer is the place id (in ``venues`` and ``aliases``).
- ``budgets`` / ``breakers``: per provider per UTC day, and each person's share; breaker state survives
  restarts so a provider that pushed back is left alone.

Nothing personal is stored beyond the roster's opaque person ids.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PERSON_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
PLACE_TTL = 7 * 86_400
_ALPHABET = "abcdefghijkmnopqrstuvwxyz23456789"

SCHEMA = """
CREATE TABLE IF NOT EXISTS people (person TEXT PRIMARY KEY, display_name TEXT, added_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS venues (
    venue_id TEXT PRIMARY KEY, data TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS aliases (alias TEXT PRIMARY KEY, venue_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS place_cache (place_id TEXT PRIMARY KEY, data TEXT NOT NULL, fetched_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS website_cache (
    place_id TEXT PRIMARY KEY, url TEXT NOT NULL, data TEXT NOT NULL, expires_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS budgets (
    day TEXT NOT NULL, provider TEXT NOT NULL, person TEXT NOT NULL, used INTEGER NOT NULL,
    PRIMARY KEY (day, provider, person));
CREATE TABLE IF NOT EXISTS breakers (
    provider TEXT PRIMARY KEY, open_until REAL NOT NULL, strikes INTEGER NOT NULL, reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS canaries (provider TEXT PRIMARY KEY, checked_at REAL NOT NULL);
"""
ALL = "*"  # the budgets row for everyone behind the egress IP


def valid_person(person: object) -> bool:
    return isinstance(person, str) and bool(PERSON_ID.match(person))


def today() -> str:
    return datetime.now(UTC).date().isoformat()


@dataclass(frozen=True)
class Breaker:
    open_until: float
    strikes: int
    reason: str

    def is_open(self, now: float | None = None) -> bool:
        return (now or time.time()) < self.open_until


class Store:
    def __init__(self, state_dir: Path, *, website_discovery: bool = False) -> None:
        self.website_discovery = website_discovery
        state_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(state_dir, 0o700)
        except OSError:
            pass
        self.path = state_dir / "dine.sqlite3"
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=5000")
        self._db.executescript(SCHEMA)
        self.prune()
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield self._db
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            self._db.execute("COMMIT")

    def _one(self, sql: str, *args: Any) -> Any:
        with self._lock:
            return self._db.execute(sql, args).fetchone()

    # --- people -------------------------------------------------------------------------------------

    def add_person(self, person: str, display_name: str | None = None) -> bool:
        if not valid_person(person):
            raise ValueError("a person id is 1-64 lower-case letters, digits, dots, dashes or underscores")
        with self._tx() as db:
            if db.execute("SELECT 1 FROM people WHERE person=?", (person,)).fetchone():
                return False
            db.execute("INSERT INTO people VALUES (?,?,?)", (person, display_name, time.time()))
            return True

    def remove_person(self, person: str) -> bool:
        with self._tx() as db:
            return db.execute("DELETE FROM people WHERE person=?", (person,)).rowcount > 0

    def has_person(self, person: str) -> bool:
        return valid_person(person) and self._one("SELECT 1 FROM people WHERE person=?", person) is not None

    def people(self) -> list[str]:
        with self._lock:
            return [row[0] for row in self._db.execute("SELECT person FROM people ORDER BY person")]

    # --- venues -------------------------------------------------------------------------------------

    def venue_for_alias(self, alias: str) -> str | None:
        row = self._one("SELECT venue_id FROM aliases WHERE alias=?", alias)
        return row[0] if row else None

    def get_venue(self, venue_id: str) -> dict[str, Any] | None:
        current = self.venue_for_alias(f"id:{venue_id}") or venue_id
        row = self._one("SELECT data FROM venues WHERE venue_id=?", current)
        return self._fresh_record(json.loads(row[0])) if row else None

    def save_venue(self, data: dict[str, Any], aliases: list[str]) -> str:
        """Store a venue under its existing id (found through any alias) or a new one; returns the id."""
        now = time.time()
        with self._tx() as db:
            venue_id = data.get("venue_id")
            if not venue_id:
                for alias in aliases:
                    row = db.execute("SELECT venue_id FROM aliases WHERE alias=?", (alias,)).fetchone()
                    if row:
                        venue_id = row[0]
                        break
            if not venue_id:
                venue_id = "v_" + "".join(secrets.choice(_ALPHABET) for _ in range(12))
                db.execute("INSERT INTO venues VALUES (?,?,?,?)", (venue_id, "{}", now, now))
            data = {**data, "venue_id": venue_id}
            db.execute("UPDATE venues SET data=?, updated_at=? WHERE venue_id=?", (json.dumps(data), now, venue_id))
            for alias in aliases:
                db.execute("INSERT OR REPLACE INTO aliases VALUES (?,?)", (alias, venue_id))
            return str(venue_id)

    def merge_venues(self, keep: str, gone: str) -> None:
        """Point every alias of ``gone`` (and ``gone`` itself) at ``keep``."""
        with self._tx() as db:
            db.execute("UPDATE aliases SET venue_id=? WHERE venue_id=?", (keep, gone))
            db.execute("INSERT OR REPLACE INTO aliases VALUES (?,?)", (f"id:{gone}", keep))
            db.execute("DELETE FROM venues WHERE venue_id=?", (gone,))

    def _fresh_record(self, record: dict[str, Any]) -> dict[str, Any]:
        record["channels"] = [
            c
            for c in record.get("channels", [])
            if (c.get("source") not in ("google", "website") or (c.get("expires_at") or 0) > time.time())
            and (c.get("source") != "website" or self.website_discovery)
        ]
        return record

    def prune(self) -> None:
        """Delete expired cached data, including legacy Google channels without provenance TTL."""
        with self._tx() as db:
            db.execute("DELETE FROM place_cache WHERE fetched_at < ?", (time.time() - PLACE_TTL,))
            db.execute("DELETE FROM website_cache WHERE expires_at <= ?", (time.time(),))
            if not self.website_discovery:
                db.execute("DELETE FROM website_cache")
            for venue_id, data in db.execute("SELECT venue_id, data FROM venues").fetchall():
                before = json.loads(data)
                after = self._fresh_record(dict(before))
                if after != before:
                    db.execute("UPDATE venues SET data=? WHERE venue_id=?", (json.dumps(after), venue_id))

    # --- Google place cache ---------------------------------------------------------------------------

    def place_fetched_at(self, place_id: str) -> float | None:
        row = self._one("SELECT fetched_at FROM place_cache WHERE place_id=?", place_id)
        return float(row[0]) if row else None

    def cached_place(self, place_id: str) -> dict[str, Any] | None:
        row = self._one("SELECT data, fetched_at FROM place_cache WHERE place_id=?", place_id)
        if not row or time.time() - row[1] > PLACE_TTL:
            return None
        return json.loads(row[0])

    def cache_place(self, place_id: str, data: dict[str, Any], *, fetched_at: float | None = None) -> None:
        with self._tx() as db:
            db.execute(
                "INSERT OR REPLACE INTO place_cache VALUES (?,?,?)",
                (place_id, json.dumps(data), time.time() if fetched_at is None else fetched_at),
            )
            db.execute("DELETE FROM place_cache WHERE fetched_at < ?", (time.time() - PLACE_TTL,))

    def cached_website(self, place_id: str, url: str) -> dict[str, Any] | None:
        if not self.website_discovery:
            return None
        row = self._one("SELECT data, expires_at FROM website_cache WHERE place_id=? AND url=?", place_id, url)
        if not row or row[1] <= time.time():
            return None
        return {"urls": json.loads(row[0]), "expires_at": float(row[1])}

    def cache_website(self, place_id: str, url: str, urls: list[str], expires_at: float) -> None:
        """Persist only recognized booking links, never raw HTML or cookies."""
        if not self.website_discovery:
            return
        with self._tx() as db:
            db.execute(
                "INSERT OR REPLACE INTO website_cache VALUES (?,?,?,?)", (place_id, url, json.dumps(urls), expires_at)
            )
            db.execute("DELETE FROM website_cache WHERE expires_at <= ?", (time.time(),))

    # --- budgets and breakers -------------------------------------------------------------------------

    def used(self, provider: str, person: str = ALL, day: str | None = None) -> int:
        row = self._one(
            "SELECT used FROM budgets WHERE day=? AND provider=? AND person=?", day or today(), provider, person
        )
        return int(row[0]) if row else 0

    def spend(self, provider: str, person: str, limit_all: int, limit_person: int) -> bool:
        """Count one request against the provider's daily budget and the person's share; False if spent."""
        day = today()
        with self._tx() as db:
            rows = dict(
                db.execute(
                    "SELECT person, used FROM budgets WHERE day=? AND provider=? AND person IN (?,?)",
                    (day, provider, ALL, person),
                ).fetchall()
            )
            if rows.get(ALL, 0) >= limit_all or rows.get(person, 0) >= limit_person:
                return False
            for who in {ALL, person}:
                db.execute(
                    "INSERT INTO budgets VALUES (?,?,?,1) ON CONFLICT(day, provider, person) DO UPDATE SET used=used+1",
                    (day, provider, who),
                )
            db.execute("DELETE FROM budgets WHERE day < ?", (day,))
            return True

    def breaker(self, provider: str) -> Breaker:
        row = self._one("SELECT open_until, strikes, reason FROM breakers WHERE provider=?", provider)
        return Breaker(*row) if row else Breaker(0.0, 0, "")

    def claim_canary(self, provider: str, cooldown: float = 900) -> bool:
        """Reserve the monitoring interval before dispatch, including failures and restarts."""
        now = time.time()
        with self._tx() as db:
            row = db.execute("SELECT checked_at FROM canaries WHERE provider=?", (provider,)).fetchone()
            if row and now - row[0] < cooldown:
                return False
            db.execute("INSERT OR REPLACE INTO canaries VALUES (?,?)", (provider, now))
            return True

    def trip(
        self, provider: str, reason: str, schedule: tuple[int, ...] = (300, 1800, 7200, 28800), *, minimum: float = 0
    ) -> Breaker:
        """Open the breaker, longer on repeated pushback (strikes decay after a day without one)."""
        now = time.time()
        with self._tx() as db:
            row = db.execute("SELECT open_until, strikes FROM breakers WHERE provider=?", (provider,)).fetchone()
            strikes = 1
            if row and now - row[0] < 86_400:
                strikes = row[1] + 1
            until = now + max(schedule[min(strikes, len(schedule)) - 1], min(minimum, 86400))
            db.execute("INSERT OR REPLACE INTO breakers VALUES (?,?,?,?)", (provider, until, strikes, reason))
            return Breaker(until, strikes, reason)

    def close(self) -> None:
        with self._lock:
            self._db.close()
