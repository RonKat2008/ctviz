"""QueryPlan: the planner's entire output. Menus + entity strings only — no URLs, numbers or data.

All fields are required-but-nullable because OpenAI strict mode demands every key be present.
Domain rules (years, comparison size, compatibility) live in agent/plan_checks.py, not here.
"""

from typing import Any, Literal

from openai.lib._pydantic import to_strict_json_schema
from pydantic import BaseModel, ConfigDict

from ctviz.schemas.enums import (
    AgencyClass,
    AnalysisKind,
    Dimension,
    InterventionType,
    Measure,
    NetworkType,
    OverallStatus,
    Phase,
    SearchParam,
    StudyType,
    TimeField,
    VizType,
)


class _Strict(BaseModel):
    """Shared config for every plan node: frozen, and no keys beyond what's declared."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class SearchTerm(_Strict):
    """One full-text search the plan asks the compiler to run, and why."""

    param: SearchParam
    value: str
    source: Literal["query_text", "structured_field"]
    rationale: str


class EnumFilters(_Strict):
    """The enum/range filters to apply; unused ones are null (§7.1)."""

    phases: list[Phase] | None
    overall_statuses: list[OverallStatus] | None
    study_types: list[StudyType] | None
    intervention_types: list[InterventionType] | None
    lead_sponsor_classes: list[AgencyClass] | None
    countries: list[str] | None
    start_year_min: int | None
    start_year_max: int | None
    nct_ids: list[str] | None


class Comparison(_Strict):
    """A side-by-side cohort comparison: which search param varies, and its 2-4 values."""

    vary_param: SearchParam
    values: list[str]


class Analysis(_Strict):
    """The aggregation to run; only the fields required by `kind` (§7.3) are non-null."""

    kind: AnalysisKind
    group_by: Dimension | None
    series_by: Dimension | None
    phase_mode: Literal["combined", "membership"] | None
    time_field: TimeField | None
    granularity: Literal["year", "month"] | None
    measure_x: Measure | None
    measure_y: Measure | None
    color_by: Dimension | None
    network_type: NetworkType | None
    top_n: int | None


class VizChoice(_Strict):
    """The visualization type the planner picked for this analysis, and why."""

    type: VizType
    title: str
    rationale: str


class QueryPlan(_Strict):
    """The complete, LLM-produced plan: scope decision, search terms, filters and viz choice."""

    answerable: bool
    out_of_scope_reason: str | None
    suggested_reframing: str | None
    interpretation: str
    search_terms: list[SearchTerm]
    filters: EnumFilters | None
    comparison: Comparison | None
    analysis: Analysis | None
    visualization: VizChoice | None
    assumptions: list[str]

    @classmethod
    def strict_json_schema(cls) -> dict[str, Any]:
        """The exact schema OpenAI receives in strict mode (linted by the schema test)."""
        return to_strict_json_schema(cls)
