"""Short, self-contained text versions of every tool result (hosts that show only text still work)."""

from __future__ import annotations

from datetime import datetime
from typing import Any


def _when(start: str) -> str:
    try:
        at = datetime.fromisoformat(start)
    except ValueError:
        return start
    return at.strftime("%a %d %b %H:%M")


def find_venue(result: dict[str, Any]) -> str:
    lines = []
    for venue in result.get("venues", []):
        channels = ", ".join(_channel(c) for c in venue.get("channels", [])) or "no booking channel known"
        lines.append(f"{venue['name']} ({venue['venue_id']}): {channels}")
    if result.get("candidates"):
        lines.append("Candidates (call again with google_place_id):")
        for c in result["candidates"]:
            lines.append(f"- {c['name']}, {c.get('address') or '?'} [{c.get('google_place_id')}]")
    if result.get("note"):
        lines.append(result["note"])
    return "\n".join(lines) or "No venue found."


def _channel(channel: dict[str, Any]) -> str:
    kind = channel.get("provider_name") or channel.get("kind")
    if channel.get("kind") in ("phone", "messaging"):
        return f"{channel['kind']} {channel.get('text') or ''}".strip()
    if channel.get("kind") == "website":
        return "website"
    return f"{kind} ({channel.get('access_mode')})"


def venue(view: dict[str, Any]) -> str:
    lines = [f"{view['name']} ({view['venue_id']})"]
    if view.get("address"):
        lines.append(view["address"])
    for channel in view.get("channels", []):
        lines.append(f"- {_channel(channel)}" + (f": {channel['url']}" if channel.get("url") else ""))
    for detail in view.get("providers", []):
        rules = detail.get("booking_rules") or {}
        bits = [f"{k}={v}" for k, v in rules.items() if v not in (None, "") and k != "source"]
        lines.append(f"{detail['provider']}: {detail.get('status')}" + (f" ({', '.join(bits)})" if bits else ""))
    if view.get("booking_note"):
        lines.append(view["booking_note"])
    return "\n".join(lines)


def availability(result: dict[str, Any]) -> str:
    query = result["query"]
    lines = [
        f"Party {query['party_size']}, {query['date_from']}–{query['date_to']}, {query['time_from']}–{query['time_to']}"
        " (venue local time). Observations, not holds:"
    ]
    for item in result["results"]:
        head = f"{item['venue_name']} · {item['channel']} · {item['status']}"
        if item["slots"]:
            times = ", ".join(
                _when(s["start"]) + ("" if s["kind"] == "bookable" else f" ({s['kind']})") for s in item["slots"]
            )
            head += f": {times}"
            if item.get("slots_omitted"):
                head += f" (+{item['slots_omitted']} more)"
        if item.get("opens_at"):
            head += f"; opens {item['opens_at']}"
        if item.get("observed_at"):
            head += f"; checked {item['observed_at']} ({item.get('served_from')})"
        if item.get("note"):
            head += f". {item['note']}"
        link = item.get("booking_link")
        if link:
            head += f" Book: {link['url']}"
        elif item.get("contact"):
            head += f" Contact: {item['contact']}"
        lines.append(head)
    return "\n".join(lines)


def status(result: dict[str, Any]) -> str:
    lines = [f"dine-mcp {result['version']}" + (" (switched off)" if result.get("disabled") else "")]
    for p in result["providers"]:
        state = "on" if p["enabled"] else f"off: {p.get('reason')}"
        extra = ""
        if "used_today" in p:
            extra = f", {p['used_today']}/{p['daily_request_budget']} requests today"
            if p.get("breaker_open_until"):
                extra += f", paused until {p['breaker_open_until']}"
        if p.get("check"):
            extra += f", canary {p['check']['status']} ({p['check']['age_seconds']}s ago)"
        lines.append(f"- {p['name']}: {p['access_mode']}, {p['maturity']}, {state}{extra}")
    discovery = result.get("website_discovery", {})
    if discovery.get("enabled"):
        lines.append(
            f"- Website discovery: on, {discovery['used_today']}/{discovery['daily_request_budget']} reads today"
            + (f", paused until {discovery['breaker_open_until']}" if discovery.get("breaker_open_until") else "")
        )
    return "\n".join(lines)
