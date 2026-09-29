"""Strict match (Q1=a): a trial the API returned via full-text search but that never actually
lists the entity is excluded and reported, not silently counted (PLAN.md §11.3, Task 6.1 Step 2).
"""

from ctviz.citations.match import apply_strict_match, strict_match_policy
from ctviz.citations.predicates import evaluate
from ctviz.ctgov.normalize import normalize
from ctviz.schemas.enums import SearchParam
from ctviz.schemas.plan import EnumFilters, SearchTerm
from tests.factories import make_study

TERM = SearchTerm(param="query.intr", value="pembrolizumab", source="query_text", rationale="drug")


def test_trial_that_only_mentions_the_drug_in_passing_is_excluded_with_reason() -> None:
    listed = normalize(
        make_study("NCT00000001", interventions=[{"type": "DRUG", "name": "Pembrolizumab"}])
    )
    passing = normalize(
        make_study("NCT03307785", interventions=[{"type": "DRUG", "name": "TSR-042"}])
    )

    outcome = apply_strict_match([listed, passing], [TERM], {}, lambda _: "strict")

    assert [m.trial.nct_id for m in outcome.kept] == ["NCT00000001"]
    assert outcome.excluded[0].nct_id == "NCT03307785"
    assert outcome.excluded[0].reason == "api_fulltext_match_only"
    assert outcome.kept[0].match_evidence[0].excerpt == "Pembrolizumab"


def test_other_names_count_as_a_match() -> None:
    trial = normalize(
        make_study(
            interventions=[{"type": "DRUG", "name": "MK-3475", "otherNames": ["Pembrolizumab"]}]
        )
    )

    outcome = apply_strict_match([trial], [TERM], {}, lambda _: "strict")

    assert outcome.kept[0].match_evidence[0].field.endswith("/otherNames/0")


def test_lenient_policy_keeps_unmatched_trials_without_match_evidence() -> None:
    trial = normalize(make_study(conditions=["Something else"]))
    term = SearchTerm(param="query.cond", value="glioblastoma", source="query_text", rationale="c")

    outcome = apply_strict_match([trial], [term], {}, lambda _: "lenient")

    assert len(outcome.kept) == 1 and outcome.kept[0].match_evidence == ()


def test_trial_matching_via_a_coreferenced_alias_is_kept() -> None:
    trial = normalize(
        make_study(interventions=[{"type": "DRUG", "name": "MK-3475", "otherNames": ["Keytruda"]}])
    )
    term = SearchTerm(param="query.intr", value="pembrolizumab", source="query_text", rationale="d")

    outcome = apply_strict_match(
        [trial], [term], {"pembrolizumab": ["keytruda"]}, lambda _: "strict"
    )

    assert [m.trial.nct_id for m in outcome.kept] == [trial.nct_id]


def test_base_predicate_evaluates_true_on_the_trial_it_kept() -> None:
    trial = normalize(make_study(interventions=[{"type": "DRUG", "name": "Pembrolizumab"}]))

    outcome = apply_strict_match([trial], [TERM], {}, lambda _: "strict")

    assert evaluate(outcome.base_predicate, trial.raw) is True


def test_base_predicate_is_empty_all_when_no_strict_terms_or_filters() -> None:
    trial = normalize(make_study())

    outcome = apply_strict_match([trial], [], {}, lambda _: "lenient")

    assert outcome.base_predicate == {"all": []}


def test_strict_match_policy_off_is_lenient_for_every_param() -> None:
    policy = strict_match_policy("off")

    assert policy(SearchParam.INTR) == "lenient"
    assert policy(SearchParam.COND) == "lenient"


def test_strict_match_policy_all_is_strict_for_every_param() -> None:
    policy = strict_match_policy("all")

    assert policy(SearchParam.INTR) == "strict"
    assert policy(SearchParam.COND) == "strict"


def test_strict_match_policy_auto_is_strict_for_drugs_lenient_for_conditions() -> None:
    """Q1=c (§22): auto is strict for drugs/sponsors, lenient for conditions (CONDITIONS_STRICT)."""
    policy = strict_match_policy("auto")

    assert policy(SearchParam.INTR) == "strict"
    assert policy(SearchParam.LEAD) == "strict"
    assert policy(SearchParam.SPONS) == "strict"
    assert policy(SearchParam.COND) == "lenient"


# --- Item 7: §11.3 match-field priority order and the M3 filter re-check -------------------------


def _match_field(study: dict) -> str:
    [kept] = apply_strict_match([normalize(study)], [TERM], {}, lambda _: "strict").kept
    return kept.match_evidence[0].field


def test_intervention_names_win_over_an_earlier_objects_other_names() -> None:
    """interventions[].name is tried across ALL objects before any otherNames entry."""
    study = make_study(
        interventions=[
            {"type": "DRUG", "name": "MK-3475", "otherNames": ["Pembrolizumab"]},
            {"type": "DRUG", "name": "Pembrolizumab IV"},
        ]
    )

    assert _match_field(study).endswith("/interventions/1/name")


def test_other_names_win_over_arm_intervention_names() -> None:
    study = make_study(
        interventions=[{"type": "DRUG", "name": "MK-3475", "otherNames": ["Pembrolizumab"]}],
        arms=[{"label": "A", "interventionNames": ["Drug: Pembrolizumab"]}],
    )

    assert _match_field(study).endswith("/interventions/0/otherNames/0")


def test_arm_intervention_names_win_over_intervention_mesh_terms() -> None:
    study = make_study(
        interventions=[{"type": "DRUG", "name": "MK-3475"}],
        arms=[{"label": "A", "interventionNames": ["Drug: Pembrolizumab"]}],
        mesh_interventions=["pembrolizumab"],
    )

    assert _match_field(study).endswith("/armGroups/0/interventionNames/0")


def test_intervention_mesh_term_is_the_last_resort_match_field() -> None:
    study = make_study(
        interventions=[{"type": "DRUG", "name": "MK-3475"}], mesh_interventions=["pembrolizumab"]
    )

    assert _match_field(study).startswith("/derivedSection/interventionBrowseModule/meshes/")


def test_filter_recheck_drops_a_record_that_fails_an_enum_filter() -> None:
    """M3: the API returned a Phase 1 trial for a Phase 3 filter -- dropped at stage 'filter'."""
    listed = [{"type": "DRUG", "name": "Pembrolizumab"}]
    phase1 = normalize(make_study("NCT00000001", phases=["PHASE1"], interventions=listed))
    phase3 = normalize(make_study("NCT00000002", phases=["PHASE3"], interventions=listed))
    no_filters = dict.fromkeys(EnumFilters.model_fields)
    filters = EnumFilters(**no_filters | {"phases": ["PHASE3"]})

    outcome = apply_strict_match([phase1, phase3], [TERM], {}, lambda _: "strict", filters)

    assert [m.trial.nct_id for m in outcome.kept] == ["NCT00000002"]
    [dropped] = outcome.excluded
    assert (dropped.nct_id, dropped.stage, dropped.reason) == (
        "NCT00000001",
        "filter",
        "api_filter_mismatch",
    )
    assert evaluate(outcome.base_predicate, phase1.raw) is False
