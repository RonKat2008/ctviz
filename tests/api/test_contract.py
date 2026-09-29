"""S8 Task 8.1: schema endpoint, health, gzip, keyless replay mode, and (D5) the §12.2 status
mapping, table-driven over every situation the spec names."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ctviz.api.app import app
from ctviz.api.errors import to_response
from ctviz.config import Settings
from ctviz.errors import (
    CitationCheckError,
    LLMUnavailableError,
    OutOfScopeError,
    PlanInvalidError,
    UpstreamError,
)
from ctviz.schemas.response import VisualizeResponse

EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "examples"
PEMBRO_TIME_QUERY = "How has the number of pembrolizumab trials changed over time?"
PEMBRO_PHASE_QUERY = "How many pembrolizumab trials are there by phase?"


def test_schema_endpoint_exports_request_and_response_json_schema() -> None:
    """§12.8: `GET /v1/schema` returns both schemas; the response's `visualization` union exports
    as a JSON Schema discriminated union (`oneOf` + `"discriminator"`, per the plan's own words)."""
    with TestClient(app) as test_client:
        response = test_client.get("/v1/schema")

    body = response.json()
    assert response.status_code == 200
    assert set(body) == {"request", "response"}
    assert body["request"]["title"] == "VisualizeRequest"
    assert body["response"]["title"] == "VisualizeResponse"
    assert "discriminator" in json.dumps(body["response"])


def test_health_reports_providers_as_booleans_only() -> None:
    """No `sk-` anywhere in the body; `providers` is booleans, never key material."""
    with TestClient(app) as test_client:
        response = test_client.get("/health")

    assert response.status_code == 200
    assert "sk-" not in response.text
    body = response.json()
    assert body["ok"] is True
    assert body["providers"] == {"openai": False, "openrouter": False}
    assert isinstance(body["providers"]["openai"], bool)
    assert isinstance(body["providers"]["openrouter"], bool)
    assert body["mode"] == "live"
    assert body["ctgov"] is None  # nothing has called client.version() yet in this process


FAKE_KEY = "sk-test-fake-key-never-sent"


def test_health_never_leaks_key_material_even_when_keys_are_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With keys actually set, `providers` is still booleans and no `sk-` reaches the body."""
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    monkeypatch.setenv("OPENROUTER_API_KEY", FAKE_KEY)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    monkeypatch.setattr("ctviz.api.app.get_settings", lambda: settings)

    with TestClient(app) as test_client:
        response = test_client.get("/health")

    assert response.status_code == 200
    assert "sk-" not in response.text
    assert FAKE_KEY not in response.text
    assert response.json()["providers"] == {"openai": True, "openrouter": True}


def test_responses_are_gzipped_when_requested(dependency_overrides: dict) -> None:
    """`GZipMiddleware(minimum_size=1000)` is always on (§11.7); a real `/v1/visualize` response
    is comfortably over that threshold."""
    import httpx
    import respx

    from ctviz.agent.planner import Planner
    from ctviz.api.app import get_planner
    from ctviz.config import CTGOV_BASE_URL
    from tests.factories import make_plan
    from tests.fixtures.load import load_fixture
    from tests.unit.agent.test_planner import FakeBackend

    plan = make_plan(
        analysis={
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
        },
        visualization={"type": "time_series", "title": "t", "rationale": "r"},
    )
    dependency_overrides[get_planner] = lambda: Planner(FakeBackend(plan))
    records = load_fixture("pembrolizumab")

    with respx.mock:
        respx.get(f"{CTGOV_BASE_URL}/studies").mock(
            side_effect=[
                httpx.Response(200, json={"totalCount": len(records), "studies": []}),
                httpx.Response(200, json={"totalCount": len(records), "studies": records}),
            ]
        )
        respx.get(f"{CTGOV_BASE_URL}/version").mock(
            return_value=httpx.Response(200, json={"apiVersion": "2.0.5", "dataTimestamp": "t"})
        )
        with TestClient(app) as test_client:
            response = test_client.post(
                "/v1/visualize",
                json={
                    "query": "How has the number of pembrolizumab trials changed over time?",
                    "drug_name": "Pembrolizumab",
                },
                headers={"Accept-Encoding": "gzip"},
            )

    assert response.status_code == 200
    assert response.headers.get("content-encoding") == "gzip"
    # httpx transparently decodes the gzip stream, so `.content` is the plain body; the
    # `content-encoding` header above is the actual proof the middleware compressed it on the
    # wire. This just confirms the body was large enough to cross GZIP_MINIMUM_SIZE in the first
    # place -- otherwise the header assertion above would be trivially true of an uncompressed
    # small body that never engaged the middleware's threshold.
    assert len(response.content) > 1000


def test_service_starts_without_keys_and_llm_routes_return_503() -> None:
    """Item 4/5: the app's lifespan never fails to start without `.env` keys, and a route that
    needs the live planner degrades to 503 `LLM_UNAVAILABLE` (never a startup crash)."""
    with TestClient(app) as test_client:  # lifespan runs here; must not raise
        response = test_client.post(
            "/v1/visualize", json={"query": "How many pembrolizumab trials are there?"}
        )

    body = response.json()
    assert response.status_code == 503
    assert body["ok"] is False
    assert body["error"]["code"] == "LLM_UNAVAILABLE"


def test_replay_mode_answers_example_requests_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """`PLANNER_MODE=replay`: the canned planner, judge and offline ctgov client answer a known
    example question end to end -- no key, no network (respx isn't even armed here)."""
    monkeypatch.setenv("PLANNER_MODE", "replay")

    with TestClient(app) as test_client:
        response = test_client.post("/v1/visualize", json={"query": PEMBRO_TIME_QUERY})

    body = response.json()
    assert response.status_code == 200
    assert body["ok"] is True, body.get("error")
    assert body["visualization"]["type"] == "time_series"
    assert body["meta"]["provenance"]["api_version"] == "replay"
    assert body["meta"]["provenance"]["data_timestamp"] == "replay"


def test_replay_mode_answers_a_second_example_by_phase(monkeypatch: pytest.MonkeyPatch) -> None:
    """A different canned example (pembrolizumab by phase) resolves to its own plan/fixture."""
    monkeypatch.setenv("PLANNER_MODE", "replay")

    with TestClient(app) as test_client:
        response = test_client.post("/v1/visualize", json={"query": PEMBRO_PHASE_QUERY})

    body = response.json()
    assert response.status_code == 200
    assert body["ok"] is True, body.get("error")
    assert body["visualization"]["type"] == "bar_chart"


def test_replay_mode_names_the_available_examples_for_an_unknown_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An off-menu question in replay mode is a readable 200 ok:false PLAN_INVALID naming what
    replay mode CAN answer -- never a crash, never a silent wrong answer."""
    monkeypatch.setenv("PLANNER_MODE", "replay")

    with TestClient(app) as test_client:
        response = test_client.post(
            "/v1/visualize", json={"query": "What is the price of pembrolizumab?"}
        )

    body = response.json()
    assert response.status_code == 200
    assert body["ok"] is False
    assert body["error"]["code"] == "PLAN_INVALID"
    assert PEMBRO_TIME_QUERY in body["error"]["message"]


def test_every_example_file_validates_against_the_response_schema() -> None:
    """Every shipped `examples/*.response.json` parses as a `VisualizeResponse` (§12.8) -- the
    exact shape a grader's client would validate against `GET /v1/schema`."""
    example_files = sorted(EXAMPLES_DIR.glob("*.response.json"))

    assert example_files, "expected at least one shipped example response file"
    for path in example_files:
        response = VisualizeResponse.model_validate_json(path.read_text(encoding="utf-8"))
        assert response.schema_version == "1.0.0"
        assert response.ok in (True, False)


# --- D5: the §12.2 status/code mapping, table-driven over every situation -------------------

_EXCEPTION_TABLE: list[tuple[Exception, int, str]] = [
    (UpstreamError("down"), 502, "UPSTREAM_API_ERROR"),
    (LLMUnavailableError("no key"), 503, "LLM_UNAVAILABLE"),
    (OutOfScopeError("opinion"), 200, "OUT_OF_SCOPE"),
    (PlanInvalidError(["bad"]), 200, "PLAN_INVALID"),
    (CitationCheckError(["bad predicate"]), 500, "CITATION_CHECK_FAILED"),
]


@pytest.mark.parametrize("exc,expected_status,expected_code", _EXCEPTION_TABLE)
def test_to_response_matches_the_12_2_table_for_every_exception_backed_code(
    exc: Exception, expected_status: int, expected_code: str
) -> None:
    """§12.2: every exception-backed row of the status table maps exactly (D5)."""
    status, response = to_response(exc)

    assert status == expected_status
    assert response.error is not None
    assert response.error.code == expected_code
    assert response.ok is False


def test_invalid_request_maps_to_422_per_12_2() -> None:
    """§12.2: an invalid body (after coercion) is 422 `INVALID_REQUEST` -- handled by FastAPI's
    `RequestValidationError` handler, not `to_response` (D5: named here so the whole table has
    one test, even though this row isn't exception-mapped)."""
    with TestClient(app) as test_client:
        response = test_client.post("/v1/visualize", json={"query": ""})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_no_matching_trials_maps_to_200_per_12_2() -> None:
    """§12.2: NO_MATCHING_TRIALS is a domain outcome (200 ok:false) -- built directly in
    `pipeline._planning_failure`, not raised as an exception (D5: named here for the same
    reason)."""
    import httpx
    import respx

    from ctviz.agent.planner import Planner
    from ctviz.api.app import get_planner
    from ctviz.config import CTGOV_BASE_URL
    from tests.factories import make_plan
    from tests.unit.agent.test_planner import FakeBackend

    with TestClient(app) as test_client:
        test_client.app.dependency_overrides[get_planner] = lambda: Planner(
            FakeBackend(make_plan())
        )
        with respx.mock:
            respx.get(f"{CTGOV_BASE_URL}/studies").mock(
                return_value=httpx.Response(200, json={"totalCount": 0, "studies": []})
            )
            response = test_client.post(
                "/v1/visualize", json={"query": "How many pembrolizumab trials are there?"}
            )
        test_client.app.dependency_overrides.clear()

    body = response.json()
    assert response.status_code == 200
    assert body["ok"] is False
    assert body["error"]["code"] == "NO_MATCHING_TRIALS"
