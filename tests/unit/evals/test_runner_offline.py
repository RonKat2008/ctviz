"""The eval runner end-to-end with fake LLM backends and the offline replay ClinicalTrials.gov
client: the harness plumbing is proven here, with no network and no API key."""

import json
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any, get_args

import openai
import pytest
from evals import run_evals
from evals.cases import EvalCase, JudgeCase
from evals.live import (
    CountingJudgeBackend,
    Recorder,
    RecordingPlanner,
    record_case,
    run_case,
    run_judge_case,
)
from evals.report import RunInfo
from evals.store import save_results
from fastapi.testclient import TestClient

from ctviz.agent.judge import Judge
from ctviz.agent.judge_backends import JudgeAnswer, JudgeTierError, TieredJudgeBackend
from ctviz.api.app import app, get_ctgov_client
from ctviz.api.replay import build_replay_ctgov_client, load_canned_examples
from ctviz.config import JUDGE_REQUEST_BUDGET_S
from ctviz.schemas.judge import CheckResult, JudgeIssue, JudgeVerdict, RubricCheck
from ctviz.schemas.plan import QueryPlan

PSORIASIS = next(e for e in load_canned_examples() if e.fixture == "psoriasis_p2")


class _FakePlannerBackend:
    def __init__(self, plan: QueryPlan) -> None:
        self.plan = plan

    def complete(self, system: str, user: str) -> QueryPlan:
        return self.plan


class _FakeJudgeBackend:
    def __init__(self, verdict: JudgeVerdict) -> None:
        self.verdict = verdict

    def complete(
        self, system: str, user: str, budget_s: float = JUDGE_REQUEST_BUDGET_S
    ) -> JudgeAnswer:
        return JudgeAnswer(self.verdict, "fake-judge")


class _FakeTier:
    """A `JudgeTier`: answers `verdict`, or fails (as a schema-invalid output does) if None."""

    provider = "openrouter"

    def __init__(self, model: str, verdict: JudgeVerdict | None) -> None:
        self.model = model
        self._verdict = verdict

    def answer(self, system: str, user: str, deadline: float) -> JudgeVerdict:
        if self._verdict is None:
            raise JudgeTierError("schema-invalid")
        return self._verdict


def _verdict(failed: str | None = None, issue_path: str | None = None) -> JudgeVerdict:
    checks = [CheckResult(check=c, passed=c != failed, evidence="q") for c in get_args(RubricCheck)]
    issues = (
        [
            JudgeIssue(
                severity="major",
                category="time_range_error",
                plan_path=issue_path,
                evidence_quote="search_terms",
                explanation="e",
                suggested_fix="f",
            )
        ]
        if issue_path
        else []
    )
    verdict = "revise" if issues else "pass"
    return JudgeVerdict(checks=checks, issues=issues, verdict=verdict, confidence="high")


@pytest.fixture
def http() -> Iterator[TestClient]:
    app.dependency_overrides[get_ctgov_client] = build_replay_ctgov_client
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def _make_planner(plan: QueryPlan) -> Any:
    return lambda rec: RecordingPlanner(_FakePlannerBackend(plan), "fake-planner", rec)


def _make_judge(verdict: JudgeVerdict) -> Any:
    return lambda rec: Judge(CountingJudgeBackend(_FakeJudgeBackend(verdict), rec), "fake-judge")


def test_run_case_scores_a_replayed_histogram_request_end_to_end(http: TestClient) -> None:
    case = EvalCase(
        id="histogram_psoriasis",
        klass="histogram",
        request={"query": PSORIASIS.display_query},
        expect={"analysis.kind": "histogram", "visualization.type": ["histogram"]},
        outcome="ok",
        planner_calls=(1,),
        note="",
    )

    result = run_case(case, http, _make_planner(PSORIASIS.plan), _make_judge(_verdict()))

    assert result.outcome_ok, result.outcome_note
    assert result.attempt1_correct and result.final_correct
    assert result.planner_calls == 1 and result.judge_calls == 1
    assert result.judge_status == "passed"
    assert result.citation_passed is True
    assert result.latency_s > 0


