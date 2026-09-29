"""JudgeVerdict: the judge model's structured output (PLAN.md §9.2).

The model answers all seven rubric checks and lists concrete issues. Its own `verdict` string is
recorded but never trusted: `agent/judge.py` recomputes the revise decision from the issues.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

RubricCheck = Literal[
    "filter_fidelity",
    "no_invented_filters",
    "dimension_match",
    "viz_fit",
    "time_range",
    "comparison_cohorts",
    "ambiguity_handled",
]
Severity = Literal["critical", "major", "minor"]
IssueCategory = Literal[
    "invented_filter",
    "missing_filter",
    "wrong_param",
    "dimension_mismatch",
    "viz_mismatch",
    "time_range_error",
    "cohort_error",
    "ambiguity_unhandled",
    "other",
]


class _Strict(BaseModel):
    """Shared config for every verdict node: frozen, and no keys beyond what's declared."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class CheckResult(_Strict):
    """One rubric check's outcome, with a quote from the query/fields/plan/probe backing it."""

    check: RubricCheck
    passed: bool
    evidence: str


class JudgeIssue(_Strict):
    """One concrete problem with the plan, where it is, and the exact fix."""

    severity: Severity
    category: IssueCategory
    plan_path: str
    evidence_quote: str  # short verbatim quote from the question/fields/plan/probe; code checks it
    explanation: str
    suggested_fix: str


class JudgeVerdict(_Strict):
    """The judge's full answer: all seven checks, every issue, and its (untrusted) verdict."""

    checks: list[CheckResult]
    issues: list[JudgeIssue]
    verdict: Literal["pass", "revise"]
    confidence: Literal["high", "medium", "low"]
