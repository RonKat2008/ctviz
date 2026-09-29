"""`meta.entity_resolution` shape (item 5): a plain dict, but its keys/shape must stay stable."""

import logging
import subprocess
from collections.abc import Iterator

import httpx
import pytest
import respx

from ctviz.agent.orchestrator import PlanningOutcome
from ctviz.agent.planner import Planner
from ctviz.analysis.aggregate import MatchedTrial
from ctviz.config import CTGOV_BASE_URL
from ctviz.ctgov.client import CtGovClient, FetchResult
from ctviz.errors import UpstreamError
from ctviz.pipeline_meta import (
    CODE_VERSION_ENV,
    UNKNOWN,
    _CohortFetch,
    _entity_resolution,
    _git_short_sha,
    _provenance,
    _warnings,
    resolve_code_version,
)
from tests.factories import make_plan
from tests.fixtures.load import load_trials
from tests.unit.agent.test_planner import FakeBackend


def _cohort(label: str, trials: list[MatchedTrial], aliases: tuple[str, ...] = ()) -> _CohortFetch:
    fetch = FetchResult(
        records=[], api_total_count=len(trials), truncated=False, truncation_rule=None
    )
    return _CohortFetch(label, label, trials, fetch, aliases=aliases)


def test_entity_resolution_shape_for_the_pembrolizumab_census() -> None:
    """`entity_resolution` is `{"top_sponsors": [{"name": str, "count": int}, ...]}`, at most 10
    entries, ranked largest-count-first, real (non-uniform) counts."""
    trials = [MatchedTrial(trial, ()) for trial in load_trials("pembrolizumab")]
    cohorts = [_cohort("pembrolizumab", trials)]

    resolution = _entity_resolution(cohorts)

    assert set(resolution) == {"top_sponsors", "aliases"}
    top_sponsors = resolution["top_sponsors"]
    assert isinstance(top_sponsors, list)
    assert 1 <= len(top_sponsors) <= 10
    for entry in top_sponsors:
        assert set(entry) == {"name", "count"}
        assert isinstance(entry["name"], str) and entry["name"]
        assert isinstance(entry["count"], int) and entry["count"] > 0
    counts = [entry["count"] for entry in top_sponsors]
    assert counts == sorted(counts, reverse=True)
    assert len(set(counts)) > 1  # real counts, not every sponsor forced to the same number


def test_entity_resolution_reports_each_cohorts_learned_aliases() -> None:
    """§10.4: "Aliases are reported in meta.entity_resolution" -- per cohort, in order."""
    cohorts = [
        _cohort("Pembrolizumab", [], aliases=("keytruda", "mk-3475")),
        _cohort("Nivolumab", [], aliases=("opdivo",)),
    ]

    resolution = _entity_resolution(cohorts)

    assert resolution["aliases"] == {
        "Pembrolizumab": ["keytruda", "mk-3475"],
        "Nivolumab": ["opdivo"],
    }


_PROBE_EMPTY = "cohort 'Nivolumab' has 0 trials on ClinicalTrials.gov; it is shown as zero bars"


def _outcome(totals: dict[str, int], warnings: list[str]) -> PlanningOutcome:
    return PlanningOutcome(
        make_plan(), None, "passed_after_revision", 2, [], probe_totals=totals, warnings=warnings
    )


def test_an_empty_cohort_the_probe_already_disclosed_is_warned_about_once() -> None:
    """Fix J: the probe's zero-cohort disclosure and the fetch's zero-bars note are one fact."""
    pembro = [MatchedTrial(t, ()) for t in load_trials("pembrolizumab")[:3]]
    cohorts = [_cohort("Pembrolizumab", pembro), _cohort("Nivolumab", [])]
    outcome = _outcome({"Pembrolizumab": 3, "Nivolumab": 0}, [_PROBE_EMPTY])

    warnings = _warnings(outcome.plan, cohorts, outcome)  # type: ignore[arg-type]

    assert [w for w in warnings if "Nivolumab" in w] == [_PROBE_EMPTY]


def test_a_cohort_emptied_only_by_strict_match_is_still_warned_about() -> None:
    """Fix J scope: the probe saw trials (>0) but strict match kept none -> still disclosed."""
    pembro = [MatchedTrial(t, ()) for t in load_trials("pembrolizumab")[:3]]
    cohorts = [_cohort("Pembrolizumab", pembro), _cohort("Nivolumab", [])]
    outcome = _outcome({"Pembrolizumab": 3, "Nivolumab": 4}, [])

    warnings = _warnings(outcome.plan, cohorts, outcome)  # type: ignore[arg-type]

    assert warnings == ["cohort 'Nivolumab' matched 0 trials; kept as zero bars, not dropped"]


# --- D3: provenance -- real api_version/data_timestamp, real code_version -------------------


def test_git_short_sha_matches_the_real_checkouts_head() -> None:
    """The raw resolver returns the same short SHA `git rev-parse --short HEAD` would."""
    expected = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()

    assert _git_short_sha() == expected