def test_run_case_records_the_revise_path_when_the_judge_objects(http: TestClient) -> None:
    case = EvalCase(
        id="histogram_psoriasis",
        klass="histogram",
        request={"query": PSORIASIS.display_query},
        expect={"analysis.kind": "histogram"},
        outcome="ok",
        planner_calls=(2,),
        note="",
    )
    verdict = _verdict("viz_fit", "visualization.type")

    result = run_case(case, http, _make_planner(PSORIASIS.plan), _make_judge(verdict))

    assert result.planner_calls == 2 and result.judge_calls == 2
    assert result.judge_status == "rejected_after_revision"
    assert result.outcome_ok


def test_run_judge_case_structured_override_passes_even_if_the_model_flags_it() -> None:
    case = JudgeCase(
        id="override",
        category="structured_override",
        label="good",
        request={"query": "Pembrolizumab trials by phase since 2015", "start_year": 2018},
        plan={
            "search_terms": [{"param": "query.intr", "value": "pembrolizumab"}],
            "filters": {"start_year_min": 2015},
            "analysis": {"kind": "count_by", "group_by": "phase"},
            "visualization": {"type": "bar_chart"},
        },
        probe_totals={"pembrolizumab": 1500},
        note="",
    )
    verdict = _verdict("time_range", "filters.start_year_min")

    result = run_judge_case(case, _make_judge(verdict), date(2026, 9, 29))

    assert result.available and not result.flagged and result.correct
    assert result.failed_checks == ("time_range",)


def test_run_judge_case_bad_plan_flagged_is_a_catch() -> None:
    case = JudgeCase(
        id="drug_as_condition",
        category="filter_fidelity",
        label="bad",
        request={"query": "Nivolumab trials by phase"},
        plan={
            "search_terms": [{"param": "query.cond", "value": "nivolumab"}],
            "analysis": {"kind": "count_by", "group_by": "phase"},
            "visualization": {"type": "bar_chart"},
        },
        probe_totals={"nivolumab": 40},
        note="",
    )
    verdict = _verdict("filter_fidelity", "search_terms[0].param")

    result = run_judge_case(case, _make_judge(verdict), date(2026, 9, 29))

    assert result.flagged and result.correct and result.expected_check_failed


def test_recorder_starts_empty() -> None:
    recorder = Recorder()

    assert recorder.raw_plans == [] and recorder.usages == [] and recorder.judge_calls == 0


def test_a_tier_two_answer_is_what_meta_validation_judge_model_shows(http: TestClient) -> None:
    # Arrange
    case = EvalCase(
        id="histogram_psoriasis",
        klass="histogram",
        request={"query": PSORIASIS.display_query},
        expect={"analysis.kind": "histogram"},
        outcome="ok",
        planner_calls=(1,),
        note="",
    )
    tiers = [
        _FakeTier("google/gemini-2.5-flash-lite", None),
        _FakeTier("anthropic/claude-haiku-4.5", _verdict()),
    ]

    def make_judge(rec: Recorder) -> Judge:
        backend = CountingJudgeBackend(TieredJudgeBackend(tiers), rec)
        return Judge(backend, "google/gemini-2.5-flash-lite")

    # Act
    run = record_case(case, http, _make_planner(PSORIASIS.plan), make_judge)

    # Assert
    judge_meta = run.payload["meta"]["validation"]["judge"]
    assert judge_meta["model"] == "anthropic/claude-haiku-4.5"
    assert judge_meta["same_family"] is False
    assert run_case(case, http, _make_planner(PSORIASIS.plan), make_judge).judge_models == (
        "anthropic/claude-haiku-4.5",
    )


def _raise_llm_call(*_args: Any, **_kwargs: Any) -> Any:
    raise AssertionError("--render-only must not construct or call any LLM client")


