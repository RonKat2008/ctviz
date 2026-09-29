"""Judge tests: the code-side verdict recompute, fail-open behaviour, and the OpenRouter backend.

The OpenRouter backend is exercised against a fake SDK client object (`_FakeParse` stands in for
`client.chat.completions.parse`), so no request ever leaves the process (HARD RULE).
"""

import json
import logging
from datetime import date
from types import SimpleNamespace
from typing import Any

import httpx
import openai
import pytest
from pydantic import BaseModel, ValidationError

from ctviz.agent.judge import (
    Judge,
    build_judge,
    model_family,
    needs_revision,
    same_family,
)
from ctviz.agent.judge_backends import (
    JUDGE_UNAVAILABLE_MESSAGE,
    OPENROUTER_BASE_URL,
    JudgeAnswer,
    MissingKeyJudgeBackend,
    OpenRouterJudgeBackend,
)
from ctviz.agent.overlay import FieldOverride, apply_overlay
from ctviz.agent.prompts import build_judge_system, build_judge_user
from ctviz.catalog.loader import load_catalog
from ctviz.config import JUDGE_MIN_CALL_S, JUDGE_REQUEST_BUDGET_S, JUDGE_TIMEOUT_S, Settings
from ctviz.errors import JudgeUnavailableError
from ctviz.schemas.judge import CheckResult, JudgeIssue, JudgeVerdict
from ctviz.schemas.plan import QueryPlan
from ctviz.schemas.request import VisualizeRequest
from tests.factories import make_plan

_CHECK_NAMES = (
    "filter_fidelity",
    "no_invented_filters",
    "dimension_match",
    "viz_fit",
    "time_range",
    "comparison_cohorts",
    "ambiguity_handled",
)
_REQUEST = VisualizeRequest(query="Pembrolizumab trials by phase")


def issue(
    severity: str = "major",
    plan_path: str = "search_terms[0].param",
    quote: str = "search_terms",
) -> JudgeIssue:
    """A `JudgeIssue` at `plan_path`."""
    return JudgeIssue(
        severity=severity,  # type: ignore[arg-type]
        category="wrong_param",
        plan_path=plan_path,
        evidence_quote=quote,
        explanation="Pembrolizumab is a drug, not a condition.",
        suggested_fix='set search_terms[0].param = "query.intr"',
    )


def verdict(
    issues: list[JudgeIssue] | None = None,
    model_verdict: str = "pass",
    failed: tuple[str, ...] = (),
) -> JudgeVerdict:
    """A `JudgeVerdict` answering all seven checks; `failed` names the checks that failed."""
    checks = [
        CheckResult(check=name, passed=name not in failed, evidence="quote")  # type: ignore[arg-type]
        for name in _CHECK_NAMES
    ]
    return JudgeVerdict(
        checks=checks,
        issues=issues or [],
        verdict=model_verdict,  # type: ignore[arg-type]
        confidence="high",
    )


class ScriptedJudgeBackend:
    """A `JudgeBackend` returning (or raising) each scripted effect in order, recording prompts;
    every verdict is reported as answered by `model`."""

    def __init__(
        self, *effects: JudgeVerdict | Exception, model: str = "google/gemini-2.5-flash-lite"
    ) -> None:
        self._effects = list(effects)
        self.calls: list[tuple[str, str]] = []
        self.model = model

    def complete(self, system: str, user: str, budget_s: float = 0.0) -> JudgeAnswer:
        self.calls.append((system, user))
        effect = self._effects.pop(0)
        if isinstance(effect, Exception):
            raise effect
        return JudgeAnswer(effect, self.model)


def _review(judge_verdict: JudgeVerdict | Exception, plan: QueryPlan | None = None) -> Any:
    backend = ScriptedJudgeBackend(judge_verdict)
    judge = Judge(backend, model_name="google/gemini-2.5-flash-lite")
    return judge.review(_REQUEST, plan or make_plan(), [], {"pembrolizumab": 2960})


# --- the recompute rule (pure) ----------------------------------------------------------------


