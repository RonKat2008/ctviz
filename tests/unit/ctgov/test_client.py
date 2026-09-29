"""Client tests: paging, truncation-with-sort, upstream errors, retry (respx mocks, no network)."""

import httpx
import pytest
import respx

from ctviz.config import CTGOV_BASE_URL
from ctviz.ctgov.client import CtGovClient
from ctviz.errors import UpstreamError

STUDIES = f"{CTGOV_BASE_URL}/studies"


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
