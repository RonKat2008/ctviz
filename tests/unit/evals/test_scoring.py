"""Eval scoring: per-case outcome/plan accuracy, cost, percentiles, and the judge rates (§16.4)."""

from typing import Any

import pytest
from evals.cases import EvalCase, JudgeCase
from evals.judge_scoring import judge_availability, score_judge_case, summarize_judge
from evals.scoring import (
    CaseRun,
    Usage,
    cost_usd,
    percentile,
    score_case,
    summarize_cases,
)

from ctviz.agent.judge import JudgeReview
from ctviz.schemas.judge import JudgeIssue
from tests.factories import make_plan

PHASE_EXPECT = {
    "search_terms": [{"param": "query.intr", "value_contains": "pembrolizumab"}],
    "analysis.group_by": "phase",
}


def _case(**overrides: Any) -> EvalCase:
    base: dict[str, Any] = {
        "id": "phase_pembro",
        "klass": "phase_distribution",
        "request": {"query": "Pembrolizumab trials by phase"},
        "expect": PHASE_EXPECT,
        "outcome": "ok",
        "planner_calls": None,
        "note": "",
    }
    return EvalCase(**(base | overrides))


def _ok_payload(plan: dict[str, Any], *, citation_passed: bool = True) -> dict[str, Any]:
    return {
        "ok": True,
        "error": None,
        "meta": {
            "plan": plan,
            "citation_check": {"passed": citation_passed},
            "validation": {"executed_attempt": 1, "judge": {"status": "passed"}},
        },
    }


def _error_payload(code: str) -> dict[str, Any]:
    return {"ok": False, "meta": None, "error": {"code": code, "message": "m"}}


def _run(case: EvalCase, payload: dict[str, Any], raw: tuple[Any, ...], **kw: Any) -> CaseRun:
    base: dict[str, Any] = {
        "case": case,
        "http_status": 200,
        "payload": payload,
        "raw_plans": raw,
        "usages": (),
        "judge_calls": 1,
        "latency_s": 2.0,
    }
    return CaseRun(**(base | kw))


def test_score_case_correct_first_attempt_and_ok_outcome() -> None:
    plan = make_plan()

    result = score_case(_run(_case(), _ok_payload(plan.model_dump(mode="json")), (plan,)))

    assert result.attempt1_correct and result.final_correct
    assert result.outcome_ok and result.actual_outcome == "ok"
    assert result.citation_passed is True
    assert result.judge_status == "passed"
    assert result.planner_calls == 1


def test_score_case_wrong_first_plan_fixed_by_the_revise_loop() -> None:
    wrong = make_plan(
        search_terms=[
            {
                "param": "query.cond",
                "value": "pembrolizumab",
                "source": "query_text",
                "rationale": "",
            }
        ]
    )
    right = make_plan()
    payload = _ok_payload(right.model_dump(mode="json"))

    result = score_case(_run(_case(), payload, (wrong, right)))

    assert not result.attempt1_correct
    assert result.final_correct
    assert result.planner_calls == 2
    assert "query.intr" in result.attempt1_failures[0]


def test_score_case_applies_the_overlay_to_the_first_attempt_plan() -> None:
    case = _case(request={"query": "Trials for this drug by phase", "drug_name": "Pembrolizumab"})
    raw = make_plan(search_terms=[])
    overlaid_final = make_plan().model_dump(mode="json")

    result = score_case(_run(case, _ok_payload(overlaid_final), (raw,)))

    assert result.attempt1_correct, result.attempt1_failures


def test_score_case_out_of_scope_expected_and_received() -> None:
    case = _case(expect={"answerable": False}, outcome="OUT_OF_SCOPE", klass="out_of_scope")
    raw = make_plan(answerable=False, analysis=None, visualization=None, search_terms=[])

    result = score_case(_run(case, _error_payload("OUT_OF_SCOPE"), (raw,), judge_calls=0))

    assert result.attempt1_correct and result.final_correct and result.outcome_ok
    assert result.citation_passed is None


def test_score_case_model_refusal_counts_as_answerable_false() -> None:
    case = _case(expect={"answerable": False}, outcome="OUT_OF_SCOPE")

    result = score_case(_run(case, _error_payload("OUT_OF_SCOPE"), (None,)))

    assert result.attempt1_correct and result.final_correct


def test_score_case_fast_path_uses_the_executed_plan_for_both_attempts() -> None:
    case = _case(expect={"analysis.kind": "trial_lookup"}, planner_calls=(0,))
    plan = make_plan(analysis={**make_plan().analysis.model_dump(), "kind": "trial_lookup"})

    result = score_case(_run(case, _ok_payload(plan.model_dump(mode="json")), (), judge_calls=0))

    assert result.attempt1_correct and result.final_correct and result.outcome_ok
    assert result.planner_calls == 0


