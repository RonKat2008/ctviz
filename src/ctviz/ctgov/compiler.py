"""QueryPlan → ClinicalTrials.gov request params, one RequestSpec per cohort. Pure and total."""

import re
from dataclasses import dataclass

from ctviz.catalog.countries import COUNTRIES
from ctviz.config import PAGE_SIZE
from ctviz.ctgov.fields import fields_for_plan
from ctviz.errors import PlanInvalidError
from ctviz.schemas.plan import EnumFilters, QueryPlan

_UNSAFE = re.compile(r'["\[\]\x00-\x1f]')
_NCT_ID = re.compile(r"^NCT\d{8}$")
MAX_VALUE_LENGTH = 120


@dataclass(frozen=True)
class RequestSpec:
    """One cohort's compiled request: its label, the value that defines it, and API params."""

    cohort_label: str
    cohort_value: str | None
    params: dict[str, str]


def _clean(value: str) -> str:
    """Strip Essie control characters and clamp length; never let LLM text write syntax."""
    cleaned = _UNSAFE.sub(" ", value).strip()[:MAX_VALUE_LENGTH]
    if not cleaned:
        raise PlanInvalidError([f"Search value cleaned to empty string: {value!r}"])
    return cleaned


def _validated_countries(countries: list[str]) -> list[str]:
    """Reject any country the LLM invented; only catalog spellings are safe to quote into Essie."""
    unknown = [c for c in countries if c not in COUNTRIES]
    if unknown:
        raise PlanInvalidError([f"Unknown country (not in catalog): {c!r}" for c in unknown])
    return countries


def _validated_nct_ids(nct_ids: list[str]) -> list[str]:
    """Reject any id not shaped like NCT followed by 8 digits before it reaches Essie."""
    malformed = [n for n in nct_ids if not _NCT_ID.fullmatch(n)]
    if malformed:
        raise PlanInvalidError([f"Malformed NCT id: {n!r}" for n in malformed])
    return nct_ids


def _any_of(area: str, values: list[str]) -> str:
    """OR together same-area clauses, parenthesized only when there is more than one."""
    joined = " OR ".join(f"AREA[{area}]{v}" for v in values)
    return f"({joined})" if len(values) > 1 else joined


def _year_range(low: int | None, high: int | None) -> str:
    """A StartDate RANGE clause; an absent bound becomes MIN/MAX."""
    start = f"{low}-01-01" if low else "MIN"
    end = f"{high}-12-31" if high else "MAX"
    return f"AREA[StartDate]RANGE[{start},{end}]"


def advanced_filter(filters: EnumFilters | None) -> str | None:
    """Build the filter.advanced Essie expression; the LLM never writes this string."""
    if filters is None:
        return None
    clauses: list[str] = []
    if filters.phases:
        clauses.append(_any_of("Phase", list(filters.phases)))
    if filters.study_types:
        clauses.append(_any_of("StudyType", list(filters.study_types)))
    if filters.intervention_types:
        clauses.append(_any_of("InterventionType", list(filters.intervention_types)))
    if filters.lead_sponsor_classes:
        clauses.append(_any_of("LeadSponsorClass", list(filters.lead_sponsor_classes)))
    if filters.countries:
        safe = _validated_countries(filters.countries)
        clauses.append(_any_of("LocationCountry", [f'"{c}"' for c in safe]))
    if filters.start_year_min or filters.start_year_max:
        clauses.append(_year_range(filters.start_year_min, filters.start_year_max))
    return " AND ".join(clauses) or None


def _base_params(plan: QueryPlan) -> dict[str, str]:
    """Params shared by every cohort of this plan: paging, fields, search terms, filters."""
    params = {
        "pageSize": str(PAGE_SIZE),
        "countTotal": "true",
        "format": "json",
        "fields": ",".join(fields_for_plan(plan)),
    }
    params |= {t.param.value: _clean(t.value) for t in plan.search_terms}
    expression = advanced_filter(plan.filters)
    if expression:
        params["filter.advanced"] = expression
    if plan.filters and plan.filters.overall_statuses:
        params["filter.overallStatus"] = ",".join(plan.filters.overall_statuses)
    if plan.filters and plan.filters.nct_ids:
        params["filter.ids"] = ",".join(_validated_nct_ids(plan.filters.nct_ids))
    return params


def compile_plan(plan: QueryPlan) -> list[RequestSpec]:
    """One RequestSpec per cohort: a single cohort, or one per compared value."""
    base = _base_params(plan)
    if plan.comparison is None:
        label = plan.search_terms[0].value if plan.search_terms else "All trials"
        return [RequestSpec(label, None, base)]
    key = plan.comparison.vary_param.value
    return [RequestSpec(v, v, base | {key: _clean(v)}) for v in plan.comparison.values]