def test_render_only_re_scores_saved_runs_with_zero_llm_calls(
    http: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange: a real recorded run, saved; then every path to an LLM is booby-trapped.
    case = EvalCase(
        id="histogram_psoriasis",
        klass="histogram",
        request={"query": PSORIASIS.display_query},
        expect={"analysis.kind": "histogram"},
        outcome="ok",
        planner_calls=(1,),
        note="",
    )
    run = record_case(case, http, _make_planner(PSORIASIS.plan), _make_judge(_verdict()))
    results, out, cases_file = tmp_path / "r.json", tmp_path / "report.md", tmp_path / "c.yaml"
    save_results(results, RunInfo("t", "p", "j", "vtest"), [run], [])
    cases_file.write_text(
        "- id: histogram_psoriasis\n  class: histogram\n"
        f"  request: {{query: {PSORIASIS.display_query!r}}}\n"
        "  expect: {analysis.kind: scatter}\n  outcome: ok\n"
    )
    monkeypatch.setattr(run_evals, "live_factories", _raise_llm_call)
    monkeypatch.setattr(run_evals, "_live_settings", _raise_llm_call)
    monkeypatch.setattr(openai.OpenAI, "__init__", _raise_llm_call)

    # Act
    code = run_evals.main(
        [
            "--render-only",
            "--results",
            str(results),
            "--out",
            str(out),
            "--cases",
            str(cases_file),
            "--notes",
            str(tmp_path / "none.yaml"),
        ]
    )

    # Assert: re-scored against the NEW expectation (scatter), not the saved score.
    report = out.read_text()
    assert code == 0
    assert "analysis.kind: expected 'scatter', got 'histogram'" in report


def test_judge_only_never_calls_the_planner_and_merges_into_saved_results(
    http: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange: a saved planner run; the planner factory raises if anything builds/calls it.
    case = EvalCase(
        id="histogram_psoriasis",
        klass="histogram",
        request={"query": PSORIASIS.display_query},
        expect={"analysis.kind": "histogram"},
        outcome="ok",
        planner_calls=(1,),
        note="",
    )
    run = record_case(case, http, _make_planner(PSORIASIS.plan), _make_judge(_verdict()))
    results, out = tmp_path / "r.json", tmp_path / "report.md"
    cases_file, judge_file = tmp_path / "c.yaml", tmp_path / "j.yaml"
    save_results(results, RunInfo("t", "p", "j", "vtest"), [run], [])
    cases_file.write_text(
        "- id: histogram_psoriasis\n  class: histogram\n"
        f"  request: {{query: {PSORIASIS.display_query!r}}}\n"
        "  expect: {analysis.kind: histogram}\n  outcome: ok\n"
    )
    judge_file.write_text(
        "- id: drug_as_condition\n  category: filter_fidelity\n  label: bad\n"
        "  request: {query: Nivolumab trials by phase}\n"
        "  plan:\n    search_terms: [{param: query.cond, value: nivolumab}]\n"
        "    analysis: {kind: count_by, group_by: phase}\n"
        "    visualization: {type: bar_chart}\n"
        "  probe_totals: {nivolumab: 40}\n  note: ''\n"
    )

    def _planner_must_not_run(_recorder: Any) -> Any:
        raise AssertionError("--judge-only must not build or call the planner")

    settings = SimpleNamespace(
        judge_model="fake-judge", judge_fallback_model="fake-fallback", planner_model="p"
    )
    fake_judge = _make_judge(_verdict("filter_fidelity", "search_terms[0].param"))
    monkeypatch.setattr(run_evals, "_live_settings", lambda: settings)
    monkeypatch.setattr(run_evals, "live_factories", lambda _s: (_planner_must_not_run, fake_judge))
    monkeypatch.setattr(run_evals, "record_case", _planner_must_not_run, raising=False)

    # Act
    code = run_evals.main(
        [
            "--judge-only",
            "--results",
            str(results),
            "--out",
            str(out),
            "--cases",
            str(cases_file),
            "--judge-cases",
            str(judge_file),
            "--notes",
            str(tmp_path / "none.yaml"),
        ]
    )

    # Assert: judge result merged, planner run kept, report re-rendered.
    saved = json.loads(results.read_text())
    assert code == 0
    assert [j["id"] for j in saved["judge"]] == ["drug_as_condition"]
    assert [r["id"] for r in saved["runs"]] == ["histogram_psoriasis"]
    assert saved["run"]["judge_model"] == "fake-judge"
    assert "drug_as_condition" in out.read_text()
