"""Token usage is logged for EVERY response the eval backends receive -- including one that then
fails schema validation -- so a failed call's spend is never under-reported. Fake SDK clients."""

from types import SimpleNamespace
from typing import Any

import pytest
from evals.live import Recorder, UsageOpenAIPlannerBackend, UsageOpenRouterJudgeBackend
from pydantic import BaseModel, ValidationError

from ctviz.agent.judge_backends import JudgeTierError
from ctviz.config import Settings
from ctviz.errors import LLMUnavailableError

HAIKU = "anthropic/claude-haiku-4.5"


def _schema_error() -> ValidationError:
    class _Tiny(BaseModel):
        n: int

    try:
        _Tiny.model_validate({"n": "x"})
    except ValidationError as exc:
        return exc
    raise AssertionError("unreachable")


class _RawResponse:
    """Stands in for the SDK's `LegacyAPIResponse`: raw JSON first, parsing on `.parse()`."""

    def __init__(self, body: dict[str, Any], parsed: Any) -> None:
        self.http_response = SimpleNamespace(json=lambda: body)
        self._parsed = parsed

    def parse(self) -> Any:
        if isinstance(self._parsed, Exception):
            raise self._parsed
        return self._parsed


def _settings() -> Settings:
    return Settings(
        _env_file=None, openai_api_key="sk-fake-never-sent", openrouter_api_key="sk-or-fake"
    )


def test_a_schema_invalid_judge_response_still_logs_its_usage() -> None:
    # Arrange
    recorder = Recorder()
    body = {"usage": {"prompt_tokens": 900, "completion_tokens": 300}}
    raw = _RawResponse(body, _schema_error())
    tier = UsageOpenRouterJudgeBackend(_settings(), HAIKU, recorder)
    tier._client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(
            completions=SimpleNamespace(with_raw_response=SimpleNamespace(parse=lambda **_k: raw))
        )
    )

    # Act
    with pytest.raises(JudgeTierError):
        tier.answer("s", "u", deadline=1e9)

    # Assert
    [usage] = recorder.usages
    assert (usage.role, usage.model) == ("judge", HAIKU)
    assert (usage.input_tokens, usage.output_tokens) == (900, 300)


def test_a_schema_invalid_planner_response_still_logs_its_usage() -> None:
    recorder = Recorder()
    body = {
        "usage": {
            "input_tokens": 6000,
            "output_tokens": 700,
            "input_tokens_details": {"cached_tokens": 4000},
        }
    }
    raw = _RawResponse(body, _schema_error())
    backend = UsageOpenAIPlannerBackend(_settings(), recorder)
    backend._client = SimpleNamespace(  # type: ignore[assignment]
        responses=SimpleNamespace(with_raw_response=SimpleNamespace(parse=lambda **_k: raw))
    )

    with pytest.raises(LLMUnavailableError):
        backend.complete("s", "u")  # schema-invalid twice: the attempt and its retry

    assert len(recorder.usages) == 2
    assert recorder.usages[0].cached_tokens == 4000
    assert all(u.role == "planner" for u in recorder.usages)
