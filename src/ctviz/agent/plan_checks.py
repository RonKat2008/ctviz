"""Deterministic plan checks (PLAN.md §7.4 checks 3-8 + the §7.3 compatibility matrix).

Each check is a small pure function returning a list of readable error strings; `check_plan`
concatenates them. An empty list means the plan may be probed. Errors are fed back to the
planner verbatim on a revise, so each names the offending slot and what would satisfy it. The
digit guard (check 9) lives in `digit_guard.py` and is re-exported here; it never fails a plan.
"""

import re
from collections.abc import Callable
from datetime import date

from ctviz.agent.digit_guard import guard_text
from ctviz.catalog.countries import COUNTRIES
from ctviz.ctgov.compiler import compile_plan
from ctviz.errors import PlanInvalidError
from ctviz.schemas.enums import AnalysisKind, VizType
from ctviz.schemas.plan import Analysis, EnumFilters, QueryPlan, SearchTerm
from ctviz.schemas.request import MAX_YEARS_AHEAD, MIN_YEAR, canonical_country

__all__ = [
    "EXECUTABLE_KINDS",
    "check_executable",
    "check_plan",
    "coerce_plan_countries",
    "guard_text",
    "normalize_plan",
]

MIN_VALUE_LENGTH = 1
MAX_VALUE_LENGTH = 120
MIN_COMPARISON_VALUES = 2
MAX_COMPARISON_VALUES = 4
MIN_TOP_N = 3
MAX_TOP_N = 50
# The analysis kinds `pipeline._aggregate` can run in this build (pinned against it by
# tests/unit/test_pipeline_dispatch.py). trial_list passes the §7.3 matrix but has no executor
# yet, so it earns a revise instead of a PLAN_INVALID after both LLMs have run; trial_lookup runs
# as the key-facts table (fix G) -- as a table only, never a metric.
EXECUTABLE_KINDS = frozenset(
    {
        AnalysisKind.COUNT_BY,
        AnalysisKind.TIME_TREND,
        AnalysisKind.HISTOGRAM,
        AnalysisKind.SCATTER,
        AnalysisKind.NETWORK,
        AnalysisKind.TRIAL_LOOKUP,
    }
)
_ESSIE_SYNTAX = re.compile(r"(AREA|RANGE|EXPANSION)\[", re.IGNORECASE)
_NCT_ID = re.compile(r"^NCT\d{8}$")

_ALLOWED_VIZ: dict[AnalysisKind, frozenset[VizType]] = {
    AnalysisKind.COUNT_BY: frozenset({VizType.BAR_CHART, VizType.GROUPED_BAR_CHART, VizType.TABLE}),
    AnalysisKind.TIME_TREND: frozenset({VizType.TIME_SERIES, VizType.BAR_CHART}),
    AnalysisKind.HISTOGRAM: frozenset({VizType.HISTOGRAM}),
    AnalysisKind.SCATTER: frozenset({VizType.SCATTER_PLOT}),
    AnalysisKind.NETWORK: frozenset({VizType.NETWORK_GRAPH}),
    AnalysisKind.TRIAL_LOOKUP: frozenset({VizType.METRIC, VizType.TABLE}),
    AnalysisKind.TRIAL_LIST: frozenset({VizType.TABLE}),
}
_REQUIRED_FIELDS: dict[AnalysisKind, tuple[str, ...]] = {
    AnalysisKind.COUNT_BY: ("group_by",),
    AnalysisKind.TIME_TREND: ("time_field", "granularity"),
    AnalysisKind.HISTOGRAM: ("measure_x",),
    AnalysisKind.SCATTER: ("measure_x", "measure_y"),
    AnalysisKind.NETWORK: ("network_type",),
    AnalysisKind.TRIAL_LOOKUP: (),
    AnalysisKind.TRIAL_LIST: (),
}
# The fields that *define* some analysis kind; any not required by this plan's kind must be null.
# (series_by / phase_mode / color_by / top_n are optional modifiers and are never flagged.)
_KIND_DEFINING_FIELDS = (
    "group_by",
    "time_field",
    "granularity",
    "measure_x",
    "measure_y",
    "network_type",
)
# Only these viz types can show several cohorts side by side (§7.3: "grouped", "multi-line").
_MULTI_COHORT_VIZ = frozenset({VizType.GROUPED_BAR_CHART, VizType.TIME_SERIES})


def _value_errors(where: str, value: str) -> list[str]:
    """Check 3 for one search value: 1-120 chars, not blank, no Essie control syntax."""
    errors = []
    if not MIN_VALUE_LENGTH <= len(value.strip()) <= MAX_VALUE_LENGTH:
        errors.append(f"{where} must be 1-120 characters and not blank; got {value!r}")
    if _ESSIE_SYNTAX.search(value):
        errors.append(f"{where} contains Essie syntax (AREA[/RANGE[/EXPANSION[): {value!r}")
    return errors


