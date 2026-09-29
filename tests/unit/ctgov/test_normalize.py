"""Normalizer tests: raw study → Trial, including the missing-sections review-focus case."""

import pytest

from ctviz.ctgov.normalize import NON_INTERVENTIONAL, normalize, parse_partial_date, phase_label
from tests.factories import make_study


@pytest.mark.parametrize(
    ("phases", "study_type", "expected"),
    [
        (["PHASE3"], "INTERVENTIONAL", "Phase 3"),
        (["PHASE2", "PHASE1"], "INTERVENTIONAL", "Phase 1/Phase 2"),
        (["NA"], "INTERVENTIONAL", "Phase N/A"),
        (["EARLY_PHASE1"], "INTERVENTIONAL", "Early Phase 1"),
        (None, "OBSERVATIONAL", NON_INTERVENTIONAL),
        (None, "EXPANDED_ACCESS", NON_INTERVENTIONAL),
        (None, "INTERVENTIONAL", "Phase not reported"),
    ],
)
def test_phase_label_uses_combined_buckets(phases, study_type, expected) -> None:
    assert phase_label(phases, study_type) == expected


@pytest.mark.parametrize(
    ("raw", "year", "month"), [("2019", 2019, None), ("2019-06", 2019, 6), ("2019-06-12", 2019, 6)]
)
def test_partial_dates_keep_their_precision(raw: str, year: int, month: int | None) -> None:
    parsed = parse_partial_date(raw, "ACTUAL")

    assert (parsed.year, parsed.month, parsed.raw) == (year, month, raw)


def test_normalize_handles_missing_sections() -> None:
    raw = make_study("NCT00000009", phases=None, study_type="OBSERVATIONAL", start=None)

    trial = normalize(raw)

    assert trial.phase_label == NON_INTERVENTIONAL
    assert trial.start is None
    assert trial.interventions == () and trial.sites == () and trial.conditions == ()
    assert trial.raw is raw  # raw kept for evidence + verification (never mutated)


def test_normalize_keeps_array_indices_for_evidence() -> None:
    raw = make_study(
        interventions=[
            {"type": "DRUG", "name": "Placebo"},
            {"type": "DRUG", "name": "Pembrolizumab", "otherNames": ["MK-3475"]},
        ],
        locations=[{"country": "Japan", "status": "RECRUITING"}],
    )

    trial = normalize(raw)

    assert trial.interventions[1].index == 1
    assert trial.interventions[1].other_names == ("MK-3475",)
    assert trial.sites[0].country == "Japan"


def test_normalize_strips_stray_whitespace_from_country() -> None:
    raw = make_study(
        locations=[{"country": "Bonaire, Saint Eustatius and Saba ", "status": "RECRUITING"}]
    )

    trial = normalize(raw)

    assert trial.sites[0].country == "Bonaire, Saint Eustatius and Saba"


def test_normalize_strips_stray_whitespace_from_sponsor_and_intervention_names() -> None:
    raw = make_study(
        sponsor=" Merck Sharp & Dohme LLC ",
        interventions=[{"type": "DRUG", "name": " Pembrolizumab "}],
        conditions=[" Melanoma "],
    )

    trial = normalize(raw)

    assert trial.lead_sponsor == "Merck Sharp & Dohme LLC"
    assert trial.interventions[0].name == "Pembrolizumab"
    assert trial.conditions[0] == "Melanoma"
