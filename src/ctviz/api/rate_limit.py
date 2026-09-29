"""In-memory rate limiting for POST /v1/visualize: a per-client sliding one-minute window plus a
global daily cap that bounds LLM spend. Single-process by design, like the response cache."""

import math
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Request

MINUTE_S = 60.0
DAY_S = 86_400.0
MAX_TRACKED_CLIENTS = 10_000  # idle client windows are pruned past this, bounding memory
UNKNOWN_CLIENT = "unknown"


@dataclass(frozen=True)
class RateDecision:
    """Whether one request may proceed, and if not, how many seconds until it could."""

    allowed: bool
    retry_after_s: int = 0


def _expire(window: deque[float], cutoff: float) -> None:
    """Drop timestamps older than `cutoff` from the front of a time-ordered window."""
    while window and window[0] <= cutoff:
        window.popleft()


def _wait_s(window: deque[float], limit: int, span_s: float, now: float) -> int:
    """Whole seconds until `window` has room under `limit` (0 = room now or limit disabled)."""
    if limit <= 0 or len(window) < limit:
        return 0
    return max(1, math.ceil(window[0] + span_s - now))


class RateLimiter:
    """`per_minute` requests per client key and `per_day` across all clients; 0 disables either.

    Rejected requests never consume quota, so a client hammering the endpoint recovers as soon
    as its oldest accepted request leaves the window.
    """

    def __init__(
        self, per_minute: int, per_day: int, clock: Callable[[], float] = time.monotonic
    ) -> None:
        """Start with empty windows; `clock` is injectable so tests control time."""
        self._per_minute = per_minute
        self._per_day = per_day
        self._clock = clock
        self._clients: dict[str, deque[float]] = {}
        self._global: deque[float] = deque()

    def check(self, key: str) -> RateDecision:
        """Record and allow the request if both limits have room; otherwise say when to retry."""
        now = self._clock()
        client = self._clients.setdefault(key, deque())
        _expire(client, now - MINUTE_S)
        _expire(self._global, now - DAY_S)
        wait = max(
            _wait_s(client, self._per_minute, MINUTE_S, now),
            _wait_s(self._global, self._per_day, DAY_S, now),
        )
        if wait:
            return RateDecision(allowed=False, retry_after_s=wait)
        if self._per_minute > 0:
            client.append(now)
        if self._per_day > 0:
            self._global.append(now)
        self._prune(now)
        return RateDecision(allowed=True)

    def _prune(self, now: float) -> None:
        """Forget clients with no requests in the last minute once too many are tracked."""
        if len(self._clients) <= MAX_TRACKED_CLIENTS:
            return
        for key in [k for k, w in self._clients.items() if not w or w[-1] <= now - MINUTE_S]:
            del self._clients[key]


def client_key(request: Request, trust_forwarded_for: bool) -> str:
    """The caller's IP. `X-Forwarded-For` is honored only behind a trusted proxy (a setting),
    since any client can forge that header."""
    if trust_forwarded_for:
        forwarded = request.headers.get("x-forwarded-for", "")
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else UNKNOWN_CLIENT