def test_major_issue_forces_revision_even_if_model_said_pass() -> None:
    # Arrange
    model_output = verdict([issue("major")], model_verdict="pass")

    # Act
    review = _review(model_output)

    # Assert
    assert review.needs_revision is True
    assert review.available is True
    assert review.model_verdict == "pass"


def test_failed_check_without_major_issue_does_not_revise() -> None:
    model_output = verdict([issue("minor")], model_verdict="revise", failed=("viz_fit",))

    review = _review(model_output)

    assert review.needs_revision is False
    assert review.failed_checks == ("viz_fit",)
    assert [i.severity for i in review.issues] == ["minor"]


def test_needs_revision_is_true_for_a_critical_issue() -> None:
    assert needs_revision(verdict([issue("critical")]), frozenset()) is True


def test_needs_revision_is_false_with_no_issues_even_if_model_said_revise() -> None:
    assert needs_revision(verdict([], model_verdict="revise"), frozenset()) is False


def test_issue_on_structured_field_slot_is_discarded() -> None:
    # Arrange: the user's structured start_year (2015) overrode the planner's 2018
    plan = make_plan(
        filters={
            "phases": None,
            "overall_statuses": None,
            "study_types": None,
            "intervention_types": None,
            "lead_sponsor_classes": None,
            "countries": None,
            "start_year_min": 2018,
            "start_year_max": None,
            "nct_ids": None,
        }
    )
    request = VisualizeRequest(query="Pembrolizumab trials since 2018", start_year=2015)
    overlaid, overrides = apply_overlay(plan, request)
    model_output = verdict([issue("critical", "filters.start_year_min")], model_verdict="revise")
    judge = Judge(ScriptedJudgeBackend(model_output), model_name="judge")

    # Act
    review = judge.review(request, overlaid, overrides, {"pembrolizumab": 10})

    # Assert
    assert overrides and overrides[0].field == "start_year"
    assert review.needs_revision is False
    assert review.issues == ()
    assert review.discarded == 1


@pytest.mark.parametrize(
    "path",
    [
        "search_terms[1].value",
        "plan.search_terms[1]",
        "/search_terms/1/param",
        " search_terms[1] ",
    ],
)
def test_needs_revision_ignores_structured_slots_in_any_path_spelling(path: str) -> None:
    assert needs_revision(verdict([issue("major", path)]), frozenset({"search_terms[1]"})) is False


def test_needs_revision_keeps_an_issue_on_a_sibling_slot() -> None:
    slots = frozenset({"search_terms[1]"})

    assert needs_revision(verdict([issue("major", "search_terms[10].value")]), slots) is True
    assert needs_revision(verdict([issue("major", "search_terms[0].value")]), slots) is True


def test_review_discards_an_issue_on_a_structured_search_term() -> None:
    # Arrange: drug_name is structured, so its appended term is authoritative
    request = VisualizeRequest(query="Trials for this drug by phase", drug_name="Pembrolizumab")
    overlaid, overrides = apply_overlay(make_plan(search_terms=[]), request)
    model_output = verdict([issue("major", "search_terms[0].value")])
    judge = Judge(ScriptedJudgeBackend(model_output), model_name="judge")

    # Act
    review = judge.review(request, overlaid, overrides, {"Pembrolizumab": 5})

    # Assert
    assert review.needs_revision is False
    assert review.discarded == 1


def test_review_discards_an_issue_on_a_comparison_the_overlay_dropped() -> None:
    plan = make_plan()
    override = FieldOverride(field="comparison", text_value="vary", applied_value="Pembrolizumab")
    model_output = verdict([issue("major", "comparison")])
    judge = Judge(ScriptedJudgeBackend(model_output), model_name="judge")

    review = judge.review(_REQUEST, plan, [override], {"pembrolizumab": 5})

    assert review.needs_revision is False


@pytest.mark.parametrize(
    "path",
    [
        "$.search_terms[1].value",
        "$search_terms[1]",
        "Search_Terms[1].Param",
        "$.PLAN.search_terms[1]",
    ],
)
def test_needs_revision_normalizes_case_and_a_leading_dollar_before_the_slot_filter(
    path: str,
) -> None:
    """Fix I: JSONPath-style '$.'/'$' prefixes and any letter case still hit the slot."""
    assert needs_revision(verdict([issue("major", path)]), frozenset({"search_terms[1]"})) is False


