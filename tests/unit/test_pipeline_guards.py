"""§10.6 empty-cohort warning: a comparison where one cohort matched 0 trials (item 3)."""

from ctviz.ctgov.client import FetchResult
from ctviz.pipeline_meta import _CohortFetch, _empty_cohort_warnings


def _cohort(label: str, trial_count: int) -> _CohortFetch:
    fetch = FetchResult(
        records=[], api_total_count=trial_count, truncated=False, truncation_rule=None
    )
    return _CohortFetch(label, label, [object()] * trial_count, fetch)  # type: ignore[list-item]


def test_empty_cohort_in_a_comparison_gets_a_warning() -> None:
    cohorts = [_cohort("pembrolizumab", 50), _cohort("nivolumab", 0)]

    warnings = _empty_cohort_warnings(cohorts)

    assert len(warnings) == 1
    assert "nivolumab" in warnings[0] and "0 trials" in warnings[0]


def test_no_warning_when_every_cohort_in_a_comparison_has_trials() -> None:
    cohorts = [_cohort("pembrolizumab", 50), _cohort("nivolumab", 30)]

    assert _empty_cohort_warnings(cohorts) == []


def test_a_single_cohort_with_zero_trials_gets_no_warning() -> None:
    """The §10.6 rule is specifically about a COMPARISON with an empty side; a lone empty
    cohort is a plain NO_MATCHING_TRIALS case elsewhere, not this warning."""
    assert _empty_cohort_warnings([_cohort("pembrolizumab", 0)]) == []
