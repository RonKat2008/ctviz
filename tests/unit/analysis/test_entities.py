"""Entity resolution: alias discovery, sponsor census, Q2 ambiguity warning (§10.4)."""

import pytest

from ctviz.analysis.aggregate import MatchedTrial
from ctviz.analysis.entities import (
    MIN_ALIAS_TRIALS,
    discover_aliases,
    sponsor_ambiguity_warning,
    sponsor_census,
)
from ctviz.ctgov.normalize import Trial, normalize
from tests.factories import make_study
from tests.fixtures.load import load_trials


def _m(*studies: dict) -> list[MatchedTrial]:
    return [MatchedTrial(normalize(s), ()) for s in studies]


def _trials(*studies: dict) -> list[Trial]:
    return [normalize(s) for s in studies]


def _pembro_trials(other_names: list[str], extra: list[dict] | None = None, n: int = 3) -> list:
    """`n` trials whose pembrolizumab object lists `other_names` (+ `extra` separate objects)."""
    return _trials(
        *[
            make_study(
                f"NCT0000000{i}",
                interventions=[
                    {"type": "DRUG", "name": "Pembrolizumab", "otherNames": other_names},
                    *(extra or []),
                ],
            )
            for i in range(n)
        ]
    )


def test_aliases_come_only_from_the_same_intervention_object() -> None:
    trials = _pembro_trials(["Keytruda", "MK-3475"], [{"type": "DRUG", "name": "Paclitaxel"}])

    assert discover_aliases(trials, "Pembrolizumab") == ["keytruda", "mk-3475"]


def test_aliases_require_at_least_three_trials() -> None:
    trials = _pembro_trials(["Keytruda"], n=MIN_ALIAS_TRIALS - 1)

    assert discover_aliases(trials, "Pembrolizumab") == []


def test_aliases_are_learned_only_from_an_object_whose_name_contains_the_term() -> None:
    """(a): an object named 'MK-3475' with otherNames ['Pembrolizumab', 'Keytruda'] names the
    term only in otherNames -- its other names are not learned (the ruling narrows §10.4)."""
    trials = _trials(
        *[
            make_study(
                f"NCT0000000{i}",
                interventions=[
                    {"type": "DRUG", "name": "MK-3475", "otherNames": ["Pembrolizumab", "Keytruda"]}
                ],
            )
            for i in range(3)
        ]
    )

    assert discover_aliases(trials, "Pembrolizumab") == []


def test_a_partner_drug_that_is_its_own_object_in_the_same_trial_is_never_an_alias() -> None:
    """(c): 'Carboplatin' sits in the pembrolizumab object's otherNames in 3 trials, but one
    trial also lists it as a SEPARATE intervention object -- a partner drug, vetoed."""
    trials = _pembro_trials(["Keytruda", "Carboplatin"])
    vetoing = make_study(
        "NCT00000009",
        interventions=[
            {"type": "DRUG", "name": "Pembrolizumab"},
            {"type": "DRUG", "name": "Carboplatin"},
        ],
    )

    assert discover_aliases([*trials, normalize(vetoing)], "Pembrolizumab") == ["keytruda"]


def test_a_trial_naming_the_drug_only_by_its_alias_does_not_veto_that_alias() -> None:
    """Ruling: the veto is scoped to trials that also carry a term-bearing object (§10.4 'in
    the same trial'). A Keytruda-only trial is exactly what the alias exists to rescue."""
    trials = _pembro_trials(["Keytruda"])
    alias_only = make_study("NCT00000009", interventions=[{"type": "DRUG", "name": "Keytruda"}])

    assert discover_aliases([*trials, normalize(alias_only)], "Pembrolizumab") == ["keytruda"]


def test_an_alias_equal_to_another_cohorts_value_is_rejected() -> None:
    trials = _pembro_trials(["Keytruda", "Nivolumab"])

    assert discover_aliases(trials, "Pembrolizumab", other_cohort_values=["Nivolumab"]) == [
        "keytruda"
    ]


@pytest.mark.parametrize(
    "generic",
    ["Placebo", "Standard of care", "Immunotherapy", "Checkpoint inhibitor", "Anti-PD-1"],
)
def test_placebo_soc_and_drug_class_names_are_never_aliases(generic: str) -> None:
    trials = _pembro_trials(["Keytruda", generic])

    assert discover_aliases(trials, "Pembrolizumab") == ["keytruda"]


def test_sponsor_census_lists_distinct_names_with_counts() -> None:
    trials = _m(
        make_study("NCT00000001", sponsor="Merck Sharp & Dohme LLC"),
        make_study("NCT00000002", sponsor="Merck KGaA, Darmstadt, Germany"),
    )

    assert dict(sponsor_census(trials)) == {
        "Merck Sharp & Dohme LLC": 1,
        "Merck KGaA, Darmstadt, Germany": 1,
    }


def test_sponsor_census_counts_are_real_trial_counts_not_all_one() -> None:
    """M24: forcing every sponsor's count to 1 must fail this test -- one sponsor here has 3
    trials, the other 1, and they must stay distinguishable (and correctly ranked, largest
    first) by their real counts."""
    trials = _m(
        make_study("NCT00000001", sponsor="Merck Sharp & Dohme LLC"),
        make_study("NCT00000002", sponsor="Merck Sharp & Dohme LLC"),
        make_study("NCT00000003", sponsor="Merck Sharp & Dohme LLC"),
        make_study("NCT00000004", sponsor="Merck KGaA, Darmstadt, Germany"),
    )

    census = sponsor_census(trials)

    assert census == [
        ("Merck Sharp & Dohme LLC", 3),
        ("Merck KGaA, Darmstadt, Germany", 1),
    ]


def test_sponsor_ambiguity_warning_names_distinct_organizations_for_a_known_term() -> None:
    census = [("Merck Sharp & Dohme LLC", 12), ("Merck KGaA, Darmstadt, Germany", 3)]

    warning = sponsor_ambiguity_warning("Merck", census)

    assert warning is not None
    assert "Merck Sharp & Dohme LLC" in warning and "Merck KGaA, Darmstadt, Germany" in warning


def test_sponsor_ambiguity_warning_is_none_for_a_single_sponsor_name() -> None:
    assert sponsor_ambiguity_warning("Merck", [("Merck Sharp & Dohme LLC", 12)]) is None


def test_sponsor_ambiguity_warning_is_none_for_an_unknown_term() -> None:
    census = [("Pfizer Inc.", 5), ("Pfizer Ltd.", 2)]

    assert sponsor_ambiguity_warning("Pfizer", census) is None


def test_sponsor_ambiguity_warning_is_none_for_the_real_pembrolizumab_fixture_census() -> None:
    """Item 5: the pembrolizumab fixture's Merck-family sponsors are ALL the same organization
    (MSD LLC 274 + a subsidiary "of Merck & Co." 2) -- one distinct org, so no warning."""
    trials = [MatchedTrial(trial, ()) for trial in load_trials("pembrolizumab")]

    census = sponsor_census(trials)

    assert sponsor_ambiguity_warning("Merck", census) is None


def test_sponsor_ambiguity_warning_fires_for_synthetic_msd_and_merck_kgaa_census() -> None:
    """Item 5: two DIFFERENT known orgs (MSD and Merck KGaA) in the census must still warn."""
    census = [("Merck Sharp & Dohme LLC", 10), ("Merck KGaA, Darmstadt, Germany", 5)]

    warning = sponsor_ambiguity_warning("Merck", census)

    assert warning is not None
    assert "Merck Sharp & Dohme LLC" in warning and "Merck KGaA, Darmstadt, Germany" in warning