def test_review_discards_an_issue_on_a_structured_top_n() -> None:
    """Fix H (kills M28): `options.top_n` fills `analysis.top_n` -- an issue there is discarded."""
    request = VisualizeRequest(query="Top sponsors for pembrolizumab", options={"top_n": 5})
    overlaid, overrides = apply_overlay(make_plan(), request)
    model_output = verdict([issue("major", "analysis.top_n")])
    judge = Judge(ScriptedJudgeBackend(model_output), model_name="judge")

    review = judge.review(request, overlaid, overrides, {"pembrolizumab": 5})

    assert review.needs_revision is False
    assert review.discarded == 1


# --- fail open --------------------------------------------------------------------------------


def test_unreachable_judge_fails_open() -> None:
    # Arrange
    error = openai.APIConnectionError(request=httpx.Request("POST", OPENROUTER_BASE_URL))

    # Act
    review = _review(error)

    # Assert
    assert review.available is False
    assert review.needs_revision is False
    assert review.issues == ()


def test_judge_unavailable_error_from_the_backend_fails_open() -> None:
    review = _review(JudgeUnavailableError(JUDGE_UNAVAILABLE_MESSAGE))

    assert (review.available, review.needs_revision) == (False, False)


def test_a_verdict_that_skips_the_rubric_is_unavailable_not_passed() -> None:
    """Fix C: `checks=[]` answers none of the seven rubric checks -> fail open, not a pass."""
    review = _review(JudgeVerdict(checks=[], issues=[], verdict="pass", confidence="high"))

    assert (review.available, review.needs_revision) == (False, False)


@pytest.mark.parametrize(
    "checks",
    [
        _CHECK_NAMES[:6],  # one rubric check missing
        (*_CHECK_NAMES[:6], _CHECK_NAMES[0]),  # seven answers, one duplicated
        (*_CHECK_NAMES, _CHECK_NAMES[0]),  # all seven plus a duplicate
    ],
    ids=["six", "seven_with_a_duplicate", "eight"],
)
def test_a_verdict_must_answer_exactly_the_seven_distinct_rubric_checks(
    checks: tuple[str, ...],
) -> None:
    """Fix C: exactly the 7 distinct checks, or the review is unavailable."""
    answered = [
        CheckResult(check=name, passed=True, evidence="q")  # type: ignore[arg-type]
        for name in checks
    ]
    review = _review(JudgeVerdict(checks=answered, issues=[], verdict="pass", confidence="high"))

    assert review.available is False


