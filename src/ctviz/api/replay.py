"""Keyless offline demo mode (Task 8.1, `PLANNER_MODE=replay`): a planner, judge and
ClinicalTrials.gov client that never touch the network or an LLM.

`make demo-offline` starts the API in this mode. A fixed set of example questions
(`examples/canned_plans.json`) each map to a hand-written `QueryPlan` and name the fixture that
serves it; `ReplayPlanner` answers only those questions (a normalized-text lookup), and
`ReplayCtGovClient` serves each plan's fixture (`tests/fixtures/ctgov/*.json.gz`) instead of
calling `https://clinicaltrials.gov`. `ReplayJudge` runs no review (`judge.status="skipped"`) --
everything downstream of the planner/judge/client boundary (checks, probe, fetch, aggregate,
cite, verify) is the exact same production code path as a live request.

Ruling: replay reads the recorded fixtures by path from `tests/fixtures/ctgov/` (resolved from the
repo root) instead of shipping a second copy. Replay is a repo-checkout feature already (it needs
`examples/canned_plans.json`), and reading a data file by path is not importing test code. Cost
if wrong: replay fails with a clear error from a distribution that omits `tests/` (checked at
startup by `build_replay_ctgov_client`).
"""

import gzip
import json
import logging
import re
from collections.abc import Sequence
from datetime import date
from functools import cache, lru_cache
from pathlib import Path
from typing import Any, NoReturn

from ctviz.agent.judge import Judge, JudgeReview
from ctviz.agent.overlay import FieldOverride
from ctviz.agent.planner import Planner
from ctviz.config import Settings
from ctviz.ctgov.client import CtGovClient, FetchResult, RequestLog
from ctviz.ctgov.compiler import compile_plan
from ctviz.errors import PlanInvalidError, UpstreamError
from ctviz.schemas.plan import QueryPlan
from ctviz.schemas.request import VisualizeRequest

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLES_DIR = REPO_ROOT / "examples"
CANNED_PLANS_PATH = EXAMPLES_DIR / "canned_plans.json"
REPLAY_FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "ctgov"
REPLAY_API_VERSION = "replay"
REPLAY_DATA_TIMESTAMP = "replay"
REPLAY_MODEL = "replay"
ParamsKey = tuple[tuple[str, str], ...]


def normalize_query(text: str) -> str:
    """One spelling for a query: lowercase, non-alphanumerics dropped, whitespace collapsed."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", "", text.lower())).strip()


class _CannedExample:
    """One offline example: the question it answers, its plan, and which fixture serves it."""

    __slots__ = ("display_query", "fixture", "plan")

    def __init__(self, display_query: str, fixture: str, plan: QueryPlan) -> None:
        self.display_query = display_query
        self.fixture = fixture
        self.plan = plan


@lru_cache(maxsize=1)
def load_canned_examples() -> tuple[_CannedExample, ...]:
    """Parse `examples/canned_plans.json` once per process into validated `QueryPlan`s."""
    body = json.loads(CANNED_PLANS_PATH.read_text(encoding="utf-8"))
    return tuple(
        _CannedExample(e["query"], e["fixture"], QueryPlan.model_validate(e["plan"]))
        for e in body["examples"]
    )


def _examples_by_normalized_query() -> dict[str, _CannedExample]:
    """The canned examples keyed by normalized query text, for `ReplayPlanner`'s lookup."""
    return {normalize_query(e.display_query): e for e in load_canned_examples()}


def available_example_queries() -> list[str]:
    """Every example question replay mode can answer, in their original display casing."""
    return [e.display_query for e in load_canned_examples()]


def unknown_query_error() -> PlanInvalidError:
    """The replay "examples only" PLAN_INVALID, listing what replay mode CAN answer."""
    available = available_example_queries()
    message = (
        "Replay mode only answers its offline example questions (no live planner is "
        f"configured). Available example queries: {'; '.join(available)}"
    )
    return PlanInvalidError([message], details={"available_queries": available})


class ReplayPlanner(Planner):
    """`Planner` (replay mode): returns the canned `QueryPlan` for a known example question.

    Overrides `plan()` directly rather than swapping only the `PlannerBackend`, because a
    `PlannerBackend` only ever sees the rendered system/user prompt strings (`Planner` builds
    those from the catalog) -- not the caller's original `VisualizeRequest.query`, which is what
    this needs to key on.
    """

    def __init__(self) -> None:
        super().__init__(_NeverCalledBackend(), model_name=REPLAY_MODEL)

    def plan(
        self,
        request: VisualizeRequest,
        feedback: list[str] | None = None,
        previous: QueryPlan | None = None,
        today: date | None = None,
    ) -> QueryPlan:
        """The matching canned plan, or `PlanInvalidError` naming the available examples."""
        del feedback, previous, today  # replay never revises: one canned plan per question
        examples = _examples_by_normalized_query()
        key = normalize_query(request.query)
        example = examples.get(key)
        if example is None:
            raise unknown_query_error()
        return example.plan


class _NeverCalledBackend:
    """A `PlannerBackend` that is never actually invoked: `ReplayPlanner` overrides `plan()`."""

    def complete(self, system: str, user: str) -> QueryPlan:
        """Unreachable: `ReplayPlanner.plan()` never delegates to a backend."""
        raise AssertionError("ReplayPlanner should never call its backend")


class _NeverCalledJudgeBackend:
    """A `JudgeBackend` that is never invoked: `ReplayJudge.review()` runs no review at all."""

    def complete(self, system: str, user: str, budget_s: float) -> NoReturn:
        """Unreachable: `ReplayJudge` never delegates to a backend."""
        raise AssertionError("ReplayJudge should never call its backend")


