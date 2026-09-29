"""Entity resolution: alias discovery, sponsor census, Q2 ambiguity warning (§10.4)."""

import pytest

from ctviz.analysis.aggregate import MatchedTrial
from ctviz.analysis.entities import (
    MIN_ALIAS_TRIALS,
    VETO_OVERRIDE_RATIO,
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


def test_an_object_listing_the_term_in_other_names_teaches_its_other_names() -> None:
    """Fix A (live "Keytruda" query): an object NAMED 'MK-3475' listing 'Pembrolizumab' in its
    otherNames co-references the term too -- its name and its other otherNames are learned."""
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

    assert discover_aliases(trials, "Pembrolizumab") == ["keytruda", "mk-3475"]


def test_a_brand_term_learns_the_generic_name_it_is_listed_under() -> None:
    """Fix A: term 'Keytruda' sits in the pembrolizumab object's otherNames -> 'pembrolizumab'
    (the object's NAME) is a candidate; a separate partner object in those trials is vetoed."""
    trials = _pembro_trials(["Keytruda", "MK-3475"], [{"type": "DRUG", "name": "Paclitaxel"}])

    assert discover_aliases(trials, "Keytruda") == ["mk-3475", "pembrolizumab"]


def test_a_bidirectional_candidate_still_respects_the_rival_and_generic_guards() -> None:
    """Fix A keeps every guard: another cohort's value and drug-class names are never learned."""
    trials = _pembro_trials(["Keytruda", "Nivolumab", "Anti-PD-1"])

    assert discover_aliases(trials, "Keytruda", other_cohort_values=["Nivolumab"]) == [
        "pembrolizumab"
    ]


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


@pytest.mark.parametrize(("vetoes", "learned"), [(1, True), (2, False)])
def test_a_rare_veto_is_overridden_only_by_overwhelming_co_reference(
    vetoes: int, learned: bool
) -> None:
    """Fix A ruling: a generic co-referenced in > VETO_OVERRIDE_RATIO x its veto trials (a
    biosimilar-vs-reference trial listing it beside the brand) survives; otherwise vetoed."""
    trials = _pembro_trials(["Keytruda"], n=VETO_OVERRIDE_RATIO + 1)
    vetoing = [
        make_study(
            f"NCT0900000{i}",
            interventions=[
                {"type": "DRUG", "name": "Keytruda"},
                {"type": "DRUG", "name": "Pembrolizumab"},
            ],
        )
        for i in range(vetoes)
    ]

    aliases = discover_aliases([*trials, *_trials(*vetoing)], "Keytruda")

    assert ("pembrolizumab" in aliases) is learned


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
