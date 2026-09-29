"""The plan judge: a different-family model (OpenRouter) reviews the plan before any real fetch.

The judge only ever *advises*. Its verdict string is recorded but never trusted: code discards
every issue aimed at a slot a structured field filled (the user's field is authoritative), then
revises iff at least one critical or major issue remains (PLAN.md §9.2). Any judge failure --
missing key, transport error, 402/503, a second 429 or schema-invalid output, an exhausted time
budget, a verdict that skips the 7-check rubric, any unexpected exception -- fails open: the
review comes back `available=False` and the plan still runs, flagged (§9.5). SDK error text is
never logged or surfaced (type + status only), since it can echo request headers.
"""

import dataclasses
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol, cast, get_args

from openai import OpenAI, OpenAIError, RateLimitError
from pydantic import ValidationError

from ctviz.agent.overlay import FieldOverride, structured_slots
from ctviz.agent.prompts import build_judge_system, build_judge_user
from ctviz.catalog.loader import load_catalog
from ctviz.config import JUDGE_MIN_CALL_S, JUDGE_REQUEST_BUDGET_S, JUDGE_TIMEOUT_S, Settings
from ctviz.errors import JudgeUnavailableError
from ctviz.schemas.judge import JudgeIssue, JudgeVerdict, RubricCheck
from ctviz.schemas.plan import QueryPlan
from ctviz.schemas.request import VisualizeRequest

log = logging.getLogger(__name__)
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
JUDGE_UNAVAILABLE_MESSAGE = "The judge LLM is unavailable; the plan runs unreviewed."
JUDGE_MAX_TOKENS = 2_000
RATE_LIMIT_DEFAULT_WAIT_S = 1.0
RATE_LIMIT_MAX_WAIT_S = 2.0
REVISING_SEVERITIES = frozenset({"critical", "major"})
_POINTER_INDEX = re.compile(r"\.(\d+)(?=\.|$)")
_JSONPATH_ROOT = re.compile(r"^\$\.?")
RUBRIC_CHECKS: frozenset[str] = frozenset(get_args(RubricCheck))
_FAMILY_PREFIXES = (
    (re.compile(r"^(gpt|o\d|chatgpt|text-|davinci)"), "openai"),
    (re.compile(r"^claude"), "anthropic"),
    (re.compile(r"^(gemini|gemma)"), "google"),
)


class JudgeBackend(Protocol):
    """Anything that can turn a system/user prompt pair into a validated `JudgeVerdict`."""

    def complete(
        self, system: str, user: str, budget_s: float = JUDGE_REQUEST_BUDGET_S
    ) -> JudgeVerdict:
        """Produce a `JudgeVerdict` for this prompt pair within `budget_s` seconds (fix B)."""
        ...


@dataclass(frozen=True)
class JudgeReview:
    """The code-side reading of one judge call: whether to revise, and what to disclose."""

    needs_revision: bool
    issues: tuple[JudgeIssue, ...]
    model: str | None
    available: bool
    model_verdict: str | None = None
    confidence: str | None = None
    failed_checks: tuple[str, ...] = ()
    discarded: int = 0
    elapsed_s: float = 0.0
    skipped: bool = False  # no judge ran at all (replay mode): reported as `judge.status="skipped"`


def model_family(model: str) -> str:
    """The vendor family of a model id ("google/gemini-2.5-flash-lite" -> "google")."""
    name = model.strip().lower()
    if "/" in name:
        return name.split("/", 1)[0]
    for pattern, family in _FAMILY_PREFIXES:
        if pattern.match(name):
            return family
    return name.split("-", 1)[0]


def same_family(planner_model: str, judge_model: str) -> bool:
    """True when planner and judge share a vendor -- disclosed, since they share blind spots."""
    return model_family(planner_model) == model_family(judge_model)


def _normalize_path(path: str) -> str:
    """One spelling for a plan path: 'plan.a[0].b', '/a/0/b', '$.A[0].b' all -> 'a[0].b'."""
    text = _JSONPATH_ROOT.sub("", path.strip().lower()).replace("/", ".").strip(".")
    text = text.removeprefix("plan.")
    return _POINTER_INDEX.sub(r"[\1]", text)


def _in_slot(path: str, slots: frozenset[str]) -> bool:
    """Whether `path` is one of `slots` or lies inside one."""
    norm = _normalize_path(path)
    return any(norm == s or norm.startswith((f"{s}.", f"{s}[")) for s in slots)


