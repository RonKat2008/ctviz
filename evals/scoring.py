"""Scoring one eval run: plan accuracy (attempt 1 / after revise), outcome, cost and latency
(PLAN.md §16.4), plus every judge review a planner case ran. Pure functions over recorded facts.
The labeled judge suite (§9.6) is scored in `evals/judge_scoring.py`.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from ctviz.agent.overlay import apply_overlay
from ctviz.schemas.plan import QueryPlan
from ctviz.schemas.request import VisualizeRequest
from evals.cases import EvalCase
from evals.matcher import check_plan

# USD per 1M tokens. Planner: gpt-5.4-mini list price per PLAN.md §4.5 (cached input assumed at
# 10% of input). Judge tier 1: google/gemini-2.5-flash-lite via OpenRouter per PLAN.md §9.5;
# tier 2: anthropic/claude-haiku-4.5 at Anthropic's list price (OpenRouter passes it through).
# A judge's cached input tokens are priced as fresh input (an over-estimate, never an under-).
PLANNER_INPUT_PER_M = 0.75
PLANNER_CACHED_INPUT_PER_M = 0.075
PLANNER_OUTPUT_PER_M = 4.50
JUDGE_INPUT_PER_M = 0.10
JUDGE_OUTPUT_PER_M = 0.40
JUDGE_FALLBACK_INPUT_PER_M = 1.00
JUDGE_FALLBACK_OUTPUT_PER_M = 5.00
PLANNER_MODEL = "gpt-5.4-mini"
JUDGE_MODEL = "google/gemini-2.5-flash-lite"
JUDGE_FALLBACK_MODEL = "anthropic/claude-haiku-4.5"
_PER_M = 1_000_000
REVISE_PLANNER_CALLS = 2
_REFUSED_PLAN: dict[str, Any] = {"answerable": False, "search_terms": [], "assumptions": []}


@dataclass(frozen=True)
class Price:
    """USD per 1M tokens for one model."""

    input_per_m: float
    output_per_m: float
    cached_input_per_m: float


PRICES: dict[str, Price] = {
    PLANNER_MODEL: Price(PLANNER_INPUT_PER_M, PLANNER_OUTPUT_PER_M, PLANNER_CACHED_INPUT_PER_M),
    JUDGE_MODEL: Price(JUDGE_INPUT_PER_M, JUDGE_OUTPUT_PER_M, JUDGE_INPUT_PER_M),
    JUDGE_FALLBACK_MODEL: Price(
        JUDGE_FALLBACK_INPUT_PER_M, JUDGE_FALLBACK_OUTPUT_PER_M, JUDGE_FALLBACK_INPUT_PER_M
    ),
}
_ROLE_DEFAULT_MODEL = {"planner": PLANNER_MODEL, "judge": JUDGE_MODEL}


@dataclass(frozen=True)
class Usage:
    """Token usage of one LLM API call (reasoning tokens are included in `output_tokens`);
    `model` None = the role's default model."""

    role: Literal["planner", "judge"]
    input_tokens: int
    output_tokens: int
    cached_tokens: int = 0
    model: str | None = None


@dataclass(frozen=True)
class CaseRun:
    """Everything observed while one planner case ran through the real HTTP endpoint."""

    case: EvalCase
    http_status: int
    payload: dict[str, Any]
    raw_plans: tuple[QueryPlan | None, ...]  # the planner's own outputs; None = model refusal
    usages: tuple[Usage, ...]
    judge_calls: int
    latency_s: float


