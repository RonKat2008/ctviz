"""request -> plan -> fetch per cohort -> normalize -> aggregate + cite -> spec (S4 slice).

Strict match, the probe, the judge and the independent verifier are wired in during S6-S7; for
now every fetched trial is passed straight through as `MatchedTrial(trial, ())` (no match
evidence yet, per the S4 ruling). Each pipeline step is a small, named helper so the top-level
function reads like the architecture diagram (PLAN.md §4.1).
"""

import asyncio
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime

from ctviz.agent.overlay import FieldOverride, apply_overlay
from ctviz.agent.planner import Planner
from ctviz.analysis.aggregate import AggregateResult, MatchedTrial, count_by, time_trend
from ctviz.ctgov.client import CtGovClient, FetchResult
from ctviz.ctgov.compiler import RequestSpec, compile_plan
from ctviz.ctgov.normalize import normalize
from ctviz.errors import PlanInvalidError
from ctviz.schemas.enums import AnalysisKind, Dimension, VizType
from ctviz.schemas.plan import QueryPlan
from ctviz.schemas.request import VisualizeRequest
from ctviz.schemas.response import (
    CitationPolicy,
    CohortSummary,
    DataCoverage,
    ErrorInfo,
    ExcludedTrial,
    JudgeSummary,
    Meta,
    Provenance,
    Validation,
    VisualizeResponse,
)
from ctviz.schemas.viz import Visualization
from ctviz.viz.builder import build_bar_chart, build_grouped_bar, build_time_series

log = logging.getLogger(__name__)
SOURCE = "clinicaltrials.gov"
CODE_VERSION = "dev"  # placeholder until CI stamps a real git sha (out of S4 scope)
CITATION_URL_TEMPLATE = "https://clinicaltrials.gov/study/{nct_id}"


@dataclass(frozen=True)
class _CohortFetch:
    """One cohort's compiled identity, its fetched trials, and the raw `FetchResult` behind them."""

    label: str
    value: str | None
    trials: list[MatchedTrial]
    fetch: FetchResult


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


def _aggregate(plan: QueryPlan, trials: list[MatchedTrial], today: date) -> AggregateResult:
    """Dispatch to the aggregator the plan's analysis kind selects (S4: count_by, time_trend)."""
    analysis = plan.analysis
    if analysis is None:
        raise PlanInvalidError(["plan.analysis is required to aggregate a result"])
    if analysis.kind is AnalysisKind.TIME_TREND:
        return time_trend(trials, today.year)
    if analysis.kind is AnalysisKind.COUNT_BY:
        if analysis.group_by is None:
            raise PlanInvalidError(["count_by requires analysis.group_by"])
        return count_by(trials, analysis.group_by, top_n=analysis.top_n)
    raise PlanInvalidError([f"analysis.kind={analysis.kind} is not yet supported (S4 slice)"])


def _dimension_label(plan: QueryPlan) -> str:
    """The group-by dimension's display label, for the x-axis title of a categorical chart."""
    dimension = plan.analysis.group_by if plan.analysis else None
    return dimension.value if dimension else ""


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


def _build(plan: QueryPlan, results: dict[str, AggregateResult], title: str) -> Visualization:
    """Dispatch to the deterministic builder the plan's visualization type selects."""
    viz = plan.visualization
    if viz is None:
        raise PlanInvalidError(["plan.visualization is required to build a chart"])
    if viz.type is VizType.TIME_SERIES:
        return build_time_series(title, results)
    if viz.type is VizType.BAR_CHART:
        if len(results) != 1:
            raise PlanInvalidError(
                [
                    f"bar_chart requires exactly one cohort, got {len(results)} (use "
                    "grouped_bar_chart for a comparison)"
                ]
            )
        [result] = results.values()
        return build_bar_chart(title, _dimension_label(plan), result)
    if viz.type is VizType.GROUPED_BAR_CHART:
        return build_grouped_bar(title, _dimension_label(plan), results)
    raise PlanInvalidError([f"visualization.type={viz.type} is not yet supported (S4 slice)"])


def _records_plotted(result: AggregateResult) -> int:
    """Distinct trials actually plotted, deduped by nct_id (one trial can join >1 bucket)."""
    return len({citation.nct_id for bucket in result.buckets for citation in bucket.citations})


def _cohort_summary(
    cohort: _CohortFetch, plan: QueryPlan, result: AggregateResult
) -> CohortSummary:
    """One cohort's identity, counts, and the predicate (search terms + filters) that define it."""
    predicate = {
        "search_terms": [term.model_dump(mode="json") for term in plan.search_terms],
        "filters": plan.filters.model_dump(mode="json") if plan.filters else None,
    }
    return CohortSummary(
        label=cohort.label,
        value=cohort.value or cohort.label,
        api_total_count=cohort.fetch.api_total_count,
        records_matched=len(cohort.trials),
        records_plotted=_records_plotted(result),
        base_predicate=predicate,
    )


