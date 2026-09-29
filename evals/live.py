"""Running eval cases: planner cases through the real HTTP endpoint, judge cases through `Judge`.

Planner and judge are injected as factories taking a per-case `Recorder`, so the live runner
wraps the real OpenAI/OpenRouter backends (recording token usage) while the unit tests inject
fakes. Keys are only ever read by the app's own `Settings` inside those backends' constructors.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from fastapi.testclient import TestClient

from ctviz.agent.judge import Judge
from ctviz.agent.judge_backends import (
    JudgeAnswer,
    JudgeBackend,
    OpenRouterJudgeBackend,
    TieredJudgeBackend,
    judge_tier_models,
)
from ctviz.agent.overlay import apply_overlay
from ctviz.agent.planner import OpenAIPlannerBackend, Planner, PlannerBackend
from ctviz.api.app import get_judge, get_planner
from ctviz.config import JUDGE_REQUEST_BUDGET_S, Settings
from ctviz.errors import OutOfScopeError
from ctviz.schemas.plan import QueryPlan
from ctviz.schemas.request import VisualizeRequest
from evals.cases import EvalCase, JudgeCase, plan_from_partial
from evals.judge_scoring import JudgeCaseResult, score_judge_case
from evals.scoring import CaseResult, CaseRun, Usage, score_case


@dataclass
class Recorder:
    """Per-case log of LLM activity: each planner output, token usage, and judge call count."""

    raw_plans: list[QueryPlan | None] = field(default_factory=list)
    usages: list[Usage] = field(default_factory=list)
    judge_calls: int = 0


PlannerFactory = Callable[[Recorder], Planner]
JudgeFactory = Callable[[Recorder], Judge]


class RecordingPlanner(Planner):
    """A `Planner` that logs every raw plan it returns (None for a model refusal)."""

    def __init__(self, backend: PlannerBackend, model_name: str, recorder: Recorder) -> None:
        """Wrap `backend` exactly as the app would, recording into `recorder`."""
        super().__init__(backend, model_name=model_name)
        self._recorder = recorder

    def plan(self, request: VisualizeRequest, *args: Any, **kwargs: Any) -> QueryPlan:
        """Delegate to the real `Planner.plan`, then log its output."""
        try:
            result = super().plan(request, *args, **kwargs)
        except OutOfScopeError:
            self._recorder.raw_plans.append(None)
            raise
        self._recorder.raw_plans.append(result)
        return result


class CountingJudgeBackend:
    """A `JudgeBackend` wrapper that counts every review the judge asks for (a review may make
    several API calls: a 429 retry, a tier-2 fallback -- each logs its own `Usage`)."""

    def __init__(self, inner: JudgeBackend, recorder: Recorder) -> None:
        """Count calls into `recorder`, delegating to `inner`."""
        self._inner = inner
        self._recorder = recorder

    def complete(
        self, system: str, user: str, budget_s: float = JUDGE_REQUEST_BUDGET_S
    ) -> JudgeAnswer:
        """Count, then delegate."""
        self._recorder.judge_calls += 1
        return self._inner.complete(system, user, budget_s=budget_s)


def _body(raw: Any) -> dict[str, Any]:
    """The raw HTTP response's JSON body ({} if unreadable), read BEFORE the SDK validates it."""
    try:
        body = raw.http_response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _planner_usage(body: dict[str, Any]) -> Usage | None:
    """A Responses-API body's token usage (`usage.input_tokens_details.cached_tokens`)."""
    usage = body.get("usage") or {}
    if not usage:
        return None
    cached = (usage.get("input_tokens_details") or {}).get("cached_tokens") or 0
    return Usage("planner", usage.get("input_tokens") or 0, usage.get("output_tokens") or 0, cached)