def test_score_case_planner_call_bound_violation_fails_the_outcome() -> None:
    case = _case(outcome="NO_MATCHING_TRIALS", planner_calls=(2,))
    plan = make_plan()

    result = score_case(_run(case, _error_payload("NO_MATCHING_TRIALS"), (plan,)))

    assert not result.outcome_ok
    assert "planner calls" in result.outcome_note


def test_score_case_wrong_error_code_fails_the_outcome() -> None:
    result = score_case(
        _run(_case(), _error_payload("LLM_UNAVAILABLE"), (), http_status=503, judge_calls=0)
    )

    assert not result.outcome_ok
    assert result.actual_outcome == "LLM_UNAVAILABLE"
    assert result.final_failures == ("no plan was produced",)


def test_cost_usd_prices_planner_and_judge_tokens() -> None:
    usages = [
        Usage(role="planner", input_tokens=1_000_000, output_tokens=1_000_000, cached_tokens=0),
        Usage(role="judge", input_tokens=1_000_000, output_tokens=1_000_000),
    ]

    assert cost_usd(usages) == pytest.approx(0.75 + 4.50 + 0.10 + 0.40)


def test_cost_usd_prices_cached_planner_input_at_the_cached_rate() -> None:
    usage = Usage(role="planner", input_tokens=1_000_000, output_tokens=0, cached_tokens=1_000_000)

    assert cost_usd([usage]) == pytest.approx(0.075)


def test_percentile_is_nearest_rank() -> None:
    values = [float(v) for v in range(1, 13)]

    assert percentile(values, 50) == 6.0
    assert percentile(values, 95) == 12.0
    assert percentile([3.0], 95) == 3.0


def test_summarize_cases_rates_and_citation_only_over_ok_responses() -> None:
    plan = make_plan()
    good = score_case(_run(_case(), _ok_payload(plan.model_dump(mode="json")), (plan,)))
    wrong = make_plan(search_terms=[])
    bad = score_case(_run(_case(), _error_payload("PLAN_INVALID"), (wrong,), latency_s=10.0))

    summary = summarize_cases([good, bad])

    assert summary.attempt1_accuracy == 0.5
    assert summary.final_accuracy == 0.5
    assert summary.e2e_success == 0.5
    assert summary.citation_pass_rate == 1.0 and summary.citation_checked == 1
    assert summary.p50_s == 2.0 and summary.p95_s == 10.0


def _judge_case(label: str, category: str) -> JudgeCase:
    return JudgeCase(
        id=f"{category}_{label}",
        category=category,
        label=label,  # type: ignore[arg-type]
        request={"query": "q"},
        plan={},
        probe_totals={},
        note="",
    )


def _review(*, flagged: bool, available: bool = True) -> JudgeReview:
    issue = JudgeIssue(
        severity="major",
        category="wrong_param",
        plan_path="search_terms[0].param",
        evidence_quote="search_terms",
        explanation="e",
        suggested_fix="f",
    )
    return JudgeReview(
        needs_revision=flagged,
        issues=(issue,) if flagged else (),
        model="m",
        available=available,
        failed_checks=("filter_fidelity",) if flagged else (),
    )


def test_score_judge_case_catch_and_expected_check() -> None:
    result = score_judge_case(_judge_case("bad", "filter_fidelity"), _review(flagged=True))

    assert result.flagged and result.correct and result.expected_check_failed


def test_catch_rate_counts_an_unavailable_bad_review_as_a_miss() -> None:
    results = [
        score_judge_case(_judge_case("bad", "filter_fidelity"), _review(flagged=True)),
        score_judge_case(_judge_case("bad", "viz_fit"), _review(flagged=False)),
        score_judge_case(_judge_case("bad", "time_range"), _review(flagged=False, available=False)),
    ]

    summary = summarize_judge(results)

    assert (summary.caught, summary.n_bad) == (1, 3)
    assert summary.catch_rate == pytest.approx(1 / 3)
    assert summary.unavailable == 1


def test_false_alarm_rate_is_over_good_cases_only_with_asymmetric_counts() -> None:
    """M4: 3 bad (2 flagged) vs 4 good (1 flagged) -- any mix-up of the two sides shows."""
    bad = [_review(flagged=True), _review(flagged=True), _review(flagged=False)]
    good = [_review(flagged=True), *(_review(flagged=False) for _ in range(3))]
    results = [score_judge_case(_judge_case("bad", "viz_fit"), r) for r in bad]
    results += [score_judge_case(_judge_case("good", "clean"), r) for r in good]

    summary = summarize_judge(results)

    assert summary.catch_rate == pytest.approx(2 / 3)
    assert (summary.false_alarms, summary.n_good) == (1, 4)
    assert summary.false_alarm_rate == pytest.approx(1 / 4)


