"""The revise loop (PLAN.md §9.4, the V3 diagram): plan -> overlay -> checks -> probe -> judge.

`orchestrate` makes at most two planner calls and two judge calls. `_try_attempt` runs one pass
and stops at the first gate that objects, naming where it stopped (`Stage`); `_conclude` maps the
(attempt 1, last attempt) pair onto exactly one named ending -- an executed plan with its
`judge.status`, or an `error_code`. Every step appends one event to the trace that becomes
`meta.validation.trace`. Sync SDK calls (planner, judge) run via `asyncio.to_thread`.
"""

import asyncio
import dataclasses
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from ctviz.agent.fast_path import fast_path_plan, is_fast_path, nct_ids_in
from ctviz.agent.judge import REVISING_SEVERITIES, Judge, JudgeReview, same_family
from ctviz.agent.overlay import FieldOverride, apply_overlay
from ctviz.agent.plan_checks import check_executable, check_plan, guard_text, normalize_plan
from ctviz.agent.planner import Planner
from ctviz.agent.probe import (
    SECOND_ATTEMPT,
    ProbeClient,
    ProbeResult,
    droppable_filters,
    probe_plan,
)
from ctviz.agent.prompts import one_line
from ctviz.config import JUDGE_REQUEST_BUDGET_S
from ctviz.schemas.judge import JudgeIssue
from ctviz.schemas.plan import QueryPlan
from ctviz.schemas.request import VisualizeRequest
from ctviz.schemas.response import JudgeStatus

__all__ = ["PlanningOutcome", "is_fast_path", "orchestrate"]

Stage = Literal[
    "out_of_scope",
    "checks_failed",
    "probe_revise",
    "no_matches",
    "judge_revise",
    "judge_pass",
    "judge_unavailable",
]
PlanningError = Literal["OUT_OF_SCOPE", "PLAN_INVALID", "NO_MATCHING_TRIALS"]
_FIRST_ATTEMPT_ENDINGS: dict[Stage, JudgeStatus] = {
    "judge_pass": "passed",
    "judge_unavailable": "unavailable",
}
_SECOND_ATTEMPT_ENDINGS: dict[Stage, JudgeStatus] = {
    "judge_pass": "passed_after_revision",
    "judge_revise": "rejected_after_revision",
    "judge_unavailable": "unavailable",
}
_FLAGGED: frozenset[JudgeStatus] = frozenset({"rejected_after_revision", "executed_previous_plan"})
JUDGE_TEXT_MAX_CHARS = 500
NO_MATCHES_MESSAGE = (
    "No trials on ClinicalTrials.gov match this question, even after one revision. "
    "Try dropping or loosening: "
)


@dataclass(frozen=True)
class PlanningOutcome:
    """Exactly one named ending of the revise loop: a plan to execute, or an error code."""

    plan: QueryPlan | None
    error_code: PlanningError | None
    judge_status: JudgeStatus
    executed_attempt: int
    trace: list[dict[str, Any]]
    overrides: list[FieldOverride] = field(default_factory=list)
    adjustments: list[str] = field(default_factory=list)
    probe_totals: dict[str, int] = field(default_factory=dict)
    judge_issues: list[str] = field(default_factory=list)
    judge_model: str | None = None
    same_family: bool = False
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    message: str = ""
    details: dict[str, Any] | None = None


@dataclass(frozen=True)
class _Deps:
    """Everything one orchestration needs, passed as one value. `request` carries NCT IDs
    pre-filled from the query text (for the planner/overlay); `judged` is the caller's own
    request, so only a real structured field is authoritative to the judge (fix H)."""

    request: VisualizeRequest
    planner: Planner
    judge: Judge | None
    client: ProbeClient
    today: date
    judged: VisualizeRequest


