"""Strict match wired into the pipeline (Task 6.2): non-matching trials are excluded and
reported, every kept trial's citations carry a `match` evidence item, and the cohort's
`base_predicate` is a real, evaluable predicate (PLAN.md §11.3, §11.4 item 3)."""

from datetime import date

import httpx
import respx

from ctviz.agent.planner import Planner
from ctviz.citations.predicates import evaluate
from ctviz.config import CTGOV_BASE_URL
from ctviz.ctgov.client import CtGovClient
from ctviz.pipeline import run_pipeline
from ctviz.schemas.request import VisualizeRequest
from tests.factories import make_plan
from tests.fixtures.load import load_fixture
from tests.unit.agent.test_planner import FakeBackend


def _mock_pembrolizumab_studies() -> None:
    records = load_fixture("pembrolizumab")
    respx.get(f"{CTGOV_BASE_URL}/studies").mock(
        side_effect=[
            httpx.Response(200, json={"totalCount": 2960, "studies": []}),
            httpx.Response(200, json={"totalCount": 2960, "studies": records}),
        ]
    )


@respx.mock
async def test_pipeline_excludes_trials_failing_strict_match_and_cites_the_rest() -> None:
    _mock_pembrolizumab_studies()
    plan = make_plan()  # default: query.intr=pembrolizumab, count_by(phase) -> bar_chart
    planner = Planner(FakeBackend(plan))
    request = VisualizeRequest(query="Trials by phase?", drug_name="Pembrolizumab")

    async with CtGovClient() as client:
        response = await run_pipeline(
            request, planner=planner, client=client, today=date(2026, 9, 28)
        )

    assert response.ok
    assert response.meta is not None
    coverage = response.meta.data_coverage
    assert coverage is not None
    assert coverage.excluded["match"]["api_fulltext_match_only"] > 0
    match_excluded = sum(coverage.excluded["match"].values())
    assert coverage.records_matched == coverage.api_total_count - match_excluded
    match_stage = [e for e in coverage.excluded_trials if e.stage == "match"]
    assert match_stage  # at least one non-matching trial was actually dropped
    assert all(e.reason == "api_fulltext_match_only" for e in match_stage)

    assert response.visualization is not None
    citations = [c for row in response.visualization.data for c in row["citations"]]
    assert citations  # the chart isn't empty
    for citation in citations:
        assert any(item.role == "match" for item in citation.evidence)

    cohort = response.meta.cohorts[0]
    aliases = response.meta.entity_resolution["aliases"][cohort.label]
    assert {"keytruda", "mk-3475"} <= set(aliases)
    assert not {"carboplatin", "nivolumab", "immunotherapy"} & set(aliases)
    raw_by_id = {
        r["protocolSection"]["identificationModule"]["nctId"]: r
        for r in load_fixture("pembrolizumab")
    }
    excluded_ids = {e.nct_id for e in match_stage}
    for nct_id in ("NCT03307785", "NCT06205732"):  # PLAN.md §11.3's named off-topic trials
        assert nct_id in excluded_ids
        assert evaluate(cohort.base_predicate, raw_by_id[nct_id]) is False
    cited_ids = {c.nct_id for c in citations}
    assert cited_ids == set(raw_by_id) - excluded_ids
    assert all(evaluate(cohort.base_predicate, raw_by_id[i]) for i in cited_ids)


@respx.mock
async def test_lenient_condition_term_keeps_unmatched_trials_without_match_evidence() -> None:
    """Q1=a: under the default `auto` policy, a condition search term is lenient (kept, no
    `match` evidence) unless config.CONDITIONS_STRICT flips it."""
    records = load_fixture("psoriasis_p2")
    respx.get(f"{CTGOV_BASE_URL}/studies").mock(
        side_effect=[
            httpx.Response(200, json={"totalCount": len(records), "studies": []}),
            httpx.Response(200, json={"totalCount": len(records), "studies": records}),
        ]
    )
    plan = make_plan(search_terms=[])
    planner = Planner(FakeBackend(plan))
    request = VisualizeRequest(query="Trials by phase?", condition="Psoriasis")

    async with CtGovClient() as client:
        response = await run_pipeline(
            request, planner=planner, client=client, today=date(2026, 9, 28)
        )

    assert response.ok
    assert response.meta is not None
    coverage = response.meta.data_coverage
    assert coverage is not None
    assert coverage.excluded["match"] == {}
    assert coverage.records_matched == coverage.api_total_count