class ReplayJudge(Judge):
    """`Judge` (replay mode): no review and no OpenRouter call; reports `status="skipped"`."""

    def __init__(self) -> None:
        super().__init__(_NeverCalledJudgeBackend(), model_name=REPLAY_MODEL)  # type: ignore[arg-type]

    def review(
        self,
        request: VisualizeRequest,
        plan: QueryPlan,
        overrides: list[FieldOverride],
        probe_totals: dict[str, int],
        today: date | None = None,
        budget_s: float = 0.0,
    ) -> JudgeReview:
        """Skip: a canned plan has no model to review it, and replay never revises."""
        del request, plan, overrides, probe_totals, today, budget_s
        return JudgeReview(
            needs_revision=False, issues=(), model=self.model_name, available=True, skipped=True
        )


def build_replay_judge() -> Judge:
    """A `Judge` that skips review, for replay mode."""
    return ReplayJudge()


# --- offline ClinicalTrials.gov client --------------------------------------------------------


def _params_key(params: dict[str, str]) -> ParamsKey:
    """A hashable, order-free identity for one compiled request's params."""
    return tuple(sorted(params.items()))


@lru_cache(maxsize=1)
def _fixture_by_params() -> dict[ParamsKey, str]:
    """Each canned plan's compiled params -> the fixture its `fixture` field names."""
    return {
        _params_key(spec.params): example.fixture
        for example in load_canned_examples()
        for spec in compile_plan(example.plan)
    }


def _all_fixture_names() -> tuple[str, ...]:
    """Every distinct fixture a canned example names, in file order."""
    return tuple(dict.fromkeys(example.fixture for example in load_canned_examples()))


@cache
def _load_replay_fixture(name: str) -> tuple[dict[str, Any], ...]:
    """Decompress + parse one replay fixture once per process, cached by name."""
    path = REPLAY_FIXTURES_DIR / f"{name}.json.gz"
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        body: dict[str, Any] = json.load(handle)
    return tuple(body["records"])


def _nct_lookup_ids(params: dict[str, str]) -> list[str] | None:
    """The NCT IDs of an ID-only lookup (the §8.4 fast path: `filter.ids`, no search terms)."""
    if any(key.startswith("query.") for key in params):
        return None
    ids = params.get("filter.ids")
    return ids.split(",") if ids else None


class ReplayCtGovClient(CtGovClient):
    """`CtGovClient` (replay mode): serves pre-recorded fixture records for a known example's
    search terms, or a known NCT ID's own record; no HTTP call is ever made. Every other pipeline
    stage (strict match, aggregate, cite, verify) runs unchanged against this data."""

    async def probe(self, params: dict[str, str]) -> int:
        """The matching record count, or 0 for params no example recognizes."""
        return len(self._records_for(params))

    async def fetch_all(self, params: dict[str, str], max_records: int) -> FetchResult:
        """Every matching record, capped at `max_records` (mirrors the live client)."""
        all_records = self._records_for(params)
        records = list(all_records[:max_records])
        request_log = RequestLog(
            url="replay://studies", fetched_at=REPLAY_DATA_TIMESTAMP, records=len(records)
        )
        return FetchResult(
            records=records,
            api_total_count=len(all_records),
            truncated=len(all_records) > max_records,
            truncation_rule=None,
            requests=[request_log],
        )

    async def get_study(self, nct_id: str) -> dict[str, Any]:
        """Look up one NCT ID across every replay fixture (not on the pipeline's own hot path)."""
        record = _find_record(nct_id)
        if record is None:
            raise UpstreamError(f"{nct_id} is not in any replay fixture")
        return dict(record)

    async def version(self) -> dict[str, Any]:
        """A fixed replay stamp -- `meta.provenance` still discloses that this ran offline."""
        return {"apiVersion": REPLAY_API_VERSION, "dataTimestamp": REPLAY_DATA_TIMESTAMP}

    def _records_for(self, params: dict[str, str]) -> Sequence[dict[str, Any]]:
        """The records answering these params: a known NCT ID's own record, or its example's
        fixture; `()` for an unknown example. An NCT lookup that finds nothing is not "no
        trials on ClinicalTrials.gov" -- replay only has examples -- so it raises instead."""
        nct_ids = _nct_lookup_ids(params)
        if nct_ids is not None:
            found = [r for r in map(_find_record, nct_ids) if r is not None]
            if not found:
                raise unknown_query_error()
            return found
        name = _fixture_by_params().get(_params_key(params))
        return () if name is None else _load_replay_fixture(name)


def _find_record(nct_id: str) -> dict[str, Any] | None:
    """The first replay-fixture record with this NCT ID, or `None`."""
    for name in _all_fixture_names():
        for record in _load_replay_fixture(name):
            if _nct_id_of(record) == nct_id:
                return record
    return None


def _nct_id_of(record: dict[str, Any]) -> str | None:
    """A raw study record's NCT ID, or `None` if the identification module is missing."""
    protocol = record.get("protocolSection", {})
    nct_id = protocol.get("identificationModule", {}).get("nctId")
    return nct_id if isinstance(nct_id, str) else None


def build_replay_planner(settings: Settings) -> Planner:
    """A `Planner` that only answers replay mode's canned example questions."""
    del settings  # no keys needed for replay; kept for a uniform `build_*` signature
    return ReplayPlanner()


def build_replay_ctgov_client() -> CtGovClient:
    """A `CtGovClient` that only serves replay mode's offline fixtures (checked to exist)."""
    if not REPLAY_FIXTURES_DIR.is_dir():
        raise FileNotFoundError(
            f"Replay fixtures not found at {REPLAY_FIXTURES_DIR}: replay mode needs a repo "
            "checkout that includes tests/fixtures/ctgov"
        )
    return ReplayCtGovClient()
