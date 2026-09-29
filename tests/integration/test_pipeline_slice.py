"""S4 vertical slice: fake planner + respx-replayed fixture -> a cited time_series (§4.1)."""

import threading
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


def _mock_pembrolizumab_studies() -> None:
    """Replay the recorded fixture behind a probe call, then a single full page."""
    records = load_fixture("pembrolizumab")
    respx.get(f"{CTGOV_BASE_URL}/studies").mock(
        side_effect=[
            httpx.Response(200, json={"totalCount": 2960, "studies": []}),
            httpx.Response(200, json={"totalCount": 2960, "studies": records}),
        ]
    )


@respx.mock
async def test_pipeline_produces_a_cited_time_series_for_pembrolizumab() -> None:
    _mock_pembrolizumab_studies()
    plan = make_plan(
        interpretation="Pembrolizumab trials by start year.",
        analysis=TIME_TREND_ANALYSIS,
        visualization=TIME_SERIES_VIZ,
    )
    planner = Planner(FakeBackend(plan))
    request = VisualizeRequest(
        query="How has the number of trials for this drug changed over time?",
        drug_name="Pembrolizumab",
    )

    async with CtGovClient() as client:
        response = await run_pipeline(
            request, planner=planner, client=client, today=date(2026, 9, 28)
        )

    assert response.ok
    assert response.visualization is not None
    assert response.visualization.type == "time_series"
    for row in response.visualization.data:
        if row["trial_count"] > 0:
            assert len(row["citations"]) == row["trial_count"]
    assert response.meta is not None
    assert response.meta.cohorts[0].api_total_count == 2960


class _ThreadRecordingBackend:
    """A `PlannerBackend` that records which OS thread ran `.complete()` (item 2)."""

    def __init__(self, plan: object) -> None:
        self.plan = plan
        self.thread_name: str | None = None

    def complete(self, system: str, user: str) -> object:
        self.thread_name = threading.current_thread().name
        return self.plan


def _mock_empty_studies(total: int = 0) -> None:
    """Replay a probe + a single (possibly empty) page, for tests that don't care about data."""
    respx.get(f"{CTGOV_BASE_URL}/studies").mock(
        return_value=httpx.Response(200, json={"totalCount": total, "studies": []})
    )


@respx.mock
async def test_pipeline_runs_planner_off_the_event_loop() -> None:
    """Item 2: `planner.plan()` (sync) must run via `asyncio.to_thread`, off the event loop."""
    _mock_empty_studies()
    backend = _ThreadRecordingBackend(make_plan())
    planner = Planner(backend)
    request = VisualizeRequest(query="Trials by phase?", drug_name="Pembrolizumab")

    async with CtGovClient() as client:
        await run_pipeline(request, planner=planner, client=client, today=date(2026, 9, 28))

    assert backend.thread_name is not None
    assert backend.thread_name != threading.main_thread().name


@respx.mock
async def test_out_of_scope_plan_never_fetches_from_ctgov() -> None:
    """Item 5: an out-of-scope plan short-circuits before any ClinicalTrials.gov request.

    No route is mocked at all: respx raises if the pipeline makes any HTTP call here.
    """
    plan = make_plan(
        answerable=False,
        out_of_scope_reason="Efficacy ranking is out of scope for this registry.",
        search_terms=[],
        filters=None,
        comparison=None,
        analysis=None,
        visualization=None,
    )
    planner = Planner(FakeBackend(plan))
    request = VisualizeRequest(query="Which drug works better for lung cancer?")

    async with CtGovClient() as client:
        response = await run_pipeline(
            request, planner=planner, client=client, today=date(2026, 9, 28)
        )

    assert response.ok is False
    assert response.error is not None and response.error.code == "OUT_OF_SCOPE"


@respx.mock
async def test_cohort_api_total_count_comes_from_the_probe_not_records_fetched() -> None:
    """Item 5: `cohorts[0].api_total_count` is the probe's total, never `len(records_fetched)`."""
    records = [make_study(f"NCT0000000{i}") for i in range(3)]
    respx.get(f"{CTGOV_BASE_URL}/studies").mock(
        side_effect=[
            httpx.Response(200, json={"totalCount": 5000, "studies": []}),
            httpx.Response(200, json={"totalCount": 5000, "studies": records}),
        ]
    )
    plan = make_plan()
    planner = Planner(FakeBackend(plan))
    request = VisualizeRequest(
        query="Trials by phase?", drug_name="Pembrolizumab", options={"max_records": 100}
    )

    async with CtGovClient() as client:
        response = await run_pipeline(
            request, planner=planner, client=client, today=date(2026, 9, 28)
        )

    assert response.meta is not None
    assert response.meta.cohorts[0].api_total_count == 5000
    assert response.meta.cohorts[0].api_total_count != len(records)


