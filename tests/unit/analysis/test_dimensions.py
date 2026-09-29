"""Per-dimension extractors: key + evidence + predicate for a trial (PLAN.md §10.5, §11.5)."""

import types

import pytest

from ctviz.analysis.dimensions import _EXTRACTORS, extract
from ctviz.citations.pointer import (
    LEAD_SPONSOR_CLASS,
    OVERALL_STATUS,
    PHASES,
    START_DATE,
    STUDY_TYPE,
    json_text,
    resolve_pointer,
)
from ctviz.ctgov.normalize import normalize
from ctviz.schemas.enums import Dimension
from tests.factories import make_study


def test_multi_phase_trial_cites_each_array_element() -> None:
    trial = normalize(make_study(phases=["PHASE2", "PHASE3"]))

    [hit] = extract(trial, Dimension.PHASE)

    assert hit.key == "Phase 2/Phase 3"
    assert [(e.field, e.excerpt) for e in hit.evidence] == [
        (f"{PHASES}/0", "PHASE2"),
        (f"{PHASES}/1", "PHASE3"),
    ]
    assert hit.predicate == {"op": "set_equals", "path": PHASES, "value": ["PHASE2", "PHASE3"]}


def test_non_interventional_bucket_is_cited_by_study_type() -> None:
    trial = normalize(make_study(phases=None, study_type="OBSERVATIONAL"))

    [hit] = extract(trial, Dimension.PHASE)

    assert hit.key == "Non-interventional"
    assert (hit.evidence[0].field, hit.evidence[0].excerpt) == (STUDY_TYPE, "OBSERVATIONAL")


def test_start_year_uses_the_partial_date_as_is() -> None:
    [hit] = extract(normalize(make_study(start="2019-10")), Dimension.START_YEAR)

    assert (hit.key, hit.evidence[0].field, hit.evidence[0].excerpt) == (
        "2019",
        START_DATE,
        "2019-10",
    )


def test_missing_start_date_yields_no_hit() -> None:
    assert extract(normalize(make_study(start=None)), Dimension.START_YEAR) == []


def test_lead_sponsor_excerpt_is_raw_value_when_name_has_trailing_space() -> None:
    trial = normalize(make_study(sponsor="Merck Sharp & Dohme LLC "))

    [hit] = extract(trial, Dimension.LEAD_SPONSOR)

    assert hit.key == "Merck Sharp & Dohme LLC"
    evidence = hit.evidence[0]
    assert evidence.excerpt == "Merck Sharp & Dohme LLC "
    assert json_text(resolve_pointer(trial.raw, evidence.field)) == evidence.excerpt


def test_non_interventional_predicate_covers_observational_and_expanded_access() -> None:
    trial = normalize(make_study(phases=None, study_type="OBSERVATIONAL"))

    [hit] = extract(trial, Dimension.PHASE)

    assert hit.predicate == {
        "all": [
            {"not": {"op": "exists", "path": PHASES}},
            {"op": "in", "path": STUDY_TYPE, "value": ["OBSERVATIONAL", "EXPANDED_ACCESS"]},
        ]
    }
    assert hit.evidence[0].field == STUDY_TYPE


def test_interventional_trial_with_no_phases_is_phase_not_reported() -> None:
    trial = normalize(make_study(phases=None, study_type="INTERVENTIONAL"))

    [hit] = extract(trial, Dimension.PHASE)

    assert hit.key == "Phase not reported"
    assert hit.predicate == {
        "all": [
            {"not": {"op": "exists", "path": PHASES}},
            {"op": "in", "path": STUDY_TYPE, "value": ["INTERVENTIONAL"]},
        ]
    }


def test_overall_status_extractor_key_path_excerpt_and_predicate() -> None:
    trial = normalize(make_study(status="TERMINATED"))

    [hit] = extract(trial, Dimension.OVERALL_STATUS)

    assert hit.key == "TERMINATED"
    assert hit.evidence[0].field == OVERALL_STATUS
    assert hit.evidence[0].excerpt == "TERMINATED"
    assert hit.predicate == {"op": "equals", "path": OVERALL_STATUS, "value": "TERMINATED"}


def test_lead_sponsor_class_extractor_key_path_excerpt_and_predicate() -> None:
    trial = normalize(make_study(sponsor_class="NIH"))

    [hit] = extract(trial, Dimension.LEAD_SPONSOR_CLASS)

    assert hit.key == "NIH"
    assert hit.evidence[0].field == LEAD_SPONSOR_CLASS
    assert hit.evidence[0].excerpt == "NIH"
    assert hit.predicate == {"op": "equals", "path": LEAD_SPONSOR_CLASS, "value": "NIH"}


def test_extractors_registry_is_read_only() -> None:
    assert isinstance(_EXTRACTORS, types.MappingProxyType)
    with pytest.raises(TypeError):
        _EXTRACTORS[Dimension.PHASE] = lambda t: []  # type: ignore[index]
