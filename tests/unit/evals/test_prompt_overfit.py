"""The prompts must not be tuned to the eval set: no eval case's key entity may appear anywhere
in `prompts.py` (so every case is held out from the prompt text), and the judge rubric's slot
list must be the one the deterministic plan checks enforce."""

from pathlib import Path
from typing import Any

import pytest
from evals.cases import load_cases, load_judge_cases

from ctviz.agent import prompts
from ctviz.agent.plan_checks import OPTIONAL_ANALYSIS_MODIFIERS, REQUIRED_ANALYSIS_FIELDS
from ctviz.schemas.enums import AnalysisKind

EVALS = Path(__file__).resolve().parents[3] / "evals"
PROMPTS_TEXT = Path(prompts.__file__).read_text(encoding="utf-8").casefold()
_STRUCTURED_ENTITY_FIELDS = ("drug_name", "condition", "sponsor", "country")


def _planner_entities(expect: dict[str, Any], request: dict[str, Any]) -> set[str]:
    """Searched entities, compared values, NCT IDs and structured values of one planner case."""
    found = {t["value_contains"] for t in expect.get("search_terms", [])}
    found |= set(expect.get("comparison.values_contain", []))
    found |= set(expect.get("filters.nct_ids_contain", []))
    return found | {request[f] for f in _STRUCTURED_ENTITY_FIELDS if request.get(f)}


def _judge_entities(plan: dict[str, Any]) -> set[str]:
    """Searched entities, compared values and countries of one judge case's plan."""
    found = {t["value"] for t in plan.get("search_terms", [])}
    found |= set((plan.get("comparison") or {}).get("values", []))
    return found | set((plan.get("filters") or {}).get("countries") or [])


def _all_entities() -> list[tuple[str, str]]:
    planner = [
        (c.id, e)
        for c in load_cases(EVALS / "cases.yaml")
        for e in _planner_entities(c.expect, c.request)
    ]
    judge = [
        (c.id, e)
        for c in load_judge_cases(EVALS / "judge_cases.yaml")
        for e in _judge_entities(c.plan)
    ]
    return planner + judge


def test_the_entity_extraction_sees_the_known_cases() -> None:
    entities = {e.casefold() for _id, e in _all_entities()}

    assert {"merck", "georgia", "nivolumab", "glioblastoma", "nct04368728"} <= entities


@pytest.mark.parametrize(("case_id", "entity"), _all_entities())
def test_no_eval_case_entity_appears_in_the_prompts(case_id: str, entity: str) -> None:
    assert entity.casefold() not in PROMPTS_TEXT, f"{case_id}: {entity!r} is in prompts.py"


def test_the_planner_prompt_does_not_teach_the_literal_recency_phrase() -> None:
    assert "recent =" not in PROMPTS_TEXT


@pytest.mark.parametrize("wording", ["lately", "co-occur", "washington", "roche", "novartis"])
def test_held_out_wording_appears_nowhere_in_the_prompts(wording: str) -> None:
    assert wording not in PROMPTS_TEXT


@pytest.mark.parametrize("kind", list(AnalysisKind))
def test_the_rubric_lists_exactly_the_slots_plan_checks_require_per_kind(
    kind: AnalysisKind,
) -> None:
    slots = ", ".join(REQUIRED_ANALYSIS_FIELDS[kind]) or "none"

    assert f"{kind.value} -> {slots};" in prompts.JUDGE_RUBRIC.replace(".", ";")


def test_the_rubric_allows_every_optional_modifier_the_code_allows() -> None:
    modifiers = ", ".join(OPTIONAL_ANALYSIS_MODIFIERS)

    assert f"optional modifiers {modifiers} are allowed with every kind" in (
        " ".join(prompts.JUDGE_RUBRIC.split())
    )
    assert "stay null" not in prompts.JUDGE_RUBRIC
