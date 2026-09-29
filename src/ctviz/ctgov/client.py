"""Async ClinicalTrials.gov client: probe counts, fetch all pages (with a cap), retry, cache."""

import asyncio
import json
import logging
import random
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, Self

import httpx

from ctviz.config import (
    CACHE_MAX_BYTES,
    CACHE_MAX_ENTRIES,
    CACHE_TTL_S,
    CTGOV_BASE_URL,
    HTTP_CONNECT_TIMEOUT_S,
    HTTP_RETRIES,
    HTTP_TIMEOUT_S,
    PAGE_SIZE,
    RETRY_AFTER_CAP_S,
    RETRY_BUDGET_S,
    RETRY_JITTER_FRACTION,
    VERSION_CACHE_TTL_S,
    VERSION_NEGATIVE_TTL_S,
)
from ctviz.errors import UpstreamError

log = logging.getLogger(__name__)
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
TRUNCATION_SORT = "StartDate:desc"
CacheKey = tuple[str, tuple[tuple[str, str], ...], str | None]
CacheEntry = tuple[float, bytes, str]  # (stored_at, raw response bytes, url)


@dataclass(frozen=True)
class RequestLog:
    """One HTTP call made against ClinicalTrials.gov, for provenance (§12.6)."""

    url: str
    fetched_at: str
    records: int
    from_cache: bool = False


@dataclass(frozen=True)
class FetchResult:
    """Every fetched record for one cohort, plus whether/how it was truncated."""

    records: list[dict[str, Any]]
    api_total_count: int
    truncated: bool
    truncation_rule: str | None
    requests: list[RequestLog] = field(default_factory=list)


