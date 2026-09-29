"""Replay mode (`PLANNER_MODE=replay`): query matching, NCT lookups, judge status, fixtures."""

from pathlib import Path

import pytest
import respx
from fastapi.testclient import TestClient

from ctviz.api import replay
from ctviz.api.app import app
from ctviz.api.replay import (
    ReplayPlanner,
    available_example_queries,
    build_replay_ctgov_client,
    normalize_query,
)
from ctviz.errors import PlanInvalidError
from ctviz.schemas.request import VisualizeRequest

REPO_ROOT = Path(__file__).resolve().parents[2]
PEMBRO_TIME_QUERY = "How has the number of pembrolizumab trials changed over time?"
KNOWN_NCT = "NCT06994806"  # first record of the pembrolizumab fixture
UNKNOWN_NCT = "NCT00000001"


def _post(monkeypatch: pytest.MonkeyPatch, query: str) -> dict:
    monkeypatch.setenv("PLANNER_MODE", "replay")
    with TestClient(app) as test_client:
        return test_client.post("/v1/visualize", json={"query": query}).json()


@pytest.mark.parametrize(
    "variant",
    [
        "How has the number of pembrolizumab trials changed over time",  # no trailing "?"
        "How has the number of pembrolizumab trials changed over time ?",
        "  how  has the number   of pembrolizumab trials changed over time?  ",
        "HOW HAS THE NUMBER OF PEMBROLIZUMAB TRIALS CHANGED OVER TIME?!",
    ],
)
def test_query_variants_normalize_to_the_same_canned_example(variant: str) -> None:  # m17
    plan = ReplayPlanner().plan(VisualizeRequest(query=variant))

    assert plan.search_terms[0].value == "Pembrolizumab"
    assert normalize_query(variant) == normalize_query(PEMBRO_TIME_QUERY)


def test_an_unrelated_question_still_does_not_match() -> None:
    with pytest.raises(PlanInvalidError):
        ReplayPlanner().plan(VisualizeRequest(query="What is the price of pembrolizumab?"))


def test_unknown_query_lists_available_queries_in_details(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _post(monkeypatch, "What is the price of pembrolizumab?")

    assert body["error"]["code"] == "PLAN_INVALID"
    assert PEMBRO_TIME_QUERY in body["error"]["message"]
    assert body["error"]["details"]["available_queries"] == available_example_queries()


def test_known_nct_id_is_served_from_its_fixture_record(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _post(monkeypatch, KNOWN_NCT)

    assert body["ok"] is True, body.get("error")
    assert body["visualization"]["type"] == "table"
    assert KNOWN_NCT in body["visualization"]["data"][0].values() or KNOWN_NCT in str(
        body["visualization"]["data"]
    )
    assert body["meta"]["citation_check"] is not None


def test_unknown_nct_id_gets_the_examples_only_message_never_no_matching_trials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = _post(monkeypatch, UNKNOWN_NCT)

    assert body["ok"] is False
    assert body["error"]["code"] == "PLAN_INVALID"
    assert "Replay mode only answers" in body["error"]["message"]
    assert body["error"]["details"]["available_queries"] == available_example_queries()


def test_replay_judge_status_is_skipped_and_nothing_touches_the_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # m16
    with respx.mock:  # any HTTP call raises: no routes are registered
        body = _post(monkeypatch, PEMBRO_TIME_QUERY)

    assert body["ok"] is True, body.get("error")
    assert body["meta"]["validation"]["judge"]["status"] == "skipped"


def test_replay_reads_the_single_test_fixture_copy() -> None:
    assert replay.REPLAY_FIXTURES_DIR == REPO_ROOT / "tests" / "fixtures" / "ctgov"
    assert not (REPO_ROOT / "examples" / "replay_fixtures").exists()
    assert not hasattr(replay, "_FIXTURE_BY_KEYWORD")


def test_each_canned_example_routes_to_the_fixture_named_in_its_plan_entry() -> None:
    import asyncio

    from ctviz.ctgov.compiler import compile_plan

    client = build_replay_ctgov_client()
    for example in replay.load_canned_examples():
        params = compile_plan(example.plan)[0].params
        assert client._records_for(params) == replay._load_replay_fixture(example.fixture)
        assert asyncio.run(client.probe(params)) > 0


def test_missing_fixture_directory_is_a_clear_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(replay, "REPLAY_FIXTURES_DIR", tmp_path / "nope")

    with pytest.raises(FileNotFoundError, match="repo checkout"):
        build_replay_ctgov_client()
