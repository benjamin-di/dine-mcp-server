"""Whole-call errors: one closed set of codes, each with fixed text.

A tool error is a result with ``isError`` whose text is one JSON envelope
``{"error": {code, retryable, retry_after_seconds, action, next_step}}``. Exception text, provider
bodies and stack traces never reach the model; ``log_detail`` is for logs only. Everything about one
provider or venue is a status inside a successful result instead (``models.VenueStatus``).
"""

from __future__ import annotations

from typing import Any

# code -> (action, retryable, next_step). Actions form a closed set for consistent client recovery.
CODES: dict[str, tuple[str, bool, str]] = {
    "invalid_argument": ("none", False, "Fix the arguments as described and call again."),
    "too_broad": (
        "none",
        False,
        "This request needs too many provider requests for one call. Ask for fewer venues or fewer days "
        "(for example 3 venues over 3 days) and call again.",
    ),
    "unknown_venue_id": (
        "none",
        False,
        "No venue has this id. Find the venue with dine_find_venue and use the venue_id it returns.",
    ),
    "unknown_person": (
        "human_account_check",
        False,
        "This request has no valid authenticated person. Tell the user: check gateway membership and identity before "
        "they can use it.",
    ),
    "disabled": (
        "none",
        False,
        "The operator has switched availability checks off. Tell the user; do not change the server's "
        "settings yourself.",
    ),
    "place_source_unavailable": (
        "retry_later",
        True,
        "Venue search is unavailable right now. Retry later, or pass the venue's booking link as url.",
    ),
    "deadline_exceeded": ("retry_later", True, "The call exceeded its time limit. Retry with a narrower query."),
    "internal": ("retry_later", True, "dine-mcp failed unexpectedly. Retry once; if it repeats, tell the operator."),
}

# The caller's own mistakes: routine, logged at INFO.
AGENT_INPUT_CODES = frozenset({"invalid_argument", "too_broad", "unknown_venue_id"})


class DineError(Exception):
    """A classified whole-call failure."""

    def __init__(
        self,
        code: str,
        detail: str | None = None,
        *,
        retry_after_seconds: int | None = None,
        log_detail: str | None = None,
    ) -> None:
        if code not in CODES:
            raise ValueError(f"unknown error code {code}")
        super().__init__(code)
        self.code = code
        self.detail = detail
        self.retry_after_seconds = retry_after_seconds
        self.log_detail = log_detail

    def payload(self) -> dict[str, Any]:
        action, retryable, next_step = CODES[self.code]
        if self.detail:
            next_step = f"{self.detail}. {next_step}"
        return {
            "code": self.code,
            "retryable": retryable,
            "retry_after_seconds": self.retry_after_seconds,
            "action": action,
            "next_step": next_step,
        }


def invalid(detail: str) -> DineError:
    return DineError("invalid_argument", detail)
