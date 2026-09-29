"""Judge backends: the §9.5 tiers over OpenRouter, sharing one per-request deadline.

Tier 1 is `settings.judge_model` (gemini-2.5-flash-lite), tier 2 `settings.judge_fallback_model`
(claude-haiku-4.5; empty disables it). `TieredJudgeBackend` computes ONE deadline per review
(clock() + budget_s, the orchestrator having already subtracted what an earlier review spent)
and every tier's calls time out inside it; a tier starts only if >= JUDGE_MIN_CALL_S remains.
A schema-invalid output, a refusal, a 503 or any transport error on a tier moves on to the next
tier (no same-tier retry); a 402 (credits) skips every remaining tier of that provider; a 429 is
retried once on the same tier after its bounded Retry-After. Only when no tier answers is the
review unavailable (fail open). SDK error text is never logged (type + status only).
"""

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, cast

from openai import APIStatusError, OpenAI, OpenAIError, RateLimitError
from pydantic import ValidationError

from ctviz.config import JUDGE_MIN_CALL_S, JUDGE_REQUEST_BUDGET_S, JUDGE_TIMEOUT_S, Settings
from ctviz.errors import JudgeUnavailableError
from ctviz.schemas.judge import JudgeVerdict

log = logging.getLogger(__name__)
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER = "openrouter"
JUDGE_UNAVAILABLE_MESSAGE = "The judge LLM is unavailable; the plan runs unreviewed."
JUDGE_MAX_TOKENS = 2_000
RATE_LIMIT_DEFAULT_WAIT_S = 1.0
RATE_LIMIT_MAX_WAIT_S = 2.0
CREDITS_EXHAUSTED_STATUS = 402


@dataclass(frozen=True)
class JudgeAnswer:
    """A validated verdict and the model that actually produced it (tier 1 or a fallback)."""

    verdict: JudgeVerdict
    model: str


class JudgeBackend(Protocol):
    """Anything that can turn a system/user prompt pair into a `JudgeAnswer` within a budget."""

    def complete(
        self, system: str, user: str, budget_s: float = JUDGE_REQUEST_BUDGET_S
    ) -> JudgeAnswer:
        """Produce a verdict for this prompt pair within `budget_s` seconds (fix B)."""
        ...


class JudgeTier(Protocol):
    """One model of the tier chain, answering by an absolute deadline."""

    model: str
    provider: str

    def answer(self, system: str, user: str, deadline: float) -> JudgeVerdict:
        """A verdict by `deadline`; raises `JudgeTierError` (or `JudgeCreditsError`) on failure."""
        ...


class JudgeTierError(JudgeUnavailableError):
    """This tier failed; the next tier may still answer."""


class JudgeCreditsError(JudgeTierError):
    """HTTP 402 on this tier: its provider is out of credits, so its other tiers are skipped."""


def judge_tier_models(settings: Settings) -> tuple[str, ...]:
    """The configured judge models in tier order; an empty or duplicate fallback adds nothing."""
    fallback = settings.judge_fallback_model.strip()
    if not fallback or fallback == settings.judge_model:
        return (settings.judge_model,)
    return settings.judge_model, fallback


def _retry_after_s(exc: RateLimitError) -> float:
    """The server's Retry-After, bounded to [0, RATE_LIMIT_MAX_WAIT_S]; a default if unusable."""
    header = exc.response.headers.get("retry-after")
    try:
        wait = float(header) if header is not None else RATE_LIMIT_DEFAULT_WAIT_S
    except ValueError:
        wait = RATE_LIMIT_DEFAULT_WAIT_S
    return max(0.0, min(wait, RATE_LIMIT_MAX_WAIT_S))