def test_an_unexpected_backend_exception_fails_open_and_logs_only_its_type(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Fix I: a non-OpenAIError at the judge boundary (a bug, a KeyError) fails open too."""
    with caplog.at_level(logging.WARNING):
        review = _review(KeyError("sk-or-LEAKED-secret-fragment"))

    assert (review.available, review.needs_revision) == (False, False)
    assert "KeyError" in caplog.text and "LEAKED" not in caplog.text


def test_review_sends_rubric_system_and_request_context_user_prompts() -> None:
    backend = ScriptedJudgeBackend(verdict())
    judge = Judge(backend, model_name="judge")

    judge.review(_REQUEST, make_plan(), [], {"pembrolizumab": 2960}, today=date(2026, 9, 29))

    system, user = backend.calls[0]
    assert "filter_fidelity" in system
    assert "2960" in user and "2026-09-29" in user


# --- prompts ----------------------------------------------------------------------------------


def test_judge_system_prompt_names_all_seven_rubric_checks_and_the_catalog() -> None:
    system = build_judge_system(load_catalog())

    for name in _CHECK_NAMES:
        assert name in system
    assert "query.intr" in system
    assert "authoritative" in system


def test_judge_user_prompt_carries_query_fields_overrides_probe_plan_and_date() -> None:
    # Arrange
    request = VisualizeRequest(query="Trials since 2018 by phase", drug_name="Pembrolizumab")
    override = FieldOverride(field="start_year", text_value="2018", applied_value="2015")

    # Act
    user = build_judge_user(
        request, make_plan(), [override], {"Pembrolizumab": 42}, date(2026, 1, 2)
    )

    # Assert
    assert "Trials since 2018 by phase" in user
    assert '"drug_name": "Pembrolizumab"' in user
    assert '"applied_value": "2015"' in user
    assert '"Pembrolizumab": 42' in user
    assert '"interpretation": "Pembrolizumab trials by phase."' in user
    assert "2026-01-02" in user
    assert "sponsor_role" not in user


def test_judge_user_prompt_shows_sponsor_role_only_with_a_sponsor() -> None:
    request = VisualizeRequest(query="Trials by phase", sponsor="Merck", sponsor_role="any")

    user = build_judge_user(request, make_plan(), [], {}, date(2026, 1, 2))

    assert '"sponsor_role": "any"' in user


# --- same-family rule -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "family"),
    [
        ("gpt-5.4-mini", "openai"),
        ("o4-mini", "openai"),
        ("openai/gpt-4.1-nano", "openai"),
        ("google/gemini-2.5-flash-lite", "google"),
        ("gemini-2.5-pro", "google"),
        ("anthropic/claude-haiku-4.5", "anthropic"),
        ("claude-haiku-4.5", "anthropic"),
        ("Mistral-Large", "mistral"),
    ],
)
def test_model_family_names_the_vendor(model: str, family: str) -> None:
    assert model_family(model) == family


def test_same_family_flags_an_openai_judge_for_an_openai_planner() -> None:
    assert same_family("gpt-5.4-mini", "gpt-4.1-nano") is True
    assert same_family("gpt-5.4-mini", "google/gemini-2.5-flash-lite") is False


# --- build_judge ------------------------------------------------------------------------------


def test_build_judge_without_openrouter_key_reports_unavailable_instead_of_failing() -> None:
    # Arrange
    judge = build_judge(Settings(_env_file=None, openrouter_api_key=None))

    # Act
    review = judge.review(_REQUEST, make_plan(), [], {})

    # Assert
    assert judge.model_name == "google/gemini-2.5-flash-lite"
    assert review.available is False


def test_build_judge_without_a_key_uses_the_fail_open_backend() -> None:
    judge = build_judge(Settings(_env_file=None, openrouter_api_key=None))

    assert isinstance(judge._backend, MissingKeyJudgeBackend)


def test_openrouter_backend_refuses_to_build_without_a_key() -> None:
    with pytest.raises(JudgeUnavailableError):
        OpenRouterJudgeBackend(Settings(_env_file=None, openrouter_api_key=None))


# --- OpenRouterJudgeBackend (fake SDK client object; no network) -----------------------------


class FakeClock:
    """A monotonic clock the tests advance by hand (the judge budget reads it)."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class _FakeParse:
    """Fakes `client.chat.completions.parse`: records kwargs, returns/raises effects in order;
    each call advances `clock` by `call_s` (a slow provider) before its effect."""

    def __init__(self, *effects: Any, clock: FakeClock | None = None, call_s: float = 0.0) -> None:
        self._effects = list(effects)
        self.calls: list[dict[str, Any]] = []
        self._clock = clock
        self._call_s = call_s

    def __call__(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self._clock is not None:
            self._clock.now += self._call_s
        effect = self._effects.pop(0)
        if isinstance(effect, BaseException):
            raise effect
        return effect


def _completion(parsed: JudgeVerdict | None) -> SimpleNamespace:
    message = SimpleNamespace(parsed=parsed, refusal=None if parsed else "no")
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _backend(
    parse: _FakeParse, sleeps: list[float] | None = None, clock: FakeClock | None = None
) -> OpenRouterJudgeBackend:
    settings = Settings(
        _env_file=None,
        openrouter_api_key="sk-or-fake-never-sent",
        app_url="http://example.test",
        app_name="ctviz-test",
    )
    recorded = sleeps if sleeps is not None else []
    backend = OpenRouterJudgeBackend(settings, sleep=recorded.append, clock=clock or FakeClock())
    backend._client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=SimpleNamespace(parse=parse))
    )
    return backend


