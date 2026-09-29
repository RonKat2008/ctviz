"""VisualizeResponse: the POST /v1/visualize response envelope. One parse path via `ok` (§12)."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from ctviz.schemas.citations import NctId
from ctviz.schemas.viz import Visualization

SCHEMA_VERSION: Literal["1.0.0"] = "1.0.0"

ErrorCode = Literal[
    "OUT_OF_SCOPE",
    "NO_MATCHING_TRIALS",
    "PLAN_INVALID",
    "INVALID_REQUEST",
    "UPSTREAM_API_ERROR",
    "LLM_UNAVAILABLE",
    "CITATION_CHECK_FAILED",
    "INTERNAL_ERROR",
    "RATE_LIMITED",
]

JudgeStatus = Literal[
    "passed",
    "passed_after_revision",
    "rejected_after_revision",
    "executed_previous_plan",
    "unavailable",
    "skipped",
]


class _Meta(BaseModel):
    """Shared config for every `meta` node: frozen, and no keys beyond what's declared."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class ExcludedTrial(_Meta):
    """One trial dropped from the chart, with the stage and reason it was excluded (§12.6)."""

    nct_id: NctId
    stage: Literal["match", "filter", "analysis"]
    reason: str


class DataCoverage(_Meta):
    """How many trials were fetched, matched, plotted, and excluded and why (§12.6)."""

    api_total_count: int
    records_fetched: int
    truncated: bool
    truncation_rule: str | None
    records_matched: int
    records_plotted: int
    excluded: dict[str, dict[str, int]]
    excluded_trials: list[ExcludedTrial]


class CohortSummary(_Meta):
    """One compared cohort's identity, counts, and the predicate that defines membership."""

    label: str
    value: str
    api_total_count: int
    records_matched: int
    records_plotted: int
    base_predicate: dict[str, Any]


class CitationPolicy(_Meta):
    """How citations are delivered for this response: mode, pointer format, deep-link template."""

    mode: Literal["full", "sample", "none"]
    pointer_format: Literal["RFC6901"]
    url_template: str


class CitationCheck(_Meta):
    """The independent verifier's result: predicates and evidence re-checked against raw data."""

    mode: Literal["full", "sample", "none"]
    passed: bool
    citations_checked: int
    evidence_checked: int
    predicates_checked: int
    recount_ok: bool
    ms: int


class JudgeSummary(_Meta):
    """The judge model's verdict on the executed plan: status, model, and any issues raised."""

    status: JudgeStatus
    model: str | None
    same_family: bool
    issues: list[str]


class Validation(_Meta):
    """The full validation trail: which attempt ran, probe counts, judge verdict, and trace."""

    executed_attempt: int
    probe: list[dict[str, Any]]
    judge: JudgeSummary
    trace: list[dict[str, Any]]


class Provenance(_Meta):
    """Exactly what was fetched, and with which model/code version, for reproducibility."""

    api_version: str
    data_timestamp: str
    api_requests: list[dict[str, Any]]
    planner_model: str
    code_version: str


class Meta(_Meta):
    """Everything explaining the response: interpretation, coverage, validation, provenance."""

    source: str
    query_interpretation: str
    plan: dict[str, Any]
    filters: dict[str, Any]
    grouping: dict[str, Any]
    sort: dict[str, Any]
    units: dict[str, str]
    assumptions: list[str]
    warnings: list[str]
    adjustments: list[str]
    entity_resolution: dict[str, Any]
    data_coverage: DataCoverage | None
    cohorts: list[CohortSummary]
    overlap: dict[str, Any] | None
    network_summary: dict[str, Any] | None
    citation_policy: CitationPolicy
    citation_check: CitationCheck | None
    validation: Validation
    provenance: Provenance
    timing_ms: dict[str, int]


class ErrorInfo(_Meta):
    """{code, message, details} shown when ok=false; `details` carries code-specific extras."""

    code: ErrorCode
    message: str
    details: dict[str, Any] | None = None


class VisualizeResponse(BaseModel):
    """The full response body: `visualization`/`meta`/`error` — always check `ok` first."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    ok: bool
    visualization: Visualization | None
    meta: Meta | None
    error: ErrorInfo | None

    @classmethod
    def failure(cls, error: ErrorInfo, meta: "Meta | None" = None) -> "VisualizeResponse":
        """Build an ok:false envelope (domain outcomes are HTTP 200; see api/errors.py)."""
        return cls(ok=False, visualization=None, meta=meta, error=error)