def _check_values(plan: QueryPlan) -> list[str]:
    """Check 3: every search-term value and every compared value is a plain, sane string."""
    errors = [
        error
        for i, term in enumerate(plan.search_terms)
        for error in _value_errors(f"search_terms[{i}].value", term.value)
    ]
    if plan.comparison is not None:
        errors += [
            error
            for i, value in enumerate(plan.comparison.values)
            for error in _value_errors(f"comparison.values[{i}]", value)
        ]
    return errors


def _check_years(filters: EnumFilters | None, today: date) -> list[str]:
    """Check 4: 1900 <= start_year_min <= start_year_max <= current year + 5."""
    if filters is None:
        return []
    low, high = filters.start_year_min, filters.start_year_max
    latest = today.year + MAX_YEARS_AHEAD
    bounds = [year for year in (low, high) if year is not None]
    in_range = all(MIN_YEAR <= year <= latest for year in bounds)
    ordered = low is None or high is None or low <= high
    if in_range and ordered:
        return []
    return [
        f"years must satisfy {MIN_YEAR} <= start_year_min <= start_year_max <= {latest}; "
        f"got start_year_min={low}, start_year_max={high}"
    ]


def _check_countries(filters: EnumFilters | None) -> list[str]:
    """Check 5: every country is one of the canonical ClinicalTrials.gov spellings."""
    countries = filters.countries if filters and filters.countries else []
    return [
        f"unknown country (not a ClinicalTrials.gov spelling): {c!r}"
        for c in countries
        if c not in COUNTRIES
    ]


def _check_nct_ids(filters: EnumFilters | None) -> list[str]:
    """Every planner-supplied NCT id is shaped NCT + 8 digits (never reaches Essie otherwise)."""
    nct_ids = filters.nct_ids if filters and filters.nct_ids else []
    return [f"malformed NCT id in filters.nct_ids: {n!r}" for n in nct_ids if not _NCT_ID.match(n)]


def _check_comparison(plan: QueryPlan) -> list[str]:
    """Check 7: 2-4 distinct values, and the varied param isn't also a fixed search term."""
    comparison = plan.comparison
    if comparison is None:
        return []
    errors = []
    distinct = {value.strip().casefold() for value in comparison.values}
    same_size = len(distinct) == len(comparison.values)
    if not (same_size and MIN_COMPARISON_VALUES <= len(distinct) <= MAX_COMPARISON_VALUES):
        errors.append(f"comparison needs 2-4 distinct values; got {comparison.values}")
    if any(term.param is comparison.vary_param for term in plan.search_terms):
        vary = comparison.vary_param.value
        errors.append(f"comparison.vary_param {vary} is also a fixed search term")
    return errors


def _check_top_n(analysis: Analysis) -> list[str]:
    """Check 8: 3 <= top_n <= 50 when set."""
    top_n = analysis.top_n
    if top_n is None or MIN_TOP_N <= top_n <= MAX_TOP_N:
        return []
    return [f"analysis.top_n must be between {MIN_TOP_N} and {MAX_TOP_N}; got {top_n}"]


def _check_analysis_fields(analysis: Analysis) -> list[str]:
    """Check 6 (fields): the kind's required fields are set and other kinds' fields are null."""
    kind = analysis.kind
    required = _REQUIRED_FIELDS[kind]
    missing = [
        f"{kind} requires analysis.{name}" for name in required if getattr(analysis, name) is None
    ]
    unrelated = [
        f"{kind} must leave analysis.{name} null"
        for name in _KIND_DEFINING_FIELDS
        if name not in required and getattr(analysis, name) is not None
    ]
    return missing + unrelated


def _check_viz_type(plan: QueryPlan, analysis: Analysis, viz_type: VizType) -> list[str]:
    """Check 6 (matrix): the viz type suits the analysis kind and the number of cohorts."""
    errors = []
    if viz_type not in _ALLOWED_VIZ[analysis.kind]:
        allowed = ", ".join(sorted(_ALLOWED_VIZ[analysis.kind]))
        errors.append(f"{analysis.kind} cannot be shown as {viz_type}; use one of: {allowed}")
    if plan.comparison is not None and viz_type not in _MULTI_COHORT_VIZ:
        errors.append(
            f"{viz_type} requires exactly one cohort; a comparison needs "
            "grouped_bar_chart or time_series"
        )
    no_series = plan.comparison is None and analysis.series_by is None
    if viz_type is VizType.GROUPED_BAR_CHART and no_series:
        errors.append("grouped_bar_chart requires a comparison or analysis.series_by")
    return errors


def _check_lookup_inputs(plan: QueryPlan, analysis: Analysis) -> list[str]:
    """Check 6 (inputs): trial_lookup needs NCT ids; trial_list needs NCT ids or a search term."""
    has_ids = bool(plan.filters and plan.filters.nct_ids)
    if analysis.kind is AnalysisKind.TRIAL_LOOKUP and not has_ids:
        return ["trial_lookup requires filters.nct_ids"]
    if analysis.kind is AnalysisKind.TRIAL_LIST and not (has_ids or plan.search_terms):
        return ["trial_list requires filters.nct_ids or at least one search term"]
    return []


