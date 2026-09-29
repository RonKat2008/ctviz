"""Golden run (§11.9): the independent verifier PASSES on real pipeline output for every
fixture and every S3-S5 analysis type. `run_pipeline` runs the verifier internally and raises
`CitationCheckError` on any violation, so a bare pass here (no exception, `response.ok`,
`citation_check.passed`) already proves it end to end -- no separate assertion needed."""

from datetime import date

import httpx
import pytest
import respx

from ctviz.agent.planner import Planner
from ctviz.config import CTGOV_BASE_URL
from ctviz.ctgov.client import CtGovClient
from ctviz.pipeline import run_pipeline
from ctviz.schemas.request import VisualizeRequest
from tests.factories import make_plan
from tests.fixtures.load import load_fixture
from tests.unit.agent.test_planner import FakeBackend

FIXTURES = (
    "pembrolizumab",
    "nivolumab",
    "ms_recruiting",
    "glioblastoma",
    "psoriasis_p2",
    "crohns_p3_completed",
    "nsclc",
)
LARGE_FIXTURES = ("pembrolizumab", "nivolumab", "nsclc")
VERIFIER_BUDGET_MS = 5000  # generous; measured ~70 ms (intervention) / ~190 ms (country)
PEMBRO_KEPT = 2635  # 2,960 fetched - 325 excluded at strict match (test_strict_match_fixtures)
SMALL_FIXTURES = tuple(f for f in FIXTURES if f not in LARGE_FIXTURES)


def _analysis(kind: str, **overrides: object) -> dict:
    base = {
        "kind": kind,
        "group_by": None,
        "series_by": None,
        "phase_mode": "combined" if kind == "count_by" else None,
        "time_field": "start_date" if kind == "time_trend" else None,
        "granularity": "year" if kind == "time_trend" else None,
        "measure_x": None,
        "measure_y": None,
        "color_by": None,
        "network_type": None,
        # A realistic top_n (PLAN.md §22 default: bar 15 + "Other"). Unset, count_by keeps every
        # distinct category as its own bucket -- for a multi-valued dimension like `intervention`
        # on a large fixture, that's hundreds of buckets, each independently re-scanning the
        # whole cohort in the verifier's O(buckets x population) completeness recount. Real plans
        # always apply a top_n; leaving this unbounded here would make the golden run needlessly
        # (and unrepresentatively) slow, not more correct.
        "top_n": 15 if kind == "count_by" else None,
    }
    return base | overrides


async def _run(fixture: str, plan: object, viz_type: str, **fields: object) -> object:
    records = load_fixture(fixture)
    respx.get(f"{CTGOV_BASE_URL}/studies").mock(
        side_effect=[
            httpx.Response(200, json={"totalCount": len(records), "studies": []}),
            httpx.Response(200, json={"totalCount": len(records), "studies": records}),
        ]
    )
    planner = Planner(FakeBackend(plan))
    # S6: an empty search_terms plan avoids the S3-era default query.intr=pembrolizumab term
    # being strict-matched against fixtures that aren't about pembrolizumab (see the S6 rulings
    # in test_pipeline_s5.py); every fixture here is exercised on its own terms via count_by
    # dimensions that don't depend on any particular drug/condition actually being searched.
    request = VisualizeRequest(query=f"{viz_type} for this data?", **fields)
    async with CtGovClient() as client:
        return await run_pipeline(request, planner=planner, client=client, today=date(2026, 9, 28))


async def _assert_golden(fixture: str, dimension: str) -> None:
    plan = make_plan(
        search_terms=[],
        analysis=_analysis("count_by", group_by=dimension),
        visualization={"type": "bar_chart", "title": "t", "rationale": "r"},
    )
    response = await _run(fixture, plan, "bar_chart")

    assert response.ok, response.error
    assert response.meta.citation_check is not None
    assert response.meta.citation_check.passed is True


@respx.mock
@pytest.mark.parametrize("fixture", FIXTURES)
@pytest.mark.parametrize(
    "dimension", ["phase", "overall_status", "study_type", "lead_sponsor_class"]
)
async def test_golden_count_by_exclusive_dims_passes_the_verifier(
    fixture: str, dimension: str
) -> None:
    """Exclusive dims: O(1) predicate evaluation per record, cheap on every fixture size."""
    await _assert_golden(fixture, dimension)


@respx.mock
@pytest.mark.parametrize("fixture", SMALL_FIXTURES)
@pytest.mark.parametrize("dimension", ["country", "intervention", "condition"])
async def test_golden_count_by_multi_valued_dims_passes_the_verifier(
    fixture: str, dimension: str
) -> None:
    """Multi-valued dims on the 4 smaller fixtures; the 3 large ones are covered by the perf
    test (nsclc) and the strict-match golden runs (pembrolizumab) below."""
    await _assert_golden(fixture, dimension)


@respx.mock
@pytest.mark.parametrize("fixture", FIXTURES)
async def test_golden_time_trend_passes_the_verifier(fixture: str) -> None:
    plan = make_plan(
        search_terms=[],
        analysis=_analysis("time_trend"),
        visualization={"type": "time_series", "title": "t", "rationale": "r"},
    )
    response = await _run(fixture, plan, "time_series")

    assert response.ok, response.error
    assert response.meta.citation_check.passed is True