class OpenRouterJudgeBackend:
    """One judge model over OpenRouter's OpenAI-compatible `chat.completions.parse` (§9.5)."""

    provider = OPENROUTER

    def __init__(
        self,
        settings: Settings,
        model: str | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """No SDK retries: this class decides what is retried (only a 429, once, in budget)."""
        if settings.openrouter_api_key is None:
            raise JudgeUnavailableError("OPENROUTER_API_KEY is not configured")
        self._client = OpenAI(
            api_key=settings.openrouter_api_key.get_secret_value(),
            base_url=OPENROUTER_BASE_URL,
            timeout=JUDGE_TIMEOUT_S,
            max_retries=0,
        )
        self.model = model or settings.judge_model
        self._headers = {"HTTP-Referer": settings.app_url, "X-Title": settings.app_name}
        self._sleep = sleep
        self._clock = clock

    def complete(
        self, system: str, user: str, budget_s: float = JUDGE_REQUEST_BUDGET_S
    ) -> JudgeAnswer:
        """This model alone, as a one-tier `JudgeBackend`."""
        return JudgeAnswer(self.answer(system, user, self._clock() + budget_s), self.model)

    def answer(self, system: str, user: str, deadline: float) -> JudgeVerdict:
        """One structured-output call (+ one budgeted 429 retry); failures become tier errors."""
        try:
            completion = self._call_with_rate_limit_retry(system, user, deadline)
        except OpenAIError as exc:
            status = getattr(exc, "status_code", None)
            log.warning(
                "Judge tier %s failed: %s (status=%s)", self.model, type(exc).__name__, status
            )
            if isinstance(exc, APIStatusError) and status == CREDITS_EXHAUSTED_STATUS:
                raise JudgeCreditsError(JUDGE_UNAVAILABLE_MESSAGE) from exc
            raise JudgeTierError(JUDGE_UNAVAILABLE_MESSAGE) from exc
        except ValidationError as exc:
            log.warning("Judge tier %s output failed schema validation", self.model)
            raise JudgeTierError(JUDGE_UNAVAILABLE_MESSAGE) from exc
        choices = getattr(completion, "choices", None) or []
        parsed = choices[0].message.parsed if choices else None
        if parsed is None:
            log.warning("Judge tier %s returned no parsed verdict (refusal or empty)", self.model)
            raise JudgeTierError(JUDGE_UNAVAILABLE_MESSAGE)
        return cast(JudgeVerdict, parsed)

    def _call_with_rate_limit_retry(self, system: str, user: str, deadline: float) -> Any:
        """One call; after a 429 (and its bounded Retry-After) exactly one more -- unless that
        would start with < JUDGE_MIN_CALL_S of the shared deadline left."""
        try:
            return self._call(system, user, deadline)
        except RateLimitError as exc:
            wait = _retry_after_s(exc)
            if deadline - self._clock() - wait < JUDGE_MIN_CALL_S:
                log.warning("Judge tier %s 429 retry skipped: budget exhausted", self.model)
                raise
            log.info("Retrying judge tier %s once after a 429", self.model)
            if wait > 0:
                self._sleep(wait)
        return self._call(system, user, deadline)

    def _request(self, system: str, user: str, deadline: float) -> dict[str, Any]:
        """The `chat.completions.parse` arguments; `temperature=0` suits these non-gpt-5 models."""
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": JudgeVerdict,
            "temperature": 0,
            "max_tokens": JUDGE_MAX_TOKENS,
            "extra_body": {"provider": {"require_parameters": True}},
            "extra_headers": self._headers,
            "timeout": min(JUDGE_TIMEOUT_S, max(deadline - self._clock(), 0.0)),
        }

    def _call(self, system: str, user: str, deadline: float) -> Any:
        """One `chat.completions.parse` call, timed out within the remaining budget."""
        return self._client.chat.completions.parse(**self._request(system, user, deadline))


class TieredJudgeBackend:
    """`JudgeBackend` trying each tier in order against one shared deadline (§9.5)."""

    def __init__(
        self, tiers: Sequence[JudgeTier], clock: Callable[[], float] = time.monotonic
    ) -> None:
        """`tiers` in priority order; `clock` must be the one the tiers time their calls with."""
        self.tiers = tuple(tiers)
        self._clock = clock

    def complete(
        self, system: str, user: str, budget_s: float = JUDGE_REQUEST_BUDGET_S
    ) -> JudgeAnswer:
        """The first tier that answers; `JudgeUnavailableError` only when none does."""
        deadline = self._clock() + budget_s
        out_of_credits: set[str] = set()
        for tier in self.tiers:
            if tier.provider in out_of_credits:
                continue
            if deadline - self._clock() < JUDGE_MIN_CALL_S:
                log.warning("Judge tier %s skipped: judge budget exhausted", tier.model)
                break
            try:
                return JudgeAnswer(tier.answer(system, user, deadline), tier.model)
            except JudgeCreditsError:
                out_of_credits.add(tier.provider)
            except JudgeTierError:
                continue
        raise JudgeUnavailableError(JUDGE_UNAVAILABLE_MESSAGE)


class MissingKeyJudgeBackend:
    """`JudgeBackend` used when no OpenRouter key is configured: every review fails open."""

    def complete(
        self, system: str, user: str, budget_s: float = JUDGE_REQUEST_BUDGET_S
    ) -> JudgeAnswer:
        """Raise immediately: there is no key to call OpenRouter with."""
        raise JudgeUnavailableError("OPENROUTER_API_KEY is not configured")


def build_judge_backend(settings: Settings) -> JudgeBackend:
    """The configured tier chain, or the fail-open backend when no OpenRouter key is set."""
    if settings.openrouter_api_key is None:
        return MissingKeyJudgeBackend()
    tiers = [OpenRouterJudgeBackend(settings, model) for model in judge_tier_models(settings)]
    return TieredJudgeBackend(tiers)
