"""S5 vertical slice: pipeline dispatch for histogram, scatter and network_graph (§4.1, §10.5)."""

from datetime import date

import httpx
import pytest
import respx

from ctviz.agent.planner import Planner
from ctviz.config import CTGOV_BASE_URL
from ctviz.ctgov.client import CtGovClient
from ctviz.errors import PlanInvalidError
from ctviz.pipeline import run_pipeline
from ctviz.schemas.request import VisualizeRequest
from tests.factories import make_plan, make_study
from tests.fixtures.load import load_fixture
from tests.unit.agent.test_planner import FakeBackend

HISTOGRAM_ANALYSIS = {
    "kind": "histogram",
    "group_by": None,
    "series_by": None,
    "phase_mode": None,
    "time_field": None,
    "granularity": None,
    "measure_x": "enrollment",
    "measure_y": None,
    "color_by": None,
    "network_type": None,
    "top_n": None,
}
HISTOGRAM_VIZ = {"type": "histogram", "title": "Enrollment distribution", "rationale": "spread"}
SCATTER_ANALYSIS = {
    "kind": "scatter",
    "group_by": None,
    "series_by": None,
    "phase_mode": None,
    "time_field": None,
    "granularity": None,
    "measure_x": "duration_months",
    "measure_y": "enrollment",
    "color_by": None,
    "network_type": None,
    "top_n": None,
}
SCATTER_VIZ = {"type": "scatter_plot", "title": "Enrollment vs duration", "rationale": "relate"}
NETWORK_ANALYSIS = {
    "kind": "network",
    "group_by": None,
    "series_by": None,
    "phase_mode": None,
    "time_field": None,
    "granularity": None,
    "measure_x": None,
    "measure_y": None,
    "color_by": None,
    "network_type": "sponsor_drug",
    "top_n": None,
}
NETWORK_VIZ = {"type": "network_graph", "title": "Sponsors and drugs", "rationale": "network"}


def _mock_records(records: list[dict], total: int) -> None:
    respx.get(f"{CTGOV_BASE_URL}/studies").mock(
        side_effect=[
            httpx.Response(200, json={"totalCount": total, "studies": []}),
            httpx.Response(200, json={"totalCount": total, "studies": records}),
        ]
    )


@respx.mock
async def test_pipeline_produces_a_cited_histogram_for_psoriasis_enrollment() -> None:
    records = load_fixture("psoriasis_p2")
    _mock_records(records, len(records))
    # S6: the plan's default query.intr=pembrolizumab term would now be strict-matched against
    # this psoriasis fixture; drop it so the request's own condition="Psoriasis" (lenient, §11.3)
    # is the only search term, preserving this test's pre-S6 golden counts.
    plan = make_plan(search_terms=[], analysis=HISTOGRAM_ANALYSIS, visualization=HISTOGRAM_VIZ)
    planner = Planner(FakeBackend(plan))
    request = VisualizeRequest(query="Enrollment distribution?", condition="Psoriasis")

    async with CtGovClient() as client:
        response = await run_pipeline(
            request, planner=planner, client=client, today=date(2026, 9, 28)
        )

    assert response.ok
    assert response.visualization is not None
    assert response.visualization.type == "histogram"
    total_plotted = sum(row["trial_count"] for row in response.visualization.data)
    # PLAN.md §14 says "10 withdrawn zeros excluded" (measured 2026-09-28); the fixture recorded
    # for this exercise actually carries 14 withdrawn zero-enrollment trials plus 2 with no
    # enrollment count at all (16 excluded total, 496 plotted) -- verified directly against the
    # fixture, not assumed. See the S5 report's golden-number table for the discrepancy.
    assert total_plotted == len(records) - 16
    for row in response.visualization.data:
        if row["trial_count"] > 0:
            assert len(row["citations"]) == row["trial_count"]