def _status_error(status: int, headers: dict[str, str] | None = None) -> openai.APIStatusError:
    request = httpx.Request("POST", f"{OPENROUTER_BASE_URL}/chat/completions")
    response = httpx.Response(status, request=request, headers=headers or {})
    error_type = openai.RateLimitError if status == 429 else openai.APIStatusError
    return error_type("sk-or-LEAKED-secret-fragment", response=response, body=None)


def test_openrouter_backend_uses_the_openrouter_base_url_and_timeout() -> None:
    backend = OpenRouterJudgeBackend(
        Settings(_env_file=None, openrouter_api_key="sk-or-fake-never-sent")
    )

    assert str(backend._client.base_url).rstrip("/") == OPENROUTER_BASE_URL
    assert backend._client.timeout == JUDGE_TIMEOUT_S
    assert backend._client.max_retries == 0


def test_openrouter_backend_sends_structured_output_temperature_zero_and_headers() -> None:
    # Arrange
    parse = _FakeParse(_completion(verdict()))
    backend = _backend(parse)

    # Act
    result = backend.complete("SYSTEM", "USER")

    # Assert
    [call] = parse.calls
    assert result == JudgeAnswer(verdict(), "google/gemini-2.5-flash-lite")
    assert call["model"] == "google/gemini-2.5-flash-lite"
    assert call["response_format"] is JudgeVerdict
    assert call["temperature"] == 0
    assert call["extra_body"] == {"provider": {"require_parameters": True}}
    assert call["extra_headers"] == {"HTTP-Referer": "http://example.test", "X-Title": "ctviz-test"}
    assert call["messages"] == [
        {"role": "system", "content": "SYSTEM"},
        {"role": "user", "content": "USER"},
    ]


@pytest.mark.parametrize("status", [402, 503])
def test_openrouter_backend_fails_open_immediately_on_402_and_503(status: int) -> None:
    parse = _FakeParse(_status_error(status))
    backend = _backend(parse)

    with pytest.raises(JudgeUnavailableError) as raised:
        backend.complete("s", "u")

    assert len(parse.calls) == 1
    assert str(raised.value) == JUDGE_UNAVAILABLE_MESSAGE


def test_openrouter_backend_retries_a_rate_limit_once_honoring_retry_after() -> None:
    # Arrange
    sleeps: list[float] = []
    parse = _FakeParse(_status_error(429, {"retry-after": "1.5"}), _completion(verdict()))
    backend = _backend(parse, sleeps)

    # Act
    result = backend.complete("s", "u")

    # Assert
    assert result.verdict == verdict()
    assert len(parse.calls) == 2
    assert sleeps == [1.5]


@pytest.mark.parametrize(
    ("header", "expected_wait"),
    [({"retry-after": "60"}, 2.0), ({"retry-after": "soon"}, 1.0), ({}, 1.0)],
    ids=["capped", "unparseable", "absent"],
)
def test_openrouter_backend_bounds_the_rate_limit_wait(
    header: dict[str, str], expected_wait: float
) -> None:
    sleeps: list[float] = []
    parse = _FakeParse(_status_error(429, header), _completion(verdict()))

    _backend(parse, sleeps).complete("s", "u")

    assert sleeps == [expected_wait]


def test_openrouter_backend_second_rate_limit_is_unavailable() -> None:
    parse = _FakeParse(_status_error(429), _status_error(429))

    with pytest.raises(JudgeUnavailableError):
        _backend(parse).complete("s", "u")

    assert len(parse.calls) == 2


def test_openrouter_backend_unparsed_completion_is_unavailable() -> None:
    parse = _FakeParse(_completion(None))

    with pytest.raises(JudgeUnavailableError):
        _backend(parse).complete("s", "u")


def test_openrouter_backend_empty_choices_is_unavailable() -> None:
    parse = _FakeParse(SimpleNamespace(choices=[]))

    with pytest.raises(JudgeUnavailableError):
        _backend(parse).complete("s", "u")


def _schema_error() -> ValidationError:
    """A real pydantic `ValidationError`, as `parse` raises for schema-invalid model output."""

    class _Tiny(BaseModel):
        n: int

    try:
        _Tiny.model_validate({"n": "not-a-number"})
    except ValidationError as exc:
        return exc
    raise AssertionError("unreachable")


