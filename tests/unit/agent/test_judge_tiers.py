"""The §9.5 judge tiers: tier 1 (gemini-2.5-flash-lite) falls back to tier 2 (claude-haiku-4.5).

Every tier is a real `OpenRouterJudgeBackend` over a fake SDK client (`_FakeParse`), all sharing
one fake clock, so the per-request deadline is exercised exactly as in production. No network.
"""

from datetime import date
from types import SimpleNamespace

from ctviz.agent.judge import Judge, build_judge, same_family
from ctviz.agent.judge_backends import (
    OpenRouterJudgeBackend,
    TieredJudgeBackend,
    judge_tier_models,
)
from ctviz.agent.orchestrator import orchestrate
from ctviz.agent.planner import Planner
from ctviz.config import JUDGE_MIN_CALL_S, JUDGE_REQUEST_BUDGET_S, Settings
from tests.factories import make_plan
from tests.unit.agent.test_judge import (
    _REQUEST,
    FakeClock,
    ScriptedJudgeBackend,
    _completion,
    _FakeParse,
    _schema_error,
    _status_error,
    issue,
    verdict,
)
from tests.unit.agent.test_orchestrator import ScriptedPlannerBackend
from tests.unit.agent.test_probe import FakeProbeClient

TIER_1 = "google/gemini-2.5-flash-lite"
TIER_2 = "anthropic/claude-haiku-4.5"
PLANNER = "gpt-5.4-mini"
TODAY = date(2026, 9, 29)
REVISE = verdict([issue("major")], model_verdict="revise")


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, openrouter_api_key="sk-or-fake-never-sent", **overrides)  # type: ignore[arg-type]


def _tier(parse: _FakeParse, model: str, clock: FakeClock) -> OpenRouterJudgeBackend:
    """One OpenRouter tier whose SDK client is the fake `parse`."""
    tier = OpenRouterJudgeBackend(_settings(), model=model, sleep=lambda _s: None, clock=clock)
    tier._client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=SimpleNamespace(parse=parse))
    )
    return tier


def _judge(first: _FakeParse, second: _FakeParse, clock: FakeClock) -> Judge:
    tiers = [_tier(first, TIER_1, clock), _tier(second, TIER_2, clock)]
    return Judge(TieredJudgeBackend(tiers, clock=clock), model_name=TIER_1, clock=clock)


def test_a_schema_invalid_tier_one_output_goes_to_tier_two_which_answers() -> None:
    # Arrange
    clock = FakeClock()
    first = _FakeParse(_schema_error(), clock=clock, call_s=2.0)
    second = _FakeParse(_completion(verdict()), clock=clock, call_s=1.0)

    # Act
    review = _judge(first, second, clock).review(_REQUEST, make_plan(), [], {"p": 5})

    # Assert
    assert (review.available, review.model) == (True, TIER_2)
    assert same_family(PLANNER, review.model) is False
    assert len(first.calls) == 1, "tier 1 is not retried on a schema-invalid output"
    assert [c["model"] for c in second.calls] == [TIER_2]


def test_tier_two_is_skipped_when_tier_one_left_less_than_one_calls_worth_of_budget() -> None:
    clock = FakeClock()
    first = _FakeParse(_schema_error(), clock=clock, call_s=10.5)
    second = _FakeParse(_completion(verdict()), clock=clock)

    review = _judge(first, second, clock).review(_REQUEST, make_plan(), [], {"p": 5})

    assert JUDGE_REQUEST_BUDGET_S - 10.5 < JUDGE_MIN_CALL_S
    assert review.available is False and review.model == TIER_1
    assert second.calls == []


def test_a_402_on_tier_one_skips_the_other_openrouter_tiers_and_fails_open() -> None:
    clock = FakeClock()
    first = _FakeParse(_status_error(402), clock=clock)
    second = _FakeParse(_completion(verdict()), clock=clock)

    review = _judge(first, second, clock).review(_REQUEST, make_plan(), [], {"p": 5})

    assert review.available is False
    assert second.calls == []


def test_a_503_on_tier_one_goes_to_tier_two() -> None:
    clock = FakeClock()
    first = _FakeParse(_status_error(503), clock=clock)
    second = _FakeParse(_completion(verdict()), clock=clock)

    review = _judge(first, second, clock).review(_REQUEST, make_plan(), [], {"p": 5})

    assert (review.available, review.model) == (True, TIER_2)