@respx.mock
async def test_pipeline_produces_a_cited_scatter_for_crohns_completed_p3() -> None:
    records = load_fixture("crohns_p3_completed")
    _mock_records(records, len(records))
    # S6: same reasoning as the histogram test above -- drop the plan's default drug term.
    plan = make_plan(search_terms=[], analysis=SCATTER_ANALYSIS, visualization=SCATTER_VIZ)
    planner = Planner(FakeBackend(plan))
    request = VisualizeRequest(
        query="Enrollment vs duration?", condition="Crohn's Disease", trial_phase="PHASE3"
    )

    async with CtGovClient() as client:
        response = await run_pipeline(
            request, planner=planner, client=client, today=date(2026, 9, 28)
        )

    assert response.ok
    assert response.visualization is not None
    assert response.visualization.type == "scatter_plot"
    # PLAN.md §14 says "127 (120 usable)" (measured 2026-09-28). Against this fixture, applying
    # every documented exclusion rule (§10.3) gives 119: the same 6 missing-date and 1
    # missing-enrollment trials, PLUS NCT00004941, whose month-only start/completion dates fall
    # in the same month (1996-07), giving a 0-month duration excluded as "implausible_duration"
    # -- a rule the golden number's original measurement may predate. See the S5 report.
    assert len(response.visualization.data) == 119
    for row in response.visualization.data:
        assert len(row["citations"]) >= 1
    # Item 2 (§12.4): scatter rows/encoding are keyed by measure name, not generic x/y.
    assert response.visualization.encoding["x"].field == "duration_months"
    assert response.visualization.encoding["y"].field == "enrollment"
    assert response.visualization.encoding["x"].unit == "months"
    assert response.visualization.encoding["y"].unit == "participants"
    for row in response.visualization.data:
        assert "duration_months" in row and "enrollment" in row


@respx.mock
async def test_pipeline_produces_a_cited_network_for_glioblastoma_sponsor_drug() -> None:
    records = load_fixture("glioblastoma")
    _mock_records(records, len(records))
    plan = make_plan(analysis=NETWORK_ANALYSIS, visualization=NETWORK_VIZ)
    planner = Planner(FakeBackend(plan))
    request = VisualizeRequest(query="Sponsor-drug network?", condition="Glioblastoma")

    async with CtGovClient() as client:
        response = await run_pipeline(
            request, planner=planner, client=client, today=date(2026, 9, 28)
        )

    assert response.ok
    assert response.visualization is not None
    assert response.visualization.type == "network_graph"
    assert len(response.visualization.data.nodes) <= 50
    assert len(response.visualization.data.edges) <= 150
    assert response.meta is not None
    assert response.meta.network_summary is not None
    assert response.meta.network_summary["nodes_after_pruning"] == len(
        response.visualization.data.nodes
    )


@respx.mock
async def test_pipeline_warns_about_ambiguous_merck_sponsor_census() -> None:
    records = [
        make_study("NCT00000001", sponsor="Merck Sharp & Dohme LLC"),
        make_study("NCT00000002", sponsor="Merck KGaA, Darmstadt, Germany"),
    ]
    _mock_records(records, len(records))
    plan = make_plan(
        search_terms=[
            {
                "param": "query.lead",
                "value": "Merck",
                "source": "query_text",
                "rationale": "sponsor",
            }
        ]
    )
    planner = Planner(FakeBackend(plan))
    request = VisualizeRequest(query="Merck trials by phase?", sponsor="Merck")

    async with CtGovClient() as client:
        response = await run_pipeline(
            request, planner=planner, client=client, today=date(2026, 9, 28)
        )

    assert response.ok
    assert response.meta is not None
    assert any("Merck Sharp & Dohme LLC" in w for w in response.meta.warnings)
    assert any("Merck KGaA, Darmstadt, Germany" in w for w in response.meta.warnings)


@respx.mock
async def test_pipeline_uses_recruiting_rule_when_status_filtered_to_recruiting() -> None:
    """HIGH item 7 (§11.5 "Country (recruiting)"): when the plan filters overall_status to
    RECRUITING, country counts must use the recruiting-site rule, not any-site -- through the
    full pipeline, reproducing the §14 golden number US = 157 on ms_recruiting."""
    records = load_fixture("ms_recruiting")
    _mock_records(records, len(records))
    # S6: same reasoning as the histogram test above -- drop the plan's default drug term.
    plan = make_plan(
        search_terms=[],
        filters={
            "phases": None,
            "overall_statuses": ["RECRUITING"],
            "study_types": None,
            "intervention_types": None,
            "lead_sponsor_classes": None,
            "countries": None,
            "start_year_min": None,
            "start_year_max": None,
            "nct_ids": None,
        },
        analysis={
            "kind": "count_by",
            "group_by": "country",
            "series_by": None,
            "phase_mode": None,
            "time_field": None,
            "granularity": None,
            "measure_x": None,
            "measure_y": None,
            "color_by": None,
            "network_type": None,
            "top_n": None,
        },
        visualization={"type": "bar_chart", "title": "Recruiting countries", "rationale": "r"},
    )
    planner = Planner(FakeBackend(plan))
    request = VisualizeRequest(query="Recruiting countries?", condition="Multiple Sclerosis")

    async with CtGovClient() as client:
        response = await run_pipeline(
            request, planner=planner, client=client, today=date(2026, 9, 28)
        )

    assert response.ok
    assert response.visualization is not None
    us_rows = [row for row in response.visualization.data if row["category"] == "United States"]
    assert len(us_rows) == 1
    assert us_rows[0]["trial_count"] == 157


