"""Multi-valued dimension extractors: intervention type, drug, condition, country (§10.3, §11.5)."""

from ctviz.analysis.dimensions import country_extractor, extract
from ctviz.citations.pointer import INTERVENTIONS, LOCATIONS, json_text, resolve_pointer
from ctviz.ctgov.normalize import normalize
from ctviz.schemas.enums import Dimension
from tests.factories import make_study


def test_intervention_type_counts_each_type_once_per_trial() -> None:
    trial = normalize(
        make_study(
            interventions=[
                {"type": "DRUG", "name": "A"},
                {"type": "DRUG", "name": "B"},
                {"type": "BEHAVIORAL", "name": "C"},
            ]
        )
    )

    hits = extract(trial, Dimension.INTERVENTION_TYPE)

    assert [(h.key, h.evidence[0].field) for h in hits] == [
        ("DRUG", f"{INTERVENTIONS}/0/type"),
        ("BEHAVIORAL", f"{INTERVENTIONS}/2/type"),
    ]


def test_country_counts_once_per_trial_and_cites_first_site() -> None:
    trial = normalize(
        make_study(
            locations=[
                {"country": "Japan"},
                {"country": "Japan"},
                {"country": "France"},
            ]
        )
    )

    hits = country_extractor(recruiting_only=False)(trial)

    assert [(h.key, h.evidence[0].field) for h in hits] == [
        ("Japan", f"{LOCATIONS}/0/country"),
        ("France", f"{LOCATIONS}/2/country"),
    ]


def test_recruiting_rule_requires_a_recruiting_site_in_that_country() -> None:
    trial = normalize(
        make_study(
            status="RECRUITING",
            locations=[
                {"country": "Germany", "status": "WITHDRAWN"},
                {"country": "Japan", "status": "RECRUITING"},
            ],
        )
    )

    hits = country_extractor(recruiting_only=True)(trial)

    assert [h.key for h in hits] == ["Japan"]
    assert [e.field for e in hits[0].evidence] == [
        f"{LOCATIONS}/1/country",
        f"{LOCATIONS}/1/status",
    ]


def test_recruiting_rule_falls_back_to_trial_status_when_site_statuses_are_null() -> None:
    trial = normalize(make_study(status="RECRUITING", locations=[{"country": "Japan"}]))

    [hit] = country_extractor(recruiting_only=True)(trial)

    assert hit.evidence[-1].field == "/protocolSection/statusModule/overallStatus"


def test_recruiting_rule_does_not_fall_back_when_only_some_site_statuses_are_null() -> None:
    """M04: the trial-status fallback applies only when EVERY site status is null. A trial with
    one non-null, non-RECRUITING site status and one null-status site must use the strict
    per-site rule (no site counted, since neither has status == RECRUITING) -- not the fallback,
    which would wrongly count the null-status site's country via the trial's own status."""
    trial = normalize(
        make_study(
            status="RECRUITING",
            locations=[
                {"country": "Germany", "status": "WITHDRAWN"},
                {"country": "Japan"},  # status is null
            ],
        )
    )

    hits = country_extractor(recruiting_only=True)(trial)

    assert hits == []


def test_country_with_trailing_space_buckets_stripped_but_cites_the_raw_value() -> None:
    """A raw `LocationCountry` with trailing whitespace ("Bonaire, Saint Eustatius and Saba ")
    must bucket under the stripped name (so it isn't silently split into two buckets), while its
    citation excerpt stays the exact raw text (with the space) and the pointer still resolves."""
    trial = normalize(make_study(locations=[{"country": "Bonaire, Saint Eustatius and Saba "}]))

    [hit] = country_extractor(recruiting_only=False)(trial)

    assert hit.key == "Bonaire, Saint Eustatius and Saba"
    evidence = hit.evidence[0]
    assert evidence.excerpt == "Bonaire, Saint Eustatius and Saba "
    assert json_text(resolve_pointer(trial.raw, evidence.field)) == evidence.excerpt


def test_drug_extractor_keys_on_normalized_name_and_skips_placebo() -> None:
    trial = normalize(
        make_study(
            interventions=[
                {"type": "DRUG", "name": "Pembrolizumab 200 mg"},
                {"type": "DRUG", "name": "Placebo"},
                {"type": "DEVICE", "name": "Infusion pump"},
            ]
        )
    )

    hits = extract(trial, Dimension.INTERVENTION)

    assert [(h.key, h.evidence[0].field) for h in hits] == [
        ("pembrolizumab", f"{INTERVENTIONS}/0/name"),
    ]


def test_condition_extractor_keys_on_normalized_text_deduped_per_trial() -> None:
    trial = normalize(make_study(conditions=["Melanoma", "melanoma ", "Lung Cancer"]))

    hits = extract(trial, Dimension.CONDITION)

    assert [h.key for h in hits] == ["melanoma", "lung cancer"]
    assert hits[0].evidence[0].field == "/protocolSection/conditionsModule/conditions/0"
