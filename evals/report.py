"""Rendering `evals/report.md`: the §16.4 target table, per-case tables and explained failures."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from evals.judge_scoring import (
    JudgeCaseResult,
    JudgeEvalSummary,
    answers_by_model,
    judge_availability,
)
from evals.scoring import (
    JUDGE_FALLBACK_INPUT_PER_M,
    JUDGE_FALLBACK_OUTPUT_PER_M,
    JUDGE_INPUT_PER_M,
    JUDGE_OUTPUT_PER_M,
    PLANNER_CACHED_INPUT_PER_M,
    PLANNER_INPUT_PER_M,
    PLANNER_OUTPUT_PER_M,
    CaseResult,
    PlanEvalSummary,
)

NO_NOTE = "_No explanation recorded for this run yet._"
NO_TARGET = "reported (no §16.4 target)"


@dataclass(frozen=True)
class RunInfo:
    """Provenance of one eval run, plus hand-written failure explanations keyed by case id."""

    started: str
    planner_model: str
    judge_model: str
    label: str
    notes: Mapping[str, str] = field(default_factory=dict)
    judge_fallback_model: str = ""


@dataclass(frozen=True)
class _Target:
    """One §16.4 row: a measured value, how to print it, and whether it meets the target
    (`is_met` None = an informational row with no target, never shown as MET)."""

    name: str
    value: float | None
    shown: str
    target: str
    is_met: Callable[[float], bool] | None


def _cell(text: object) -> str:
    """A markdown table cell: pipes escaped, line breaks flattened."""
    return " ".join(str(text).split()).replace("|", "\\|")


def _pct(value: float | None) -> str:
    """A rate as a percentage, or n/a."""
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _ratio(value: float | None, hits: int, total: int) -> str:
    """A rate with the counts behind it, e.g. "90.0% (9/10)"."""
    return f"{_pct(value)} ({hits}/{total})"


def _targets(
    plans: PlanEvalSummary, judge: JudgeEvalSummary, availability: tuple[int, int]
) -> list[_Target]:
    """Every §16.4 metric with its measured value and target predicate. Judge rates count
    every labeled case (an unavailable review is a miss); availability is its own row."""
    avail, reviews = availability
    avail_rate = avail / reviews if reviews else None
    return [
        _Target("Plan accuracy, attempt 1", plans.attempt1_accuracy, _pct(plans.attempt1_accuracy),
                "≥ 85%", lambda v: v >= 0.85),
        _Target("Plan accuracy, after revise", plans.final_accuracy, _pct(plans.final_accuracy),
                "≥ 95%", lambda v: v >= 0.95),
        _Target("End-to-end success (expected outcome)", plans.e2e_success,
                _pct(plans.e2e_success), "≥ 95%", lambda v: v >= 0.95),
        _Target("Citation check pass rate", plans.citation_pass_rate,
                _pct(plans.citation_pass_rate), "100%", lambda v: v == 1.0),
        _Target("Judge catch rate (unavailable = miss)", judge.catch_rate,
                _ratio(judge.catch_rate, judge.caught, judge.n_bad), "≥ 90%",
                lambda v: v >= 0.90),
        _Target("Judge false-alarm rate", judge.false_alarm_rate,
                _ratio(judge.false_alarm_rate, judge.false_alarms, judge.n_good), "≤ 15%",
                lambda v: v <= 0.15),
        _Target("Judge availability (available reviews ÷ reviews)", avail_rate,
                _ratio(avail_rate, avail, reviews), NO_TARGET, None),
        _Target("p50 latency", plans.p50_s, f"{plans.p50_s:.1f} s", "≤ 9 s", lambda v: v <= 9),
        _Target("p95 latency", plans.p95_s, f"{plans.p95_s:.1f} s", "≤ 30 s", lambda v: v <= 30),
        _Target("Mean LLM cost per request", plans.mean_cost_usd, f"${plans.mean_cost_usd:.4f}",
                "≤ $0.01", lambda v: v <= 0.01),
    ]  # fmt: skip


def _status(t: _Target) -> str:
    """MET / MISS against the target; INFO for a row without one; N/A without a value."""
    if t.is_met is None:
        return "INFO"
    if t.value is None:
        return "N/A"
    return "MET" if t.is_met(t.value) else "MISS"


def _target_section(
    plans: PlanEvalSummary, judge: JudgeEvalSummary, availability: tuple[int, int]
) -> list[str]:
    """The §16.4 comparison table."""
    lines = [
        "## Targets (PLAN.md §16.4)",
        "",
        "| Metric | Result | Target | Status |",
        "|---|---|---|---|",
    ]
    for t in _targets(plans, judge, availability):
        lines.append(f"| {t.name} | {t.shown} | {t.target} | {_status(t)} |")
    return lines


def _cost_section(plans: PlanEvalSummary, judge: JudgeEvalSummary) -> list[str]:
    """Call counts, spend and the pricing assumption behind every dollar figure."""
    return [
        "## Cost and calls",
        "",
        f"- Planner cases: {plans.planner_calls} planner calls, {plans.judge_calls} judge reviews "
        f"(one review may make several API calls: a 429 retry, a tier-2 fallback), "
        f"${plans.total_cost_usd:.4f} total (${plans.mean_cost_usd:.4f} per request).",
        f"- Judge cases: {judge.reviews} reviews, ${judge.total_cost_usd:.4f} total.",
        "- Pricing assumption (USD per 1M tokens, from PLAN.md §4.5/§9.5): planner gpt-5.4-mini "
        f"${PLANNER_INPUT_PER_M:.2f} input / ${PLANNER_CACHED_INPUT_PER_M:.3f} cached input "
        f"(assumed 10% of input) / ${PLANNER_OUTPUT_PER_M:.2f} output (reasoning tokens "
        f"included); judge tier 1 gemini-2.5-flash-lite ${JUDGE_INPUT_PER_M:.2f} input / "
        f"${JUDGE_OUTPUT_PER_M:.2f} output; judge tier 2 claude-haiku-4.5 "
        f"${JUDGE_FALLBACK_INPUT_PER_M:.2f} input / ${JUDGE_FALLBACK_OUTPUT_PER_M:.2f} output. "
        "Token counts are the SDKs' own `usage` fields, read from every response received -- "
        "including ones that failed schema validation; a call that errored before any "
        "response has no usage and is not priced.",
        "- Latency is wall-clock per HTTP request, live ClinicalTrials.gov fetches included; "
        "percentiles are nearest-rank.",
    ]


def _mark(ok: bool) -> str:
    """A pass/fail mark for a table cell."""
    return "pass" if ok else "FAIL"


def _case_row(r: CaseResult) -> str:
    """One planner case as a table row."""
    outcome = f"{r.expected_outcome} → {r.actual_outcome}"
    cells = [
        r.id, r.klass, r.query, _mark(r.attempt1_correct), _mark(r.final_correct),
        f"{outcome} ({_mark(r.outcome_ok)})", r.judge_status or "-",
        "-" if r.citation_passed is None else _mark(r.citation_passed),
        f"{r.planner_calls}/{r.judge_calls}", f"{r.latency_s:.1f} s", f"${r.cost_usd:.4f}",
    ]  # fmt: skip
    return "| " + " | ".join(_cell(c) for c in cells) + " |"


def _case_section(results: Sequence[CaseResult]) -> list[str]:
    """The per-case planner table."""
    header = (
        "| Case | Class | Query | Plan @1 | Plan after revise | Outcome (expected → actual) "
        "| Judge status | Citations | Planner calls / judge reviews | Latency | Cost |"
    )
    return ["## Planner cases", "", header, "|" + "---|" * 11, *map(_case_row, results)]


def _judge_row(r: JudgeCaseResult) -> str:
    """One judge case as a table row."""
    flagged = "unavailable" if not r.available else ("yes" if r.flagged else "no")
    right_check = "yes" if r.expected_check_failed else "no"
    cells = [
        r.id,
        r.category,
        r.label,
        flagged,
        _mark(r.correct),
        ", ".join(r.failed_checks) or "-",
        right_check,
    ]
    return "| " + " | ".join(_cell(c) for c in cells) + " |"


def _answered_by(answers: Mapping[str, int]) -> str:
    """Which judge model answered how many available reviews (tier 1 vs tier 2)."""
    shown = ", ".join(f"{model}: {n}" for model, n in answers.items()) or "none"
    return f"Available reviews answered by model (planner cases + judge cases): {shown}."


def _judge_section(
    results: Sequence[JudgeCaseResult], s: JudgeEvalSummary, answers: Mapping[str, int]
) -> list[str]:
    """The per-category judge table (flagged = the code-recomputed revise decision)."""
    return [
        "## Judge cases",
        "",
        f"Catch rate {_pct(s.catch_rate)} ({s.caught}/{s.n_bad} labeled-bad flagged); false-alarm "
        f"rate {_pct(s.false_alarm_rate)} ({s.false_alarms}/{s.n_good} labeled-good flagged); "
        f"{s.unavailable} of {s.reviews} reviews unavailable -- each counts as a miss on a bad "
        "plan and as not flagged on a good one (see the availability row). 'Flagged' is the "
        "code-side decision: a critical/major issue survived the structured-slot filter.",
        "",
        _answered_by(answers),
        "",
        "| Case | Category | Label | Flagged | Correct | Failed checks | Expected check failed |",
        "|---|---|---|---|---|---|---|",
        *map(_judge_row, results),
    ]


def _case_failure(r: CaseResult, notes: Mapping[str, str]) -> list[str]:
    """One failing planner case: every failed property, the outcome, and its explanation."""
    lines = [f"### {r.id} ({r.klass})", "", f"- Query: {r.query}"]
    lines += [f"- Attempt 1: {f}" for f in r.attempt1_failures]
    lines += [f"- After revise: {f}" for f in r.final_failures]
    if not r.outcome_ok:
        lines.append(f"- Outcome: {r.outcome_note}")
    if r.citation_passed is False:
        lines.append("- Citation check FAILED (a bug by definition)")
    return [*lines, f"- Explanation: {notes.get(r.id, NO_NOTE)}", ""]


def _judge_failure(r: JudgeCaseResult, notes: Mapping[str, str]) -> list[str]:
    """One judge case scored wrong (or unavailable), with its issues and explanation."""
    verdict = "unavailable" if not r.available else ("flagged" if r.flagged else "not flagged")
    lines = [f"### {r.id} (judge, {r.category}, labeled {r.label})", "", f"- Judge: {verdict}"]
    lines += [f"- Issue: {i}" for i in r.issues]
    return [*lines, f"- Explanation: {notes.get(r.id, NO_NOTE)}", ""]


def _failure_section(
    cases: Sequence[CaseResult], judge: Sequence[JudgeCaseResult], notes: Mapping[str, str]
) -> list[str]:
    """Every failure with its explanation; says so when there are none."""
    bad_cases = [
        r for r in cases
        if not (r.attempt1_correct and r.final_correct and r.outcome_ok)
        or r.citation_passed is False
    ]  # fmt: skip
    bad_judge = [r for r in judge if not r.correct]
    lines = ["## Failures", ""]
    if not bad_cases and not bad_judge:
        return [*lines, "None."]
    for r in bad_cases:
        lines += _case_failure(r, notes)
    for j in bad_judge:
        lines += _judge_failure(j, notes)
    return lines


def render_report(
    info: RunInfo,
    cases: Sequence[CaseResult],
    plans: PlanEvalSummary,
    judge: Sequence[JudgeCaseResult],
    judge_summary: JudgeEvalSummary,
) -> str:
    """The full markdown report for one eval run."""
    fallback = f" → tier 2 `{info.judge_fallback_model}`" if info.judge_fallback_model else ""
    header = [
        "# ctviz eval report",
        "",
        f"Generated {info.started} by `uv run python -m evals.run_evals` · prompts: "
        f"**{info.label}** · planner `{info.planner_model}` · judge `{info.judge_model}`"
        f"{fallback} · {plans.n} planner cases, {len(judge)} judge cases.",
    ]
    availability = judge_availability(cases, judge)
    sections = [
        header,
        _target_section(plans, judge_summary, availability),
        _cost_section(plans, judge_summary),
        _case_section(cases),
        _judge_section(judge, judge_summary, answers_by_model(cases, judge)),
        _failure_section(cases, judge, info.notes),
    ]
    return "\n\n".join("\n".join(section) for section in sections).rstrip() + "\n"