@respx.mock
async def test_excluded_trials_are_reported_for_a_missing_start_date() -> None:
    """Item 5: a trial with no start date is excluded from a time_trend, with the reason kept.

    S6: the request's `drug_name="Pembrolizumab"` is now strict-matched, so both records need a
    matching intervention or they'd be dropped at the match stage instead (a different S6 rule
    entirely) before this test's own missing-start-date exclusion is even reached.
    """
    pembro_intervention = [{"type": "DRUG", "name": "Pembrolizumab"}]
    records = [
        make_study("NCT00000001", start="2020-06-01", interventions=pembro_intervention),
        make_study("NCT00000002", start=None, interventions=pembro_intervention),
    ]
    respx.get(f"{CTGOV_BASE_URL}/studies").mock(
        side_effect=[
            httpx.Response(200, json={"totalCount": 2, "studies": []}),
            httpx.Response(200, json={"totalCount": 2, "studies": records}),
        ]
    )
    plan = make_plan(
        interpretation="Pembrolizumab trials by start year.",
        analysis=TIME_TREND_ANALYSIS,
        visualization=TIME_SERIES_VIZ,
    )
    planner = Planner(FakeBackend(plan))
    request = VisualizeRequest(query="Trend over time?", drug_name="Pembrolizumab")

    async with CtGovClient() as client:
        response = await run_pipeline(
            request, planner=planner, client=client, today=date(2026, 9, 28)
        )

    assert response.meta is not None
    coverage = response.meta.data_coverage
    assert coverage is not None
    [excluded] = coverage.excluded_trials
    assert (excluded.nct_id, excluded.reason) == ("NCT00000002", "missing_start_date")


@respx.mock
async def test_today_drives_partial_period_and_projected_flags() -> None:
    """Item 5: the pipeline's `today` (not `date.today()`) drives partial/projected year flags.

    S6: `drug_name="Pembrolizumab"` is now strict-matched (see the test above for why every
    record needs a matching intervention).
    """
    pembro_intervention = [{"type": "DRUG", "name": "Pembrolizumab"}]
    records = [
        make_study("NCT00000001", start="2020-06-01", interventions=pembro_intervention),
        make_study("NCT00000002", start="2021-03-01", interventions=pembro_intervention),
        make_study("NCT00000003", start="2022-01-01", interventions=pembro_intervention),
    ]
    respx.get(f"{CTGOV_BASE_URL}/studies").mock(
        side_effect=[
            httpx.Response(200, json={"totalCount": 3, "studies": []}),
            httpx.Response(200, json={"totalCount": 3, "studies": records}),
        ]
    )
    plan = make_plan(analysis=TIME_TREND_ANALYSIS, visualization=TIME_SERIES_VIZ)
    planner = Planner(FakeBackend(plan))
    request = VisualizeRequest(query="Trend over time?", drug_name="Pembrolizumab")

    async with CtGovClient() as client:
        response = await run_pipeline(
            request, planner=planner, client=client, today=date(2021, 6, 1)
        )

    assert response.visualization is not None
    flags_by_year = {row["year"]: row["flags"] for row in response.visualization.data}
    assert flags_by_year["2020"] == []
    assert flags_by_year["2021"] == ["partial_period"]
    assert flags_by_year["2022"] == ["projected"]


@respx.mock
async def test_field_overrides_appear_in_meta_assumptions() -> None:
    """Item 5: a structured-field override (§6.1) is disclosed in `meta.assumptions`."""
    _mock_empty_studies()
    plan = make_plan(
        filters={
            "phases": None,
            "overall_statuses": None,
            "study_types": None,
            "intervention_types": None,
            "lead_sponsor_classes": None,
            "countries": None,
            "start_year_min": 2018,
            "start_year_max": None,
            "nct_ids": None,
        }
    )
    planner = Planner(FakeBackend(plan))
    request = VisualizeRequest(
        query="Trials since 2018?", drug_name="Pembrolizumab", start_year=2015
    )

    async with CtGovClient() as client:
        response = await run_pipeline(
            request, planner=planner, client=client, today=date(2026, 9, 28)
        )

    assert response.meta is not None
    assert any("2018" in note and "2015" in note for note in response.meta.assumptions)