def _data_coverage(cohort: _CohortFetch, result: AggregateResult) -> DataCoverage:
    """Single-cohort coverage summary (only meaningful without a comparison, per §12.6)."""
    excluded_trials = [
        ExcludedTrial(nct_id=nct_id, stage="analysis", reason=reason)
        for nct_id, reason in result.excluded.items()
    ]
    reasons: dict[str, int] = {}
    for reason in result.excluded.values():
        reasons[reason] = reasons.get(reason, 0) + 1
    return DataCoverage(
        api_total_count=cohort.fetch.api_total_count,
        records_fetched=len(cohort.fetch.records),
        truncated=cohort.fetch.truncated,
        truncation_rule=cohort.fetch.truncation_rule,
        records_matched=len(cohort.trials),
        records_plotted=_records_plotted(result),
        excluded={"match": {}, "analysis": reasons},
        excluded_trials=excluded_trials,
    )


def _override_note(override: FieldOverride) -> str:
    """One human-readable `meta.assumptions` line for a structured-field override (§6.1)."""
    field, old, new = override.field, override.text_value, override.applied_value
    return f"{field}: {old!r} -> {new!r} (structured field)"


def _override_notes(overrides: list[FieldOverride]) -> list[str]:
    """Human-readable notes for `meta.assumptions`, one per structured-field override (§6.1)."""
    return [_override_note(o) for o in overrides]


def _grouping(plan: QueryPlan) -> dict[str, str | None]:
    """`meta.grouping`: the dimension, time granularity and phase mode behind the aggregation."""
    analysis = plan.analysis
    if analysis is None:
        return {"dimension": None, "time_granularity": None, "phase_mode": None}
    dimension = analysis.group_by or (
        Dimension.START_YEAR if analysis.kind is AnalysisKind.TIME_TREND else None
    )
    return {
        "dimension": dimension.value if dimension else None,
        "time_granularity": analysis.granularity,
        "phase_mode": analysis.phase_mode,
    }


def _requested_filters(request: VisualizeRequest) -> dict[str, object]:
    """`meta.filters`: the structured fields the caller actually supplied (never the LLM's)."""
    names = (
        "drug_name",
        "condition",
        "sponsor",
        "trial_phase",
        "status",
        "country",
        "start_year",
        "end_year",
        "study_type",
        "nct_ids",
    )
    return {name: getattr(request, name) for name in names if getattr(request, name) is not None}


def _validation_block() -> Validation:
    """S4's validation block: no probe/judge/revise loop yet, but the shape matches §12.6."""
    judge = JudgeSummary(status="skipped", model=None, same_family=False, issues=[])
    return Validation(executed_attempt=1, probe=[], judge=judge, trace=[])


def _provenance(cohorts: list[_CohortFetch], planner: Planner) -> Provenance:
    """Exactly what was fetched, and with which model/code version, for reproducibility (§12.6)."""
    api_requests = [asdict(log_entry) for c in cohorts for log_entry in c.fetch.requests]
    return Provenance(
        api_version="unknown",
        data_timestamp=datetime.now(UTC).isoformat(),
        api_requests=api_requests,
        planner_model=planner.model_name,
        code_version=CODE_VERSION,
    )


def _meta(
    plan: QueryPlan,
    overrides: list[FieldOverride],
    cohorts: list[_CohortFetch],
    results: dict[str, AggregateResult],
    request: VisualizeRequest,
    planner: Planner,
) -> Meta:
    """Assemble the response's `meta`; every block populated by S4 uses a typed shape."""
    cohort_summaries = [_cohort_summary(c, plan, results[c.label]) for c in cohorts]
    single = cohorts[0] if len(cohorts) == 1 else None
    data_coverage = _data_coverage(single, results[single.label]) if single else None
    validation = _validation_block()
    provenance = _provenance(cohorts, planner)
    return Meta(
        source=SOURCE,
        query_interpretation=plan.interpretation,
        plan=plan.model_dump(mode="json"),
        filters=_requested_filters(request),
        grouping=_grouping(plan),
        sort={},
        units={"trial_count": "trials"},
        assumptions=[*plan.assumptions, *_override_notes(overrides)],
        warnings=[],
        adjustments=[],
        entity_resolution={},
        data_coverage=data_coverage,
        cohorts=cohort_summaries,
        overlap=None,
        network_summary=None,
        citation_policy=CitationPolicy(
            mode=request.options.citations,
            pointer_format="RFC6901",
            url_template=CITATION_URL_TEMPLATE,
        ),
        citation_check=None,
        validation=validation,
        provenance=provenance,
        timing_ms={},
    )


async def run_pipeline(
    request: VisualizeRequest, *, planner: Planner, client: CtGovClient, today: date
) -> VisualizeResponse:
    """request -> plan -> fetch per cohort -> normalize -> aggregate + cite -> spec (S4 slice)."""
    raw_plan = await asyncio.to_thread(planner.plan, request, today=today)
    plan, overrides = apply_overlay(raw_plan, request)
    if not plan.answerable:
        return _out_of_scope(plan)
    cohorts = await _fetch_cohorts(compile_plan(plan), client, request.options.max_records)
    results = {c.label: _aggregate(plan, c.trials, today) for c in cohorts}
    visualization = _build(plan, results, _resolve_title(plan, cohorts))
    meta = _meta(plan, overrides, cohorts, results, request, planner)
    return VisualizeResponse(ok=True, visualization=visualization, meta=meta, error=None)
