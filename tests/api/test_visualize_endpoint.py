"""API contract test: dependency-injected fake planner + respx-mocked client (no network)."""

import logging
from typing import Any

import httpx
import openai
import pytest
import respx
from fastapi.testclient import TestClient

from ctviz.agent.planner import CLIENT_UNAVAILABLE_MESSAGE, Planner
from ctviz.api.app import app, get_ctgov_client, get_planner
from ctviz.api.errors import to_response
from ctviz.config import CTGOV_BASE_URL
from ctviz.ctgov.client import CtGovClient
from ctviz.errors import LLMUnavailableError, OutOfScopeError, PlanInvalidError, UpstreamError
from tests.factories import make_plan
from tests.fixtures.load import load_fixture
from tests.unit.agent.test_planner import FakeBackend, _fake_backend, _FakeParse, _install

TIME_TREND_ANALYSIS = {
    "kind": "time_trend",
    "group_by": None,
    "series_by": None,
    "phase_mode": None,
    "time_field": "start_date",
    "granularity": "year",
    "measure_x": None,
    "measure_y": None,
    "color_by": None,
    "network_type": None,
    "top_n": None,
}
TIME_SERIES_VIZ = {
    "type": "time_series",
    "title": "Pembrolizumab trials by year",
    "rationale": "trend over time",
}
REQUEST_BODY = {
    "query": "How has the number of trials for this drug changed over time?",
    "drug_name": "Pembrolizumab",
}


