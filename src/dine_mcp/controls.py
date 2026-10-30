"""One end-to-end deadline and actual-dispatch budget, shared by nested service work."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from typing import Any

from .errors import DineError


@dataclass
class Call:
    deadline: float
    requests: int = 0
    limit: int = 20

    def reserve(self) -> None:
        if time.monotonic() >= self.deadline:
            raise TimeoutError
        if self.requests >= self.limit:
            raise DineError("too_broad")
        self.requests += 1

    def remaining(self) -> float:
        return max(0.0, self.deadline - time.monotonic())


CURRENT: ContextVar[Call | None] = ContextVar("dine_call", default=None)


@contextmanager
def call_scope(seconds: float) -> Iterator[Call]:
    current = CURRENT.get()
    if current is not None:
        yield current
        return
    call = Call(time.monotonic() + seconds)
    token = CURRENT.set(call)
    try:
        yield call
    finally:
        CURRENT.reset(token)


def bounded(method: Any) -> Any:
    @wraps(method)
    async def run(self: Any, *args: Any, **kwargs: Any) -> Any:
        with call_scope(self.settings.deadline_seconds) as call:
            try:
                async with asyncio.timeout(call.remaining()):
                    return await method(self, *args, **kwargs)
            except TimeoutError:
                raise DineError("deadline_exceeded") from None

    return run


# Request metadata remains scoped to the call and is never supplied as provider credentials.
TRACE: ContextVar[str | None] = ContextVar("dine_trace", default=None)
PROGRESS: ContextVar[Any] = ContextVar("dine_progress", default=None)
AUDIT: ContextVar[dict[str, Any] | None] = ContextVar("dine_audit", default=None)


def attribute(**fields: Any) -> None:
    record = AUDIT.get()
    if record is not None:
        record.update(fields)
