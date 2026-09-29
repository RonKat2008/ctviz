"""request -> plan -> fetch per cohort -> normalize -> aggregate + cite -> spec (S4 slice).

Strict match, the probe, the judge and the independent verifier are wired in during S6-S7; for
now every fetched trial is passed straight through as `MatchedTrial(trial, ())` (no match
evidence yet, per the S4 ruling). Each pipeline step is a small, named helper so the top-level
function reads like the architecture diagram (PLAN.md §4.1). Response `meta` assembly lives in
`pipeline_meta.py` and the guard/viz-builder dispatch lives in `pipeline_analysis.py` (both split
out of this module to stay under the module-size budget); `run_pipeline` below still reads
top-to-bottom as the single source of truth for the pipeline's shape.
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import date

from ctviz.agent.overlay import apply_overlay
from ctviz.agent.planner import Planner
from ctviz.analysis.aggregate import MatchedTrial, count_by, time_trend
from ctviz.analysis.dimensions import DimensionHit, country_extractor
from ctviz.ctgov.client import CtGovClient
from ctviz.ctgov.compiler import RequestSpec, compile_plan
from ctviz.ctgov.normalize import Trial, normalize
from ctviz.errors import PlanInvalidError
from ctviz.pipeline_analysis import AGGREGATORS, AnalysisResult, aggregate_network, apply_viz_guards
from ctviz.pipeline_analysis import _build as build_visualization
from ctviz.pipeline_meta import _CohortFetch, _meta
from ctviz.schemas.enums import AnalysisKind, Dimension, OverallStatus
from ctviz.schemas.plan import QueryPlan
from ctviz.schemas.request import VisualizeRequest
from ctviz.schemas.response import ErrorInfo, VisualizeResponse

log = logging.getLogger(__name__)


def _out_of_scope(plan: QueryPlan) -> VisualizeResponse:
    """ok:false OUT_OF_SCOPE when the planner marks the question unanswerable (§12.2)."""
    reason = (
        plan.out_of_scope_reason or "This question is out of scope for ClinicalTrials.gov data."
    )
    details = (
        {"suggested_reframing": plan.suggested_reframing} if plan.suggested_reframing else None
    )
    return VisualizeResponse.failure(
        ErrorInfo(code="OUT_OF_SCOPE", message=reason, details=details)
    )


async def _fetch_one(spec: RequestSpec, client: CtGovClient, max_records: int) -> _CohortFetch:
    """Fetch and normalize one cohort. No strict match yet, so every trial is kept (S4 slice)."""
    fetch = await client.fetch_all(spec.params, max_records)
    trials = [MatchedTrial(normalize(record), ()) for record in fetch.records]
    return _CohortFetch(spec.cohort_label, spec.cohort_value, trials, fetch)


async def _fetch_cohorts(
    specs: list[RequestSpec], client: CtGovClient, max_records: int
) -> list[_CohortFetch]:
    """Fetch every cohort concurrently (one request stream per compared value, or just one)."""
    return list(await asyncio.gather(*(_fetch_one(spec, client, max_records) for spec in specs)))


def _country_extractor_for(plan: QueryPlan) -> Callable[[Trial], list[DimensionHit]] | None:
    """Item 7 (§11.5 "Country (recruiting)"): use the recruiting-site rule only when the plan
    filters overall_status to RECRUITING and nothing else; `None` lets `count_by` use the
    registered (any-site) extractor for every other dimension and every other status filter."""
    statuses = plan.filters.overall_statuses if plan.filters else None
    if statuses == [OverallStatus.RECRUITING]:
        return country_extractor(recruiting_only=True)
    return None


def _aggregate(
    plan: QueryPlan, trials: list[MatchedTrial], today: date, include_collaborators: bool = False
) -> AnalysisResult:
    """Dispatch to the aggregator the plan's analysis kind selects (§10.5); histogram and scatter
    dispatch through `pipeline_analysis.AGGREGATORS` to keep this function small."""
    analysis = plan.analysis
    if analysis is None:
        raise PlanInvalidError(["plan.analysis is required to aggregate a result"])
    if analysis.kind is AnalysisKind.TIME_TREND:
        return time_trend(trials, today.year)
    if analysis.kind is AnalysisKind.COUNT_BY:
        if analysis.group_by is None:
            raise PlanInvalidError(["count_by requires analysis.group_by"])
        extractor = _country_extractor_for(plan) if analysis.group_by is Dimension.COUNTRY else None
        return count_by(trials, analysis.group_by, top_n=analysis.top_n, extractor=extractor)
    if analysis.kind is AnalysisKind.NETWORK:
        return aggregate_network(trials, analysis, include_collaborators)
    if analysis.kind in AGGREGATORS:
        return AGGREGATORS[analysis.kind](trials, analysis)
    raise PlanInvalidError([f"analysis.kind={analysis.kind} is not yet supported (S5 slice)"])


def _title_subject(cohorts: list[_CohortFetch]) -> str | None:
    """Cohort label(s) joined for a title subject, or `None` when no cohort has a real identity."""
    labels = [c.label for c in cohorts if c.label and c.label != "All trials"]
    return " vs ".join(labels) if labels else None


def _deterministic_title(plan: QueryPlan, cohorts: list[_CohortFetch]) -> str | None:
    """Build a title from cohort identity + analysis when both are known (item 8 ruling below).

    Ruling: the planner is deliberately never shown structured field *values* (§8.2, §6.1) --
    only their names -- so its own title is often generic ("Trials for the specified drug over
    time"). Rather than leak values into the planner prompt, the title is built here in code from
    data we already have (the cohort label(s) the compiler derived from the search terms, plus
    the chosen analysis). The planner's title is kept only as a fallback for shapes with no
    single cohort identity (e.g. a network). Cost if wrong: a title that states a value the
    planner never reasoned about, decoupling `visualization.title` from `plan.visualization`
    (mitigated: it is always literally the cohort label the same request produced).
    """
    subject = _title_subject(cohorts)
    if subject is None or plan.analysis is None:
        return None
    if plan.analysis.kind is AnalysisKind.TIME_TREND:
        return f"{subject} trials by start year"
    if plan.analysis.kind is AnalysisKind.COUNT_BY and plan.analysis.group_by is not None:
        return f"{subject} trials by {plan.analysis.group_by.value.replace('_', ' ')}"
    return None


def _resolve_title(plan: QueryPlan, cohorts: list[_CohortFetch]) -> str:
    """The chart title: deterministic from cohort identity + analysis, else the planner's title."""
    fallback = plan.visualization.title if plan.visualization else ""
    return _deterministic_title(plan, cohorts) or fallback


async def run_pipeline(
    request: VisualizeRequest, *, planner: Planner, client: CtGovClient, today: date
) -> VisualizeResponse:
    """request -> plan -> fetch per cohort -> normalize -> aggregate + cite -> spec (§4.1)."""
    raw_plan = await asyncio.to_thread(planner.plan, request, today=today)
    plan, overrides = apply_overlay(raw_plan, request)
    if not plan.answerable:
        return _out_of_scope(plan)
    cohorts = await _fetch_cohorts(compile_plan(plan), client, request.options.max_records)
    results = {
        c.label: _aggregate(plan, c.trials, today, request.options.include_collaborators)
        for c in cohorts
    }
    plan, results, adjustments = apply_viz_guards(plan, results)
    visualization = build_visualization(plan, results, _resolve_title(plan, cohorts))
    meta = _meta(plan, overrides, cohorts, results, request, planner, adjustments)
    return VisualizeResponse(ok=True, visualization=visualization, meta=meta, error=None)
