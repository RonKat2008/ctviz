"""Entity resolution: alias discovery, sponsor census, Q2 ambiguity warning (§10.4)."""

from ctviz.analysis.aggregate import MatchedTrial
from ctviz.analysis.entities import discover_aliases, sponsor_ambiguity_warning, sponsor_census
from ctviz.ctgov.normalize import normalize
from tests.factories import make_study
from tests.fixtures.load import load_trials


def _m(*studies: dict) -> list[MatchedTrial]:
    return [MatchedTrial(normalize(s), ()) for s in studies]


def test_aliases_come_only_from_the_same_intervention_object() -> None:
    trials = _m(
        *[
            make_study(
                f"NCT0000000{i}",
                interventions=[
                    {
                        "type": "DRUG",
                        "name": "Pembrolizumab",
                        "otherNames": ["Keytruda", "MK-3475"],
                    },
                    {"type": "DRUG", "name": "Paclitaxel"},
                ],
            )
            for i in range(3)
        ]
    )

    assert discover_aliases(trials, "Keytruda") == ["mk-3475", "pembrolizumab"]


def test_aliases_require_at_least_three_trials() -> None:
    trials = _m(
        make_study(
            "NCT00000001",
            interventions=[{"type": "DRUG", "name": "Pembrolizumab", "otherNames": ["Keytruda"]}],
        )
    )

    assert discover_aliases(trials, "Keytruda") == []


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
