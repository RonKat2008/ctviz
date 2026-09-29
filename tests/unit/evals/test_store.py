"""Saved eval runs keep every raw planner plan and the executed plan, so a run is auditable and
can be re-scored offline (`--render-only`) against the current case expectations."""

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest
from evals.cases import EvalCase
from evals.judge_scoring import JudgeCaseResult
from evals.report import RunInfo
from evals.scoring import CaseRun, Usage, score_case
from evals.store import load_results, save_results

from tests.factories import make_plan

CASE = EvalCase(
    id="c1",
    klass="phase_distribution",
    request={"query": "Pembrolizumab trials by phase"},
    expect={"analysis.group_by": "phase"},
    outcome="ok",
    planner_calls=None,
    note="",
)
JUDGED = JudgeCaseResult(
    id="j1",
    category="viz_fit",
    label="bad",
    available=True,
    flagged=True,
    failed_checks=("viz_fit",),
    issues=("[major] visualization.type: e",),
    cost_usd=0.0004,
    model="anthropic/claude-haiku-4.5",
)
INFO = RunInfo("2026-09-29T10:00:00Z", "gpt-5.4-mini", "gemini", "v2", {"c1": "old note"})


def _run() -> CaseRun:
    plan = make_plan()
    payload: dict[str, Any] = {
        "ok": True,
        "error": None,
        "data": {"chart": "a large payload that is not needed to re-score"},
        "meta": {
            "plan": plan.model_dump(mode="json"),
            "citation_check": {"passed": True, "checked": 12},
            "validation": {"executed_attempt": 1, "judge": {"status": "passed"}, "trace": []},
            "citations": ["big"],
        },
    }
    usages = (
        Usage("planner", 5000, 500, 4000),
        Usage("judge", 900, 200, model="anthropic/claude-haiku-4.5"),
    )
    return CaseRun(CASE, 200, payload, (plan, None), usages, 1, 3.5)


def test_saved_runs_round_trip_and_rescore_identically(tmp_path: Path) -> None:
    # Arrange
    path = tmp_path / "results.json"
    run = _run()

    # Act
    save_results(path, INFO, [run], [JUDGED])
    info, runs, judged = load_results(path, {CASE.id: CASE})

    # Assert
    assert [score_case(r) for r in runs] == [score_case(run)]
    assert judged == [JUDGED]
    assert info.label == "v2" and dict(info.notes) == {}


def test_results_json_holds_the_raw_and_executed_plans_but_no_chart_data(tmp_path: Path) -> None:
    path = tmp_path / "results.json"

    save_results(path, INFO, [_run()], [JUDGED])
    [saved] = json.loads(path.read_text())["runs"]

    assert saved["raw_plans"][0]["interpretation"] == make_plan().interpretation
    assert saved["raw_plans"][1] is None
    assert saved["executed_plan"] == make_plan().model_dump(mode="json")
    assert "data" not in saved["payload"] and "citations" not in saved["payload"]["meta"]


def test_load_results_rescores_against_the_current_expectations(tmp_path: Path) -> None:
    path = tmp_path / "results.json"
    save_results(path, INFO, [_run()], [])
    stricter = dataclasses.replace(CASE, expect={"analysis.group_by": "country"})

    _info, [run], _judged = load_results(path, {CASE.id: stricter})

    assert not score_case(run).final_correct


def test_load_results_names_a_saved_case_missing_from_the_case_file(tmp_path: Path) -> None:
    path = tmp_path / "results.json"
    save_results(path, INFO, [_run()], [])

    with pytest.raises(ValueError, match="c1"):
        load_results(path, {})
