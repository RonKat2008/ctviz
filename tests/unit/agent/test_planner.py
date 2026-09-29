"""Planner tests with a fake backend: no network, just the prompt-assembly contract.

The `OpenAIPlannerBackend` tests below fake the SDK client object itself (`_FakeParse` stands in
for `client.responses.parse`) so the retry/refusal/error-mapping logic is exercised without ever
calling OpenAI (HARD RULE: no network, never call OpenAI/OpenRouter).
"""

import logging
from datetime import date
from types import SimpleNamespace
from typing import Any

import httpx
import openai
import pytest
from pydantic import ValidationError

from ctviz.agent.planner import (
    CLIENT_UNAVAILABLE_MESSAGE,
    MAX_OUTPUT_TOKENS,
    RETRY_MAX_OUTPUT_TOKENS,
    OpenAIPlannerBackend,
    Planner,
    _MissingKeyBackend,
    build_planner,
)
from ctviz.config import Settings
from ctviz.errors import LLMUnavailableError, OutOfScopeError
from ctviz.schemas.plan import QueryPlan
from ctviz.schemas.request import VisualizeRequest
from tests.factories import make_plan


class FakeBackend:
    """A `PlannerBackend` stand-in that records every call instead of hitting OpenAI."""

    def __init__(self, plan: QueryPlan) -> None:
        self.plan, self.calls = plan, []

    def complete(self, system: str, user: str) -> QueryPlan:
        self.calls.append((system, user))
        return self.plan


class _FakeResponse:
    """A minimal stand-in for `openai.types.responses.ParsedResponse`."""

    def __init__(
        self, status: str = "completed", output_parsed: Any = None, output: tuple[Any, ...] = ()
    ) -> None:
        self.status = status
        self.output_parsed = output_parsed
        self.output = output


class _FakeParse:
    """Fakes `client.responses.parse`: records kwargs, returns/raises each effect in order."""

    def __init__(self, *effects: Any) -> None:
        self._effects = list(effects)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        effect = self._effects.pop(0)
        if isinstance(effect, BaseException):
            raise effect
        return effect


def _fake_backend(planner_reasoning_effort: str = "low") -> OpenAIPlannerBackend:
    """A real `OpenAIPlannerBackend` built from a throwaway key; its `_client` is swapped out."""
    settings = Settings(
        _env_file=None,
        openai_api_key="sk-test-fake-key-never-sent",
        planner_reasoning_effort=planner_reasoning_effort,
    )
    return OpenAIPlannerBackend(settings)


def _install(backend: OpenAIPlannerBackend, parse: _FakeParse) -> None:
    """Replace the backend's real SDK client object with a fake `responses.parse`."""
    backend._client = SimpleNamespace(responses=SimpleNamespace(parse=parse))  # type: ignore[attr-defined]


def _refusal_response() -> _FakeResponse:
    """A response shaped like a model safety refusal (§8.3)."""
    refusal_content = SimpleNamespace(type="refusal", refusal="I can't help rank drug efficacy.")
    message = SimpleNamespace(content=[refusal_content])
    return _FakeResponse(status="completed", output_parsed=None, output=[message])


def test_planner_sends_catalog_in_system_and_question_in_user_message() -> None:
    backend = FakeBackend(make_plan())

    Planner(backend).plan(VisualizeRequest(query="Trials by phase?", drug_name="Pembrolizumab"))

    system, user = backend.calls[0]
    assert "query.intr" in system and "provides" in system.lower()
    assert "Trials by phase?" in user
    assert "drug_name" in user and "Pembrolizumab" not in system


def test_planner_includes_feedback_on_revise() -> None:
    backend = FakeBackend(make_plan())

    Planner(backend).plan(
        VisualizeRequest(query="q q"),
        feedback=["0 trials for query.cond='Keytruda'"],
        previous=make_plan(),
    )

    assert "0 trials for query.cond='Keytruda'" in backend.calls[0][1]


def test_planner_passes_today_into_the_prompt() -> None:
    """Item 13: `Planner.plan` threads `today` into the user prompt instead of `date.today()`."""
    backend = FakeBackend(make_plan())

    Planner(backend).plan(VisualizeRequest(query="Trials this year?"), today=date(2020, 1, 1))

    assert "2020-01-01" in backend.calls[0][1]


def test_build_planner_system_mentions_every_viz_type() -> None:
    """Item 5: the planner-facing text (catalog + compatibility table) names every `VizType`."""
    from ctviz.agent.prompts import build_planner_system
    from ctviz.catalog.loader import load_catalog
    from ctviz.schemas.enums import VizType

    system = build_planner_system(load_catalog())

    for viz_type in VizType:
        assert viz_type.value in system


# --- OpenAIPlannerBackend: fakes the SDK client object; no network (HARD RULE) ---------------


def test_planner_never_passes_temperature() -> None:
    backend = _fake_backend()
    parse = _FakeParse(_FakeResponse(output_parsed=make_plan()))
    _install(backend, parse)

    backend.complete("system", "user")

    assert "temperature" not in parse.calls[0]