@dataclass(frozen=True)
class _Attempt:
    """One pass through the gates: where it stopped, and what the next attempt should hear.

    `plan` is post-overlay (used for checks/probe/judge/execution); `raw_plan` is the planner's
    OWN unmodified output, before `apply_overlay`/`normalize_plan` -- it is what a revise shows
    the planner as "the previous plan" (§8.2: the planner sees structured field NAMES only, so
    it must never be shown the VALUES the overlay wrote into its own prior plan)."""

    number: int
    stage: Stage
    plan: QueryPlan
    raw_plan: QueryPlan
    overrides: list[FieldOverride]
    events: list[dict[str, Any]]
    notes: list[str] = field(default_factory=list)
    feedback: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    probe: ProbeResult | None = None
    review: JudgeReview | None = None


def _issue_text(issue: JudgeIssue) -> str:
    """One judge issue as one capped line (revise feedback, `meta.validation` issues + trace):
    the text is LLM-authored, so it is flattened and cut to JUDGE_TEXT_MAX_CHARS (fix J)."""
    text = f"[{issue.severity}] {issue.plan_path}: {issue.explanation} Fix: {issue.suggested_fix}"
    return one_line(text, JUDGE_TEXT_MAX_CHARS)


def _probe_event(probe: ProbeResult, attempt: int | None) -> dict[str, Any]:
    """The trace event for one probe."""
    event: dict[str, Any] = {"step": "probe", "verdict": probe.verdict, "totals": probe.totals}
    event |= {"feedback": probe.feedback, "warnings": probe.warnings}
    return event if attempt is None else {"attempt": attempt, **event}


def _judge_event(review: JudgeReview, attempt: int) -> dict[str, Any]:
    """The trace event for one judge review (the model's own verdict recorded, not trusted)."""
    return {
        "attempt": attempt,
        "step": "judge",
        "available": review.available,
        "needs_revision": review.needs_revision,
        "model_verdict": review.model_verdict,
        "confidence": review.confidence,
        "failed_checks": list(review.failed_checks),
        "issues": [_issue_text(i) for i in review.issues],
        "discarded_structured_slot_issues": review.discarded,
    }


async def _review(
    deps: _Deps, attempt: _Attempt, totals: dict[str, int], budget_s: float
) -> JudgeReview:
    """Ask the judge off the event loop; no judge configured reads as unavailable (fail open)."""
    if deps.judge is None:
        return JudgeReview(needs_revision=False, issues=(), model=None, available=False)
    return await asyncio.to_thread(
        deps.judge.review,
        deps.judged,
        attempt.plan,
        attempt.overrides,
        totals,
        deps.today,
        budget_s,
    )


def _judge_budget_left(previous: _Attempt | None) -> float:
    """Fix B: both reviews share one per-request judge budget (see config.JUDGE_*)."""
    spent = previous.review.elapsed_s if previous and previous.review else 0.0
    return JUDGE_REQUEST_BUDGET_S - spent


def _judge_stage(review: JudgeReview) -> Stage:
    """Where a judged attempt stopped: unavailable, revise (code's rule), or pass."""
    if not review.available:
        return "judge_unavailable"
    return "judge_revise" if review.needs_revision else "judge_pass"


async def _probe_and_judge(deps: _Deps, attempt: _Attempt, budget_s: float) -> _Attempt:
    """Gates 3-4: probe every cohort (§9.1), then judge the plan against the totals (§9.2)."""
    probe = await probe_plan(
        attempt.plan, deps.client, deps.request.options.max_records, attempt.number
    )
    events = [*attempt.events, _probe_event(probe, attempt.number)]
    if probe.verdict == "revise":
        feedback = [f"probe: {line}" for line in probe.feedback]
        return dataclasses.replace(
            attempt, stage="probe_revise", events=events, feedback=feedback, probe=probe
        )
    if probe.verdict == "no_matches":
        return dataclasses.replace(attempt, stage="no_matches", events=events, probe=probe)
    review = await _review(deps, attempt, probe.totals, budget_s)
    feedback = [
        f"judge: {_issue_text(i)}" for i in review.issues if i.severity in REVISING_SEVERITIES
    ]
    return dataclasses.replace(
        attempt,
        stage=_judge_stage(review),
        events=[*events, _judge_event(review, attempt.number)],
        feedback=feedback,
        probe=probe,
        review=review,
    )