def _check_compatibility(plan: QueryPlan) -> list[str]:
    """Check 6: an answerable plan has an analysis and a viz, and they fit the §7.3 matrix."""
    analysis, viz = plan.analysis, plan.visualization
    errors = [
        f"an answerable plan requires {name}"
        for name, slot in (("analysis", analysis), ("visualization", viz))
        if slot is None
    ]
    if analysis is None:
        return errors
    errors += _check_analysis_fields(analysis)
    errors += _check_lookup_inputs(plan, analysis)
    errors += _check_top_n(analysis)
    if viz is not None:
        errors += _check_viz_type(plan, analysis, viz.type)
    return errors


def _check_compiles(plan: QueryPlan) -> list[str]:
    """Last line of defence: the compiler must accept the plan, so the probe never raises."""
    try:
        compile_plan(plan)
    except PlanInvalidError as exc:
        return list(exc.errors)
    return []


def check_plan(plan: QueryPlan, today: date) -> list[str]:
    """Every §7.4 check 3-8 error for an answerable plan (an empty list means OK to probe)."""
    checks: list[Callable[[], list[str]]] = [
        lambda: _check_values(plan),
        lambda: _check_years(plan.filters, today),
        lambda: _check_countries(plan.filters),
        lambda: _check_nct_ids(plan.filters),
        lambda: _check_comparison(plan),
        lambda: _check_compatibility(plan),
    ]
    errors = [error for check in checks for error in check()]
    return errors or _check_compiles(plan)


def check_executable(plan: QueryPlan) -> list[str]:
    """Whether this build's executor can run the plan's analysis kind (revise feedback if not)."""
    analysis = plan.analysis
    viz = plan.visualization
    if analysis is not None and analysis.kind is AnalysisKind.TRIAL_LOOKUP and viz is not None:
        return [] if viz.type is VizType.TABLE else ["trial_lookup is executable as a table only"]
    if analysis is None or analysis.kind in EXECUTABLE_KINDS:
        return []
    return [
        f"analysis.kind={analysis.kind} is not executable yet; use count_by with a table instead "
        "(e.g. group_by=overall_status)"
    ]


def _coerce_country(name: str) -> str:
    """The canonical spelling of `name`, or `name` unchanged when it is not a known country."""
    try:
        return canonical_country(name)
    except ValueError:
        return name


def coerce_plan_countries(plan: QueryPlan) -> tuple[QueryPlan, list[str]]:
    """Map planner country aliases onto canonical spellings before check 5 ("after coercion")."""
    if plan.filters is None or not plan.filters.countries:
        return plan, []
    before = plan.filters.countries
    after = [_coerce_country(name) for name in before]
    notes = [
        f"country {old!r} -> {new!r} (canonical spelling)"
        for old, new in zip(before, after, strict=True)
        if old != new
    ]
    if not notes:
        return plan, []
    filters = plan.filters.model_copy(update={"countries": after})
    return plan.model_copy(update={"filters": filters}), notes


def _drop_duplicated_vary_terms(plan: QueryPlan) -> tuple[QueryPlan, list[str]]:
    """Fix D(1): a fixed term on the compared param whose value IS a compared value is the
    comparison restated -- drop it in code. Any other fixed term there is kept (check 7 fires)."""
    comparison = plan.comparison
    if comparison is None:
        return plan, []
    compared = {value.strip().casefold() for value in comparison.values}
    vary = comparison.vary_param

    def duplicated(term: SearchTerm) -> bool:
        return term.param is vary and term.value.strip().casefold() in compared

    dropped = [t for t in plan.search_terms if duplicated(t)]
    if not dropped:
        return plan, []
    kept = [t for t in plan.search_terms if not duplicated(t)]
    notes = [
        f"search term {t.param.value}='{t.value}' dropped: it is one of the compared values "
        f"(the comparison varies {vary.value})"
        for t in dropped
    ]
    return plan.model_copy(update={"search_terms": kept}), notes


def _null_unrelated_analysis_fields(plan: QueryPlan) -> tuple[QueryPlan, list[str]]:
    """Fix D(2) (§7.4-6 "unrelated ones are null"): a kind-defining field this kind doesn't
    use is set to null in code rather than spending a revise on it."""
    analysis = plan.analysis
    if analysis is None:
        return plan, []
    required = _REQUIRED_FIELDS[analysis.kind]
    stray = [
        name
        for name in _KIND_DEFINING_FIELDS
        if name not in required and getattr(analysis, name) is not None
    ]
    if not stray:
        return plan, []
    cleaned = analysis.model_copy(update=dict.fromkeys(stray))
    notes = [f"analysis.{name} set to null: {analysis.kind} does not use it" for name in stray]
    return plan.model_copy(update={"analysis": cleaned}), notes


def normalize_plan(plan: QueryPlan) -> tuple[QueryPlan, list[str]]:
    """Deterministic, disclosed fixes before checks 3-8 (country aliases, fix D's duplicated
    comparison term and stray analysis fields); each note goes to `meta.adjustments`."""
    notes: list[str] = []
    for step in (
        coerce_plan_countries,
        _drop_duplicated_vary_terms,
        _null_unrelated_analysis_fields,
    ):
        plan, step_notes = step(plan)
        notes += step_notes
    return plan, notes
