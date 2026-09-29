"""Predicate evaluator: every op from PLAN.md §11.6, table-driven (Task 6.1 Step 1)."""

import pytest

from ctviz.citations.pointer import INTERVENTIONS, PHASES, START_DATE
from ctviz.citations.predicates import evaluate, referenced_paths
from tests.factories import make_study

DOC = make_study(
    phases=["PHASE2", "PHASE3"],
    start="2019-10",
    interventions=[
        {
            "type": "DRUG",
            "name": "Pembrolizumab 200 mg",
            "otherNames": ["Keytruda"],
        }
    ],
)


@pytest.mark.parametrize(
    ("predicate", "expected"),
    [
        ({"op": "set_equals", "path": PHASES, "value": ["PHASE3", "PHASE2"]}, True),
        ({"op": "contains", "path": PHASES, "value": "PHASE3"}, True),
        ({"op": "year_equals", "path": START_DATE, "value": 2019}, True),
        ({"op": "year_in_range", "path": START_DATE, "value": [2015, None]}, True),
        ({"op": "exists", "path": "/protocolSection/conditionsModule"}, False),
        (
            {
                "op": "any_element",
                "path": INTERVENTIONS,
                "where": [{"op": "normalizes_to", "path": "/name", "value": "pembrolizumab"}],
            },
            True,
        ),
        (
            {
                "op": "any_element",
                "path": INTERVENTIONS,
                "where": [{"op": "text_matches", "path": "/otherNames", "value": "keytruda"}],
            },
            True,
        ),
        ({"not": {"op": "equals", "path": PHASES, "value": ["PHASE1"]}}, True),
        ({"all": [{"op": "contains", "path": PHASES, "value": "PHASE1"}]}, False),
    ],
)
def test_predicate_ops(predicate: dict, expected: bool) -> None:
    assert evaluate(predicate, DOC) is expected


def test_unknown_op_is_an_error_not_false() -> None:
    with pytest.raises(ValueError, match="unknown predicate op"):
        evaluate({"op": "fuzzy", "path": PHASES, "value": 1}, DOC)


def test_referenced_paths_collects_nested_paths() -> None:
    predicate = {
        "all": [
            {"op": "exists", "path": PHASES},
            {"op": "equals", "path": START_DATE, "value": 1},
        ]
    }
    assert referenced_paths(predicate) == {PHASES, START_DATE}


# --- Item 7: op-level tests that kill the reviewer's surviving mutants -------------------------

LOCATIONS_PATH = "/protocolSection/contactsLocationsModule/locations"
STATUS_PATH = "/protocolSection/statusModule/overallStatus"
ENROLLMENT_PATH = "/protocolSection/designModule/enrollmentInfo/count"


def test_equals_strips_whitespace_on_both_sides() -> None:
    """Raw API text can carry stray whitespace; `equals`/`in` compare stripped strings."""
    doc = make_study(status=" RECRUITING ")

    assert evaluate({"op": "equals", "path": STATUS_PATH, "value": "RECRUITING"}, doc) is True
    assert evaluate({"op": "in", "path": STATUS_PATH, "value": ["RECRUITING "]}, doc) is True


@pytest.mark.parametrize("empty", [[], None])
def test_exists_is_false_for_an_empty_list_or_null(empty: object) -> None:
    doc = make_study(phases=["PHASE1"])
    doc["protocolSection"]["designModule"]["phases"] = empty

    assert evaluate({"op": "exists", "path": PHASES}, doc) is False


def test_exists_is_true_for_a_present_non_empty_value() -> None:
    assert evaluate({"op": "exists", "path": PHASES}, DOC) is True


def test_any_element_requires_the_same_element_to_satisfy_every_clause() -> None:
    """Japan is COMPLETED and the US is RECRUITING: no single site is a recruiting Japan site."""
    doc = make_study(
        locations=[
            {"country": "Japan", "status": "COMPLETED"},
            {"country": "United States", "status": "RECRUITING"},
        ]
    )
    predicate = {
        "op": "any_element",
        "path": LOCATIONS_PATH,
        "where": [
            {"op": "equals", "path": "/country", "value": "Japan"},
            {"op": "equals", "path": "/status", "value": "RECRUITING"},
        ],
    }

    assert evaluate(predicate, doc) is False


@pytest.mark.parametrize(
    ("bounds", "expected"),
    [
        ([2019, 2019], True),  # both bounds inclusive
        ([2019, None], True),
        ([None, 2019], True),
        ([2020, None], False),
        ([None, 2018], False),
    ],
)
def test_year_in_range_bounds_are_inclusive(bounds: list[int | None], expected: bool) -> None:
    predicate = {"op": "year_in_range", "path": START_DATE, "value": bounds}

    assert evaluate(predicate, DOC) is expected  # DOC starts "2019-10"


@pytest.mark.parametrize(
    ("bounds", "expected"),
    [
        ([100, 200], True),  # lo inclusive
        ([50, 100], False),  # hi exclusive
        ([100, None], True),  # hi=None is open-ended
        ([101, None], False),
    ],
)
def test_in_range_is_half_open(bounds: list[int | None], expected: bool) -> None:
    doc = make_study(enrollment=100)

    assert evaluate({"op": "in_range", "path": ENROLLMENT_PATH, "value": bounds}, doc) is expected


def test_normalizes_to_text_and_drug_differ_where_the_dose_suffix_matters() -> None:
    """`drug` strips the dose ('Pembrolizumab 200 mg' -> 'pembrolizumab'); `text` only
    casefolds/collapses whitespace, so the same key must NOT match under fn='text'."""
    path = f"{INTERVENTIONS}/0/name"

    drug = {"op": "normalizes_to", "path": path, "value": "pembrolizumab", "fn": "drug"}
    text = {"op": "normalizes_to", "path": path, "value": "pembrolizumab", "fn": "text"}
    text_full = {"op": "normalizes_to", "path": path, "value": "pembrolizumab 200 mg", "fn": "text"}

    assert evaluate(drug, DOC) is True
    assert evaluate(text, DOC) is False
    assert evaluate(text_full, DOC) is True


def test_unknown_normalizer_fn_is_an_error_not_a_silent_fallback() -> None:
    predicate = {"op": "normalizes_to", "path": f"{INTERVENTIONS}/0/name", "value": "x", "fn": "zz"}

    with pytest.raises(ValueError, match="unknown normalizes_to fn"):
        evaluate(predicate, DOC)
