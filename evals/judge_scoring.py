"""Scoring the labeled judge suite (PLAN.md §9.6): catch rate, false-alarm rate, availability."""

from collections.abc import Sequence
from dataclasses import dataclass

from ctviz.agent.judge import JudgeReview
from evals.cases import JudgeCase
from evals.scoring import CaseResult, Usage, cost_usd


def _rate(hits: int, total: int) -> float | None:
    """hits / total, or None when there is nothing to divide by."""
    return hits / total if total else None


@dataclass(frozen=True)
class JudgeCaseResult:
    """One scored judge case, using the code-recomputed decision (`needs_revision`)."""

    id: str
    category: str
    label: str
    available: bool
    flagged: bool
    failed_checks: tuple[str, ...]
    issues: tuple[str, ...]
    cost_usd: float
    model: str | None = None  # the judge model that answered (tier 2 when tier 1 failed)

    @property
    def correct(self) -> bool:
        """Available, and flagged exactly when the plan is labeled bad."""
        return self.available and self.flagged == (self.label == "bad")

    @property
    def expected_check_failed(self) -> bool:
        """The judge failed the very rubric check this case was built to exercise."""
        return self.category in self.failed_checks


def score_judge_case(
    case: JudgeCase, review: JudgeReview, usages: Sequence[Usage] = ()
) -> JudgeCaseResult:
    """Score one judge review: flagged = available and code says revise."""
    issues = tuple(f"[{i.severity}] {i.plan_path}: {i.explanation}" for i in review.issues)
    return JudgeCaseResult(
        id=case.id,
        category=case.category,
        label=case.label,
        available=review.available,
        flagged=review.available and review.needs_revision,
        failed_checks=tuple(review.failed_checks),
        issues=issues,
        cost_usd=cost_usd(usages),
        model=review.model,
    )


@dataclass(frozen=True)
class JudgeEvalSummary:
    """§9.6 rates over EVERY labeled case: an unavailable review of a bad plan is a miss (it
    caught nothing); availability (available reviews / reviews) is reported beside them."""

    n_bad: int
    n_good: int
    caught: int
    false_alarms: int
    unavailable: int
    reviews: int
    available: int
    catch_rate: float | None
    false_alarm_rate: float | None
    availability: float | None
    total_cost_usd: float


def summarize_judge(results: Sequence[JudgeCaseResult]) -> JudgeEvalSummary:
    """Catch rate (bad flagged / all bad) and false-alarm rate (good flagged / all good)."""
    bad = [r for r in results if r.label == "bad"]
    good = [r for r in results if r.label == "good"]
    caught = sum(r.flagged for r in bad)
    alarms = sum(r.flagged for r in good)
    available = sum(r.available for r in results)
    return JudgeEvalSummary(
        n_bad=len(bad),
        n_good=len(good),
        caught=caught,
        false_alarms=alarms,
        unavailable=len(results) - available,
        reviews=len(results),
        available=available,
        catch_rate=_rate(caught, len(bad)),
        false_alarm_rate=_rate(alarms, len(good)),
        availability=_rate(available, len(results)),
        total_cost_usd=sum(r.cost_usd for r in results),
    )


def judge_availability(
    cases: Sequence[CaseResult], judged: Sequence[JudgeCaseResult]
) -> tuple[int, int]:
    """(available reviews, all reviews) over planner-case reviews and judge cases."""
    available = sum(c.judge_available for c in cases) + sum(j.available for j in judged)
    return available, sum(c.judge_reviews for c in cases) + len(judged)


def answers_by_model(
    cases: Sequence[CaseResult], judged: Sequence[JudgeCaseResult]
) -> dict[str, int]:
    """How many available reviews each judge model answered (tier 1 vs tier 2)."""
    models = [m for c in cases for m in c.judge_models]
    models += [j.model for j in judged if j.available and j.model]
    return {m: models.count(m) for m in sorted(set(models))}