def kept_issues(verdict: JudgeVerdict, overridden_paths: frozenset[str]) -> list[JudgeIssue]:
    """The judge's issues minus any aimed at a structured-field slot (§9.2 step 1)."""
    return [i for i in verdict.issues if not _in_slot(i.plan_path, overridden_paths)]


def needs_revision(verdict: JudgeVerdict, overridden_paths: frozenset[str]) -> bool:
    """Revise iff a critical or major issue survives the structured-slot filter (§9.2)."""
    return any(i.severity in REVISING_SEVERITIES for i in kept_issues(verdict, overridden_paths))


def _overridden_paths(
    plan: QueryPlan, request: VisualizeRequest, overrides: list[FieldOverride]
) -> frozenset[str]:
    """Structured slots, plus `comparison` when the overlay dropped a conflicting comparison."""
    dropped = {"comparison"} if any(o.field == "comparison" for o in overrides) else set()
    return structured_slots(plan, request) | dropped


def answers_the_rubric(verdict: JudgeVerdict) -> bool:
    """Fix C: exactly the seven distinct rubric checks, each answered once (§9.2 "all 7")."""
    answered = [c.check for c in verdict.checks]
    return len(answered) == len(RUBRIC_CHECKS) and set(answered) == RUBRIC_CHECKS


def _unavailable(model: str) -> JudgeReview:
    """A fail-open review: the plan runs unreviewed, flagged `unavailable` (§9.5)."""
    return JudgeReview(needs_revision=False, issues=(), model=model, available=False)


def _review_of(verdict: JudgeVerdict, slots: frozenset[str], model: str) -> JudgeReview:
    """Recompute the decision from the per-issue answers; the model's verdict is only recorded."""
    kept = kept_issues(verdict, slots)
    failed = tuple(c.check for c in verdict.checks if not c.passed)
    if failed:
        log.info("Judge failed checks (minor unless an issue is major): %s", ", ".join(failed))
    return JudgeReview(
        needs_revision=needs_revision(verdict, slots),
        issues=tuple(kept),
        model=model,
        available=True,
        model_verdict=verdict.verdict,
        confidence=verdict.confidence,
        failed_checks=failed,
        discarded=len(verdict.issues) - len(kept),
    )