@respx.mock
async def test_citation_policy_mode_reflects_request_options() -> None:
    """Item 5: `meta.citation_policy.mode` follows `request.options.citations`, not a default."""
    _mock_empty_studies()
    planner = Planner(FakeBackend(make_plan()))
    request = VisualizeRequest(
        query="Trials by phase?", drug_name="Pembrolizumab", options={"citations": "sample"}
    )

    async with CtGovClient() as client:
        response = await run_pipeline(
            request, planner=planner, client=client, today=date(2026, 9, 28)
        )

    assert response.meta is not None
    assert response.meta.citation_policy.mode == "sample"


@respx.mock
async def test_deterministic_title_replaces_a_generic_llm_title_for_a_single_cohort() -> None:
    """Item 8: the title is built from the cohort's real identity, not the planner's guess."""
    _mock_empty_studies()
    plan = make_plan(
        analysis=TIME_TREND_ANALYSIS,
        visualization={
            "type": "time_series",
            "title": "Trials for the specified drug over time",
            "rationale": "trend over time",
        },
    )
    planner = Planner(FakeBackend(plan))
    request = VisualizeRequest(query="Trend over time?", drug_name="Pembrolizumab")

    async with CtGovClient() as client:
        response = await run_pipeline(
            request, planner=planner, client=client, today=date(2026, 9, 28)
        )

    assert response.visualization is not None
    assert response.visualization.title == "Pembrolizumab trials by start year"


@respx.mock
async def test_deterministic_title_for_a_comparison_names_both_cohorts() -> None:
    """Item 8: a comparison's title names both cohorts, e.g. 'A vs B trials by phase'."""
    _mock_empty_studies()
    plan = make_plan(
        search_terms=[],
        comparison={"vary_param": "query.intr", "values": ["Pembrolizumab", "Nivolumab"]},
        visualization={
            "type": "grouped_bar_chart",
            "title": "Comparison of two drugs",
            "rationale": "compare cohorts",
        },
    )
    planner = Planner(FakeBackend(plan))
    request = VisualizeRequest(query="Compare pembrolizumab vs nivolumab by phase")

    async with CtGovClient() as client:
        response = await run_pipeline(
            request, planner=planner, client=client, today=date(2026, 9, 28)
        )

    assert response.visualization is not None
    assert response.visualization.title == "Pembrolizumab vs Nivolumab trials by phase"


@respx.mock
async def test_llm_title_is_kept_when_no_cohort_has_a_real_identity() -> None:
    """Item 8: with no search terms (cohort label 'All trials'), the planner's title is kept."""
    _mock_empty_studies()
    plan = make_plan(
        search_terms=[],
        visualization={
            "type": "bar_chart",
            "title": "Total trial landscape overview",
            "rationale": "overview",
        },
    )
    planner = Planner(FakeBackend(plan))
    request = VisualizeRequest(query="Trials by phase overall?")

    async with CtGovClient() as client:
        response = await run_pipeline(
            request, planner=planner, client=client, today=date(2026, 9, 28)
        )

    assert response.visualization is not None
    assert response.visualization.title == "Total trial landscape overview"


@respx.mock
async def test_bar_chart_with_multiple_cohorts_raises_plan_invalid_not_value_error() -> None:
    """Item 9: a bar_chart plan that ends up with >1 cohort is a readable PlanInvalidError,
    never the raw `ValueError` from unpacking `[result] = results.values()`."""
    _mock_empty_studies()
    plan = make_plan(
        search_terms=[],
        comparison={"vary_param": "query.intr", "values": ["Pembrolizumab", "Nivolumab"]},
    )
    planner = Planner(FakeBackend(plan))
    request = VisualizeRequest(query="Compare pembrolizumab vs nivolumab by phase")

    async with CtGovClient() as client:
        with pytest.raises(PlanInvalidError, match="bar_chart requires exactly one cohort"):
            await run_pipeline(request, planner=planner, client=client, today=date(2026, 9, 28))