@respx.mock
async def test_pipeline_downgrades_a_sparse_network_to_a_cited_bar_chart_of_node_degrees() -> None:
    """Q-C item 3: a single-trial cohort produces at most 1 sponsor-drug edge -- below the
    §10.6 floor of 2 even after the min_weight=1 retry -- so the pipeline must deliver a
    bar_chart of node degrees instead, with the note recorded in `meta.adjustments`."""
    records = [
        make_study(
            "NCT00000001",
            sponsor="Merck",
            interventions=[
                {"type": "DRUG", "name": "Pembrolizumab"},
            ],
        )
    ]
    _mock_records(records, len(records))
    plan = make_plan(analysis=NETWORK_ANALYSIS, visualization=NETWORK_VIZ)
    planner = Planner(FakeBackend(plan))
    request = VisualizeRequest(query="Sponsor-drug network?", condition="Glioblastoma")

    async with CtGovClient() as client:
        response = await run_pipeline(
            request, planner=planner, client=client, today=date(2026, 9, 28)
        )

    assert response.ok
    assert response.visualization is not None
    assert response.visualization.type == "bar_chart"
    assert any("network_graph" in note for note in response.meta.adjustments)
    assert len(response.visualization.data) == 2  # sponsor:merck, drug:pembrolizumab
    for row in response.visualization.data:
        assert len(row["citations"]) == row["trial_count"] == 1


@respx.mock
async def test_pipeline_downgrades_a_single_category_bar_chart_to_a_cited_metric() -> None:
    """Q-C item 3: METRIC branch of `_build`, reached through the full pipeline + guard wiring."""
    records = [
        make_study(f"NCT0000000{i}", phases=["PHASE3"], study_type="INTERVENTIONAL")
        for i in range(3)
    ]
    _mock_records(records, len(records))
    # S6: same reasoning as the histogram test above -- drop the plan's default drug term.
    plan = make_plan(search_terms=[])  # count_by(phase) -> bar_chart
    planner = Planner(FakeBackend(plan))
    request = VisualizeRequest(query="Trials by phase?", condition="Glioblastoma")

    async with CtGovClient() as client:
        response = await run_pipeline(
            request, planner=planner, client=client, today=date(2026, 9, 28)
        )

    assert response.ok
    assert response.visualization is not None
    assert response.visualization.type == "metric"
    assert any("-> metric" in note for note in response.meta.adjustments)
    [row] = response.visualization.data
    assert row["trial_count"] == len(row["citations"]) == 3


@respx.mock
async def test_histogram_without_measure_x_raises_plan_invalid_not_attribute_error() -> None:
    _mock_records([], 0)
    plan = make_plan(
        analysis={**HISTOGRAM_ANALYSIS, "measure_x": None}, visualization=HISTOGRAM_VIZ
    )
    planner = Planner(FakeBackend(plan))
    request = VisualizeRequest(query="Enrollment distribution?", condition="Psoriasis")

    async with CtGovClient() as client:
        with pytest.raises(PlanInvalidError, match=r"histogram requires analysis\.measure_x"):
            await run_pipeline(request, planner=planner, client=client, today=date(2026, 9, 28))


@respx.mock
async def test_scatter_without_measure_y_raises_plan_invalid_not_attribute_error() -> None:
    _mock_records([], 0)
    plan = make_plan(analysis={**SCATTER_ANALYSIS, "measure_y": None}, visualization=SCATTER_VIZ)
    planner = Planner(FakeBackend(plan))
    request = VisualizeRequest(query="Enrollment vs duration?", condition="Crohn's Disease")

    async with CtGovClient() as client:
        with pytest.raises(PlanInvalidError, match="scatter requires"):
            await run_pipeline(request, planner=planner, client=client, today=date(2026, 9, 28))


@respx.mock
async def test_network_without_network_type_raises_plan_invalid_not_attribute_error() -> None:
    _mock_records([], 0)
    plan = make_plan(analysis={**NETWORK_ANALYSIS, "network_type": None}, visualization=NETWORK_VIZ)
    planner = Planner(FakeBackend(plan))
    request = VisualizeRequest(query="Sponsor-drug network?", condition="Glioblastoma")

    async with CtGovClient() as client:
        with pytest.raises(PlanInvalidError, match=r"network requires analysis\.network_type"):
            await run_pipeline(request, planner=planner, client=client, today=date(2026, 9, 28))
