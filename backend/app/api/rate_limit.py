"""Per-client request limits, so a public deployment cannot spend the LLM quota.

Limits are kept in memory, per process: run one backend worker (the work is
I/O-bound and async, so one is enough). Behind a reverse proxy, start uvicorn
with --proxy-headers so the client address is the rider's, not the proxy's.
A limit of 0 turns that window off, which is the default for local use.
"""

import math
import time
from collections import deque
from typing import Callable

from fastapi import HTTPException, Request, status

MINUTE_S = 60.0
DAY_S = 86_400.0


class RateLimiter:
    def __init__(
        self,
        per_minute: int = 0,
        per_day: int = 0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._windows = [(limit, span) for limit, span in ((per_minute, MINUTE_S), (per_day, DAY_S)) if limit > 0]
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}

    @property
    def enabled(self) -> bool:
        return bool(self._windows)

    def wait_seconds(self, key: str) -> float | None:
        """Record a request, or return how long the client must wait if it is over a limit."""
        if not self._windows:
            return None
        now = self._clock()
        longest = max(span for _, span in self._windows)
        hits = self._hits.setdefault(key, deque())
        while hits and hits[0] <= now - longest:
            hits.popleft()

        for limit, span in self._windows:
            recent = [t for t in hits if t > now - span]
            if len(recent) >= limit:
                # Free again when the oldest request in the window ages out.
                return recent[-limit] + span - now
        hits.append(now)
        self._forget_idle(now - longest)
        return None

    def _forget_idle(self, cutoff: float) -> None:
        """Drop clients with no recent requests, so memory stays bounded."""
        if len(self._hits) < 10_000:
            return
        for key in [k for k, hits in self._hits.items() if not hits or hits[-1] <= cutoff]:
            del self._hits[key]


def limit_requests(limiter_name: str, message: str):
    """A dependency that applies the limiter stored on app.state under that name."""

    def dependency(request: Request) -> None:
        limiter: RateLimiter | None = getattr(request.app.state, limiter_name, None)
        if limiter is None or not limiter.enabled:
            return
        client = request.client.host if request.client else "unknown"
        wait = limiter.wait_seconds(client)
        if wait is not None:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                message,
                headers={"Retry-After": str(max(1, math.ceil(wait)))},
            )

    return dependency