class _RaisingBackend:
    """A `PlannerBackend` that always raises a given exception instead of returning a plan."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def complete(self, system: str, user: str) -> Any:
        raise self._exc


def _plan() -> object:
    return make_plan(
        interpretation="Pembrolizumab trials by start year.",
        analysis=TIME_TREND_ANALYSIS,
        visualization=TIME_SERIES_VIZ,
    )


def _mock_pembrolizumab_studies() -> None:
    records = load_fixture("pembrolizumab")
    respx.get(f"{CTGOV_BASE_URL}/studies").mock(
        side_effect=[
            httpx.Response(200, json={"totalCount": 2960, "studies": []}),
            httpx.Response(200, json={"totalCount": 2960, "studies": records}),
        ]
    )


def test_visualize_returns_ok_true_on_the_happy_path(dependency_overrides: dict) -> None:
    dependency_overrides[get_planner] = lambda: Planner(FakeBackend(_plan()))

    with respx.mock:
        _mock_pembrolizumab_studies()
        with TestClient(app) as test_client:
            response = test_client.post("/v1/visualize", json=REQUEST_BODY)

    body = response.json()
    assert response.status_code == 200
    assert body["ok"] is True
    assert body["visualization"]["type"] == "time_series"


def test_visualize_returns_422_invalid_request_for_a_blank_query() -> None:
    with TestClient(app) as test_client:
        response = test_client.post("/v1/visualize", json={"query": ""})

    body = response.json()
    assert response.status_code == 422
    assert body["ok"] is False
    assert body["error"]["code"] == "INVALID_REQUEST"


def test_empty_query_returns_422_even_without_openai_key(dependency_overrides: dict) -> None:
    """Item 4: an invalid body is still 422, not 503, when the planner dependency would fail."""
    with TestClient(app) as test_client:
        response = test_client.post("/v1/visualize", json={"query": ""})

    body = response.json()
    assert response.status_code == 422
    assert body["error"]["code"] == "INVALID_REQUEST"


def test_422_message_is_readable_and_names_the_bad_field() -> None:
    """Item 12: the message lists each bad field and why, not the raw pydantic `.errors()` repr."""
    with TestClient(app) as test_client:
        response = test_client.post("/v1/visualize", json={"query": ""})

    message = response.json()["error"]["message"]
    assert "loc" not in message  # not the raw pydantic error-dict repr
    assert "query" in message
    assert "3 characters" in message or "at least" in message


def test_visualize_returns_502_upstream_api_error_when_ctgov_keeps_failing(
    dependency_overrides: dict,
) -> None:
    dependency_overrides[get_planner] = lambda: Planner(FakeBackend(_plan()))
    # Item 15: a fast (no-sleep) client, so retries don't slow this test down.
    dependency_overrides[get_ctgov_client] = lambda: CtGovClient(backoff_base_s=0)

    with respx.mock:
        respx.get(f"{CTGOV_BASE_URL}/studies").mock(
            return_value=httpx.Response(500, text="internal server error")
        )
        with TestClient(app) as test_client:
            response = test_client.post("/v1/visualize", json=REQUEST_BODY)

    body = response.json()
    assert response.status_code == 502
    assert body["ok"] is False
    assert body["error"]["code"] == "UPSTREAM_API_ERROR"


def test_visualize_returns_503_llm_unavailable_when_the_planner_fails(
    dependency_overrides: dict,
) -> None:
    """Item 5: `LLMUnavailableError` maps to 503 `LLM_UNAVAILABLE` through the full API stack."""
    dependency_overrides[get_planner] = lambda: Planner(
        _RaisingBackend(LLMUnavailableError("boom"))
    )

    with TestClient(app) as test_client:
        response = test_client.post("/v1/visualize", json=REQUEST_BODY)

    body = response.json()
    assert response.status_code == 503
    assert body["ok"] is False
    assert body["error"]["code"] == "LLM_UNAVAILABLE"


def test_visualize_returns_200_out_of_scope_when_the_planner_refuses(
    dependency_overrides: dict,
) -> None:
    """Item 10: a planner refusal is `OutOfScopeError` -> 200 ok:false OUT_OF_SCOPE, never 503."""
    dependency_overrides[get_planner] = lambda: Planner(
        _RaisingBackend(OutOfScopeError("I can't rank drug efficacy."))
    )

    with TestClient(app) as test_client:
        response = test_client.post("/v1/visualize", json=REQUEST_BODY)

    body = response.json()
    assert response.status_code == 200
    assert body["ok"] is False
    assert body["error"]["code"] == "OUT_OF_SCOPE"


def test_visualize_returns_200_plan_invalid_with_the_named_error_code(
    dependency_overrides: dict,
) -> None:
    """Item 5: `PlanInvalidError` maps to 200 ok:false PLAN_INVALID (a domain outcome)."""
    dependency_overrides[get_planner] = lambda: Planner(
        _RaisingBackend(PlanInvalidError(["analysis.kind=network is not yet supported"]))
    )

    with TestClient(app) as test_client:
        response = test_client.post("/v1/visualize", json=REQUEST_BODY)

    body = response.json()
    assert response.status_code == 200
    assert body["ok"] is False
    assert body["error"]["code"] == "PLAN_INVALID"


def test_visualize_returns_500_internal_error_with_no_exception_text_in_the_body(
    dependency_overrides: dict,
) -> None:
    """Item 5: an unexpected exception is 500 INTERNAL_ERROR; the body never echoes it."""
    secret_looking_detail = "KeyError: 'super-secret-internal-field-xyz'"

    class _BoomBackend:
        def complete(self, system: str, user: str) -> Any:
            raise KeyError(secret_looking_detail)

    dependency_overrides[get_planner] = lambda: Planner(_BoomBackend())

    # `raise_server_exceptions=False`: we're asserting on the 500 response our handler builds,
    # not on pytest re-raising the original exception (TestClient's default test-debugging mode).
    with TestClient(app, raise_server_exceptions=False) as test_client:
        response = test_client.post("/v1/visualize", json=REQUEST_BODY)

    body = response.json()
    assert response.status_code == 500
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert secret_looking_detail not in body["error"]["message"]
    assert "KeyError" not in body["error"]["message"]


def test_planner_sdk_error_never_leaks_a_key_fragment_into_the_api_response(
    dependency_overrides: dict,
) -> None:
    """Item 1: a masked-key SDK error message never reaches the client response body."""
    key_fragment = "sk-proj-AbCd****wxyz"
    sdk_message = f"Incorrect API key provided: {key_fragment}"
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    http_response = httpx.Response(401, request=request, json={"error": {"message": sdk_message}})
    sdk_error = openai.AuthenticationError(message=sdk_message, response=http_response, body=None)

    backend = _fake_backend()
    _install(backend, _FakeParse(sdk_error))
    dependency_overrides[get_planner] = lambda: Planner(backend)

    with TestClient(app) as test_client:
        response = test_client.post("/v1/visualize", json=REQUEST_BODY)

    body_text = response.text
    assert response.status_code == 503
    assert "sk-" not in body_text
    assert key_fragment not in body_text
    assert sdk_message not in body_text
    assert response.json()["error"]["message"] == CLIENT_UNAVAILABLE_MESSAGE


def test_plan_invalid_logs_a_warning_with_no_traceback(caplog: pytest.LogCaptureFixture) -> None:
    """Item 14: a 200 domain outcome logs at warning with no traceback, not a full exception log."""
    with caplog.at_level(logging.WARNING, logger="ctviz.api.errors"):
        to_response(PlanInvalidError(["bad plan"]))

    [record] = [r for r in caplog.records if "domain outcome" in r.message]
    assert record.levelname == "WARNING"
    assert record.exc_info is None


def test_upstream_error_still_logs_the_full_traceback(caplog: pytest.LogCaptureFixture) -> None:
    """A real failure (502) keeps the full traceback in the server log, unlike a domain outcome."""
    try:
        raise UpstreamError("ClinicalTrials.gov unreachable", status_code=502)
    except UpstreamError as exc:
        with caplog.at_level(logging.WARNING, logger="ctviz.api.errors"):
            to_response(exc)

    [record] = [r for r in caplog.records if "Pipeline failure" in r.message]
    assert record.levelname == "ERROR"
    assert record.exc_info is not None


def test_health_endpoint_reports_configured_providers_without_leaking_keys() -> None:
    with TestClient(app) as test_client:
        response = test_client.get("/health")

    body = response.json()
    assert response.status_code == 200
    assert body == {"status": "ok", "openai_configured": False, "openrouter_configured": False}


def test_to_response_falls_back_to_internal_error_for_an_unmapped_ctviz_error() -> None:
    """An unmapped `CtvizError` subtype still degrades to 500, never crashes `to_response`."""
    from ctviz.errors import CtvizError

    class _UnmappedError(CtvizError):
        pass

    status, response = to_response(_UnmappedError("surprise"))

    assert status == 500
    assert response.error is not None and response.error.code == "INTERNAL_ERROR"


def test_lifespan_closes_the_ctgov_client_on_shutdown() -> None:
    """Item 5: the pooled `CtGovClient` is closed when the app's lifespan exits."""
    with TestClient(app) as test_client:
        client: CtGovClient = test_client.app.state.ctgov_client
        assert client._http.is_closed is False

    assert client._http.is_closed is True
