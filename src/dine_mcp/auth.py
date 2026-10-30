"""Who is calling: one HTTP bearer check, then exactly one person per request.

Modes (``DINE_AUTH_MODE``, or inferred):

- ``gateway``: an authenticating gateway holds the backend bearer
  (``MCP_BEARER_TOKEN``, comma-separated for rotation, each at least 32 characters) and asserts the
  person in a header (``DINE_PERSON_HEADER``, e.g. ``X-Fleet-Person``). The header is read only on
  requests carrying that bearer. A valid asserted person gets a record on first contact; missing
  or malformed identity is refused, never served as someone else.
- ``users``: standalone multi-user. ``DINE_USERS_FILE`` lists users with the SHA-256 of their own token
  (``dine-mcp users add <id>`` prints a new token once). The token chooses the person.
- ``single``: one person ("owner") behind ``MCP_BEARER_TOKEN``.
- stdio: the local client is the person ("local"); no token.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import tempfile
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from fastmcp.server.auth import AccessToken, TokenVerifier
from filelock import FileLock

from .errors import DineError
from .state import Store, valid_person

TOKEN_VAR = "MCP_BEARER_TOKEN"
MIN_TOKEN_LENGTH = 32
GATEWAY = "gateway"
LOCAL = "local"
OWNER = "owner"
HEADER = re.compile(r"[A-Za-z0-9-]{1,64}\Z")


class AuthError(Exception):
    """Authentication is misconfigured; raised at startup only."""


@dataclass(frozen=True)
class AuthConfig:
    mode: str  # gateway, users or single
    tokens: tuple[str, ...] = ()
    person_header: str | None = None
    users: Mapping[str, str] | None = None  # person -> sha256 hex of their token

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> AuthConfig:
        env = os.environ if env is None else env
        header = (env.get("DINE_PERSON_HEADER") or "").strip() or None
        users_file = (env.get("DINE_USERS_FILE") or "").strip() or None
        mode = (env.get("DINE_AUTH_MODE") or "").strip() or (
            "users" if users_file else "gateway" if header else "single"
        )
        tokens = tuple(t.strip() for t in (env.get(TOKEN_VAR) or "").split(",") if t.strip())
        if mode == "users":
            if not users_file:
                raise AuthError("DINE_USERS_FILE must name the users file in users mode")
            return cls(mode, users=load_users(Path(users_file)))
        if mode not in ("gateway", "single"):
            raise AuthError("DINE_AUTH_MODE is gateway, users or single")
        if not tokens:
            raise AuthError(f"{TOKEN_VAR} must be set to serve over HTTP (comma-separated for several tokens)")
        if any(len(t) < MIN_TOKEN_LENGTH for t in tokens):
            raise AuthError(f"each {TOKEN_VAR} token needs at least {MIN_TOKEN_LENGTH} characters")
        if mode == "gateway" and (not header or not HEADER.match(header)):
            raise AuthError("DINE_PERSON_HEADER must name the gateway's person header in gateway mode")
        return cls(mode, tokens=tokens, person_header=header)


def digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def load_users(path: Path) -> dict[str, str]:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise AuthError(f"users file {path}: {exc.strerror}") from None
    except tomllib.TOMLDecodeError as exc:
        raise AuthError(f"users file {path}: {exc}") from None
    users = data.get("users", {})
    out: dict[str, str] = {}
    hashes: set[str] = set()
    if not isinstance(users, dict) or len(users) > 1000:
        raise AuthError(f"users file {path}: [users.<id>] tables, at most 1000")
    for person, table in users.items():
        value = table.get("token_sha256") if isinstance(table, dict) else None
        if not valid_person(person) or not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            raise AuthError(f"users file {path}: users.{person} needs a valid id and token_sha256")
        if value in hashes:
            raise AuthError(f"users file {path}: each token_sha256 must identify exactly one user")
        out[person] = value
        hashes.add(value)
    return out


def add_user(path: Path, person: str) -> str:
    """Add a user with a new random token; returns the token (shown once, stored only as a hash)."""
    if not valid_person(person):
        raise AuthError("a user id is 1-64 lower-case letters, digits, dots, dashes or underscores")
    path = path.resolve()
    with FileLock(str(path) + ".lock", mode=0o600, timeout=10):
        users = load_users(path) if path.exists() else {}
        if person in users:
            raise AuthError(f"user {person} exists; remove the line to replace their token")
        if len(users) >= 1000:
            raise AuthError("users file already has the maximum 1000 users")
        token = secrets.token_urlsafe(32)
        users[person] = digest(token)
        lines = ["# dine-mcp users: each line holds the SHA-256 of that user's token, never the token.\n"]
        lines += [f'[users."{p}"]\ntoken_sha256 = "{h}"\n' for p, h in sorted(users.items())]
        # Private from creation, unique per writer, and replaced atomically under the file lease.
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False
        ) as file:
            tmp = Path(file.name)
            try:
                file.write("".join(lines))
                file.close()
                tmp.replace(path)
            finally:
                tmp.unlink(missing_ok=True)
        return token


class Verifier(TokenVerifier):
    """Accepts the backend bearer (gateway/single) or a user's token (users)."""

    def __init__(self, config: AuthConfig) -> None:
        super().__init__()
        self.config = config

    async def verify_token(self, token: str) -> AccessToken | None:
        if self.config.mode == "users":
            hashed = digest(token)
            for person, expected in (self.config.users or {}).items():
                if hmac.compare_digest(hashed, expected):
                    return AccessToken(token="", client_id=f"user:{person}", scopes=[])
            return None
        for expected in self.config.tokens:
            if hmac.compare_digest(token.encode(), expected.encode()):
                client = GATEWAY if self.config.mode == "gateway" else f"user:{OWNER}"
                return AccessToken(token="", client_id=client, scopes=[])
        return None


def person_for(config: AuthConfig | None, client_id: str | None, headers: Mapping[str, str], store: Store) -> str:
    """The person a request acts for. Raises unknown_person rather than guessing."""
    if config is None:  # stdio
        return LOCAL
    if client_id and client_id.startswith("user:"):
        return client_id.removeprefix("user:")
    if client_id == GATEWAY and config.person_header:
        person = (headers.get(config.person_header.lower()) or "").strip()
        if valid_person(person):
            store.add_person(person)
            return person
    raise DineError("unknown_person")