@dataclass(frozen=True)
class CaseResult:
    """One scored planner case."""

    id: str
    klass: str
    query: str
    http_status: int
    expected_outcome: str
    actual_outcome: str
    outcome_ok: bool
    outcome_note: str
    attempt1_failures: tuple[str, ...]
    final_failures: tuple[str, ...]
    planner_calls: int
    judge_calls: int
    latency_s: float
    cost_usd: float
    judge_status: str | None
    executed_attempt: int | None
    citation_passed: bool | None
    judge_model: str | None = None  # meta.validation.judge.model: the model that answered
    judge_reviews: int = 0  # judge reviews in the trace (incl. unavailable ones)
    judge_available: int = 0
    judge_models: tuple[str, ...] = ()  # the model that answered each available review
    judge_revised: bool = False  # the judge flagged attempt 1 and plan 2 was produced

    @property
    def attempt1_correct(self) -> bool:
        """The planner's first plan (after the structured-field overlay) met every property."""
        return not self.attempt1_failures

    @property
    def final_correct(self) -> bool:
        """The plan the orchestrator executed (or last produced) met every property."""
        return not self.final_failures


def _price(usage: Usage) -> Price:
    """The list price of the model that made this call; an unknown model fails loudly."""
    model = usage.model or _ROLE_DEFAULT_MODEL[usage.role]
    if model not in PRICES:
        raise ValueError(f"no list price recorded for model {model!r}; add it to PRICES")
    return PRICES[model]


def cost_usd(usages: Sequence[Usage]) -> float:
    """Estimated USD for these calls at the module's list prices."""
    total = 0.0
    for u in usages:
        price = _price(u)
        fresh = u.input_tokens - u.cached_tokens
        total += fresh * price.input_per_m + u.cached_tokens * price.cached_input_per_m
        total += u.output_tokens * price.output_per_m
    return total / _PER_M


def percentile(values: Sequence[float], p: float) -> float:
    """Nearest-rank percentile (the smallest value with at least p% of values at or below it)."""
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[rank - 1]


def _as_plan_dict(raw: QueryPlan | None, request: VisualizeRequest) -> dict[str, Any]:
    """A raw planner output as the orchestrator sees it: overlaid, or a refusal's scope answer."""
    if raw is None:
        return _REFUSED_PLAN
    return apply_overlay(raw, request)[0].model_dump(mode="json")


def _executed_plan(payload: dict[str, Any]) -> dict[str, Any] | None:
    """`meta.plan` of an ok response: the plan the orchestrator actually executed."""
    meta = payload.get("meta") or {}
    plan = meta.get("plan") if payload.get("ok") else None
    return plan if isinstance(plan, dict) else None


def _plans(run: CaseRun) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """(attempt-1 plan, final plan). No planner call (fast path) = the executed plan for both."""
    request = VisualizeRequest.model_validate(run.case.request)
    executed = _executed_plan(run.payload)
    if not run.raw_plans:
        return executed, executed
    first = _as_plan_dict(run.raw_plans[0], request)
    return first, executed or _as_plan_dict(run.raw_plans[-1], request)


def _actual_outcome(run: CaseRun) -> str:
    """ "ok", the envelope's error code, or the bare HTTP status when there is neither."""
    if run.payload.get("ok"):
        return "ok"
    error = run.payload.get("error") or {}
    return str(error.get("code") or f"HTTP {run.http_status}")