class UsageOpenAIPlannerBackend(OpenAIPlannerBackend):
    """The real OpenAI planner backend, logging every response's token usage -- also one that
    then fails schema validation, so a retried call's spend is counted (never its content)."""

    def __init__(self, settings: Settings, recorder: Recorder) -> None:
        """Build the real backend from the app's settings."""
        super().__init__(settings)
        self._recorder = recorder

    def _call(self, system: str, user: str, max_output_tokens: int) -> Any:
        """The same request via `with_raw_response`: usage first, then the SDK's own parse."""
        request = self._request(system, user, max_output_tokens)
        raw = self._client.responses.with_raw_response.parse(**request)
        usage = _planner_usage(_body(raw))
        if usage is not None:
            self._recorder.usages.append(usage)
        return raw.parse()


class UsageOpenRouterJudgeBackend(OpenRouterJudgeBackend):
    """One real OpenRouter judge tier, logging every response's token usage under its model."""

    def __init__(self, settings: Settings, model: str, recorder: Recorder) -> None:
        """Build the real tier from the app's settings."""
        super().__init__(settings, model=model)
        self._recorder = recorder

    def _call(self, system: str, user: str, deadline: float) -> Any:
        """The same request via `with_raw_response`: usage first, then the SDK's own parse."""
        request = self._request(system, user, deadline)
        raw = self._client.chat.completions.with_raw_response.parse(**request)
        usage = _body(raw).get("usage") or {}
        if usage:
            prompt, completion = (
                usage.get("prompt_tokens") or 0,
                usage.get("completion_tokens") or 0,
            )
            self._recorder.usages.append(Usage("judge", prompt, completion, model=self.model))
        return raw.parse()


def live_factories(settings: Settings) -> tuple[PlannerFactory, JudgeFactory]:
    """Factories building the app's real planner and judge (with usage logging) per case."""

    def make_planner(rec: Recorder) -> Planner:
        backend = UsageOpenAIPlannerBackend(settings, rec)
        return RecordingPlanner(backend, settings.planner_model, rec)

    def make_judge(rec: Recorder) -> Judge:
        tiers = [UsageOpenRouterJudgeBackend(settings, m, rec) for m in judge_tier_models(settings)]
        backend = CountingJudgeBackend(TieredJudgeBackend(tiers), rec)
        return Judge(backend, model_name=settings.judge_model)

    return make_planner, make_judge


def record_case(
    case: EvalCase, http: TestClient, make_planner: PlannerFactory, make_judge: JudgeFactory
) -> CaseRun:
    """POST one case to /v1/visualize with a recording planner and judge; return what happened."""
    recorder = Recorder()
    planner, judge = make_planner(recorder), make_judge(recorder)
    overrides = http.app.dependency_overrides  # type: ignore[attr-defined]
    overrides[get_planner] = lambda: planner
    overrides[get_judge] = lambda: judge
    start = time.perf_counter()
    response = http.post("/v1/visualize", json=case.request)
    latency = time.perf_counter() - start
    return CaseRun(
        case=case,
        http_status=response.status_code,
        payload=response.json(),
        raw_plans=tuple(recorder.raw_plans),
        usages=tuple(recorder.usages),
        judge_calls=recorder.judge_calls,
        latency_s=latency,
    )


def run_case(
    case: EvalCase, http: TestClient, make_planner: PlannerFactory, make_judge: JudgeFactory
) -> CaseResult:
    """Record one case through the endpoint, then score it."""
    return score_case(record_case(case, http, make_planner, make_judge))


def run_judge_case(case: JudgeCase, make_judge: JudgeFactory, today: date) -> JudgeCaseResult:
    """Overlay the case's raw plan exactly as the orchestrator does, judge it, and score it."""
    recorder = Recorder()
    request = VisualizeRequest.model_validate(case.request)
    plan, overrides = apply_overlay(plan_from_partial(case.plan), request)
    review = make_judge(recorder).review(request, plan, overrides, case.probe_totals, today)
    return score_judge_case(case, review, recorder.usages)