async def _try_attempt(deps: _Deps, number: int, previous: _Attempt | None) -> _Attempt:
    """One pass: plan (with any feedback) -> overlay -> scope -> checks -> probe -> judge."""
    feedback = previous.feedback if previous else None
    prior_plan = previous.raw_plan if previous else None
    raw = await asyncio.to_thread(deps.planner.plan, deps.request, feedback, prior_plan, deps.today)
    plan, overrides = apply_overlay(raw, deps.request)
    plan_event = {
        "attempt": number,
        "step": "plan",
        "answerable": plan.answerable,
        "overrides": len(overrides),
    }
    if not plan.answerable:
        return _Attempt(number, "out_of_scope", plan, raw, overrides, [plan_event])
    plan, notes = normalize_plan(plan)
    errors = check_plan(plan, deps.today) or check_executable(plan)
    checks = {"attempt": number, "step": "checks", "ok": not errors, "errors": errors}
    events = [plan_event, {**checks, "adjustments": notes}]
    attempt = _Attempt(number, "checks_failed", plan, raw, overrides, events, notes)
    if errors:
        return dataclasses.replace(
            attempt, feedback=[f"plan check: {e}" for e in errors], errors=errors
        )
    return await _probe_and_judge(deps, attempt, _judge_budget_left(previous))


def _trace(attempts: list[_Attempt], ending: dict[str, Any]) -> list[dict[str, Any]]:
    """Every attempt's events in order, then the named ending."""
    return [*(e for attempt in attempts for e in attempt.events), {"step": "outcome", **ending}]


def _execute(
    deps: _Deps,
    attempt: _Attempt,
    status: JudgeStatus,
    attempts: list[_Attempt],
    disclosures: tuple[str, ...] = (),
) -> PlanningOutcome:
    """An executable ending: the digit-guarded plan plus everything `meta.validation` shows."""
    plan, guard_notes = guard_text(attempt.plan, deps.request)
    review, judge = attempt.review, deps.judge
    issues = [_issue_text(i) for i in review.issues] if review and status in _FLAGGED else []
    return PlanningOutcome(
        plan=plan,
        error_code=None,
        judge_status=status,
        executed_attempt=attempt.number,
        trace=_trace(attempts, {"status": status, "executed_attempt": attempt.number}),
        overrides=attempt.overrides,
        adjustments=[*attempt.notes, *guard_notes],
        probe_totals=attempt.probe.totals if attempt.probe else {},
        judge_issues=issues,
        judge_model=judge.model_name if judge else None,
        same_family=same_family(deps.planner.model_name, judge.model_name) if judge else False,
        warnings=[*(attempt.probe.warnings if attempt.probe else []), *disclosures],
    )


def _no_matches(
    plan: QueryPlan, probe: ProbeResult, trace: list[dict[str, Any]]
) -> PlanningOutcome:
    """NO_MATCHING_TRIALS: every cohort empty after the revise, with what the user could drop."""
    drop = droppable_filters(plan)
    return PlanningOutcome(
        plan=None,
        error_code="NO_MATCHING_TRIALS",
        judge_status="skipped",
        executed_attempt=0,
        trace=trace,
        message=NO_MATCHES_MESSAGE + ("; ".join(drop) or "nothing -- the plan has no filters"),
        details={
            "probe": [{"cohort": c, "total": t} for c, t in probe.totals.items()],
            "filters_to_drop": drop,
        },
    )


def _refusal(code: PlanningError, last: _Attempt, attempts: list[_Attempt]) -> PlanningOutcome:
    """An ok:false ending (OUT_OF_SCOPE / PLAN_INVALID / NO_MATCHING_TRIALS); judge not trusted."""
    trace = _trace(attempts, {"error_code": code})
    if code == "NO_MATCHING_TRIALS" and last.probe is not None:
        return _no_matches(last.plan, last.probe, trace)
    plan = last.plan if code == "OUT_OF_SCOPE" else None
    return PlanningOutcome(plan, code, "skipped", 0, trace, errors=last.errors)


