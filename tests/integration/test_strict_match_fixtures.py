"""Strict match + co-referenced aliases on the real recorded fixtures (§10.4, §11.3, item 1)."""

import pytest

from ctviz.analysis.entities import discover_aliases
from ctviz.citations.match import MatchOutcome, apply_strict_match
from ctviz.common.names import normalize_text
from ctviz.schemas.plan import SearchTerm
from tests.fixtures.load import load_trials

# Genuine pembrolizumab synonyms: brand, INN-era and code names, the CAS number, Merck
# co-formulation codes that CONTAIN pembrolizumab, and biosimilar codes whose own intervention
# object is literally named "Pembrolizumab biosimilar <code>". An alias may bundle several of
# these in one otherNames string ("keytruda, mk-3475"), so each comma-part is checked.
GENUINE_PEMBRO_SYNONYMS = frozenset(
    {
        "keytruda",
        "keytruda®",
        "mk-3475",
        "mk 3475",
        "mk3475",
        "sch 900475",
        "sch-900475",
        "sch900475",
        "sch 9000475",
        "lambrolizumab",
        "pembro",  # the standard abbreviation (also catches misspellings like "pembrolizmub")
        "1374853-91-4",
        "mk-3475a",
        "mk-1308a",
        "mk-7684a",
        "mk-4280a",
        "bcd-201",
        "gme751",
        "gme 751",
        "ql2107",
        "rph-075",
        "rph 075",
        "rph075",
        "sb27",
        "sb 27",
        "sb-27",
    }
)
PARTNERS_AND_CLASSES = (
    "carboplatin",
    "cisplatin",
    "paclitaxel",
    "nivolumab",
    "immunotherapy",
    "checkpoint inhibitor",
    "placebo",
)
OFF_TOPIC_NCT_IDS = ("NCT03307785", "NCT06205732")  # named in PLAN.md §11.3
# Pinned fixture truth under the fixed alias rule (PLAN.md §11.3 quotes 331; the difference is
# explained in `test_strict_match_excludes_exactly_the_pinned_number_of_pembro_trials`).
PEMBRO_EXCLUDED_AT_MATCH = 325
PEMBRO_API_TOTAL = 2960
RESCUED_BEYOND_SPEC_331 = (
    "NCT03523702",  # interventions/0/name "PembroRT"
    "NCT03818893",  # interventions/0/name "Combination of GEN0101 and Pembrolizmub"
    "NCT06110793",  # interventions/1/name "Pembrolizuma"
    "NCT06949761",  # interventions/1/name "QLC1101+QL2107"
    "NCT06972615",  # interventions/0/otherNames/0 "pembrolizumb"
    "NCT07618793",  # interventions/1/otherNames/2 "Pembrokizumab"
)


def _intr(value: str) -> SearchTerm:
    return SearchTerm(param="query.intr", value=value, source="query_text", rationale="drug")


def _strict(fixture: str, term: str, other_values: tuple[str, ...] = ()) -> MatchOutcome:
    trials = list(load_trials(fixture))
    aliases = discover_aliases(trials, term, other_cohort_values=other_values)
    return apply_strict_match(trials, [_intr(term)], {term: aliases}, lambda _: "strict")


def test_pembrolizumab_aliases_are_all_genuine_synonyms() -> None:
    aliases = discover_aliases(list(load_trials("pembrolizumab")), "pembrolizumab")

    parts = {part.strip() for alias in aliases for part in alias.split(",")}
    assert parts <= GENUINE_PEMBRO_SYNONYMS, parts - GENUINE_PEMBRO_SYNONYMS
    assert {"keytruda", "mk-3475", "sch 900475", "lambrolizumab"} <= set(aliases)
    for bad in PARTNERS_AND_CLASSES:
        assert not any(bad in alias for alias in aliases), bad


def test_strict_match_excludes_the_named_off_topic_trials_with_the_fulltext_reason() -> None:
    outcome = _strict("pembrolizumab", "pembrolizumab")

    reasons = {e.nct_id: (e.stage, e.reason) for e in outcome.excluded}
    for nct_id in OFF_TOPIC_NCT_IDS:
        assert reasons[nct_id] == ("match", "api_fulltext_match_only")
    assert not {m.trial.nct_id for m in outcome.kept} & set(OFF_TOPIC_NCT_IDS)


def test_strict_match_excludes_exactly_the_pinned_number_of_pembro_trials() -> None:
    """325 = 2,960 - 2,635 kept. PLAN.md §11.3 quotes 331, which is exactly what the brand/code
    aliases alone give (Keytruda, MK-3475, SCH 900475, lambrolizumab; 337 with no aliases). The
    co-reference rule also learns "pembro" and the biosimilar code "QL2107", rescuing 6 trials,
    all via the first two §11.3 fields (interventions[].name / otherNames), none via arm names
    or MeSH: 5 misspellings caught by "pembro" and 1 via "QLC1101+QL2107"."""
    outcome = _strict("pembrolizumab", "pembrolizumab")

    assert len(outcome.kept) + len(outcome.excluded) == PEMBRO_API_TOTAL
    assert len(outcome.excluded) == PEMBRO_EXCLUDED_AT_MATCH
    kept = {m.trial.nct_id for m in outcome.kept}
    assert set(RESCUED_BEYOND_SPEC_331) <= kept


def test_match_evidence_points_at_a_pembrolizumab_entry_never_a_partner_drug() -> None:
    outcome = _strict("pembrolizumab", "pembrolizumab")
    needles = ("pembrolizumab", *GENUINE_PEMBRO_SYNONYMS)

    for matched in outcome.kept:
        [evidence] = matched.match_evidence
        excerpt = normalize_text(evidence.excerpt)
        assert any(needle in excerpt for needle in needles), (matched.trial.nct_id, excerpt)


@pytest.mark.parametrize(
    ("fixture", "term", "other"),
    [("pembrolizumab", "pembrolizumab", "nivolumab"), ("nivolumab", "nivolumab", "pembrolizumab")],
)
def test_compared_cohorts_never_learn_each_other_as_an_alias(
    fixture: str, term: str, other: str
) -> None:
    aliases = discover_aliases(list(load_trials(fixture)), term, other_cohort_values=[other])

    assert not any(other in alias for alias in aliases)


def test_pembro_vs_nivo_cohorts_overlap_only_on_trials_naming_both_drugs() -> None:
    pembro = _strict("pembrolizumab", "pembrolizumab", ("nivolumab",))
    nivo = _strict("nivolumab", "nivolumab", ("pembrolizumab",))
    pembro_ids = {m.trial.nct_id for m in pembro.kept}
    nivo_by_id = {m.trial.nct_id: m.trial for m in nivo.kept}

    shared = pembro_ids & set(nivo_by_id)
    assert shared  # real trials testing both drugs sit in both cohorts
    pembro_needles = ("pembrolizumab", *GENUINE_PEMBRO_SYNONYMS)
    nivo_only = [
        nct_id
        for nct_id, trial in nivo_by_id.items()
        if not any(
            needle in normalize_text(name)
            for needle in pembro_needles
            for iv in trial.interventions
            for name in (iv.name, *iv.other_names)
        )
    ]
    assert nivo_only
    assert not set(nivo_only) & pembro_ids
