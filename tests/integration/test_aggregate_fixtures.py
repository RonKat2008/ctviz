"""Aggregation against a recorded fixture: the golden phase count (PLAN.md §11.4)."""

from ctviz.analysis.aggregate import MatchedTrial, count_by
from ctviz.schemas.enums import Dimension
from tests.fixtures.load import load_trials


def test_pembrolizumab_phase_buckets_sum_to_all_fetched_trials() -> None:
    trials = [MatchedTrial(trial, ()) for trial in load_trials("pembrolizumab")]

    result = count_by(trials, Dimension.PHASE)

    assert sum(b.trial_count for b in result.buckets) == len(trials)
    assert {b.key: b.trial_count for b in result.buckets}["Phase 3"] == 324
