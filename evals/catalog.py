"""A synthetic world for evaluations: invented venues on fake providers and a fake place source.

Slots repeat weekly from each venue's current day for three weeks (venue local time), so tasks can say "this
Friday" without a fixed clock. Nothing here is real.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parents[1] / "tests"))

from conftest import FakePlaces, FakeProvider  # noqa: E402

from dine_mcp.models import (
    BookingRules,  # noqa: E402
    VenueStatus,  # noqa: E402
)
from dine_mcp.places import Place  # noqa: E402
from dine_mcp.providers import REGISTRY  # noqa: E402
from dine_mcp.providers.base import Window, publish  # noqa: E402
from dine_mcp.transport import ProviderError  # noqa: E402


def weekly(tz: str, by_weekday: dict[int, list[str]], days: int = 21) -> dict[str, list[str]]:
    today = datetime.now(ZoneInfo(tz)).date()
    out = {}
    for n in range(days):
        day = today + timedelta(days=n)
        out[day.isoformat()] = by_weekday.get(day.weekday(), [])
    return out


FRI, SAT, SUN = 4, 5, 6
EVERY = {d: ["18:00", "20:30"] for d in range(6)}  # Mon-Sat; Sunday none

PLACES = [
    Place(
        "ChIJ_eval_harbour_grill00",
        "Harbour Grill",
        address="8 Pier Road, Raffles Place, Singapore",
        time_zone="Asia/Singapore",
        phone="+65 6550 0101",
        reservation_links=(("https://www.sevenrooms.com/reservations/harbourgrill", "sevenrooms.com"),),
    ),
    Place(
        "ChIJ_eval_kinmokusei000000",
        "Kinmokusei",
        address="2-1 Ginza, Chuo City, Tokyo",
        time_zone="Asia/Tokyo",
        reservation_links=(("https://www.tablecheck.com/shops/kinmokusei/reserve", "tablecheck.com"),),
    ),
    Place(
        "ChIJ_eval_copper_pot000000",
        "The Copper Pot",
        address="14 Market Street, London",
        time_zone="Europe/London",
        reservation_links=(("https://booking.resdiary.com/widget/Standard/TheCopperPot/4242", "resdiary.com"),),
    ),
    Place(
        "ChIJ_eval_lantern_house000",
        "Lantern House",
        address="3 Lantern Lane, Tanjong Pagar, Singapore",
        time_zone="Asia/Singapore",
        phone="+65 6550 0303",
    ),
    Place(
        "ChIJ_eval_amberley_hk00000",
        "Amberley Kitchen",
        address="20 Club Street, Singapore",
        time_zone="Asia/Singapore",
        phone="+65 6550 0404",
    ),
    Place(
        "ChIJ_eval_amberley_syd0000",
        "Amberley",
        address="5 George Street, Sydney",
        time_zone="Australia/Sydney",
        phone="+61 2 5550 0505",
    ),
    Place(
        "ChIJ_eval_sora_omakase0000",
        "Sora Omakase",
        address="1 Telok Ayer Street, Raffles Place, Singapore",
        time_zone="Asia/Singapore",
        reservation_links=(("https://www.tablecheck.com/shops/sora-omakase/reserve", "tablecheck.com"),),
    ),
    Place(
        "ChIJ_eval_bistro_verde0000",
        "Bistro Verde",
        address="9 Keong Saik Road, Duxton, Singapore",
        time_zone="Asia/Singapore",
        reservation_links=(("https://www.opentable.com/r/bistro-verde-singapore", "opentable.com"),),
    ),
    Place(
        "ChIJ_eval_juniper000000000",
        "Juniper",
        address="11 Amoy Street, Raffles Place, Singapore",
        time_zone="Asia/Singapore",
        phone="+65 6550 0909",
        reservation_links=(("https://www.sevenrooms.com/reservations/juniperhk", "sevenrooms.com"),),
    ),
]

PLACES += [
    Place(
        "ChIJ_eval_group_table0000",
        "Group Table",
        address="20 Example Lane, Singapore",
        time_zone="Asia/Singapore",
        phone="+65 0000 0000",
        reservation_links=(("https://www.sevenrooms.com/reservations/synthetic-group-table", "sevenrooms.com"),),
    ),
    Place(
        "ChIJ_eval_japanese0000000",
        "青嵐亭 ひかり",
        address="7 Example Lane, Tokyo",
        time_zone="Asia/Tokyo",
        reservation_links=(("https://www.tablecheck.com/shops/synthetic-hikari/reserve", "tablecheck.com"),),
    ),
    Place(
        "ChIJ_eval_chinese00000000",
        "星澄小館",
        address="9 Example Lane, Singapore",
        time_zone="Asia/Singapore",
        reservation_links=(("https://www.sevenrooms.com/reservations/synthetic-xingcheng", "sevenrooms.com"),),
    ),
    Place("ChIJ_eval_cedar_sg0000000", "Cedar Room", address="11 Example Lane, Singapore", time_zone="Asia/Singapore"),
    Place("ChIJ_eval_cedar_gb0000000", "Cedar Room", address="12 Example Lane, London", time_zone="Europe/London"),
    Place(
        "ChIJ_eval_messaging000000",
        "Paper Lantern Studio",
        address="14 Example Lane, Singapore",
        time_zone="Asia/Singapore",
    ),
    Place(
        "ChIJ_eval_batchwork000000",
        "Batchwork Grill",
        address="16 Example Lane, Singapore",
        time_zone="Asia/Singapore",
        reservation_links=(("https://www.sevenrooms.com/reservations/synthetic-batchwork", "sevenrooms.com"),),
    ),
    Place(
        "ChIJ_eval_late_tokyo00000",
        "Late Lantern",
        address="18 Example Lane, Tokyo",
        time_zone="Asia/Tokyo",
        reservation_links=(("https://www.tablecheck.com/shops/synthetic-late-lantern/reserve", "tablecheck.com"),),
    ),
]


def curated() -> dict:
    return {
        "ChIJ_eval_messaging000000": {
            "channels": [{"kind": "messaging", "url": "https://wa.me/6500000000", "text": "WhatsApp only"}],
            "booking_note": "Contact the venue yourself on WhatsApp; no online availability is published.",
        }
    }


class PushbackProvider(FakeProvider):
    async def get_venue(self, ref, person):
        venue = await super().get_venue(ref, person)
        if ref.ref == "synthetic-group-table":
            return replace(venue, rules=BookingRules(min_party=4, max_party=8))
        return venue

    async def availability(self, venue, window, person):
        if venue.ref.ref == "juniperhk":
            raise ProviderError(VenueStatus.BLOCKED, "synthetic blocked provider")
        if venue.ref.ref == "synthetic-batchwork":
            first = Window(window.dates[:1], window.time_from, window.time_to, window.party_size)
            publish(await super().availability(venue, first, person))
            raise ProviderError(VenueStatus.RATE_LIMITED, "synthetic later batch pushback")
        return await super().availability(venue, window, person)


def providers() -> dict[str, FakeProvider]:
    hk, tokyo, london = "Asia/Singapore", "Asia/Tokyo", "Europe/London"
    seven = PushbackProvider("sevenrooms", rules=BookingRules(window_days=30, max_party=8))
    seven.venues = {
        "harbourgrill": weekly(
            hk, {FRI: ["19:00", "19:30", "21:00"], SAT: ["17:30"], **{d: ["18:00", "18:15"] for d in range(4)}}
        ),
        "juniperhk": weekly(hk, EVERY),
    }
    table = FakeProvider("tablecheck", rules=BookingRules(window_days=14, max_party=6), zones={"kinmokusei": tokyo})
    table.venues = {
        "kinmokusei": weekly(tokyo, {d: ["17:30", "20:00"] for d in range(5)}),
        "sora-omakase": weekly(hk, {}),
    }
    res = FakeProvider(
        "resdiary", rules=BookingRules(window_days=60, max_party=10), zones={"TheCopperPot/4242": london}
    )
    res.venues = {"TheCopperPot/4242": weekly(london, {SAT: ["12:30", "19:45"], SUN: ["13:00"]})}
    seven.venues["synthetic-group-table"] = weekly(hk, {})
    seven.venues["synthetic-xingcheng"] = weekly(hk, {d: ["20:15"] for d in range(7)})
    seven.venues["synthetic-batchwork"] = weekly(hk, {d: ["19:00"] for d in range(7)})
    table.venues["synthetic-hikari"] = weekly(tokyo, {d: ["19:15"] for d in range(7)})
    table.venues["synthetic-late-lantern"] = weekly(tokyo, {d: ["00:30", "23:45"] for d in range(7)})
    table.zones.update({"synthetic-hikari": tokyo, "synthetic-late-lantern": tokyo})
    return {**{key: FakeProvider(key) for key in REGISTRY}, "sevenrooms": seven, "tablecheck": table, "resdiary": res}


def places() -> FakePlaces:
    return FakePlaces(PLACES)