@respx.mock
@pytest.mark.parametrize("fixture", FIXTURES)
async def test_golden_histogram_passes_the_verifier(fixture: str) -> None:
    plan = make_plan(
        search_terms=[],
        analysis=_analysis("histogram", measure_x="enrollment"),
        visualization={"type": "histogram", "title": "t", "rationale": "r"},
    )
    response = await _run(fixture, plan, "histogram")

    assert response.ok, response.error
    assert response.meta.citation_check.passed is True


@respx.mock
@pytest.mark.parametrize("fixture", FIXTURES)
async def test_golden_scatter_passes_the_verifier(fixture: str) -> None:
    plan = make_plan(
        search_terms=[],
        analysis=_analysis("scatter", measure_x="duration_months", measure_y="enrollment"),
        visualization={"type": "scatter_plot", "title": "t", "rationale": "r"},
    )
    response = await _run(fixture, plan, "scatter_plot")

    assert response.ok, response.error
    assert response.meta.citation_check.passed is True


@respx.mock
@pytest.mark.parametrize("fixture", FIXTURES)
@pytest.mark.parametrize("network_type", ["sponsor_drug", "drug_drug", "condition_drug"])
async def test_golden_network_passes_the_verifier(fixture: str, network_type: str) -> None:
    plan = make_plan(
        search_terms=[],
        analysis=_analysis("network", network_type=network_type),
        visualization={"type": "network_graph", "title": "t", "rationale": "r"},
    )
    response = await _run(fixture, plan, "network_graph")

    assert response.ok, response.error
    # §10.6: a persistently sparse network downgrades to a bar_chart of node degrees -- still
    # a real, verified visualization, just not a network_graph; either way the verifier ran.
    assert response.meta.citation_check.passed is True


@respx.mock
@pytest.mark.parametrize("dimension", ["intervention", "country"])
async def test_verifier_is_fast_on_the_largest_fixtures_multi_valued_dims(dimension: str) -> None:
    """Item 5: nsclc (8,572 records) intervention/country with top-15 + 'Other' (= not-any of
    15 predicates). Target < 2 s (PLAN.md §11.6); the bound here is generous to stay unflaky."""
    plan = make_plan(
        search_terms=[],
        analysis=_analysis("count_by", group_by=dimension),
        visualization={"type": "bar_chart", "title": "t", "rationale": "r"},
    )

    response = await _run("nsclc", plan, "bar_chart")

    assert response.meta.citation_check.passed is True
    assert response.meta.citation_check.ms < VERIFIER_BUDGET_MS


PEMBRO_TERM = {
    "param": "query.intr",
    "value": "pembrolizumab",
    "source": "query_text",
    "rationale": "d",
}
MSD_TERM = {
    "param": "query.lead",
    "value": "Merck Sharp & Dohme",
    "source": "query_text",
    "rationale": "sponsor",
}
STRICT_CASES = [
    ("count_by", "bar_chart", {"group_by": "phase"}),
    ("count_by", "bar_chart", {"group_by": "country"}),
    ("count_by", "bar_chart", {"group_by": "intervention"}),
    ("time_trend", "time_series", {}),
    ("histogram", "histogram", {"measure_x": "enrollment"}),
    ("network", "network_graph", {"network_type": "sponsor_drug"}),
]


def _uses_text_matches(predicate: object) -> bool:
    return "text_matches" in str(predicate)


@respx.mock
@pytest.mark.parametrize(("kind", "viz", "overrides"), STRICT_CASES)
async def test_golden_strict_match_on_a_real_drug_search_passes_the_verifier(
    kind: str, viz: str, overrides: dict
) -> None:
    """Item 8: a real query.intr term, so strict match runs and `base_predicate` is non-trivial
    -- every cited trial must satisfy it, and the 325 off-topic trials must stay out."""
    plan = make_plan(
        search_terms=[PEMBRO_TERM],
        analysis=_analysis(kind, **overrides),
        visualization={"type": viz, "title": "t", "rationale": "r"},
    )

    response = await _run("pembrolizumab", plan, viz)

    assert response.ok, response.error
    [cohort] = response.meta.cohorts
    assert _uses_text_matches(cohort.base_predicate)
    assert cohort.records_matched == PEMBRO_KEPT
    assert response.meta.citation_check.passed is True


@respx.mock
async def test_golden_strict_match_on_a_lead_sponsor_search_passes_the_verifier() -> None:
    plan = make_plan(
        search_terms=[MSD_TERM],
        analysis=_analysis("count_by", group_by="phase"),
        visualization={"type": "bar_chart", "title": "t", "rationale": "r"},
    )

    response = await _run("pembrolizumab", plan, "bar_chart")

    assert response.ok, response.error
    [cohort] = response.meta.cohorts
    assert _uses_text_matches(cohort.base_predicate)
    assert 0 < cohort.records_matched < len(load_fixture("pembrolizumab"))
    assert response.meta.citation_check.passed is True
