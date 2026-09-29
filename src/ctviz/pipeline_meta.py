"""S5 response `meta` assembly, split out of `pipeline.py` (module budget).

`_CohortFetch` (one cohort's compiled identity, fetched trials, and raw fetch result) lives here
because every meta helper below is built from a `list[_CohortFetch]`; `pipeline.py` imports it
back for `_fetch_one`/`_fetch_cohorts`. `_meta` is the single entry point `run_pipeline` calls.
"""

import logging
import os
import subprocess
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path

from ctviz.agent.orchestrator import PlanningOutcome
from ctviz.agent.overlay import FieldOverride
from ctviz.agent.planner import Planner
from ctviz.analysis.aggregate import MatchedTrial
from ctviz.analysis.entities import sponsor_ambiguity_warning, sponsor_census
from ctviz.ctgov.client import CtGovClient, FetchResult
from ctviz.errors import UpstreamError
from ctviz.pipeline_analysis import AnalysisResult, excluded_of, plotted_ids
from ctviz.pipeline_analysis import network_summary as network_summary_of
from ctviz.schemas.citations import Predicate
from ctviz.schemas.enums import AnalysisKind, Dimension, SearchParam
from ctviz.schemas.plan import QueryPlan
from ctviz.schemas.request import VisualizeRequest
from ctviz.schemas.response import (
    CitationPolicy,
    CohortSummary,
    DataCoverage,
    ExcludedTrial,
    JudgeSummary,
    Meta,
    Provenance,
    Validation,
)

log = logging.getLogger(__name__)

SOURCE = "clinicaltrials.gov"
UNKNOWN = "unknown"  # D3: shared fallback for a git sha, api_version or data_timestamp we
# genuinely could not learn -- never a crash, always disclosed as exactly this string.
CITATION_URL_TEMPLATE = "https://clinicaltrials.gov/study/{nct_id}"
_GIT_SHA_TIMEOUT_S = 2.0
CODE_VERSION_ENV = "CTVIZ_CODE_VERSION"  # stamped into the zip deliverable (no .git there)


def _git_short_sha() -> str:
    """The checkout's short git SHA, or UNKNOWN when this isn't a git checkout (D3).

    Never raises: any failure (git missing, not a repo, timeout) degrades to UNKNOWN so a
    provenance lookup can never crash a request.
    """
    repo_dir = Path(__file__).resolve().parent
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=_GIT_SHA_TIMEOUT_S,
            cwd=repo_dir,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("Could not resolve git SHA for code_version: %s", type(exc).__name__)
        return UNKNOWN
    sha = result.stdout.strip()
    return sha or UNKNOWN


@lru_cache(maxsize=1)
def resolve_code_version() -> str:
    """The process-wide `code_version` (D3), resolved once: `CTVIZ_CODE_VERSION` if set (the zip
    deliverable has no .git), else the git short SHA, else UNKNOWN. The app lifespan calls this
    at startup so no request ever pays for the git subprocess."""
    return os.environ.get(CODE_VERSION_ENV, "").strip() or _git_short_sha()


@dataclass(frozen=True)
class _CohortFetch:
    """One cohort's compiled identity, its KEPT (post strict-match) trials, the raw `FetchResult`
    behind them, every trial the match/filter re-check dropped (§11.3), and the cohort's real,
    evaluable membership rule (§11.4 item 3 -- what the S4-era placeholder predicate became)."""

    label: str
    value: str | None
    trials: list[MatchedTrial]
    fetch: FetchResult
    match_excluded: list[ExcludedTrial] = field(default_factory=list)
    base_predicate: Predicate = field(default_factory=dict)
    aliases: tuple[str, ...] = ()


def _records_plotted(result: AnalysisResult) -> int:
    """Distinct trials actually plotted, deduped by nct_id (one trial can join >1 bucket)."""
    return len(plotted_ids(result))


def _cohort_summary(cohort: _CohortFetch, plan: QueryPlan, result: AnalysisResult) -> CohortSummary:
    """One cohort's identity, counts, and its real, evaluable membership rule (§11.4 item 3)."""
    del plan  # kept in the signature: every other meta helper here takes (cohort, plan, result)
    return CohortSummary(
        label=cohort.label,
        value=cohort.value or cohort.label,
        api_total_count=cohort.fetch.api_total_count,
        records_matched=len(cohort.trials),
        records_plotted=_records_plotted(result),
        base_predicate=cohort.base_predicate,
    )


