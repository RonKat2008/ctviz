"""Aggregation where counting and citing are one step (PLAN.md §10.5, §11.2)."""

from ctviz.analysis.aggregate import MatchedTrial, count_by, time_trend
from ctviz.citations.pointer import PHASES, START_DATE
from ctviz.ctgov.normalize import normalize
from ctviz.schemas.citations import Evidence
from ctviz.schemas.enums import Dimension
from tests.factories import make_study


def _matched(*studies: dict) -> list[MatchedTrial]:
    return [MatchedTrial(normalize(s), ()) for s in studies]


def test_count_by_phase_counts_each_trial_once_in_combined_buckets() -> None:
    trials = _matched(
        make_study("NCT00000001", phases=["PHASE3"]),
        make_study("NCT00000002", phases=["PHASE2", "PHASE3"]),
        make_study("NCT00000003", phases=["PHASE3"]),
    )

    result = count_by(trials, Dimension.PHASE)

    counts = {b.key: b.trial_count for b in result.buckets}
    assert counts == {"Phase 2/Phase 3": 1, "Phase 3": 2}
    assert sum(counts.values()) == len(trials)


def test_every_citation_belongs_to_its_bucket_trial() -> None:
    result = count_by(_matched(make_study("NCT00000001", phases=["PHASE1"])), Dimension.PHASE)

    [bucket] = result.buckets
    [citation] = bucket.citations
    assert (citation.nct_id, citation.excerpt) == ("NCT00000001", "PHASE1")


def test_top_n_rolls_the_rest_into_a_cited_other_row() -> None:
    trials = _matched(*[make_study(f"NCT0000000{i}", sponsor=f"Sponsor {i}") for i in range(5)])

    result = count_by(trials, Dimension.LEAD_SPONSOR, top_n=3)

    assert [b.key for b in result.buckets][-1] == "Other"
    assert result.buckets[-1].trial_count == 2
    assert result.buckets[-1].folded_categories == 2
    assert "not" in result.buckets[-1].predicate


def test_other_row_never_double_cites_a_trial_that_folds_into_two_multi_valued_keys() -> None:
    """A trial with sites in 3 rare countries all folded into 'Other' must appear once, not 3x
    (multi-valued dims can put one trial in several folded keys; the rollup still dedupes)."""
    trials = _matched(
        make_study(
            "NCT00000001",
            locations=[{"country": "Andorra"}, {"country": "Monaco"}, {"country": "Fiji"}],
        )
    )

    result = count_by(trials, Dimension.COUNTRY, top_n=1)

    other = next(b for b in result.buckets if b.key == "Other")
    ids = [c.nct_id for c in other.citations]
    assert ids == ["NCT00000001"]
    assert other.trial_count == 1


def test_citation_for_multiphase_trial_carries_extra_bucket_and_match_evidence() -> None:
    match_evidence = (Evidence(role="match", field="/x", excerpt="pembrolizumab"),)
    trial = normalize(make_study("NCT00000001", phases=["PHASE2", "PHASE3"]))

    result = count_by([MatchedTrial(trial, match_evidence)], Dimension.PHASE)

    [bucket] = result.buckets
    [citation] = bucket.citations
    assert citation.field == f"{PHASES}/0"
    fields = {e.field: e.excerpt for e in citation.evidence}
    assert fields[f"{PHASES}/1"] == "PHASE3"
    assert match_evidence[0] in citation.evidence


def test_other_row_predicate_is_negation_of_kept_predicates() -> None:
    trials = _matched(*[make_study(f"NCT0000000{i}", sponsor=f"Sponsor {i}") for i in range(5)])

    result = count_by(trials, Dimension.LEAD_SPONSOR, top_n=3)

    kept_predicates = [b.predicate for b in result.buckets[:-1]]
    assert result.buckets[-1].predicate == {"not": {"any": kept_predicates}}


def test_phase_buckets_are_ordered_by_display_order_regardless_of_counts() -> None:
    trials = _matched(
        *[make_study(f"NCT{100 + i:08d}", phases=["PHASE3"]) for i in range(5)],
        make_study(f"NCT{200:08d}", phases=["PHASE1"]),
    )

    result = count_by(trials, Dimension.PHASE)

    assert [b.key for b in result.buckets] == ["Phase 1", "Phase 3"]


def test_time_trend_zero_filled_year_has_predicate_and_no_citations() -> None:
    trials = _matched(
        make_study("NCT00000001", start="2024-01"),
        make_study("NCT00000002", start="2026-01"),
    )

    result = time_trend(trials, today_year=2026)

    gap = next(b for b in result.buckets if b.key == "2025")
    assert gap.predicate == {"op": "year_equals", "path": START_DATE, "value": 2025}
    assert gap.citations == ()


def test_count_by_phase_top_n_keeps_largest_then_display_order() -> None:
    trials = _matched(
        make_study("NCT00000001", phases=["PHASE1"]),
        make_study("NCT00000002", phases=["PHASE4"]),
        make_study("NCT00000003", phases=["PHASE3"]),
        make_study("NCT00000004", phases=["PHASE3"]),
        make_study("NCT00000005", phases=["PHASE2"]),
        make_study("NCT00000006", phases=["PHASE2"]),
        make_study("NCT00000007", phases=["PHASE2"]),
    )

    result = count_by(trials, Dimension.PHASE, top_n=2)

    assert [b.key for b in result.buckets] == ["Phase 2", "Phase 3", "Other"]
    assert result.buckets[-1].trial_count == 2


def test_group_uses_named_missing_reason_constant_for_lead_sponsor() -> None:
    trials = _matched(make_study("NCT00000001", sponsor=None))

    result = count_by(trials, Dimension.LEAD_SPONSOR)

    assert result.excluded == {"NCT00000001": "missing_lead_sponsor"}


def test_count_by_start_year_orders_chronologically() -> None:
    trials = _matched(
        make_study("NCT00000001", start="2022-01"),
        make_study("NCT00000002", start="2022-02"),
        make_study("NCT00000003", start="2022-03"),
        make_study("NCT00000004", start="2018-01"),
    )

    result = count_by(trials, Dimension.START_YEAR)

    assert [b.key for b in result.buckets] == ["2018", "2022"]


def test_time_trend_fills_gaps_and_flags_partial_and_projected_years() -> None:
    trials = _matched(
        make_study("NCT00000001", start="2024-01"),
        make_study("NCT00000002", start="2026-03"),
        make_study("NCT00000003", start="2027-01", start_type="ESTIMATED"),
        make_study("NCT00000004", start=None),
    )

    result = time_trend(trials, today_year=2026)

    assert [(b.key, b.trial_count) for b in result.buckets] == [
        ("2024", 1),
        ("2025", 0),
        ("2026", 1),
        ("2027", 1),
    ]
    assert result.buckets[2].flags == ("partial_period",)
    assert result.buckets[3].flags == ("projected",)
    assert result.excluded == {"NCT00000004": "missing_start_date"}
