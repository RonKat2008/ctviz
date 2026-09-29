"""The OpenAI planner: `gpt-5.4-mini` with strict structured outputs (PLAN.md §8).

`Planner` owns prompt assembly and is backend-agnostic (tested with a `FakeBackend`, no
network). `OpenAIPlannerBackend` is the real `PlannerBackend`: it never passes `temperature`,
retries once on a truncated or invalid response, treats a model refusal as out-of-scope (never a
retry), and maps every SDK failure to a fixed `LLMUnavailableError` message so the API layer can
return HTTP 503 without ever leaking a raw SDK exception — or a key fragment in it — to the
client. SDK failure detail (type + status only, never the message body) goes to the server log.
"""

import logging
from datetime import date
from typing import Any, Protocol, cast

from openai import OpenAI, OpenAIError
from pydantic import ValidationError

from ctviz.agent.prompts import build_planner_system, build_planner_user
from ctviz.catalog.loader import load_catalog
from ctviz.config import LLM_TIMEOUT_S, Settings
from ctviz.errors import LLMUnavailableError, OutOfScopeError
from ctviz.schemas.plan import QueryPlan
from ctviz.schemas.request import VisualizeRequest

log = logging.getLogger(__name__)
MAX_OUTPUT_TOKENS = 2_000
RETRY_MAX_OUTPUT_TOKENS = 4_000
CLIENT_UNAVAILABLE_MESSAGE = "The planner LLM is unavailable; try again later."
CLIENT_AUTH_MESSAGE = (
    "The planner LLM's credentials were rejected by the provider; "
    "the server's API key needs attention."
)
AUTH_FAILURE_STATUSES = frozenset({401, 403})


class PlannerBackend(Protocol):
    """Anything that can turn a system/user prompt pair into a validated `QueryPlan`."""

    def complete(self, system: str, user: str) -> QueryPlan:
        """Produce a `QueryPlan` for this system/user prompt pair."""
        ...


class Planner:
    """Builds the catalog-grounded prompt pair and delegates completion to a `PlannerBackend`."""

    def __init__(self, backend: PlannerBackend, model_name: str = "unknown") -> None:
        """`model_name` is recorded for `meta.provenance` only; the backend owns the real call."""
        self._backend = backend
        self.model_name = model_name

    def plan(
        self,
        request: VisualizeRequest,
        feedback: list[str] | None = None,
        previous: QueryPlan | None = None,
        today: date | None = None,
    ) -> QueryPlan:
        """Render the system/user prompts and return the backend's `QueryPlan`."""
        catalog = load_catalog()
        system = build_planner_system(catalog)
        user = build_planner_user(request, feedback, previous, today)
        return self._backend.complete(system, user)


class _MissingKeyBackend:
    """`PlannerBackend` used when no OpenAI key is configured; fails only when actually called."""

    def complete(self, system: str, user: str) -> QueryPlan:
        """Raise immediately: there is no key to call OpenAI with."""
        raise LLMUnavailableError("OPENAI_API_KEY is not configured")


class _RetryNeededError(Exception):
    """Internal signal: this attempt was truncated or schema-invalid; retry with more budget."""


def build_planner(settings: Settings) -> Planner:
    """Build the process-wide `Planner` once; a missing key defers failure to first use, not
    startup (so the app still starts, and body validation still runs, without a key)."""
    if settings.openai_api_key is None:
        return Planner(_MissingKeyBackend(), model_name=settings.planner_model)
    return Planner(OpenAIPlannerBackend(settings), model_name=settings.planner_model)


def _refusal_text(response: Any) -> str | None:
    """The model's refusal explanation, if the response contains one (§8.3)."""
    for item in getattr(response, "output", None) or []:
        for content in getattr(item, "content", None) or []:
            if getattr(content, "type", None) == "refusal":
                return cast(str, content.refusal)
    return None


def _map_sdk_error(exc: OpenAIError) -> LLMUnavailableError:
    """Log type + status only (never SDK text) and pick the fixed client-safe message."""
    status = getattr(exc, "status_code", None)
    if status in AUTH_FAILURE_STATUSES:
        log.error("planner auth failed: %s status=%s", type(exc).__name__, status)
        return LLMUnavailableError(CLIENT_AUTH_MESSAGE)
    log.error("OpenAI planner call failed: %s (status=%s)", type(exc).__name__, status)
    return LLMUnavailableError(CLIENT_UNAVAILABLE_MESSAGE)


class OpenAIPlannerBackend:
    """`PlannerBackend` over `client.responses.parse`: strict structured output, no temperature."""

    def __init__(self, settings: Settings) -> None:
        """Fail fast (as `LLMUnavailableError`) if no OpenAI key is configured at all."""
        if settings.openai_api_key is None:
            raise LLMUnavailableError("OPENAI_API_KEY is not configured")
        self._client = OpenAI(api_key=settings.openai_api_key.get_secret_value())
        self._model = settings.planner_model
        self._effort = settings.planner_reasoning_effort

    def complete(self, system: str, user: str) -> QueryPlan:
        """One attempt; on truncation or invalid JSON, retry once with a larger token budget."""
        try:
            return self._attempt(system, user, MAX_OUTPUT_TOKENS)
        except _RetryNeededError:
            pass
        try:
            return self._attempt(system, user, RETRY_MAX_OUTPUT_TOKENS)
        except _RetryNeededError as exc:
            raise LLMUnavailableError(CLIENT_UNAVAILABLE_MESSAGE) from exc

    def _attempt(self, system: str, user: str, max_output_tokens: int) -> QueryPlan:
        """One `responses.parse` call: refuse -> `OutOfScopeError`; incomplete/invalid -> retry."""
        try:
            response = self._call(system, user, max_output_tokens)
        except OpenAIError as exc:
            raise _map_sdk_error(exc) from exc
        except ValidationError as exc:
            log.warning("Planner response failed schema validation: %s", type(exc).__name__)
            raise _RetryNeededError from exc
        refusal = _refusal_text(response)
        if refusal is not None:
            raise OutOfScopeError(refusal)
        if response.status == "incomplete" or response.output_parsed is None:
            raise _RetryNeededError("incomplete or unparsed response")
        return cast(QueryPlan, response.output_parsed)

    def _request(self, system: str, user: str, max_output_tokens: int) -> dict[str, Any]:
        """The `responses.parse` arguments; the SDK never receives `temperature` for gpt-5.x."""
        return {
            "model": self._model,
            "reasoning": {"effort": self._effort},
            "input": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "text_format": QueryPlan,
            "timeout": LLM_TIMEOUT_S,
            "max_output_tokens": max_output_tokens,
        }

    def _call(self, system: str, user: str, max_output_tokens: int) -> Any:
        """One `responses.parse` call."""
        return self._client.responses.parse(**self._request(system, user, max_output_tokens))