def _reason_counts(excluded_trials: list[ExcludedTrial], stage: str) -> dict[str, int]:
    """How many trials were excluded at `stage`, grouped by reason (§12.6)."""
    counts: dict[str, int] = {}
    for e in excluded_trials:
        if e.stage == stage:
            counts[e.reason] = counts.get(e.reason, 0) + 1
    return counts


def _data_coverage(cohort: _CohortFetch, result: AnalysisResult) -> DataCoverage:
    """Single-cohort coverage summary (only meaningful without a comparison, per §12.6)."""
    analysis_excluded = excluded_of(result)
    all_excluded_trials = [
        *cohort.match_excluded,
        *(
            ExcludedTrial(nct_id=nct_id, stage="analysis", reason=reason)
            for nct_id, reason in analysis_excluded.items()
        ),
    ]
    return DataCoverage(
        api_total_count=cohort.fetch.api_total_count,
        records_fetched=len(cohort.fetch.records),
        truncated=cohort.fetch.truncated,
        truncation_rule=cohort.fetch.truncation_rule,
        records_matched=len(cohort.trials),
        records_plotted=_records_plotted(result),
        excluded={
            "match": _reason_counts(all_excluded_trials, "match"),
            "filter": _reason_counts(all_excluded_trials, "filter"),
            "analysis": _reason_counts(all_excluded_trials, "analysis"),
        },
        excluded_trials=all_excluded_trials,
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


def _validation_block(outcome: PlanningOutcome) -> Validation:
    """`meta.validation` (§12.6): executed attempt, probe totals, judge summary, full trace."""
    judge = JudgeSummary(
        status=outcome.judge_status,
        model=outcome.judge_model,
        same_family=outcome.same_family,
        issues=outcome.judge_issues,
    )
    probe = [{"cohort": label, "total": total} for label, total in outcome.probe_totals.items()]
    return Validation(
        executed_attempt=outcome.executed_attempt, probe=probe, judge=judge, trace=outcome.trace
    )


async def _api_version_and_data_timestamp(client: CtGovClient) -> tuple[str, str]:
    """`client.version()` (cached by the client itself, §10.2) for `api_version`/`data_timestamp`
    (D3); any failure degrades to UNKNOWN + a logged warning, never a 500 (§12.6).

    Deliberately broad, mirroring the judge's single boundary (agent/judge.py `_complete`):
    `api_version`/`data_timestamp` are a best-effort provenance disclosure, not load-bearing for
    the response itself, so ANY failure here -- a known `UpstreamError`, or an unexpected bug in
    this best-effort path -- must never turn into a 500 for the caller's real question.
    """
    try:
        body = await client.version()
    except UpstreamError as exc:
        log.warning(
            "Could not fetch ClinicalTrials.gov /version for provenance: %s", type(exc).__name__
        )
        return UNKNOWN, UNKNOWN
    except Exception:
        log.exception("Unexpected error fetching ClinicalTrials.gov /version for provenance")
        return UNKNOWN, UNKNOWN
    api_version = body.get("apiVersion")
    data_timestamp = body.get("dataTimestamp")
    return (
        str(api_version) if api_version else UNKNOWN,
        str(data_timestamp) if data_timestamp else UNKNOWN,
    )


async def _provenance(
    cohorts: list[_CohortFetch], planner: Planner, client: CtGovClient
) -> Provenance:
    """Exactly what was fetched, and with which model/code version, for reproducibility (§12.6)."""
    api_requests = [asdict(log_entry) for c in cohorts for log_entry in c.fetch.requests]
    api_version, data_timestamp = await _api_version_and_data_timestamp(client)
    return Provenance(
        api_version=api_version,
        data_timestamp=data_timestamp,
        api_requests=api_requests,
        planner_model=planner.model_name,
        code_version=resolve_code_version(),
    )


def _entity_warnings(plan: QueryPlan, cohorts: list[_CohortFetch]) -> list[str]:
    """Q2=a (§22): warn when a lead-sponsor search term names a known ambiguous organization."""
    lead_terms = [t.value for t in plan.search_terms if t.param is SearchParam.LEAD]
    if not lead_terms:
        return []
    census = sponsor_census([trial for c in cohorts for trial in c.trials])
    warnings = (sponsor_ambiguity_warning(term, census) for term in lead_terms)
    return [w for w in warnings if w is not None]


def _entity_resolution(cohorts: list[_CohortFetch]) -> dict[str, object]:
    """Item 5: the top-10 sponsor census (always), plus each cohort's co-referenced aliases."""
    census = sponsor_census([trial for c in cohorts for trial in c.trials])
    return {
        "top_sponsors": [{"name": name, "count": count} for name, count in census],
        "aliases": {c.label: list(c.aliases) for c in cohorts},
    }


def _empty_cohort_warnings(
    cohorts: list[_CohortFetch], probe_totals: dict[str, int] | None = None
) -> list[str]:
    """§10.6: a comparison where one cohort is empty is kept (zero bars), but warned about --
    once: a cohort the probe already disclosed as 0 on ClinicalTrials.gov isn't repeated (fix J)."""
    if len(cohorts) < 2:
        return []
    return [
        f"cohort '{c.label}' matched 0 trials; kept as zero bars, not dropped"
        for c in cohorts
        if not c.trials and (probe_totals or {}).get(c.label) != 0
    ]


def _unknown_id_warnings(plan: QueryPlan, cohorts: list[_CohortFetch]) -> list[str]:
    """Fix G: requested NCT IDs ClinicalTrials.gov returned no record for, named explicitly."""
    requested = plan.filters.nct_ids if plan.filters and plan.filters.nct_ids else []
    returned = {t.trial.nct_id for c in cohorts for t in c.trials} | {
        e.nct_id for c in cohorts for e in c.match_excluded
    }
    return [
        f"{nct_id}: ClinicalTrials.gov returned no record for this ID (it does not exist, or "
        "the request's other filters exclude it); it is not in the results"
        for nct_id in requested
        if nct_id not in returned
    ]


def _warnings(plan: QueryPlan, cohorts: list[_CohortFetch], outcome: PlanningOutcome) -> list[str]:
    """`meta.warnings`: ambiguous sponsors, empty cohorts (§10.6), unknown NCT IDs, and probe
    disclosures (§9.1)."""
    return [
        *_entity_warnings(plan, cohorts),
        *_empty_cohort_warnings(cohorts, outcome.probe_totals),
        *_unknown_id_warnings(plan, cohorts),
        *outcome.warnings,
    ]


async def _meta(
    plan: QueryPlan,
    outcome: PlanningOutcome,
    cohorts: list[_CohortFetch],
    results: dict[str, AnalysisResult],
    request: VisualizeRequest,
    planner: Planner,
    adjustments: list[str],
    client: CtGovClient,
) -> Meta:
    """Assemble the response's `meta`; every block populated so far uses a typed shape."""
    cohort_summaries = [_cohort_summary(c, plan, results[c.label]) for c in cohorts]
    single = cohorts[0] if len(cohorts) == 1 else None
    data_coverage = _data_coverage(single, results[single.label]) if single else None
    provenance = await _provenance(cohorts, planner, client)
    return Meta(
        source=SOURCE,
        query_interpretation=plan.interpretation,
        plan=plan.model_dump(mode="json"),
        filters=_requested_filters(request),
        grouping=_grouping(plan),
        sort={},
        units={"trial_count": "trials"},
        assumptions=[*plan.assumptions, *_override_notes(outcome.overrides)],
        warnings=_warnings(plan, cohorts, outcome),
        adjustments=[*outcome.adjustments, *adjustments],
        entity_resolution=_entity_resolution(cohorts),
        data_coverage=data_coverage,
        cohorts=cohort_summaries,
        overlap=None,
        network_summary=network_summary_of(results),
        citation_policy=CitationPolicy(
            mode=request.options.citations,
            pointer_format="RFC6901",
            url_template=CITATION_URL_TEMPLATE,
        ),
        citation_check=None,
        validation=_validation_block(outcome),
        provenance=provenance,
        timing_ms={},
    )