class Judge:
    """Builds the rubric prompt pair, calls a `JudgeBackend`, and applies the code-side rule."""

    def __init__(
        self,
        backend: JudgeBackend,
        model_name: str = "unknown",
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """`model_name` is disclosed in `meta.validation.judge.model`; `clock` times the call."""
        self._backend = backend
        self.model_name = model_name
        self._clock = clock

    def review(
        self,
        request: VisualizeRequest,
        plan: QueryPlan,
        overrides: list[FieldOverride],
        probe_totals: dict[str, int],
        today: date | None = None,
        budget_s: float = JUDGE_REQUEST_BUDGET_S,
    ) -> JudgeReview:
        """Judge one plan within what is left of the request's judge budget (fix B); any
        backend failure, an exhausted budget or an unanswered rubric fails open (unavailable)."""
        if budget_s < JUDGE_MIN_CALL_S:
            log.warning("Judge budget exhausted before this review; failing open")
            return _unavailable(self.model_name)
        system = build_judge_system(load_catalog())
        user = build_judge_user(request, plan, overrides, probe_totals, today or date.today())
        start = self._clock()
        review = self._complete(system, user, budget_s, _overridden_paths(plan, request, overrides))
        return dataclasses.replace(review, elapsed_s=self._clock() - start)

    def _complete(
        self, system: str, user: str, budget_s: float, slots: frozenset[str]
    ) -> JudgeReview:
        """One backend round (its own single retry included), read by the code-side rule."""
        try:
            verdict = self._backend.complete(system, user, budget_s=budget_s)
        # Fix I: the single judge boundary. The judge only ever advises, so ANY failure here --
        # an SDK error, or a bug/unexpected exception in a backend -- fails open rather than
        # failing the request; only the type (+ status) is logged, never the exception text.
        except Exception as exc:  # noqa: BLE001
            status = getattr(exc, "status_code", None)
            log.warning(
                "Judge unavailable, failing open: %s (status=%s)", type(exc).__name__, status
            )
            return _unavailable(self.model_name)
        if not answers_the_rubric(verdict):
            log.warning("Judge verdict did not answer the 7 rubric checks; failing open")
            return _unavailable(self.model_name)
        return _review_of(verdict, slots, self.model_name)


class _MissingKeyJudgeBackend:
    """`JudgeBackend` used when no OpenRouter key is configured: every review fails open."""

    def complete(
        self, system: str, user: str, budget_s: float = JUDGE_REQUEST_BUDGET_S
    ) -> JudgeVerdict:
        """Raise immediately: there is no key to call OpenRouter with."""
        raise JudgeUnavailableError("OPENROUTER_API_KEY is not configured")


def build_judge(settings: Settings) -> Judge:
    """Build the process-wide `Judge` once; a missing key means "unavailable", never a crash."""
    if settings.openrouter_api_key is None:
        return Judge(_MissingKeyJudgeBackend(), model_name=settings.judge_model)
    return Judge(OpenRouterJudgeBackend(settings), model_name=settings.judge_model)


def _retry_after_s(exc: RateLimitError) -> float:
    """The server's Retry-After, bounded to [0, RATE_LIMIT_MAX_WAIT_S]; a default if unusable."""
    header = exc.response.headers.get("retry-after")
    try:
        wait = float(header) if header is not None else RATE_LIMIT_DEFAULT_WAIT_S
    except ValueError:
        wait = RATE_LIMIT_DEFAULT_WAIT_S
    return max(0.0, min(wait, RATE_LIMIT_MAX_WAIT_S))


class OpenRouterJudgeBackend:
    """`JudgeBackend` over OpenRouter's OpenAI-compatible `chat.completions.parse` (§9.5)."""

    def __init__(
        self,
        settings: Settings,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """No SDK retries (402/503 fail open at once); a 429 or a schema-invalid output is
        retried once, by hand, only while the judge budget allows (fix B)."""
        if settings.openrouter_api_key is None:
            raise JudgeUnavailableError("OPENROUTER_API_KEY is not configured")
        self._client = OpenAI(
            api_key=settings.openrouter_api_key.get_secret_value(),
            base_url=OPENROUTER_BASE_URL,
            timeout=JUDGE_TIMEOUT_S,
            max_retries=0,
        )
        self._model = settings.judge_model
        self._headers = {"HTTP-Referer": settings.app_url, "X-Title": settings.app_name}
        self._sleep = sleep
        self._clock = clock

    def complete(
        self, system: str, user: str, budget_s: float = JUDGE_REQUEST_BUDGET_S
    ) -> JudgeVerdict:
        """One structured-output call (+ one budgeted retry); every failure becomes a fixed
        `JudgeUnavailableError`."""
        deadline = self._clock() + budget_s
        try:
            completion = self._call_with_one_retry(system, user, deadline)
        except OpenAIError as exc:
            status = getattr(exc, "status_code", None)
            log.warning("OpenRouter judge call failed: %s (status=%s)", type(exc).__name__, status)
            raise JudgeUnavailableError(JUDGE_UNAVAILABLE_MESSAGE) from exc
        except ValidationError as exc:
            log.warning("Judge output failed schema validation: %s", type(exc).__name__)
            raise JudgeUnavailableError(JUDGE_UNAVAILABLE_MESSAGE) from exc
        choices = getattr(completion, "choices", None) or []
        parsed = choices[0].message.parsed if choices else None
        if parsed is None:
            log.warning("Judge returned no parsed verdict (refusal or empty output)")
            raise JudgeUnavailableError(JUDGE_UNAVAILABLE_MESSAGE)
        return cast(JudgeVerdict, parsed)

    def _call_with_one_retry(self, system: str, user: str, deadline: float) -> Any:
        """One call; on a 429 (after its bounded Retry-After) or a schema-invalid output, try
        exactly once more -- unless that would start with < JUDGE_MIN_CALL_S of budget left."""
        try:
            return self._call(system, user, deadline)
        except (RateLimitError, ValidationError) as exc:
            wait = _retry_after_s(exc) if isinstance(exc, RateLimitError) else 0.0
            if deadline - self._clock() - wait < JUDGE_MIN_CALL_S:
                log.warning("Judge retry skipped, budget exhausted: %s", type(exc).__name__)
                raise
            log.info("Retrying the judge once after: %s", type(exc).__name__)
            if wait > 0:
                self._sleep(wait)
        return self._call(system, user, deadline)

    def _call(self, system: str, user: str, deadline: float) -> Any:
        """One `chat.completions.parse` call, timed out within the remaining budget;
        `temperature=0` is fine for this non-gpt-5 judge."""
        return self._client.chat.completions.parse(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format=JudgeVerdict,
            temperature=0,
            max_tokens=JUDGE_MAX_TOKENS,
            extra_body={"provider": {"require_parameters": True}},
            extra_headers=self._headers,
            timeout=min(JUDGE_TIMEOUT_S, max(deadline - self._clock(), 0.0)),
        )