def test_unavailable_only_when_every_tier_fails() -> None:
    clock = FakeClock()
    first = _FakeParse(_schema_error(), clock=clock)
    second = _FakeParse(_schema_error(), clock=clock)

    review = _judge(first, second, clock).review(_REQUEST, make_plan(), [], {"p": 5})

    assert review.available is False
    assert (len(first.calls), len(second.calls)) == (1, 1)


def test_every_tier_call_times_out_within_the_one_shared_deadline() -> None:
    clock = FakeClock()
    first = _FakeParse(_schema_error(), clock=clock, call_s=7.0)
    second = _FakeParse(_completion(verdict()), clock=clock)

    _judge(first, second, clock).review(_REQUEST, make_plan(), [], {"p": 5})

    assert second.calls[0]["timeout"] == JUDGE_REQUEST_BUDGET_S - 7.0


async def test_two_reviews_with_tier_two_stay_within_the_request_judge_budget() -> None:
    """Review 1: tier 1 invalid (4 s) -> tier 2 says revise (3 s). Review 2 gets the 5 s left:
    tier 1 invalid (4 s) leaves 1 s < JUDGE_MIN_CALL_S, so tier 2 never starts."""
    # Arrange
    clock = FakeClock()
    first = _FakeParse(_schema_error(), _schema_error(), clock=clock, call_s=4.0)
    second = _FakeParse(_completion(REVISE), _completion(verdict()), clock=clock, call_s=3.0)

    # Act
    outcome = await orchestrate(
        _REQUEST,
        planner=Planner(ScriptedPlannerBackend(make_plan(), make_plan()), model_name=PLANNER),
        judge=_judge(first, second, clock),
        client=FakeProbeClient({"pembrolizumab": 50}),  # type: ignore[arg-type]
        today=TODAY,
    )

    # Assert
    assert clock.now <= JUDGE_REQUEST_BUDGET_S
    assert (len(first.calls), len(second.calls)) == (2, 1)
    assert (outcome.judge_status, outcome.executed_attempt) == ("unavailable", 2)


async def test_meta_judge_model_and_same_family_come_from_the_model_that_answered() -> None:
    clock = FakeClock()
    first = _FakeParse(_schema_error(), clock=clock)
    second = _FakeParse(_completion(verdict()), clock=clock)

    outcome = await orchestrate(
        _REQUEST,
        planner=Planner(ScriptedPlannerBackend(make_plan()), model_name=PLANNER),
        judge=_judge(first, second, clock),
        client=FakeProbeClient({"pembrolizumab": 50}),  # type: ignore[arg-type]
        today=TODAY,
    )

    assert outcome.judge_status == "passed"
    assert outcome.judge_model == TIER_2
    assert outcome.same_family is False
    [event] = [e for e in outcome.trace if e["step"] == "judge"]
    assert event["model"] == TIER_2


async def test_same_family_is_flagged_when_an_openai_model_actually_answered() -> None:
    backend = ScriptedJudgeBackend(verdict(), model="openai/gpt-4.1-nano")

    outcome = await orchestrate(
        _REQUEST,
        planner=Planner(ScriptedPlannerBackend(make_plan()), model_name=PLANNER),
        judge=Judge(backend, model_name=TIER_1),
        client=FakeProbeClient({"pembrolizumab": 50}),  # type: ignore[arg-type]
        today=TODAY,
    )

    assert outcome.judge_model == "openai/gpt-4.1-nano"
    assert outcome.same_family is True


def test_settings_default_the_tier_two_model_to_claude_haiku() -> None:
    assert Settings(_env_file=None).judge_fallback_model == TIER_2


def test_judge_tier_models_lists_tier_one_then_the_fallback_unless_disabled() -> None:
    assert judge_tier_models(_settings()) == (TIER_1, TIER_2)
    assert judge_tier_models(_settings(judge_fallback_model="")) == (TIER_1,)
    assert judge_tier_models(_settings(judge_fallback_model=TIER_1)) == (TIER_1,)


def test_build_judge_wires_one_openrouter_tier_per_configured_model() -> None:
    judge = build_judge(_settings())

    backend = judge._backend
    assert isinstance(backend, TieredJudgeBackend)
    assert [t.model for t in backend.tiers] == [TIER_1, TIER_2]
    assert judge.model_name == TIER_1
