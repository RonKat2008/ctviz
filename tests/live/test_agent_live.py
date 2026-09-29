"""Live S7 smoke: one real request per analysis kind through the full revise loop (§14).

Marked `live` (deselected by default). Needs OPENAI_API_KEY and OPENROUTER_API_KEY in `.env` and
network access: `uv run pytest -m live tests/live/test_agent_live.py -v`.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from ctviz.api.app import app

pytestmark = pytest.mark.live

EXECUTED = {
    "passed",
    "passed_after_revision",
    "rejected_after_revision",
    "executed_previous_plan",
    "unavailable",
}
CASES: list[tuple[str, dict[str, Any], set[str]]] = [
    (
        "count_by",
        {"query": "How are pembrolizumab trials distributed by phase?"},
        {"bar_chart", "table", "metric"},
    ),
    (
        "time_trend",
        {
            "query": "How has the number of trials for this drug changed over time?",
            "drug_name": "Pembrolizumab",
        },
        {"time_series", "bar_chart"},
    ),
    (
        "histogram",
        {"query": "What is the distribution of enrollment sizes for glioblastoma trials?"},
        {"histogram"},
    ),
    (
        "scatter",
        {"query": "Plot enrollment against trial duration for Crohn's disease trials"},
        {"scatter_plot"},
    ),
    (
        "network",
        {
            "query": "Show a network of sponsors and drugs",
            "condition": "glioblastoma",
            "trial_phase": "Phase 3",
            "options": {"top_n": 40},
        },
        {"network_graph", "bar_chart"},
    ),
    (
        "comparison",
        {"query": "Compare phases for trials involving pembrolizumab vs nivolumab"},
        {"grouped_bar_chart", "time_series"},
    ),
]


@pytest.mark.parametrize(("kind", "body", "viz_types"), CASES, ids=[c[0] for c in CASES])
def test_live_request_per_analysis_kind_succeeds(
    kind: str, body: dict[str, Any], viz_types: set[str]
) -> None:
    with TestClient(app) as client:
        response = client.post("/v1/visualize", json=body)

    payload = response.json()
    assert response.status_code == 200, payload
    assert payload["ok"] is True, payload["error"]
    assert payload["visualization"]["type"] in viz_types
    assert payload["meta"]["validation"]["judge"]["status"] in EXECUTED
    assert payload["meta"]["citation_check"]["passed"] is True


def test_live_misspelled_drug_revises_then_answers_or_reports_no_matches() -> None:
    with TestClient(app) as client:
        response = client.post("/v1/visualize", json={"query": "Keytrudda trials by phase"})

    payload = response.json()
    if payload["ok"]:
        steps = [e["step"] for e in payload["meta"]["validation"]["trace"]]
        assert steps.count("plan") in (1, 2)
    else:
        assert payload["error"]["code"] == "NO_MATCHING_TRIALS"


def test_live_efficacy_ranking_is_out_of_scope() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/v1/visualize", json={"query": "Which drug is most effective for lung cancer?"}
        )

    payload = response.json()
    assert payload["ok"] is False
    assert payload["error"]["code"] == "OUT_OF_SCOPE"
