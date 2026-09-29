"""Overlay: write `VisualizeRequest` structured fields into a `QueryPlan`'s slots (§6.1, D14).

Structured fields are never planner output; they are applied here, deterministically, after the
plan is produced. A structured field always wins over any planner value in the same slot
(including a comparison varying that same param, or the planner's `analysis.top_n`), and every
such replacement is logged as a `FieldOverride` so the judge (S6+) can treat it as authoritative
and `meta.assumptions` can disclose it. Pure additions -- a slot the planner left empty -- are
never logged; only an actual replacement is.
"""

from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from ctviz.schemas.enums import SearchParam
from ctviz.schemas.plan import EnumFilters, QueryPlan, SearchTerm
from ctviz.schemas.request import VisualizeRequest

STRUCTURED_FIELD_SOURCE: Literal["structured_field"] = "structured_field"
_BLANK_FILTERS: dict[str, Any] = {
    "phases": None,
    "overall_statuses": None,
    "study_types": None,
    "intervention_types": None,
    "lead_sponsor_classes": None,
    "countries": None,
    "start_year_min": None,
    "start_year_max": None,
    "nct_ids": None,
}
# (request field, EnumFilters field, coercion from the request value to the filter's shape)
_FILTER_SLOTS: tuple[tuple[str, str, Callable[[Any], Any]], ...] = (
    ("trial_phase", "phases", list),
    ("status", "overall_statuses", list),
    ("country", "countries", lambda v: [v]),
    ("start_year", "start_year_min", lambda v: v),
    ("end_year", "start_year_max", lambda v: v),
    ("study_type", "study_types", lambda v: [v]),
    ("nct_ids", "nct_ids", list),
)
# Which request field a structured search-term param was derived from (for override messages).
_PARAM_TO_REQUEST_FIELD: dict[SearchParam, str] = {
    SearchParam.INTR: "drug_name",
    SearchParam.COND: "condition",
    SearchParam.LEAD: "sponsor",
    SearchParam.SPONS: "sponsor",
}


class FieldOverride(BaseModel):
    """One structured field that replaced an existing planner value in the same plan slot."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    field: str
    text_value: str
    applied_value: str


def _search_term_additions(request: VisualizeRequest) -> list[tuple[SearchParam, str]]:
    """Every structured entity field present on the request, paired with its search param."""
    additions: list[tuple[SearchParam, str]] = []
    if request.drug_name:
        additions.append((SearchParam.INTR, request.drug_name))
    if request.condition:
        additions.append((SearchParam.COND, request.condition))
    if request.sponsor:
        param = SearchParam.SPONS if request.sponsor_role == "any" else SearchParam.LEAD
        additions.append((param, request.sponsor))
    return additions


def _apply_search_terms(
    plan: QueryPlan, request: VisualizeRequest
) -> tuple[QueryPlan, list[FieldOverride]]:
    """Drop any LLM term sharing a param with a structured field, then append the field's term.

    When the dropped term's value differs from the structured field's, log the replacement.
    """
    additions = _search_term_additions(request)
    if not additions:
        return plan, []
    overridden_params = {param for param, _ in additions}
    displaced = {t.param: t.value for t in plan.search_terms if t.param in overridden_params}
    kept = [t for t in plan.search_terms if t.param not in overridden_params]
    new_terms = [
        *kept,
        *(
            SearchTerm(
                param=param,
                value=value,
                source=STRUCTURED_FIELD_SOURCE,
                rationale="structured field",
            )
            for param, value in additions
        ),
    ]
    overrides = [
        FieldOverride(
            field=_PARAM_TO_REQUEST_FIELD.get(param, param.value),
            text_value=displaced[param],
            applied_value=value,
        )
        for param, value in additions
        if param in displaced and displaced[param] != value
    ]
    return plan.model_copy(update={"search_terms": new_terms}), overrides


def _drop_conflicting_comparison(
    plan: QueryPlan, request: VisualizeRequest
) -> tuple[QueryPlan, list[FieldOverride]]:
    """Drop `plan.comparison` when a structured field targets the same param (§6.1 ruling:
    structured fields always win)."""
    if plan.comparison is None:
        return plan, []
    conflict = next(
        (
            value
            for param, value in _search_term_additions(request)
            if param == plan.comparison.vary_param
        ),
        None,
    )
    if conflict is None:
        return plan, []
    override = FieldOverride(
        field="comparison",
        text_value=f"vary {plan.comparison.vary_param.value} over {plan.comparison.values}",
        applied_value=conflict,
    )
    return plan.model_copy(update={"comparison": None}), [override]


def _apply_filters(
    plan: QueryPlan, request: VisualizeRequest
) -> tuple[QueryPlan, list[FieldOverride]]:
    """Replace each present filter slot via `model_copy`, logging any planner value it displaced."""
    present = [
        (field, filt_field, coerce(getattr(request, field)))
        for field, filt_field, coerce in _FILTER_SLOTS
        if getattr(request, field) is not None
    ]
    if not present:
        return plan, []
    base = plan.filters if plan.filters is not None else EnumFilters(**_BLANK_FILTERS)
    updates: dict[str, Any] = {}
    overrides: list[FieldOverride] = []
    for field, filt_field, applied in present:
        previous = getattr(base, filt_field)
        updates[filt_field] = applied
        if previous is not None and previous != applied:
            overrides.append(
                FieldOverride(field=field, text_value=str(previous), applied_value=str(applied))
            )
    new_filters = base.model_copy(update=updates)
    return plan.model_copy(update={"filters": new_filters}), overrides


def _apply_top_n_override(
    plan: QueryPlan, request: VisualizeRequest
) -> tuple[QueryPlan, list[FieldOverride]]:
    """`request.options.top_n` always wins over the planner's `analysis.top_n` (§6)."""
    top_n = request.options.top_n
    if top_n is None or plan.analysis is None:
        return plan, []
    previous = plan.analysis.top_n
    new_plan = plan.model_copy(
        update={"analysis": plan.analysis.model_copy(update={"top_n": top_n})}
    )
    if previous is None or previous == top_n:
        return new_plan, []
    override = FieldOverride(
        field="options.top_n", text_value=str(previous), applied_value=str(top_n)
    )
    return new_plan, [override]


def apply_overlay(
    plan: QueryPlan, request: VisualizeRequest
) -> tuple[QueryPlan, list[FieldOverride]]:
    """Write every structured field on `request` into `plan`; return the new plan and overrides.

    Never mutates `plan` or `request`; every returned object is freshly copied.
    """
    with_terms, term_overrides = _apply_search_terms(plan, request)
    without_conflict, comparison_overrides = _drop_conflicting_comparison(with_terms, request)
    with_filters, filter_overrides = _apply_filters(without_conflict, request)
    with_top_n, top_n_overrides = _apply_top_n_override(with_filters, request)
    overrides = [*term_overrides, *comparison_overrides, *filter_overrides, *top_n_overrides]
    return with_top_n, overrides
