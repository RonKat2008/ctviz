"""Client tests: paging, truncation-with-sort, upstream errors, retry (respx mocks, no network)."""

from datetime import date

import httpx
import pytest
import respx

from ctviz.agent.planner import Planner
from ctviz.config import (
    CACHE_MAX_ENTRIES,
    CACHE_TTL_S,
    CTGOV_BASE_URL,
    RETRY_AFTER_CAP_S,
    VERSION_CACHE_TTL_S,
    VERSION_NEGATIVE_TTL_S,
)
from ctviz.ctgov.client import CtGovClient
from ctviz.errors import UpstreamError
from ctviz.pipeline import run_pipeline
from ctviz.schemas.request import VisualizeRequest
from tests.factories import make_plan
from tests.fixtures.load import load_fixture
from tests.unit.agent.test_planner import FakeBackend

STUDIES = f"{CTGOV_BASE_URL}/studies"
VERSION = f"{CTGOV_BASE_URL}/version"


class FakeClock:
    """A settable monotonic clock injected into the client (no real waiting)."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _page(ids: range, token: str | None, total: int | None = None) -> dict:
    body: dict = {
        "studies": [
            {"protocolSection": {"identificationModule": {"nctId": f"NCT{i:08d}"}}} for i in ids
        ]
    }
    if token:
        body["nextPageToken"] = token
    if total is not None:
        body["totalCount"] = total
    return body


@respx.mock
async def test_probe_returns_total_count() -> None:
    respx.get(STUDIES).mock(
        return_value=httpx.Response(200, json=_page(range(1), None, total=2960))
    )

    async with CtGovClient() as client:
        assert await client.probe({"query.intr": "pembrolizumab"}) == 2960


@respx.mock
async def test_fetch_all_follows_page_tokens() -> None:
    route = respx.get(STUDIES).mock(
        side_effect=[
            httpx.Response(200, json=_page(range(0, 1), None, total=3)),  # probe
            httpx.Response(200, json=_page(range(0, 2), "t1", total=3)),
            httpx.Response(200, json=_page(range(2, 3), None)),
        ]
    )

    async with CtGovClient() as client:
        result = await client.fetch_all({"query.intr": "x"}, max_records=100)

    assert len(result.records) == 3 and result.api_total_count == 3
    assert result.truncated is False
    assert route.calls[2].request.url.params["pageToken"] == "t1"


@respx.mock
async def test_fetch_all_truncates_and_sorts_when_over_cap() -> None:
    route = respx.get(STUDIES).mock(
        side_effect=[
            httpx.Response(200, json=_page(range(0, 1), None, total=50_000)),  # probe
            httpx.Response(200, json=_page(range(0, 2), "t1", total=50_000)),
            httpx.Response(200, json=_page(range(2, 4), "t2")),
        ]
    )

    async with CtGovClient() as client:
        result = await client.fetch_all({"query.cond": "oncology"}, max_records=3)

    assert len(result.records) == 3
    assert result.truncated is True
    assert result.truncation_rule == "most recent 3 by start date"
    assert route.calls[1].request.url.params["sort"] == "StartDate:desc"


@respx.mock
async def test_client_raises_upstream_error_with_text_body() -> None:
    respx.get(STUDIES).mock(
        return_value=httpx.Response(
            400, text="Parameter 'fields' contains invalid field name: 'Nope'"
        )
    )

    async with CtGovClient() as client:
        with pytest.raises(UpstreamError, match="invalid field name") as info:
            await client.probe({"fields": "Nope"})

    assert info.value.status_code == 400


@respx.mock
async def test_client_retries_transient_5xx_then_succeeds() -> None:
    respx.get(STUDIES).mock(
        side_effect=[
            httpx.Response(503, text="busy"),
            httpx.Response(200, json=_page(range(1), None, total=1)),
        ]
    )

    async with CtGovClient(backoff_base_s=0) as client:
        assert await client.probe({}) == 1


@respx.mock
async def test_client_raises_upstream_error_after_retries_exhausted_on_5xx() -> None:
    respx.get(STUDIES).mock(return_value=httpx.Response(500, text="server error"))

    async with CtGovClient(backoff_base_s=0) as client:
        with pytest.raises(UpstreamError, match="server error"):
            await client.probe({})


@respx.mock
async def test_client_raises_upstream_error_on_timeout() -> None:
    respx.get(STUDIES).mock(side_effect=httpx.ConnectTimeout("timed out"))

    async with CtGovClient(backoff_base_s=0) as client:
        with pytest.raises(UpstreamError, match="unreachable"):
            await client.probe({})


@respx.mock
async def test_client_raises_upstream_error_on_non_json_success_body() -> None:
    respx.get(STUDIES).mock(return_value=httpx.Response(200, text="not json"))

    async with CtGovClient(backoff_base_s=0) as client:
        with pytest.raises(UpstreamError):
            await client.probe({})


@respx.mock
async def test_fetch_all_stops_on_empty_page_with_token() -> None:
    route = respx.get(STUDIES).mock(
        side_effect=[
            httpx.Response(200, json=_page(range(0, 1), None, total=5)),  # probe
            httpx.Response(200, json={"studies": [], "nextPageToken": "t1"}),  # empty page
        ]
    )

    async with CtGovClient() as client:
        result = await client.fetch_all({"query.intr": "x"}, max_records=100)

    assert result.records == []
    assert route.call_count == 2


@respx.mock
async def test_cached_request_log_keeps_original_url() -> None:
    respx.get(STUDIES).mock(
        side_effect=[
            httpx.Response(200, json=_page(range(0, 1), None, total=1)),  # probe
            httpx.Response(200, json=_page(range(0, 1), None, total=1)),  # page
        ]
    )

    async with CtGovClient() as client:
        first = await client.fetch_all({"query.intr": "x"}, max_records=10)
        second = await client.fetch_all({"query.intr": "x"}, max_records=10)

    assert second.requests[0].url == first.requests[0].url
    assert second.requests[0].url != "cache"
    assert second.requests[0].from_cache is True
    assert first.requests[0].from_cache is False


@respx.mock
async def test_client_error_message_names_status_when_body_empty() -> None:
    respx.get(STUDIES).mock(return_value=httpx.Response(404, text=""))

    async with CtGovClient(backoff_base_s=0) as client:
        with pytest.raises(UpstreamError, match=r"ClinicalTrials\.gov HTTP 404") as info:
            await client.probe({})

    assert info.value.status_code == 404


@respx.mock
async def test_client_does_not_retry_on_400() -> None:
    route = respx.get(STUDIES).mock(return_value=httpx.Response(400, text="bad request"))

    async with CtGovClient(backoff_base_s=0) as client:
        with pytest.raises(UpstreamError):
            await client.probe({})

    assert route.call_count == 1


@pytest.mark.parametrize(("status", "body"), [(401, "unauthorized"), (403, "forbidden")])
@respx.mock
async def test_client_does_not_retry_auth_statuses_and_reports_a_readable_error(
    status: int, body: str
) -> None:
    route = respx.get(STUDIES).mock(return_value=httpx.Response(status, text=body))

    async with CtGovClient(backoff_base_s=0) as client:
        with pytest.raises(UpstreamError) as info:
            await client.probe({})

    assert route.call_count == 1
    assert str(info.value) == f"ClinicalTrials.gov HTTP {status}: {body}"
    assert info.value.status_code == status


@respx.mock
async def test_client_truncates_a_huge_error_body() -> None:
    respx.get(STUDIES).mock(return_value=httpx.Response(403, text="x" * 5000))

    async with CtGovClient(backoff_base_s=0) as client:
        with pytest.raises(UpstreamError) as info:
            await client.probe({})

    assert len(str(info.value)) < 400


# --- D2: Retry-After + jitter --------------------------------------------------------------


@respx.mock
async def test_client_honors_retry_after_header_on_429() -> None:
    respx.get(STUDIES).mock(
        side_effect=[
            httpx.Response(429, text="slow down", headers={"retry-after": "2"}),
            httpx.Response(200, json=_page(range(1), None, total=1)),
        ]
    )
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    async with CtGovClient(backoff_base_s=0, sleep=fake_sleep) as client:
        assert await client.probe({}) == 1

    assert waits == [2.0]


@respx.mock
async def test_client_caps_retry_after_to_the_named_constant() -> None:
    respx.get(STUDIES).mock(
        side_effect=[
            httpx.Response(429, text="slow down", headers={"retry-after": "99999"}),
            httpx.Response(200, json=_page(range(1), None, total=1)),
        ]
    )
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    async with CtGovClient(backoff_base_s=0, sleep=fake_sleep) as client:
        assert await client.probe({}) == 1

    assert waits == [RETRY_AFTER_CAP_S]


@respx.mock
async def test_client_falls_back_to_backoff_when_429_has_no_retry_after_header() -> None:
    respx.get(STUDIES).mock(
        side_effect=[
            httpx.Response(429, text="slow down"),
            httpx.Response(200, json=_page(range(1), None, total=1)),
        ]
    )
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    async with CtGovClient(backoff_base_s=1.0, sleep=fake_sleep, rand=lambda: 0.0) as client:
        assert await client.probe({}) == 1

    assert waits == [1.0]  # attempt 0: base 1.0 * 2**0, zero jitter (rand=0)


@respx.mock
async def test_client_adds_jitter_to_exponential_backoff_on_5xx() -> None:
    respx.get(STUDIES).mock(
        side_effect=[
            httpx.Response(503, text="busy"),
            httpx.Response(200, json=_page(range(1), None, total=1)),
        ]
    )
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    async with CtGovClient(backoff_base_s=1.0, sleep=fake_sleep, rand=lambda: 1.0) as client:
        assert await client.probe({}) == 1

    # base 1.0 * 2**0 = 1.0, jitter = rand() * base * RETRY_JITTER_FRACTION = 1.0 * 1.0 * 0.25
    assert waits == [1.25]


async def test_client_backoff_has_no_jitter_when_backoff_base_is_zero() -> None:
    # No respx needed here: this checks the delay math directly (zero mutations to network state).
    async with CtGovClient(backoff_base_s=0.0, rand=lambda: 1.0) as client:
        assert client._backoff_delay(0) == 0.0


# --- D1: bounded LRU cache with TTL purge and immutable cached bodies -----------------------


@respx.mock
async def test_cache_evicts_the_least_recently_used_entry_past_the_named_max() -> None:
    route = respx.get(STUDIES).mock(
        side_effect=[httpx.Response(200, json=_page(range(1), None, total=i)) for i in range(500)]
    )

    async with CtGovClient() as client:
        for i in range(CACHE_MAX_ENTRIES + 1):
            await client.probe({"query.intr": f"drug-{i}"})
        assert len(client._cache) <= CACHE_MAX_ENTRIES
        # The very first key was evicted, so requesting it again is a fresh network call.
        calls_before = route.call_count
        await client.probe({"query.intr": "drug-0"})
        assert route.call_count == calls_before + 1


@respx.mock
async def test_cached_response_body_is_not_the_same_mutable_object_across_calls() -> None:
    respx.get(STUDIES).mock(
        side_effect=[
            httpx.Response(200, json=_page(range(0, 1), None, total=1)),  # probe
            httpx.Response(200, json=_page(range(0, 1), None, total=1)),  # page
        ]
    )

    async with CtGovClient() as client:
        first = await client.fetch_all({"query.intr": "x"}, max_records=10)
        first.records[0]["INJECTED"] = "mutated"
        second = await client.fetch_all({"query.intr": "x"}, max_records=10)

    assert "INJECTED" not in second.records[0]


# --- S8 fix pass: /version cache, LRU recency, TTL, key, bytes bound, Retry-After -----------


def _studies_reply(request: httpx.Request) -> httpx.Response:
    """Probe (pageSize=1) gets an empty page; a real page gets 3 pembrolizumab records."""
    records = load_fixture("pembrolizumab")[:3]
    probe = request.url.params.get("pageSize") == "1"
    return httpx.Response(200, json={"totalCount": 3, "studies": [] if probe else records})


@respx.mock
async def test_version_fetched_once_and_studies_not_duplicated_across_three_pipeline_runs() -> None:
    studies = respx.get(STUDIES).mock(side_effect=_studies_reply)
    version = respx.get(VERSION).mock(
        return_value=httpx.Response(200, json={"apiVersion": "2.0.5", "dataTimestamp": "T1"})
    )
    planner = Planner(FakeBackend(make_plan()))

    async with CtGovClient() as client:
        for _ in range(3):
            response = await run_pipeline(
                VisualizeRequest(query="pembrolizumab trials by phase"),
                planner=planner,
                client=client,
                today=date(2026, 9, 28),
            )
            assert response.ok, response.error

    assert version.call_count == 1
    assert studies.call_count == 2  # one probe + one page, total; runs 2 and 3 are cache hits


@respx.mock
async def test_failed_version_is_cached_for_the_negative_ttl_then_retried() -> None:
    route = respx.get(VERSION).mock(return_value=httpx.Response(503, text="down"))
    clock = FakeClock()

    async with CtGovClient(backoff_base_s=0, clock=clock) as client:
        with pytest.raises(UpstreamError):
            await client.version()
        calls_after_first = route.call_count
        with pytest.raises(UpstreamError):
            await client.version()
        assert route.call_count == calls_after_first  # served from the negative cache
        clock.now += VERSION_NEGATIVE_TTL_S + 1
        with pytest.raises(UpstreamError):
            await client.version()

    assert route.call_count > calls_after_first


@respx.mock
async def test_a_down_version_endpoint_never_blocks_studies_requests() -> None:
    respx.get(VERSION).mock(return_value=httpx.Response(503, text="down"))
    respx.get(STUDIES).mock(return_value=httpx.Response(200, json=_page(range(1), None, total=7)))

    async with CtGovClient(backoff_base_s=0) as client:
        assert await client.probe({"query.intr": "x"}) == 7


@respx.mock
async def test_version_is_refetched_after_its_ttl() -> None:
    route = respx.get(VERSION).mock(
        return_value=httpx.Response(200, json={"apiVersion": "1", "dataTimestamp": "T"})
    )
    clock = FakeClock()

    async with CtGovClient(clock=clock) as client:
        await client.version()
        await client.version()
        assert route.call_count == 1
        clock.now += VERSION_CACHE_TTL_S + 1
        await client.version()

    assert route.call_count == 2


@respx.mock
async def test_a_recently_read_cache_entry_survives_lru_eviction() -> None:  # m01
    route = respx.get(STUDIES).mock(
        side_effect=[httpx.Response(200, json=_page(range(1), None, total=i)) for i in range(500)]
    )

    async with CtGovClient() as client:
        for i in range(CACHE_MAX_ENTRIES):
            await client.probe({"query.intr": f"drug-{i}"})
        await client.probe({"query.intr": "drug-0"})  # re-read: now most recently used
        await client.probe({"query.intr": "new"})  # forces one eviction
        calls = route.call_count
        await client.probe({"query.intr": "drug-0"})
        assert route.call_count == calls  # survived
        await client.probe({"query.intr": "drug-1"})
        assert route.call_count == calls + 1  # the true LRU entry was the one evicted


@respx.mock
async def test_expired_entries_are_purged_and_refetched() -> None:  # m03
    route = respx.get(STUDIES).mock(
        side_effect=[httpx.Response(200, json=_page(range(1), None, total=i)) for i in range(50)]
    )
    clock = FakeClock()

    async with CtGovClient(clock=clock) as client:
        await client.probe({"query.intr": "a"})
        await client.probe({"query.intr": "b"})
        clock.now += CACHE_TTL_S + 1
        await client.probe({"query.intr": "c"})
        assert len(client._cache) == 1  # a and b were proactively purged
        calls = route.call_count
        await client.probe({"query.intr": "a"})

    assert route.call_count == calls + 1


@respx.mock
async def test_a_new_data_timestamp_gives_new_studies_cache_entries() -> None:  # m04
    versions = iter(["T1", "T2"])
    respx.get(VERSION).mock(
        side_effect=lambda _r: httpx.Response(
            200, json={"apiVersion": "1", "dataTimestamp": next(versions)}
        )
    )
    studies = respx.get(STUDIES).mock(
        return_value=httpx.Response(200, json=_page(range(1), None, total=1))
    )
    clock = FakeClock()

    async with CtGovClient(clock=clock) as client:
        await client.probe({"query.intr": "x"})
        await client.probe({"query.intr": "x"})
        assert studies.call_count == 1
        clock.now += VERSION_CACHE_TTL_S + 1  # < CACHE_TTL_S: old entries are still fresh
        await client.version()  # learns T2
        await client.probe({"query.intr": "x"})

    assert studies.call_count == 2


@respx.mock
async def test_cache_hits_return_independent_objects_every_time() -> None:  # m05a
    respx.get(STUDIES).mock(
        return_value=httpx.Response(200, json=_page(range(0, 1), None, total=1))
    )

    async with CtGovClient() as client:
        await client.fetch_all({"query.intr": "x"}, max_records=10)
        hit = await client.fetch_all({"query.intr": "x"}, max_records=10)  # served from cache
        hit.records[0]["INJECTED"] = "mutated"
        again = await client.fetch_all({"query.intr": "x"}, max_records=10)

    assert "INJECTED" not in again.records[0]


@respx.mock
async def test_cache_is_bounded_by_total_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    route = respx.get(STUDIES).mock(
        side_effect=[httpx.Response(200, json=_page(range(1), None, total=i)) for i in range(50)]
    )
    monkeypatch.setattr("ctviz.ctgov.client.CACHE_MAX_BYTES", 500)

    async with CtGovClient() as client:
        for i in range(10):
            await client.probe({"query.intr": f"d{i}"})
        assert client._cache_bytes <= 500
        assert 0 < len(client._cache) < 10
        calls = route.call_count
        await client.probe({"query.intr": "d0"})  # evicted by the byte bound -> refetched

    assert route.call_count == calls + 1


@respx.mock
async def test_retry_after_http_date_form_falls_back_to_backoff() -> None:
    respx.get(STUDIES).mock(
        side_effect=[
            httpx.Response(
                429, text="slow", headers={"retry-after": "Wed, 21 Oct 2015 07:28:00 GMT"}
            ),
            httpx.Response(200, json=_page(range(1), None, total=1)),
        ]
    )
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    async with CtGovClient(backoff_base_s=1.0, sleep=fake_sleep, rand=lambda: 0.0) as client:
        await client.probe({})

    assert waits == [1.0]


async def test_retry_after_cap_is_five_seconds() -> None:
    assert RETRY_AFTER_CAP_S == 5.0


@respx.mock
async def test_retry_sleeps_never_exceed_the_remaining_retry_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    respx.get(STUDIES).mock(
        side_effect=[
            httpx.Response(429, text="s", headers={"retry-after": "5"}),
            httpx.Response(429, text="s", headers={"retry-after": "5"}),
            httpx.Response(200, json=_page(range(1), None, total=1)),
        ]
    )
    monkeypatch.setattr("ctviz.ctgov.client.RETRY_BUDGET_S", 7.0)
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    async with CtGovClient(backoff_base_s=0, sleep=fake_sleep) as client:
        await client.probe({})

    assert waits == [5.0, 2.0]  # second wait clipped to the 2 s left of a 7 s budget
