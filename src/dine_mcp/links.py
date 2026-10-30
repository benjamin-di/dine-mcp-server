"""Booking links: clean them, recognise the provider and venue they point at, check their host.

Nothing here fetches a URL. A link the model passes in is only matched against the providers' URL
patterns; a link a provider or Google returns is shown only when its host belongs to that provider.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .definitions import ProviderDefinition
from .models import ProviderVenueRef

# Attribution and tracking parameters Google, Yext and campaign tools append to booking links.
TRACKING = frozenset({"rwg_token", "src", "ref", "source", "gclid", "fbclid", "mc_cid", "mc_eid", "y_source"})
MAX_URL = 2048


def clean(url: str) -> str | None:
    """The URL without tracking parameters or fragment; None unless it is a plain https URL."""
    if not isinstance(url, str) or len(url) > MAX_URL or any(ord(c) < 32 or ord(c) == 127 for c in url):
        return None
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return None
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or port is not None
        or parts.netloc.lower() != parts.hostname.lower()
    ):
        return None
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in TRACKING and not key.lower().startswith("utm_")
    ]
    return urlunsplit(("https", parts.hostname.lower(), parts.path or "/", urlencode(query), ""))


def host_of(url: str) -> str | None:
    try:
        return urlsplit(url).hostname
    except ValueError:
        return None


def host_allowed(url: str, hosts: tuple[str, ...]) -> bool:
    """True when the URL is https on one of these hosts (exact, or a subdomain of a ``.host`` entry)."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host:
        return False
    for allowed in hosts:
        if allowed.startswith("."):
            if host.endswith(allowed) or host == allowed[1:]:
                return True
        elif host == allowed:
            return True
    return False


def match(url: str, definitions: dict[str, ProviderDefinition]) -> ProviderVenueRef | None:
    """The provider venue a cleaned booking URL points at, or None."""
    cleaned = clean(url)
    if cleaned is None:
        return None
    for definition in definitions.values():
        if not host_allowed(cleaned, definition.hosts):
            continue
        for pattern in definition.url_patterns:
            found = pattern.match(cleaned)
            if found and found.group("ref"):
                return ProviderVenueRef(definition.key, found.group("ref"))
    return None