def _judge_events(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Every judge review in an ok response's validation trace (none for an error envelope)."""
    meta = payload.get("meta") if payload.get("ok") else None
    trace = ((meta or {}).get("validation") or {}).get("trace") or []
    return [e for e in trace if isinstance(e, dict) and e.get("step") == "judge"]


def _judge_flagged_attempt_1(payload: dict[str, Any]) -> bool:
    """The code-side rule said "revise" on an available review of plan 1."""
    return any(
        e.get("attempt") == 1 and e.get("available") and e.get("needs_revision")
        for e in _judge_events(payload)
    )


def _outcome(run: CaseRun) -> tuple[str, bool, str]:
    """(actual outcome, whether it is the expected one, why not). A judge flag on attempt 1
    must be followed by a revise: that is what makes "after revise" differ from attempt 1."""
    actual = _actual_outcome(run)
    calls = len(run.raw_plans)
    allowed = run.case.planner_calls
    if actual != run.case.outcome:
        return actual, False, f"expected {run.case.outcome}, got {actual}"
    if allowed is not None and calls not in allowed:
        return actual, False, f"expected planner calls in {list(allowed)}, got {calls}"
    if _judge_flagged_attempt_1(run.payload) and calls != REVISE_PLANNER_CALLS:
        return actual, False, f"judge flagged attempt 1 but no revise ran ({calls} planner calls)"
    return actual, True, ""


def _validation(payload: dict[str, Any]) -> tuple[str | None, str | None, int | None, bool | None]:
    """(judge status, judge model, executed attempt, citation check passed) from an ok meta."""
    meta = payload.get("meta") if payload.get("ok") else None
    if not meta:
        return None, None, None, None
    validation = meta.get("validation") or {}
    judge = validation.get("judge") or {}
    check = meta.get("citation_check")
    passed = bool(check.get("passed")) if check else None
    return judge.get("status"), judge.get("model"), validation.get("executed_attempt"), passed


def _review_facts(run: CaseRun) -> dict[str, Any]:
    """The CaseResult fields describing every judge review this case ran."""
    events = _judge_events(run.payload)
    answered = [e for e in events if e.get("available")]
    return {
        "judge_reviews": len(events),
        "judge_available": len(answered),
        "judge_models": tuple(str(e.get("model")) for e in answered),
        "judge_revised": _judge_flagged_attempt_1(run.payload)
        and len(run.raw_plans) == REVISE_PLANNER_CALLS,
    }


def score_case(run: CaseRun) -> CaseResult:
    """Score one recorded run: both plans against the properties, outcome, cost and latency."""
    first, final = _plans(run)
    actual, outcome_ok, note = _outcome(run)
    judge_status, judge_model, executed_attempt, citation = _validation(run.payload)
    return CaseResult(
        id=run.case.id,
        klass=run.case.klass,
        query=str(run.case.request.get("query", "")),
        http_status=run.http_status,
        expected_outcome=run.case.outcome,
        actual_outcome=actual,
        outcome_ok=outcome_ok,
        outcome_note=note,
        attempt1_failures=tuple(check_plan(first, run.case.expect)),
        final_failures=tuple(check_plan(final, run.case.expect)),
        planner_calls=len(run.raw_plans),
        judge_calls=run.judge_calls,
        latency_s=run.latency_s,
        cost_usd=cost_usd(run.usages),
        judge_status=judge_status,
        executed_attempt=executed_attempt,
        citation_passed=citation,
        judge_model=judge_model,
        **_review_facts(run),
    )


@dataclass(frozen=True)
class PlanEvalSummary:
    """Aggregate planner-eval metrics, compared against §16.4 in the report."""

    n: int
    attempt1_accuracy: float
    final_accuracy: float
    e2e_success: float
    citation_pass_rate: float | None
    citation_checked: int
    p50_s: float
    p95_s: float
    mean_cost_usd: float
    total_cost_usd: float
    planner_calls: int
    judge_calls: int


def _rate(hits: int, total: int) -> float | None:
    """hits / total, or None when there is nothing to divide by."""
    return hits / total if total else None


def summarize_cases(results: Sequence[CaseResult]) -> PlanEvalSummary:
    """Accuracy, success, citation, latency and cost aggregates over every scored case."""
    n = len(results)
    checked = [r.citation_passed for r in results if r.citation_passed is not None]
    latencies = [r.latency_s for r in results]
    total_cost = sum(r.cost_usd for r in results)
    return PlanEvalSummary(
        n=n,
        attempt1_accuracy=sum(r.attempt1_correct for r in results) / n,
        final_accuracy=sum(r.final_correct for r in results) / n,
        e2e_success=sum(r.outcome_ok for r in results) / n,
        citation_pass_rate=_rate(sum(checked), len(checked)),
        citation_checked=len(checked),
        p50_s=percentile(latencies, 50),
        p95_s=percentile(latencies, 95),
        mean_cost_usd=total_cost / n,
        total_cost_usd=total_cost,
        planner_calls=sum(r.planner_calls for r in results),
        judge_calls=sum(r.judge_calls for r in results),
    )