def test_judge_availability_is_available_reviews_over_all_reviews() -> None:
    results = [
        score_judge_case(_judge_case("bad", "viz_fit"), _review(flagged=True)),
        score_judge_case(_judge_case("good", "clean"), _review(flagged=False, available=False)),
    ]

    summary = summarize_judge(results)

    assert (summary.available, summary.reviews) == (1, 2)
    assert summary.availability == 0.5


def test_an_unavailable_review_is_never_correct() -> None:
    """M6: an unavailable review of a good plan is not flagged, but it is still not correct."""
    good = score_judge_case(_judge_case("good", "clean"), _review(flagged=False, available=False))
    bad = score_judge_case(_judge_case("bad", "viz_fit"), _review(flagged=False, available=False))

    assert good.correct is False
    assert bad.correct is False


def test_flagged_requires_an_available_review() -> None:
    """M14: needs_revision=True on an unavailable review is not a flag."""
    review = JudgeReview(needs_revision=True, issues=(), model="m", available=False)

    result = score_judge_case(_judge_case("bad", "viz_fit"), review)

    assert result.flagged is False


def test_score_judge_case_records_the_model_that_answered() -> None:
    review = JudgeReview(
        needs_revision=False, issues=(), model="anthropic/claude-haiku-4.5", available=True
    )

    result = score_judge_case(_judge_case("good", "clean"), review)

    assert result.model == "anthropic/claude-haiku-4.5"


def _judge_event(attempt: int, *, available: bool, flagged: bool, model: str) -> dict[str, Any]:
    return {
        "attempt": attempt,
        "step": "judge",
        "available": available,
        "needs_revision": flagged,
        "model": model,
    }


def _payload_with_trace(plan: dict[str, Any], trace: list[dict[str, Any]]) -> dict[str, Any]:
    payload = _ok_payload(plan)
    payload["meta"]["validation"] |= {
        "trace": trace,
        "judge": {"status": "passed", "model": "anthropic/claude-haiku-4.5"},
    }
    return payload


def test_score_case_reads_every_judge_review_from_the_trace() -> None:
    plan = make_plan()
    trace = [
        _judge_event(1, available=True, flagged=True, model="google/gemini-2.5-flash-lite"),
        _judge_event(2, available=True, flagged=False, model="anthropic/claude-haiku-4.5"),
    ]
    payload = _payload_with_trace(plan.model_dump(mode="json"), trace)

    result = score_case(_run(_case(), payload, (plan, plan), judge_calls=2))

    assert (result.judge_reviews, result.judge_available) == (2, 1 + 1)
    assert result.judge_models == ("google/gemini-2.5-flash-lite", "anthropic/claude-haiku-4.5")
    assert result.judge_model == "anthropic/claude-haiku-4.5"
    assert result.judge_revised is True


def test_a_judge_flag_on_attempt_one_without_a_revise_fails_the_outcome() -> None:
    plan = make_plan()
    trace = [_judge_event(1, available=True, flagged=True, model="m")]
    payload = _payload_with_trace(plan.model_dump(mode="json"), trace)

    result = score_case(_run(_case(), payload, (plan,)))

    assert not result.outcome_ok
    assert "judge flagged attempt 1" in result.outcome_note


def test_judge_availability_pools_planner_case_reviews_and_judge_cases() -> None:
    plan = make_plan()
    trace = [_judge_event(1, available=False, flagged=False, model="m")]
    case = score_case(
        _run(_case(), _payload_with_trace(plan.model_dump(mode="json"), trace), (plan,))
    )
    judged = [score_judge_case(_judge_case("bad", "viz_fit"), _review(flagged=True))]

    assert judge_availability([case], judged) == (1, 2)


def test_cost_usd_prices_a_tier_two_judge_call_at_its_own_rate() -> None:
    usage = Usage(
        role="judge",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        model="anthropic/claude-haiku-4.5",
    )

    assert cost_usd([usage]) == pytest.approx(1.00 + 5.00)


def test_cost_usd_refuses_to_guess_an_unknown_model_price() -> None:
    usage = Usage(role="judge", input_tokens=1, output_tokens=1, model="acme/unknown-1")

    with pytest.raises(ValueError, match="acme/unknown-1"):
        cost_usd([usage])