def test_git_short_sha_falls_back_to_unknown_when_git_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Never raises: an unusable git binary (not a checkout, missing) degrades to "unknown"."""

    def _boom(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError("git not found")

    monkeypatch.setattr(subprocess, "run", _boom)

    assert _git_short_sha() == UNKNOWN


def test_resolve_code_version_is_a_non_empty_sha_in_this_checkout() -> None:
    """The cached, process-wide entry point returns a real (non-"unknown") sha here."""
    assert resolve_code_version() != UNKNOWN
    assert resolve_code_version() == resolve_code_version()  # stable across calls (lru_cache)


async def test_provenance_reads_api_version_and_data_timestamp_from_client_version() -> None:
    planner = Planner(FakeBackend(make_plan()), model_name="gpt-5.4-mini")
    with respx.mock:
        respx.get(f"{CTGOV_BASE_URL}/version").mock(
            return_value=httpx.Response(
                200, json={"apiVersion": "2.0.5", "dataTimestamp": "2026-09-28T12:00:05"}
            )
        )
        async with CtGovClient() as client:
            provenance = await _provenance([], planner, client)

    assert provenance.api_version == "2.0.5"
    assert provenance.data_timestamp == "2026-09-28T12:00:05"
    assert provenance.planner_model == "gpt-5.4-mini"
    assert provenance.code_version == resolve_code_version()


async def test_provenance_falls_back_to_unknown_when_version_is_unreachable() -> None:
    """D3: a failed `/version` call degrades to "unknown" + a warning, never a 500."""
    planner = Planner(FakeBackend(make_plan()), model_name="gpt-5.4-mini")
    with respx.mock:
        respx.get(f"{CTGOV_BASE_URL}/version").mock(return_value=httpx.Response(503, text="down"))
        async with CtGovClient(backoff_base_s=0) as client:
            provenance = await _provenance([], planner, client)

    assert provenance.api_version == UNKNOWN
    assert provenance.data_timestamp == UNKNOWN


# --- S8 fix pass: code_version resolved once; env override; provenance logging -------------


@pytest.fixture
def fresh_code_version() -> Iterator[None]:
    """`resolve_code_version` is process-cached; isolate it per test."""
    resolve_code_version.cache_clear()
    yield
    resolve_code_version.cache_clear()


def test_git_sha_is_resolved_once_across_many_provenance_calls(
    monkeypatch: pytest.MonkeyPatch, fresh_code_version: None
) -> None:  # m15
    import asyncio

    calls: list[int] = []
    monkeypatch.setattr("ctviz.pipeline_meta._git_short_sha", lambda: calls.append(1) or "abc1234")
    monkeypatch.delenv(CODE_VERSION_ENV, raising=False)
    planner = Planner(FakeBackend(make_plan()), model_name="m")

    async def _run() -> list[str]:
        with respx.mock:
            respx.get(f"{CTGOV_BASE_URL}/version").mock(
                return_value=httpx.Response(200, json={"apiVersion": "1", "dataTimestamp": "t"})
            )
            async with CtGovClient() as client:
                return [(await _provenance([], planner, client)).code_version for _ in range(3)]

    assert asyncio.run(_run()) == ["abc1234"] * 3
    assert len(calls) == 1


def test_app_lifespan_resolves_code_version_at_startup_not_on_first_request(
    monkeypatch: pytest.MonkeyPatch, fresh_code_version: None
) -> None:
    from fastapi.testclient import TestClient

    from ctviz.api.app import app

    calls: list[int] = []
    monkeypatch.setattr("ctviz.pipeline_meta._git_short_sha", lambda: calls.append(1) or "abc1234")
    monkeypatch.delenv(CODE_VERSION_ENV, raising=False)

    with TestClient(app):  # startup only: no request has been made
        assert len(calls) == 1


def test_code_version_prefers_the_env_var_over_git(
    monkeypatch: pytest.MonkeyPatch, fresh_code_version: None
) -> None:
    monkeypatch.setenv(CODE_VERSION_ENV, "zip-2026.09.29")
    monkeypatch.setattr("ctviz.pipeline_meta._git_short_sha", lambda: "gitsha1")

    assert resolve_code_version() == "zip-2026.09.29"


def test_code_version_falls_back_to_git_then_unknown(
    monkeypatch: pytest.MonkeyPatch, fresh_code_version: None
) -> None:
    monkeypatch.delenv(CODE_VERSION_ENV, raising=False)
    monkeypatch.setattr("ctviz.pipeline_meta._git_short_sha", lambda: "gitsha1")
    assert resolve_code_version() == "gitsha1"

    resolve_code_version.cache_clear()
    monkeypatch.setattr("ctviz.pipeline_meta._git_short_sha", lambda: UNKNOWN)
    assert resolve_code_version() == UNKNOWN


class _RaisingClient:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def version(self) -> dict[str, str]:
        raise self._exc


async def test_upstream_error_in_provenance_logs_a_typed_warning_without_traceback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from ctviz.pipeline_meta import _api_version_and_data_timestamp

    with caplog.at_level(logging.WARNING, logger="ctviz.pipeline_meta"):
        result = await _api_version_and_data_timestamp(
            _RaisingClient(UpstreamError("down"))  # type: ignore[arg-type]
        )

    assert result == (UNKNOWN, UNKNOWN)
    (record,) = caplog.records
    assert record.levelno == logging.WARNING and "UpstreamError" in record.getMessage()
    assert record.exc_info is None


async def test_unexpected_error_in_provenance_is_logged_with_a_traceback_and_still_degrades(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from ctviz.pipeline_meta import _api_version_and_data_timestamp

    with caplog.at_level(logging.WARNING, logger="ctviz.pipeline_meta"):
        result = await _api_version_and_data_timestamp(
            _RaisingClient(RuntimeError("boom"))  # type: ignore[arg-type]
        )

    assert result == (UNKNOWN, UNKNOWN)
    (record,) = caplog.records
    assert record.levelno == logging.ERROR and record.exc_info is not None