def _conclude(deps: _Deps, first: _Attempt, last: _Attempt) -> PlanningOutcome:
    """Map (attempt 1, last attempt) onto exactly one named §9.4 ending."""
    attempts = [first] if last is first else [first, last]
    if last.stage == "out_of_scope":
        return _refusal("OUT_OF_SCOPE", last, attempts)
    if last is first:
        return _execute(deps, first, _FIRST_ATTEMPT_ENDINGS[first.stage], attempts)
    if last.stage in _SECOND_ATTEMPT_ENDINGS:
        return _execute(deps, last, _SECOND_ATTEMPT_ENDINGS[last.stage], attempts)
    too_broad = _only_too_broad(first)
    if last.stage == "no_matches" and not too_broad:
        return _refusal("NO_MATCHING_TRIALS", last, attempts)
    if first.stage == "judge_revise":  # plan 1 passed its checks and its probe
        return _execute(deps, first, "executed_previous_plan", attempts)
    if too_broad:  # fix E: plan 1 passed its checks; its probe only found it too broad
        disclosures = _too_broad_disclosures(first, last, deps.request.options.max_records)
        return _execute(deps, first, "executed_previous_plan", attempts, disclosures)
    return _refusal("PLAN_INVALID", last, attempts)


def _only_too_broad(attempt: _Attempt) -> bool:
    """Plan passed its checks and its probe objected only to size (no cohort was empty)."""
    probe = attempt.probe
    return (
        attempt.stage == "probe_revise"
        and probe is not None
        and all(total > 0 for total in probe.totals.values())
    )


def _too_broad_disclosures(first: _Attempt, last: _Attempt, cap: int) -> tuple[str, ...]:
    """Fix E: why plan 1 runs, that it is over the cap, and that no judge ever reviewed it."""
    why = "matched no trials" if last.stage == "no_matches" else "failed its plan checks"
    totals = first.probe.totals if first.probe else {}
    over = [
        f"{total:,} trials for cohort '{label}' exceed the {cap:,}-record cap; the most recent "
        f"{cap:,} by start date are analyzed"
        for label, total in totals.items()
        if total > cap
    ]
    note = (
        f"The first plan was executed because its revision {why}; it was sent back only for "
        "being too broad, so it was not reviewed by the judge."
    )
    return (note, *over)


async def _fast_path(deps: _Deps, nct_ids: list[str]) -> PlanningOutcome:
    """§8.4: a code-written lookup plan, probed (an unknown ID is NO_MATCHING_TRIALS), no LLM."""
    plan = fast_path_plan(nct_ids)
    probe = await probe_plan(plan, deps.client, deps.request.options.max_records, SECOND_ATTEMPT)
    events = [{"step": "fast_path", "nct_ids": nct_ids}, _probe_event(probe, None)]
    if probe.verdict == "no_matches":
        return _no_matches(
            plan, probe, [*events, {"step": "outcome", "error_code": "NO_MATCHING_TRIALS"}]
        )
    ending = {"step": "outcome", "status": "skipped", "executed_attempt": 1}
    return PlanningOutcome(plan, None, "skipped", 1, [*events, ending], probe_totals=probe.totals)


def _with_query_nct_ids(request: VisualizeRequest) -> VisualizeRequest:
    """§8.4 "otherwise": NCT IDs found in the query are pre-filled as the structured filter."""
    nct_ids = nct_ids_in(request)
    if request.nct_ids is not None or not nct_ids:
        return request
    return request.model_copy(update={"nct_ids": nct_ids})


async def orchestrate(
    request: VisualizeRequest,
    *,
    planner: Planner,
    judge: Judge | None,
    client: ProbeClient,
    today: date,
) -> PlanningOutcome:
    """Run the §9.4 revise loop (or the §8.4 fast path) to exactly one named ending."""
    fast_ids = is_fast_path(request)
    deps = _Deps(_with_query_nct_ids(request), planner, judge, client, today, request)
    if fast_ids is not None:
        return await _fast_path(deps, fast_ids)
    first = await _try_attempt(deps, 1, previous=None)
    if first.stage in _FIRST_ATTEMPT_ENDINGS or first.stage == "out_of_scope":
        return _conclude(deps, first, first)
    second = await _try_attempt(deps, 2, previous=first)
    return _conclude(deps, first, second)