class CtGovClient:
    """Thin, well-behaved wrapper over GET /studies; all errors surface as UpstreamError."""

    def __init__(
        self,
        base_url: str = CTGOV_BASE_URL,
        backoff_base_s: float = 0.5,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rand: Callable[[], float] = random.random,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Open a pooled async httpx client; backoff_base_s is 0 in tests to skip real waits.

        `sleep`/`rand` are injectable (D2) so tests can assert exact retry/backoff delays without
        waiting in real time or depending on real randomness; `clock` likewise drives cache and
        /version TTLs.
        """
        self._http = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(HTTP_TIMEOUT_S, connect=HTTP_CONNECT_TIMEOUT_S),
        )
        self._backoff_base_s = backoff_base_s
        self._sleep = sleep
        self._rand = rand
        self._clock = clock
        # D1: bounded LRU -- `OrderedDict` gives us move-to-end-on-access eviction for free. Raw
        # bytes are stored (not parsed dicts): each hit re-parses, so callers own a fresh object.
        self._cache: OrderedDict[CacheKey, CacheEntry] = OrderedDict()
        self._cache_bytes = 0
        self._version_body: dict[str, Any] | None = None
        self._version_at = 0.0
        self._version_failed_at: float | None = None
        self._known_data_timestamp: str | None = None
        self._known_api_version: str | None = None

    @property
    def cached_api_version(self) -> str | None:
        """The ClinicalTrials.gov `apiVersion` this process has already learned (via a prior
        `version()` call), or `None` if it hasn't yet -- a cheap, network-free read for `/health`,
        which must never block a liveness probe on an outbound HTTP call."""
        return self._known_api_version

    async def __aenter__(self) -> Self:
        """Support `async with CtGovClient() as client:` for guaranteed cleanup."""
        return self

    async def __aexit__(
        self, *exc: type[BaseException] | BaseException | TracebackType | None
    ) -> None:
        """Close the underlying HTTP client on context exit."""
        await self.aclose()

    async def aclose(self) -> None:
        """Release the pooled HTTP connections."""
        await self._http.aclose()

    def _cache_key(self, path: str, params: dict[str, str]) -> CacheKey:
        """(path, sorted params, known API dataTimestamp) — §10.2. The timestamp component is
        `None` until this process has learned it; a ClinicalTrials.gov data refresh (a new
        dataTimestamp) changes the key and the old entries age out, never served as current.
        `/version` itself is not cached here (see `version()`), so its key never shifts."""
        return path, tuple(sorted(params.items())), self._known_data_timestamp

    def _drop(self, key: CacheKey) -> None:
        """Remove one cache entry and its bytes from the running total."""
        _, raw, _ = self._cache.pop(key)
        self._cache_bytes -= len(raw)

    def _purge_expired(self, now: float) -> None:
        """Drop every cache entry whose TTL has lapsed (D1: proactive purge, not skip-on-read)."""
        expired = [
            k for k, (stored_at, _, _) in self._cache.items() if now - stored_at >= CACHE_TTL_S
        ]
        for key in expired:
            self._drop(key)

    def _evict_over_capacity(self) -> None:
        """D1: bounded LRU by entry count AND total bytes -- drop the least-recently-used first."""
        while self._cache and (
            len(self._cache) > CACHE_MAX_ENTRIES or self._cache_bytes > CACHE_MAX_BYTES
        ):
            self._drop(next(iter(self._cache)))

    def _store(self, key: CacheKey, now: float, raw: bytes, url: str) -> None:
        """Insert one entry (replacing any same-key one), then enforce the bounds."""
        if key in self._cache:
            self._drop(key)
        self._cache[key] = (now, raw, url)
        self._cache_bytes += len(raw)
        self._evict_over_capacity()

    async def _get(self, path: str, params: dict[str, str]) -> tuple[dict[str, Any], str, bool]:
        """A cached GET: identical (path, params, known dataTimestamp) within CACHE_TTL_S skips
        the network. The body is parsed from the stored bytes on every call, so a caller can
        mutate what it gets without corrupting the cache or another caller."""
        key = self._cache_key(path, params)
        now = self._clock()
        self._purge_expired(now)
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            _, raw, url = cached
            return json.loads(raw), url, True
        raw, url = await self._get_with_retries(path, params)
        self._store(key, now, raw, url)
        return json.loads(raw), url, False

    def _backoff_delay(self, attempt: int) -> float:
        """D2: exponential backoff with up to +RETRY_JITTER_FRACTION random jitter on top.

        Zero base (tests) always yields zero delay regardless of `rand()`, since the jitter term
        is proportional to the base.
        """
        base = self._backoff_base_s * 2.0**attempt
        return base + self._rand() * base * RETRY_JITTER_FRACTION

    def _retry_after_delay(self, response: httpx.Response) -> float | None:
        """D2: a 429's `Retry-After` (seconds), capped to RETRY_AFTER_CAP_S; `None` if unusable."""
        header = response.headers.get("retry-after")
        if header is None:
            return None
        try:
            seconds = float(header)
        except ValueError:
            return None
        return max(0.0, min(seconds, RETRY_AFTER_CAP_S))

    def _next_delay(self, response: httpx.Response, attempt: int) -> float:
        """D2: honor a 429's Retry-After when present, else the jittered exponential backoff."""
        if response.status_code == 429:
            retry_after = self._retry_after_delay(response)
            if retry_after is not None:
                return retry_after
        return self._backoff_delay(attempt)

    async def _pause(self, delay: float, slept: float) -> float:
        """Sleep `delay`, clipped to what remains of RETRY_BUDGET_S; returns the time slept."""
        actual = min(delay, max(0.0, RETRY_BUDGET_S - slept))
        await self._sleep(actual)
        return actual

    async def _get_with_retries(self, path: str, params: dict[str, str]) -> tuple[bytes, str]:
        """Retry transient failures with exponential backoff (+ jitter) or a 429's Retry-After
        (total sleep bounded by RETRY_BUDGET_S); raise UpstreamError otherwise."""
        slept = 0.0
        for attempt in range(HTTP_RETRIES):
            try:
                response = await self._http.get(path, params=params)
            except httpx.TransportError as exc:
                if attempt == HTTP_RETRIES - 1:
                    raise UpstreamError(f"ClinicalTrials.gov unreachable: {exc}") from exc
                slept += await self._pause(self._backoff_delay(attempt), slept)
                continue
            if response.status_code == 200:
                return self._decode(response)
            if response.status_code not in RETRYABLE_STATUS or attempt == HTTP_RETRIES - 1:
                body_text = response.text.strip()[:300]
                message = f"ClinicalTrials.gov HTTP {response.status_code}: {body_text}"
                raise UpstreamError(message, response.status_code)
            slept += await self._pause(self._next_delay(response, attempt), slept)
        raise UpstreamError("retries exhausted")  # unreachable; keeps mypy happy

    @staticmethod
    def _decode(response: httpx.Response) -> tuple[bytes, str]:
        """Validate a 200 response's JSON body and keep its raw bytes; malformed is an
        UpstreamError, not a crash."""
        try:
            json.loads(response.content)
            return response.content, str(response.url)
        except ValueError as exc:
            raise UpstreamError(f"ClinicalTrials.gov returned an invalid response: {exc}") from exc

    async def probe(self, params: dict[str, str]) -> int:
        """Total matching trials for these params, using a 1-record request."""
        await self._learn_data_timestamp()
        body, _, _ = await self._get(
            "/studies", params | {"pageSize": "1", "countTotal": "true", "fields": "NCTId"}
        )
        return int(body.get("totalCount", 0))

    async def fetch_all(self, params: dict[str, str], max_records: int) -> FetchResult:
        """Every page up to max_records; over the cap, the most recent trials by start date."""
        total = await self.probe(params)
        truncated = total > max_records
        page_params = params | {"pageSize": str(min(PAGE_SIZE, max_records))}
        if truncated:
            page_params["sort"] = TRUNCATION_SORT
        records: list[dict[str, Any]] = []
        logs: list[RequestLog] = []
        token: str | None = None
        while len(records) < max_records:
            body, url, from_cache = await self._get(
                "/studies", page_params | ({"pageToken": token} if token else {})
            )
            page = body.get("studies", [])
            records += page
            logs.append(RequestLog(url, datetime.now(UTC).isoformat(), len(page), from_cache))
            token = body.get("nextPageToken")
            if not token or not page:
                break
        rule = f"most recent {max_records:,} by start date" if truncated else None
        return FetchResult(records[:max_records], total, truncated, rule, logs)

    async def get_study(self, nct_id: str) -> dict[str, Any]:
        """Fetch one full study record by NCT ID."""
        await self._learn_data_timestamp()
        body, _, _ = await self._get(f"/studies/{nct_id}", {})
        return body

    async def _learn_data_timestamp(self) -> None:
        """Best-effort: learn the dataTimestamp before the first /studies request so its cache
        entries are keyed correctly from the start (§10.2). A down /version never blocks."""
        if self._known_data_timestamp is not None:
            return
        try:
            await self.version()
        except Exception as exc:  # noqa: BLE001 -- best-effort lookup; must never block a fetch
            log.warning(
                "ClinicalTrials.gov /version unavailable (%s); /studies cache is unscoped",
                type(exc).__name__,
            )

    async def version(self) -> dict[str, Any]:
        """The API's version/build info (provenance, §12.6), fetched once per process per
        VERSION_CACHE_TTL_S (a failure is remembered for VERSION_NEGATIVE_TTL_S). Its own cache,
        deliberately NOT keyed on dataTimestamp, which this call is what learns (§10.2)."""
        now = self._clock()
        if self._version_body is not None and now - self._version_at < VERSION_CACHE_TTL_S:
            return dict(self._version_body)
        failed_at = self._version_failed_at
        if failed_at is not None and now - failed_at < VERSION_NEGATIVE_TTL_S:
            raise UpstreamError("ClinicalTrials.gov /version failed recently; not retrying yet")
        try:
            raw, _ = await self._get_with_retries("/version", {})
        except UpstreamError:
            self._version_failed_at = now
            raise
        body: dict[str, Any] = json.loads(raw)
        self._version_body, self._version_at, self._version_failed_at = body, now, None
        timestamp = body.get("dataTimestamp")
        if isinstance(timestamp, str):
            self._known_data_timestamp = timestamp
        api_version = body.get("apiVersion")
        if isinstance(api_version, str):
            self._known_api_version = api_version
        return dict(body)
