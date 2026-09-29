"""The eval case files load, cover every §14 class and every judge rubric category."""

from pathlib import Path
from typing import get_args

import pytest
from evals.cases import load_cases, load_judge_cases, plan_from_partial
from evals.coverage import EVAL_CLASS_FOR, REQUIRED_CLASSES, SECTION_14_CLASSES

from ctviz.schemas.judge import RubricCheck
from ctviz.schemas.request import VisualizeRequest

EVALS = Path(__file__).resolve().parents[3] / "evals"


def test_the_coverage_source_maps_every_section_14_row_to_an_eval_class() -> None:
    assert set(SECTION_14_CLASSES) == set(range(1, 20))
    assert set(SECTION_14_CLASSES.values()) == set(EVAL_CLASS_FOR)


def test_cases_yaml_covers_exactly_the_required_classes_with_unique_ids() -> None:
    cases = load_cases(EVALS / "cases.yaml")

    assert {c.klass for c in cases} == REQUIRED_CLASSES
    assert len({c.id for c in cases}) == len(cases)


def test_section_14_row_19_is_asked_verbatim_and_must_revise() -> None:
    [case] = [c for c in load_cases(EVALS / "cases.yaml") if c.klass == "network_unscoped"]

    assert case.request == {"query": "Which drugs frequently co-occur in combination studies?"}
    assert case.planner_calls == (2,)


def test_every_case_request_is_a_valid_visualize_request() -> None:
    for case in load_cases(EVALS / "cases.yaml"):
        VisualizeRequest.model_validate(case.request)
        assert case.expect, case.id
        assert case.outcome


def test_judge_cases_cover_every_rubric_check_plus_override_and_two_clean() -> None:
    cases = load_judge_cases(EVALS / "judge_cases.yaml")
    bad = [c for c in cases if c.label == "bad"]
    good = [c for c in cases if c.label == "good"]

    assert len(cases) == 12
    assert {c.category for c in bad} == set(get_args(RubricCheck))
    assert sorted(c.category for c in good) == [
        "ambiguity_handled",
        "clean",
        "clean",
        "structured_override",
    ]


def test_every_judge_case_plan_and_request_validate() -> None:
    for case in load_judge_cases(EVALS / "judge_cases.yaml"):
        VisualizeRequest.model_validate(case.request)
        plan_from_partial(case.plan)


def test_plan_from_partial_fills_null_slots() -> None:
    plan = plan_from_partial(
        {
            "search_terms": [{"param": "query.cond", "value": "asthma"}],
            "analysis": {"kind": "count_by", "group_by": "phase"},
            "visualization": {"type": "bar_chart"},
        }
    )

    assert plan.answerable is True
    assert plan.analysis is not None and plan.analysis.time_field is None
    assert plan.search_terms[0].source == "query_text"
    assert plan.filters is None


def test_plan_from_partial_rejects_unknown_keys() -> None:
    with pytest.raises(ValueError):
        plan_from_partial({"analysis": {"kind": "count_by", "bogus": 1}})


def test_load_cases_rejects_a_case_without_an_outcome(tmp_path: Path) -> None:
    path = tmp_path / "cases.yaml"
    path.write_text(
        "- id: x\n  class: trend\n  request: {query: abc}\n  expect: {answerable: true}\n"
    )

    with pytest.raises(ValueError, match="outcome"):
        load_cases(path)
