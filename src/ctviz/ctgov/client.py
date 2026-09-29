"""Async ClinicalTrials.gov client: probe counts, fetch all pages (with a cap), retry, cache."""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, Self

import httpx

from ctviz.config import (
    CACHE_TTL_S,
    CTGOV_BASE_URL,
    HTTP_CONNECT_TIMEOUT_S,
    HTTP_RETRIES,
    HTTP_TIMEOUT_S,
    PAGE_SIZE,
)
from ctviz.errors import UpstreamError

log = logging.getLogger(__name__)
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
TRUNCATION_SORT = "StartDate:desc"


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

    def __init__(self, base_url: str = CTGOV_BASE_URL, backoff_base_s: float = 0.5) -> None:
        """Open a pooled async httpx client; backoff_base_s is 0 in tests to skip real waits."""
        self._http = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(HTTP_TIMEOUT_S, connect=HTTP_CONNECT_TIMEOUT_S),
        )
        self._backoff_base_s = backoff_base_s
        self._cache: dict[
            tuple[str, tuple[tuple[str, str], ...]], tuple[float, dict[str, Any], str]
        ] = {}

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

    async def _get(self, path: str, params: dict[str, str]) -> tuple[dict[str, Any], str, bool]:
        """A cached GET: identical (path, params) within CACHE_TTL_S skips the network."""
        key = (path, tuple(sorted(params.items())))
        cached = self._cache.get(key)
        now = asyncio.get_running_loop().time()
        if cached and now - cached[0] < CACHE_TTL_S:
            return cached[1], cached[2], True
        body, url = await self._get_with_retries(path, params)
        self._cache[key] = (now, body, url)
        return body, url, False

    async def _get_with_retries(
        self, path: str, params: dict[str, str]
    ) -> tuple[dict[str, Any], str]:
        """Retry transient failures with exponential backoff; raise UpstreamError otherwise."""
        for attempt in range(HTTP_RETRIES):
            try:
                response = await self._http.get(path, params=params)
            except httpx.TransportError as exc:
                if attempt == HTTP_RETRIES - 1:
                    raise UpstreamError(f"ClinicalTrials.gov unreachable: {exc}") from exc
            else:
                if response.status_code == 200:
                    return self._decode(response)
                if response.status_code not in RETRYABLE_STATUS or attempt == HTTP_RETRIES - 1:
                    body_text = response.text.strip()[:300]
                    message = f"ClinicalTrials.gov HTTP {response.status_code}: {body_text}"
                    raise UpstreamError(message, response.status_code)
            await asyncio.sleep(self._backoff_base_s * 2**attempt)
        raise UpstreamError("retries exhausted")  # unreachable; keeps mypy happy

    @staticmethod
    def _decode(response: httpx.Response) -> tuple[dict[str, Any], str]:
        """Parse a 200 response's JSON body; a malformed body is an UpstreamError, not a crash."""
        try:
            return response.json(), str(response.url)
        except (httpx.HTTPError, ValueError) as exc:
            raise UpstreamError(f"ClinicalTrials.gov returned an invalid response: {exc}") from exc

    async def probe(self, params: dict[str, str]) -> int:
        """Total matching trials for these params, using a 1-record request."""
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
        body, _, _ = await self._get(f"/studies/{nct_id}", {})
        return body

    async def version(self) -> dict[str, Any]:
        """Fetch the API's version/build info, for provenance."""
        body, _, _ = await self._get("/version", {})
        return body
