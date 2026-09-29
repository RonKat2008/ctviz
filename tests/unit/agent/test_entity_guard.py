"""Entity guard: planner prose may not name entities absent from the request, plan or catalog."""

import pytest

from ctviz.agent.entity_guard import guard_entities
from ctviz.agent.plan_checks import guard_text
from ctviz.schemas.request import VisualizeRequest
from tests.factories import make_plan

QUERY = "How has the number of pembrolizumab trials changed over time?"


def _request(query: str = QUERY, **fields: object) -> VisualizeRequest:
    return VisualizeRequest(query=query, **fields)  # type: ignore[arg-type]


def _assumptions(*sentences: str, **overrides: object):  # type: ignore[no-untyped-def]
    return make_plan(assumptions=list(sentences), **overrides)


def test_invented_drug_name_in_an_assumption_sentence_is_dropped() -> None:
    plan = _assumptions("Zorbinex is treated as the comparator. Counts cover every start year.")

    guarded, notes = guard_entities(plan, _request())

    assert guarded.assumptions == ["Counts cover every start year."]
    assert len(notes) == 1 and "Zorbinex" in notes[0]


def test_entity_named_in_the_query_is_kept() -> None:
    plan = _assumptions("Pembrolizumab is the intervention of interest.")

    guarded, notes = guard_entities(plan, _request())

    assert guarded == plan and notes == []


def test_entity_from_a_structured_field_is_kept() -> None:
    plan = _assumptions("Assumes Merck is the lead sponsor.")

    guarded, notes = guard_entities(plan, _request(sponsor="Merck"))

    assert guarded == plan and notes == []


def test_entity_from_the_plans_own_search_term_is_kept() -> None:
    term = {"param": "query.intr", "value": "Zorbinex", "source": "query_text", "rationale": "drug"}
    plan = _assumptions("Zorbinex matches are counted.", search_terms=[term])

    guarded, notes = guard_entities(plan, _request())

    assert guarded == plan and notes == []


def test_invented_nct_id_is_dropped_but_one_from_the_query_is_kept() -> None:
    plan = _assumptions("Anchored on NCT99999999 as reference.", "Includes NCT01234567.")

    guarded, notes = guard_entities(plan, _request("Show trial NCT01234567 over time please"))

    assert guarded.assumptions == ["Includes NCT01234567."]
    assert len(notes) == 1 and "NCT99999999" in notes[0]


def test_lowercase_drug_suffix_name_is_flagged() -> None:
    plan = _assumptions("Excludes vemurafenib combination trials.")

    guarded, _ = guard_entities(plan, _request())

    assert guarded.assumptions == []


@pytest.mark.parametrize(
    "sentence",
    [
        "Phase is combined across Phase 2/Phase 3 labels.",
        "Recruiting and Completed trials are both included.",
        "Interpreting the question as a yearly trend by start date.",
        "Sponsor class Industry is the ClinicalTrials.gov category.",
    ],
)
def test_catalog_words_and_sentence_openers_are_not_flagged(sentence: str) -> None:
    plan = _assumptions(sentence)

    guarded, notes = guard_entities(plan, _request())

    assert guarded == plan and notes == []


def test_invented_entity_in_the_title_falls_back_to_the_code_built_title() -> None:
    viz = {"type": "bar_chart", "title": "Zorbinex Trials by Phase", "rationale": "categories"}
    plan = make_plan(visualization=viz)

    guarded, notes = guard_entities(plan, _request())

    assert guarded.visualization is not None
    assert guarded.visualization.title == "Trial count by phase: pembrolizumab"
    assert len(notes) == 1 and "Zorbinex" in notes[0]


def test_a_title_made_of_query_and_catalog_words_is_kept() -> None:
    viz = {"type": "bar_chart", "title": "Pembrolizumab Trials by Phase", "rationale": "categories"}
    plan = make_plan(visualization=viz)

    guarded, notes = guard_entities(plan, _request())

    assert guarded == plan and notes == []


def test_invented_entity_in_the_interpretation_falls_back_to_a_code_sentence() -> None:
    plan = make_plan(interpretation="Compares Zorbinex against placebo.")

    guarded, notes = guard_entities(plan, _request())

    assert guarded.interpretation == "Trial count by phase for pembrolizumab."
    assert len(notes) == 1 and "interpretation" in notes[0]


def test_guard_text_runs_the_entity_guard_after_the_digit_guard() -> None:
    plan = _assumptions("Zorbinex is the comparator.", "Counts 7 trials.")

    guarded, notes = guard_text(plan, _request())

    assert guarded.assumptions == []
    assert len(notes) == 2


def test_an_ungrounded_acronym_inside_a_hyphenated_token_is_flagged() -> None:
    plan = _assumptions("Excludes the sk-proj-FAKEKEY-value token.")

    guarded, notes = guard_entities(plan, _request())

    assert guarded.assumptions == []
    assert len(notes) == 1 and "FAKEKEY" in notes[0]


def _titled(title: str):  # type: ignore[no-untyped-def]
    return make_plan(visualization={"type": "bar_chart", "title": title, "rationale": "r"})


def test_title_opening_word_is_not_treated_as_an_entity() -> None:
    plan = _titled("Total trial landscape overview")

    guarded, notes = guard_entities(plan, _request("Trials by phase overall?"))

    assert guarded.visualization is not None
    assert guarded.visualization.title == "Total trial landscape overview"
    assert notes == []


def test_title_with_an_invented_later_entity_is_still_replaced() -> None:
    plan = _titled("Trials funded by Pfizer")

    guarded, notes = guard_entities(plan, _request("Trials by phase overall?"))

    assert guarded.visualization is not None
    assert "Pfizer" not in guarded.visualization.title
    assert notes
