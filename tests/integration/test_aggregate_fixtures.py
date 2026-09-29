"""Aggregation against a recorded fixture: the golden phase count (PLAN.md §11.4)."""

from ctviz.analysis.aggregate import MatchedTrial, count_by
from ctviz.ctgov.normalize import normalize
from ctviz.schemas.enums import Dimension
from tests.fixtures.load import load_fixture


def test_pembrolizumab_phase_buckets_sum_to_all_fetched_trials() -> None:
    trials = [MatchedTrial(normalize(r), ()) for r in load_fixture("pembrolizumab")]

    result = count_by(trials, Dimension.PHASE)

    assert sum(b.trial_count for b in result.buckets) == len(trials)
    assert {b.key: b.trial_count for b in result.buckets}["Phase 3"] == 324
