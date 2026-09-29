"""Aggregation against a recorded fixture: the golden phase count (PLAN.md §11.4)."""

from ctviz.analysis.aggregate import MatchedTrial, count_by
from ctviz.schemas.enums import Dimension
from tests.fixtures.load import load_trials


def test_pembrolizumab_phase_buckets_sum_to_all_fetched_trials() -> None:
    trials = [MatchedTrial(trial, ()) for trial in load_trials("pembrolizumab")]

    result = count_by(trials, Dimension.PHASE)

    assert sum(b.trial_count for b in result.buckets) == len(trials)
    assert {b.key: b.trial_count for b in result.buckets}["Phase 3"] == 324


def test_nsclc_country_other_row_never_repeats_a_trial_from_a_kept_bar() -> None:
    """Item 4: before the fix, 996 of the 1,471 'Other' trials were also in kept bars."""
    trials = [MatchedTrial(trial, ()) for trial in load_trials("nsclc")]

    result = count_by(trials, Dimension.COUNTRY, top_n=15)

    *kept, other = result.buckets
    assert other.key == "Other"
    kept_ids = {c.nct_id for b in kept for c in b.citations}
    other_ids = [c.nct_id for c in other.citations]
    assert other_ids  # the long tail is real
    assert len(other_ids) == len(set(other_ids))
    assert not set(other_ids) & kept_ids
