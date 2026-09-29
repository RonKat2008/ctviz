"""Report rendering: the §16.4 target table, per-case table, judge section and failure notes."""

import dataclasses
from typing import Any

from evals.cases import EvalCase, JudgeCase
from evals.judge_scoring import score_judge_case, summarize_judge
from evals.report import RunInfo, render_report
from evals.scoring import CaseRun, Usage, score_case, summarize_cases

from ctviz.agent.judge import JudgeReview
from tests.factories import make_plan

INFO = RunInfo(
    started="2026-09-29T10:00:00Z",
    planner_model="gpt-5.4-mini",
    judge_model="google/gemini-2.5-flash-lite",
    label="v1",
    notes={"bad_case": "The planner put the drug on query.cond; fixed in v2."},
)


def _case(case_id: str, expect: dict[str, Any]) -> EvalCase:
    return EvalCase(
        id=case_id,
        klass="phase_distribution",
        request={"query": "Pembrolizumab | trials by phase"},
        expect=expect,
        outcome="ok",
        planner_calls=None,
        note="",
    )


def _results() -> list[Any]:
    plan = make_plan()
    payload = {
        "ok": True,
        "error": None,
        "meta": {
            "plan": plan.model_dump(mode="json"),
            "citation_check": {"passed": True},
            "validation": {"executed_attempt": 1, "judge": {"status": "passed"}},
        },
    }
    usage = (Usage(role="planner", input_tokens=5000, output_tokens=500),)
    good = CaseRun(
        _case("good_case", {"analysis.kind": "count_by"}), 200, payload, (plan,), usage, 1, 3.0
    )
    bad = CaseRun(
        _case("bad_case", {"analysis.kind": "time_trend"}), 200, payload, (plan,), usage, 1, 4.0
    )
    return [score_case(good), score_case(bad)]


def _judge() -> list[Any]:
    case = JudgeCase("jc1", "viz_fit", "bad", {"query": "q"}, {}, {}, "")
    review = JudgeReview(needs_revision=False, issues=(), model="m", available=True)
    return [score_judge_case(case, review)]


def _render() -> str:
    cases = _results()
    judge = _judge()
    return render_report(INFO, cases, summarize_cases(cases), judge, summarize_judge(judge))


def test_report_has_every_section_and_the_pricing_assumption() -> None:
    text = _render()

    for heading in (
        "## Targets (PLAN.md §16.4)",
        "## Planner cases",
        "## Judge cases",
        "## Failures",
    ):
        assert heading in text
    assert "$0.75" in text and "$4.50" in text and "$0.10" in text
    assert "gpt-5.4-mini" in text and "v1" in text


def test_report_marks_targets_met_and_missed() -> None:
    text = _render()

    assert "| Plan accuracy, attempt 1 | 50.0% | ≥ 85% | MISS |" in text
    assert "| Citation check pass rate | 100.0% | 100% | MET |" in text
    assert "| Judge catch rate (unavailable = miss) | 0.0% (0/1) | ≥ 90% | MISS |" in text


def test_report_lists_failures_with_their_explanation_note() -> None:
    text = _render()
    failures = text.split("## Failures", 1)[1]

    assert "bad_case" in failures
    assert "expected 'time_trend', got 'count_by'" in failures
    assert "fixed in v2" in failures
    assert "good_case" not in failures
    assert "jc1" in failures


def test_report_escapes_pipes_in_table_cells() -> None:
    assert "Pembrolizumab \\| trials" in _render()


def _render_with(judge: list[Any], **plan_fields: Any) -> str:
    cases = _results()
    plans = dataclasses.replace(summarize_cases(cases), **plan_fields)
    return render_report(INFO, cases, plans, judge, summarize_judge(judge))


def test_a_citation_pass_rate_below_one_hundred_percent_is_a_miss() -> None:
    """M11: the target is exactly 100% -- 99% is a bug, never MET."""
    text = _render_with(_judge(), citation_pass_rate=0.99)

    assert "| Citation check pass rate | 99.0% | 100% | MISS |" in text


def _judged(label: str, *, available: bool, flagged: bool, model: str = "m") -> Any:
    case = JudgeCase(f"{label}_{available}_{flagged}", "viz_fit", label, {"query": "q"}, {}, {}, "")  # type: ignore[arg-type]
    review = JudgeReview(needs_revision=flagged, issues=(), model=model, available=available)
    return score_judge_case(case, review)


def test_an_unavailable_bad_review_is_a_miss_so_the_catch_rate_is_not_met() -> None:
    judge = [_judged("bad", available=True, flagged=True) for _ in range(9)]
    judge.append(_judged("bad", available=False, flagged=False))

    text = _render_with(judge)

    assert "| Judge catch rate (unavailable = miss) | 90.0% (9/10) | ≥ 90% | MET |" in text
    judge.append(_judged("bad", available=False, flagged=False))
    assert "| Judge catch rate (unavailable = miss) | 81.8% (9/11) | ≥ 90% | MISS |" in (
        _render_with(judge)
    )


def test_the_target_table_has_a_judge_availability_row_over_every_review() -> None:
    judge = [
        _judged("bad", available=True, flagged=True),
        _judged("good", available=False, flagged=False),
    ]

    text = _render_with(judge)

    assert (
        "| Judge availability (available reviews ÷ reviews) | 50.0% (1/2) | "
        "reported (no §16.4 target) | INFO |"
    ) in text


def test_the_report_counts_reviews_not_calls_and_names_the_model_that_answered() -> None:
    judge = [
        _judged("bad", available=True, flagged=True, model="anthropic/claude-haiku-4.5"),
        _judged("good", available=True, flagged=False, model="google/gemini-2.5-flash-lite"),
    ]

    text = _render_with(judge)

    assert "| Planner calls / judge reviews |" in text
    assert "Judge cases: 2 reviews" in text
    assert "anthropic/claude-haiku-4.5: 1" in text and "google/gemini-2.5-flash-lite: 1" in text