def _openrouter_judge(parse: _FakeParse, clock: FakeClock) -> Judge:
    """A `Judge` over the OpenRouter backend with a fake SDK client and a shared fake clock."""
    return Judge(
        _backend(parse, clock=clock), model_name="google/gemini-2.5-flash-lite", clock=clock
    )


def test_a_schema_invalid_output_is_not_retried_on_the_same_tier() -> None:
    """§9.5 ruling: a schema-invalid output goes to the next tier, never back to this one."""
    clock = FakeClock()
    parse = _FakeParse(_schema_error(), _completion(verdict()), clock=clock, call_s=1.0)

    review = _openrouter_judge(parse, clock).review(_REQUEST, make_plan(), [], {"p": 5})

    assert review.available is False
    assert len(parse.calls) == 1


def test_no_rate_limit_retry_when_the_judge_budget_is_exhausted() -> None:
    """Fix B: a first call that burns the budget (11 s of 12) is not retried -- fail open."""
    clock = FakeClock()
    slow = JUDGE_REQUEST_BUDGET_S - JUDGE_MIN_CALL_S + 1.0
    parse = _FakeParse(
        _status_error(429, {"retry-after": "0"}), _completion(verdict()), clock=clock, call_s=slow
    )

    review = _openrouter_judge(parse, clock).review(_REQUEST, make_plan(), [], {"p": 5})

    assert review.available is False
    assert len(parse.calls) == 1


def test_a_rate_limit_wait_counts_against_the_judge_budget() -> None:
    """Fix B: a 429 whose Retry-After would overrun the budget is not waited out."""
    clock = FakeClock()
    sleeps: list[float] = []
    parse = _FakeParse(_status_error(429, {"retry-after": "2"}), clock=clock, call_s=9.0)

    with pytest.raises(JudgeUnavailableError):
        _backend(parse, sleeps, clock).complete("s", "u")

    assert sleeps == [] and len(parse.calls) == 1


def test_each_call_times_out_within_the_remaining_budget() -> None:
    """Fix B: per-call timeout = min(JUDGE_TIMEOUT_S, what is left of the budget)."""
    clock = FakeClock()
    first = _status_error(429, {"retry-after": "0"})
    parse = _FakeParse(first, _completion(verdict()), clock=clock, call_s=6.0)

    _backend(parse, clock=clock).complete("s", "u")

    assert [c["timeout"] for c in parse.calls] == [
        JUDGE_TIMEOUT_S,
        JUDGE_REQUEST_BUDGET_S - 6.0,
    ]


def test_a_review_with_less_than_one_calls_worth_of_budget_skips_the_call() -> None:
    """Fix B: the request's judge budget is shared by both reviews; an exhausted one fails open."""
    backend = ScriptedJudgeBackend(verdict())
    judge = Judge(backend, model_name="judge")

    review = judge.review(_REQUEST, make_plan(), [], {}, budget_s=JUDGE_MIN_CALL_S / 2)

    assert review.available is False and backend.calls == []


def test_a_review_reports_the_judge_time_it_spent() -> None:
    clock = FakeClock()
    parse = _FakeParse(_completion(verdict()), clock=clock, call_s=3.0)

    review = _openrouter_judge(parse, clock).review(_REQUEST, make_plan(), [], {"p": 5})

    assert review.elapsed_s == 3.0


def test_openrouter_backend_logs_type_and_status_but_never_the_sdk_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    parse = _FakeParse(_status_error(402))

    with caplog.at_level(logging.WARNING), pytest.raises(JudgeUnavailableError) as raised:
        _backend(parse).complete("s", "u")

    assert "LEAKED" not in caplog.text
    assert "LEAKED" not in str(raised.value)
    assert "APIStatusError" in caplog.text and "402" in caplog.text


def test_judge_verdict_schema_is_accepted_by_strict_structured_outputs() -> None:
    from openai.lib._pydantic import to_strict_json_schema

    schema = to_strict_json_schema(JudgeVerdict)

    assert schema["additionalProperties"] is False
    assert json.dumps(schema)  # serialisable
