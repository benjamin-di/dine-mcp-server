"""Conservative, optional booking-link detection in a place source's venue website.

No JavaScript, crawling or arbitrary model URLs. Multiple distinct venue refs are ambiguous and
produce no inferred channel. The only retained website content is a recognized booking link.
"""

from __future__ import annotations

import asyncio
import re
import time
from html.parser import HTMLParser
from urllib.parse import urljoin
from weakref import WeakValueDictionary

from . import links
from .definitions import ProviderDefinition
from .places import Place
from .state import PLACE_TTL, Store
from .transport import ProviderError, WebsiteTransport, website_url

WEBSITE_TTL = 86_400
FAILURE_TTL = 900
DISCOVERY_TIMEOUT = 5


class WidgetLinks(HTMLParser):
    def __init__(self, definitions: dict[str, ProviderDefinition], page_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.definitions = definitions
        self.page_url = page_url
        self.found: dict[tuple[str, str], str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if len(self.found) > 1:
            return
        values = dict(attrs)
        if tag == "div" and "zc-widget-config" in (values.get("class") or "").split():
            widget_ref = values.get("data-restaurant") or ""
            if "zenchef" in self.definitions and re.fullmatch(r"[1-9][0-9]{0,11}", widget_ref):
                self.found[("zenchef", widget_ref)] = f"https://bookings.zenchef.com/results?rid={widget_ref}"
        if tag == "div" and "ft-widget-b2" in (values.get("class") or "").split():
            widget_ref = values.get("data-restaurant") or ""
            if "formitable" in self.definitions and re.fullmatch(r"[a-f0-9]{6,64}", widget_ref):
                self.found[("formitable", widget_ref)] = f"https://widget.formitable.com/side/en/{widget_ref}/"
        allowed = {"a": {"href"}, "iframe": {"src", "data-src"}, "button": {"data-href", "data-reservation-url"}}
        for key, value in attrs:
            if key not in allowed.get(tag, set()) or not value or len(value) > links.MAX_URL:
                continue
            url = links.clean(urljoin(self.page_url, value))
            ref = links.match(url, self.definitions) if url else None
            if ref is not None and url is not None:
                # Zenchef identifies its venue in a query; retain only that reviewed identifier.
                self.found[(ref.provider, ref.ref)] = (
                    f"https://bookings.zenchef.com/results?rid={ref.ref}"
                    if ref.provider == "zenchef"
                    else url.split("?", 1)[0]
                )
                if len(self.found) > 1:
                    return


def widget_links(html: str, page_url: str, definitions: dict[str, ProviderDefinition]) -> list[str]:
    parser = WidgetLinks(definitions, page_url)
    parser.feed(html)
    # A hotel or group page can contain booking widgets for several different restaurants.
    return list(parser.found.values()) if len(parser.found) == 1 else []


class WebsiteDiscovery:
    def __init__(self, definitions: dict[str, ProviderDefinition], store: Store, transport: WebsiteTransport) -> None:
        self.definitions = definitions
        self.store = store
        self.transport = transport
        self._locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()

    async def discover(self, place: Place, person: str) -> None:
        url = website_url(place.website or "")
        if url is None:
            return
        # Website reads require the current source record, not a caller-supplied URL/Place.
        lock = self._locks.setdefault(place.place_id, asyncio.Lock())
        source_expiry: float | None = None
        try:
            async with asyncio.timeout(DISCOVERY_TIMEOUT), lock:
                cached_place = self.store.cached_place(place.place_id)
                if not cached_place or cached_place.get("website") != place.website:
                    return
                source_expiry = (self.store.place_fetched_at(place.place_id) or 0) + PLACE_TTL
                if self.store.cached_website(place.place_id, url) is not None:
                    return
                urls: list[str] = []
                ttl = FAILURE_TTL
                try:
                    if links.match(url, self.definitions) is not None:
                        # A source website already naming a provider venue needs no website fetch.
                        urls = [url]
                    else:
                        html, final_url = await self.transport.read(url, person)
                        urls = widget_links(html, final_url, self.definitions)
                    ttl = WEBSITE_TTL
                except (ProviderError, ValueError):
                    # Optional enrichment must not turn a directory result into an outage.
                    pass
                current = self.store.cached_place(place.place_id)
                if not current or current.get("website") != place.website:
                    return
                self.store.cache_website(place.place_id, url, urls, min(time.time() + ttl, source_expiry))
        except TimeoutError:
            # Includes waiting for another discovery of this place; only reads get a cooldown.
            current = self.store.cached_place(place.place_id)
            if source_expiry is not None and current and current.get("website") == place.website:
                self.store.cache_website(place.place_id, url, [], min(time.time() + FAILURE_TTL, source_expiry))

    def cached(self, place: Place) -> dict | None:
        url = website_url(place.website or "")
        current = self.store.cached_place(place.place_id)
        if not current or current.get("website") != place.website:
            return None
        return self.store.cached_website(place.place_id, url) if url else None