def test_planner_passes_timeout_and_reasoning_effort() -> None:
    backend = _fake_backend(planner_reasoning_effort="medium")
    parse = _FakeParse(_FakeResponse(output_parsed=make_plan()))
    _install(backend, parse)

    backend.complete("system", "user")

    from ctviz.config import LLM_TIMEOUT_S

    assert parse.calls[0]["timeout"] == LLM_TIMEOUT_S
    assert parse.calls[0]["reasoning"] == {"effort": "medium"}


def test_planner_retries_once_then_raises_on_invalid_json() -> None:
    """A truncated/malformed response raises `pydantic.ValidationError` inside the SDK call."""
    backend = _fake_backend()
    try:
        QueryPlan.model_validate({})
    except ValidationError as exc:
        sdk_validation_error = exc
    parse = _FakeParse(sdk_validation_error, sdk_validation_error)
    _install(backend, parse)

    with pytest.raises(LLMUnavailableError):
        backend.complete("system", "user")

    assert len(parse.calls) == 2
    assert parse.calls[0]["max_output_tokens"] == MAX_OUTPUT_TOKENS
    assert parse.calls[1]["max_output_tokens"] == RETRY_MAX_OUTPUT_TOKENS


def test_planner_retries_on_incomplete_status_then_succeeds() -> None:
    backend = _fake_backend()
    parse = _FakeParse(
        _FakeResponse(status="incomplete", output_parsed=None),
        _FakeResponse(status="completed", output_parsed=make_plan()),
    )
    _install(backend, parse)

    plan = backend.complete("system", "user")

    assert plan == make_plan()
    assert len(parse.calls) == 2
    assert parse.calls[1]["max_output_tokens"] == RETRY_MAX_OUTPUT_TOKENS


def test_planner_maps_openai_error_to_llm_unavailable_without_retry(caplog: Any) -> None:
    backend = _fake_backend()
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    response = httpx.Response(429, request=request, json={"error": {"message": "rate limited"}})
    sdk_error = openai.RateLimitError(message="rate limited", response=response, body=None)
    parse = _FakeParse(sdk_error)
    _install(backend, parse)

    with caplog.at_level(logging.ERROR), pytest.raises(LLMUnavailableError) as exc_info:
        backend.complete("system", "user")

    assert str(exc_info.value) == CLIENT_UNAVAILABLE_MESSAGE
    assert len(parse.calls) == 1  # a transport error is not retried at this layer
    assert "RateLimitError" in caplog.text


def test_planner_error_message_never_contains_sdk_text(caplog: Any) -> None:
    """Item 1: a masked-key SDK message never reaches the client response or the server log."""
    backend = _fake_backend()
    key_fragment = "sk-proj-AbCd****wxyz"
    sdk_message = f"Incorrect API key provided: {key_fragment}"
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    response = httpx.Response(401, request=request, json={"error": {"message": sdk_message}})
    sdk_error = openai.AuthenticationError(message=sdk_message, response=response, body=None)
    _install(backend, _FakeParse(sdk_error))

    with pytest.raises(LLMUnavailableError) as exc_info:
        backend.complete("system", "user")

    message = str(exc_info.value)
    assert message == CLIENT_UNAVAILABLE_MESSAGE
    assert "sk-" not in message
    assert key_fragment not in message
    assert key_fragment not in caplog.text
    assert sdk_message not in caplog.text


def test_planner_refusal_raises_out_of_scope_without_retry() -> None:
    """Item 10: a model refusal is `OutOfScopeError` (-> 200 ok:false), never `LLMUnavailableError`,
    and is never retried (only one fake effect is configured; a retry would raise `IndexError`)."""
    backend = _fake_backend()
    _install(backend, _FakeParse(_refusal_response()))

    with pytest.raises(OutOfScopeError) as exc_info:
        backend.complete("system", "user")

    assert "can't help" in str(exc_info.value)


def test_build_planner_returns_planner_that_raises_only_when_used_without_a_key() -> None:
    """Item 4: a missing key must not raise at planner-build time, only on first `.plan()` call."""
    settings = Settings(_env_file=None, openai_api_key=None)

    planner = build_planner(settings)  # must not raise

    assert isinstance(planner._backend, _MissingKeyBackend)
    with pytest.raises(LLMUnavailableError):
        planner.plan(VisualizeRequest(query="Trials by phase?"))


def test_openai_planner_backend_raises_immediately_without_a_key() -> None:
    """Direct construction (not through `build_planner`) still fails fast without a key."""
    with pytest.raises(LLMUnavailableError):
        OpenAIPlannerBackend(Settings(_env_file=None, openai_api_key=None))


def test_build_planner_uses_openai_backend_when_a_key_is_configured() -> None:
    settings = Settings(_env_file=None, openai_api_key="sk-test-fake-key-never-sent")

    planner = build_planner(settings)

    assert isinstance(planner._backend, OpenAIPlannerBackend)
