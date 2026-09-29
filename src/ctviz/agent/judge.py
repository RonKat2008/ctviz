"""The plan judge: a different-family model (OpenRouter) reviews the plan before any real fetch.

The judge only ever *advises*. Its verdict string is recorded but never trusted: code discards
every issue aimed at a slot a structured field filled (the user's field is authoritative), then
revises iff at least one critical or major issue remains (PLAN.md §9.2). Any judge failure --
missing key, transport error, 402/503, a second 429 or schema-invalid output, an exhausted time
budget, a verdict that skips the 7-check rubric, any unexpected exception -- fails open: the
review comes back `available=False` and the plan still runs, flagged (§9.5). The §9.5 model
tiers (tier 1 -> tier 2 fallback) live in `judge_backends.py`; `JudgeReview.model` is the model
that actually answered. SDK error text is never logged or surfaced (type + status only).
"""

import dataclasses
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import get_args

from ctviz.agent.judge_backends import JudgeAnswer, JudgeBackend, build_judge_backend
from ctviz.agent.overlay import FieldOverride, structured_slots
from ctviz.agent.prompts import build_judge_system, build_judge_user
from ctviz.catalog.loader import load_catalog
from ctviz.config import JUDGE_MIN_CALL_S, JUDGE_REQUEST_BUDGET_S, Settings
from ctviz.schemas.judge import JudgeIssue, JudgeVerdict, RubricCheck
from ctviz.schemas.plan import QueryPlan
from ctviz.schemas.request import VisualizeRequest

log = logging.getLogger(__name__)
REVISING_SEVERITIES = frozenset({"critical", "major"})
_POINTER_INDEX = re.compile(r"\.(\d+)(?=\.|$)")
_JSONPATH_ROOT = re.compile(r"^\$\.?")
_EDGE_CHARS = "\"'`\u201c\u201d\u2018\u2019.,;:!?()[]{}"
RUBRIC_CHECKS: frozenset[str] = frozenset(get_args(RubricCheck))
_FAMILY_PREFIXES = (
    (re.compile(r"^(gpt|o\d|chatgpt|text-|davinci)"), "openai"),
    (re.compile(r"^claude"), "anthropic"),
    (re.compile(r"^(gemini|gemma)"), "google"),
)


@dataclass(frozen=True)
class JudgeReview:
    """The code-side reading of one judge call: whether to revise, and what to disclose.
    `model` is the model that answered (a fallback tier's, if tier 1 failed); on an
    unavailable review it is the configured tier-1 model."""

    needs_revision: bool
    issues: tuple[JudgeIssue, ...]
    model: str | None
    available: bool
    model_verdict: str | None = None
    confidence: str | None = None
    failed_checks: tuple[str, ...] = ()
    discarded: int = 0
    discarded_ungrounded: int = 0  # issues whose evidence quote is not in the judge's context
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
    return _revising(kept_issues(verdict, overridden_paths))


def _revising(issues: list[JudgeIssue]) -> bool:
    """Whether any of `issues` is severe enough to force a revise."""
    return any(i.severity in REVISING_SEVERITIES for i in issues)


def _normalize_text(text: str) -> str:
    """Casefold, collapse whitespace, strip quotes/punctuation from both ends."""
    return " ".join(text.casefold().split()).strip(_EDGE_CHARS + " ")


def grounding_corpus(
    request: VisualizeRequest,
    plan: QueryPlan,
    overrides: list[FieldOverride],
    probe_totals: dict[str, int],
) -> str:
    """Everything the judge was shown, normalized: query, field names/values, overrides, plan
    JSON and probe totals -- the only text an issue's evidence quote may come from."""
    fields = request.model_dump(mode="json", exclude_none=True, exclude={"query"})
    parts = [
        request.query,
        json.dumps(fields, default=str),
        json.dumps([o.model_dump() for o in overrides], default=str),
        json.dumps(probe_totals),
        json.dumps(plan.model_dump(mode="json"), indent=2),
    ]
    return " ".join(" ".join(parts).casefold().split())


def _is_grounded(issue: JudgeIssue, corpus: str) -> bool:
    """A non-empty evidence quote that appears verbatim (after normalization) in `corpus`."""
    quote = _normalize_text(issue.evidence_quote)
    return bool(quote) and quote in corpus


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


def _review_of(answer: JudgeAnswer, slots: frozenset[str], corpus: str) -> JudgeReview:
    """Recompute the decision from the per-issue answers; the model's verdict is only recorded.
    Issues on structured slots, then issues whose evidence quote is not in `corpus`, are dropped."""
    verdict = answer.verdict
    slot_kept = kept_issues(verdict, slots)
    kept = [i for i in slot_kept if _is_grounded(i, corpus)]
    failed = tuple(c.check for c in verdict.checks if not c.passed)
    if failed:
        log.info("Judge failed checks (minor unless an issue is major): %s", ", ".join(failed))
    return JudgeReview(
        needs_revision=_revising(kept),
        issues=tuple(kept),
        model=answer.model,
        available=True,
        model_verdict=verdict.verdict,
        confidence=verdict.confidence,
        failed_checks=failed,
        discarded=len(verdict.issues) - len(slot_kept),
        discarded_ungrounded=len(slot_kept) - len(kept),
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
        slots = _overridden_paths(plan, request, overrides)
        corpus = grounding_corpus(request, plan, overrides, probe_totals)
        review = self._complete(system, user, budget_s, slots, corpus)
        return dataclasses.replace(review, elapsed_s=self._clock() - start)

    def _complete(
        self, system: str, user: str, budget_s: float, slots: frozenset[str], corpus: str
    ) -> JudgeReview:
        """One backend round (its own single retry included), read by the code-side rule."""
        try:
            answer = self._backend.complete(system, user, budget_s=budget_s)
        # Fix I: the single judge boundary. The judge only ever advises, so ANY failure here --
        # an SDK error, or a bug/unexpected exception in a backend -- fails open rather than
        # failing the request; only the type (+ status) is logged, never the exception text.
        except Exception as exc:  # noqa: BLE001
            status = getattr(exc, "status_code", None)
            log.warning(
                "Judge unavailable, failing open: %s (status=%s)", type(exc).__name__, status
            )
            return _unavailable(self.model_name)
        if not answers_the_rubric(answer.verdict):
            log.warning("Judge verdict did not answer the 7 rubric checks; failing open")
            return _unavailable(self.model_name)
        return _review_of(answer, slots, corpus)


def build_judge(settings: Settings) -> Judge:
    """Build the process-wide `Judge` once; a missing key means "unavailable", never a crash."""
    return Judge(build_judge_backend(settings), model_name=settings.judge_model)
