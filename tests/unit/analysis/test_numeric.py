"""Numeric analyses: histogram bin selection and scatter point exclusion rules (§10.5, §11.5)."""

import pytest

from ctviz.analysis.aggregate import MatchedTrial
from ctviz.analysis.numeric import histogram, scatter
from ctviz.citations.pointer import ENROLLMENT_TYPE
from ctviz.ctgov.normalize import normalize
from ctviz.errors import PlanInvalidError
from ctviz.schemas.enums import Measure
from tests.factories import make_study
from tests.fixtures.load import load_trials


def _m(*studies: dict) -> list[MatchedTrial]:
    return [MatchedTrial(normalize(s), ()) for s in studies]


def test_histogram_uses_log_bins_for_skewed_enrollment_and_excludes_withdrawn_zeros() -> None:
    # Plotted-only skewness (Q-A): [3, 5, 8, 12, 30, 80, 2500] has adjusted G1 ~= 2.64 (> the
    # 2.0 threshold) computed on the PLOTTED values alone, so this must choose log bins even
    # though the withdrawn zero is excluded before skewness is ever computed.
    trials = _m(
        *[
            make_study(f"NCT0000000{i}", enrollment=n)
            for i, n in enumerate([3, 5, 8, 12, 30, 80, 2500])
        ],
        make_study("NCT00000009", enrollment=0, status="WITHDRAWN"),
    )

    result = histogram(trials, Measure.ENROLLMENT)

    labels = {b.key: b.trial_count for b in result.buckets if b.trial_count}
    assert labels == {"0–9": 3, "10–24": 1, "25–49": 1, "50–99": 1, "2000–4999": 1}  # noqa: RUF001
    assert result.excluded == {"NCT00000009": "withdrawn_zero_enrollment"}


def test_histogram_bin_choice_is_unaffected_by_an_excluded_withdrawn_zero() -> None:
    """Q-A regression: [5, 30, 80, 2500] alone has plotted-only skewness ~1.996 (< 2.0, so FD
    bins), but folding in a withdrawn zero used to push it to ~2.23 (> 2.0, log bins) -- a
    different *shape*, not just a different count. An excluded value must never do that."""
    trials = _m(
        *[make_study(f"NCT0000000{i}", enrollment=n) for i, n in enumerate([5, 30, 80, 2500])],
        make_study("NCT00000009", enrollment=0, status="WITHDRAWN"),
    )

    result = histogram(trials, Measure.ENROLLMENT)

    labels = {b.key: b.trial_count for b in result.buckets if b.trial_count}
    assert labels == {"5–316": 3, "2189–2500": 1}  # noqa: RUF001 FD bins, not log-bin edges
    assert result.excluded == {"NCT00000009": "withdrawn_zero_enrollment"}


def test_histogram_uses_freedman_diaconis_bins_for_unskewed_enrollment() -> None:
    values = list(range(100, 120))  # uniform, not skewed
    trials = _m(*[make_study(f"NCT{i:08d}", enrollment=v) for i, v in enumerate(values)])

    result = histogram(trials, Measure.ENROLLMENT)

    non_empty = [b for b in result.buckets if b.trial_count]
    assert 8 <= len(result.buckets) <= 30
    assert sum(b.trial_count for b in non_empty) == len(values)
    assert result.excluded == {}


def test_histogram_of_site_count_raises_plan_invalid_instead_of_crashing() -> None:
    """HIGH item 8: site_count has no §11.5 histogram evidence recipe; reject, don't IndexError."""
    trials = _m(make_study("NCT00000001", locations=[{"country": "Japan"}]))

    with pytest.raises(PlanInvalidError, match="site_count"):
        histogram(trials, Measure.SITE_COUNT)


def test_histogram_of_duration_months_raises_plan_invalid_no_expressible_predicate() -> None:
    """MEDIUM item 12: duration is derived (completion - start); no §11.6 op can range-check it,
    so an `in_range` predicate over it (previously pointed at START_DATE) is unverifiable."""
    trials = _m(make_study("NCT00000001", enrollment=100, start="2017-08", completion="2020-08"))

    with pytest.raises(PlanInvalidError, match="duration_months"):
        histogram(trials, Measure.DURATION_MONTHS)


def test_histogram_citations_include_the_enrollment_type_evidence_item() -> None:
    """M07: a histogram bucket's citation must carry the enrollment-type evidence item (§11.5
    "Histogram bin" recipe: count + type), not just the count."""
    trials = _m(make_study("NCT00000001", enrollment=150, enrollment_type="ACTUAL"))

    result = histogram(trials, Measure.ENROLLMENT)

    [citation] = [c for b in result.buckets for c in b.citations]
    [type_item] = [item for item in citation.evidence if item.field == ENROLLMENT_TYPE]
    assert type_item.excerpt == "ACTUAL"
    # The type qualifies the count; it is not what places the trial in this bin (the bin's
    # predicate reads only the count), so it is `context`, not `bucket`, evidence (§11.6 step 3).
    assert type_item.role == "context"


def test_histogram_bucket_actual_vs_estimated_split_is_not_swapped() -> None:
    """M12: `_histogram_bucket`'s actual/estimated flag counts must match which trials actually
    carry which enrollment type -- swapping the two counts must fail this test."""
    values = list(range(100, 120))  # uniform, unskewed -> Freedman-Diaconis bins (as above)
    estimated_indices = {0, 5, 10}  # 3 trials ESTIMATED, 17 ACTUAL
    trials = _m(
        *[
            make_study(
                f"NCT{i:08d}",
                enrollment=v,
                enrollment_type="ESTIMATED" if i in estimated_indices else "ACTUAL",
            )
            for i, v in enumerate(values)
        ]
    )

    result = histogram(trials, Measure.ENROLLMENT)

    all_flags = [f for b in result.buckets for f in b.flags]
    total_actual = sum(int(f.split(":")[1]) for f in all_flags if f.startswith("actual:"))
    total_estimated = sum(int(f.split(":")[1]) for f in all_flags if f.startswith("estimated:"))
    assert (total_actual, total_estimated) == (17, 3)


def test_histogram_bucket_flags_untyped_enrollment_alongside_actual_and_estimated() -> None:
    """Item 3: a bin with an untyped-enrollment trial (no `enrollmentInfo.type`) must flag
    `untyped:N` so `actual + estimated + untyped == trial_count` for every bin -- a bin can't
    silently drop a trial from the actual/estimated/untyped split."""
    values = list(range(100, 122))  # uniform, unskewed -> Freedman-Diaconis bins (single bin)
    estimated_indices = {0, 5}
    untyped_indices = {1, 10}
    trials = _m(
        *[
            make_study(
                f"NCT{i:08d}",
                enrollment=v,
                enrollment_type=(
                    "ESTIMATED"
                    if i in estimated_indices
                    else None
                    if i in untyped_indices
                    else "ACTUAL"
                ),
            )
            for i, v in enumerate(values)
        ]
    )

    result = histogram(trials, Measure.ENROLLMENT)

    for bucket in result.buckets:
        counts = {f.split(":")[0]: int(f.split(":")[1]) for f in bucket.flags}
        assert set(counts) == {"actual", "estimated", "untyped"}
        assert sum(counts.values()) == bucket.trial_count
    all_flags = [f for b in result.buckets for f in b.flags]
    total_untyped = sum(int(f.split(":")[1]) for f in all_flags if f.startswith("untyped:"))
    assert total_untyped == len(untyped_indices)


def test_psoriasis_fixture_histogram_bins_satisfy_the_actual_estimated_untyped_invariant() -> None:
    """Item 3 invariant check over a real fixture: every enrollment histogram bin must split its
    trial_count exactly into actual/estimated/untyped, with none left uncounted."""
    trials = [MatchedTrial(trial, ()) for trial in load_trials("psoriasis_p2")]

    result = histogram(trials, Measure.ENROLLMENT)

    assert result.buckets  # sanity: the fixture actually produces bins
    for bucket in result.buckets:
        counts = {f.split(":")[0]: int(f.split(":")[1]) for f in bucket.flags}
        assert sum(counts.values()) == bucket.trial_count


def test_histogram_missing_enrollment_is_excluded() -> None:
    trials = _m(make_study("NCT00000001", enrollment=None))

    result = histogram(trials, Measure.ENROLLMENT)

    assert result.excluded == {"NCT00000001": "missing_enrollment"}
    assert result.buckets == ()


def test_scatter_excludes_estimated_dates_and_flags_untyped_ones() -> None:
    trials = _m(
        make_study(
            "NCT00000001",
            enrollment=100,
            start="2017-08",
            start_type=None,
            completion="2020-08",
            completion_type="ACTUAL",
        ),
        make_study(
            "NCT00000002",
            enrollment=200,
            start="2017-08",
            completion="2020-08",
            completion_type="ACTUAL",
        ),
        make_study(
            "NCT00000003",
            enrollment=300,
            start="2024-01",
            completion="2028-05",
            completion_type="ESTIMATED",
        ),
    )

    points, excluded = scatter(trials, Measure.DURATION_MONTHS, Measure.ENROLLMENT)

    assert [(p.nct_id, p.date_type) for p in points] == [
        ("NCT00000001", "untyped"),
        ("NCT00000002", "actual"),
    ]
    assert excluded == {"NCT00000003": "non_actual_duration"}


def test_scatter_keeps_a_short_duration_when_dates_have_day_precision() -> None:
    """Q-B: the < 1 month implausible-duration cut applies ONLY when both dates are month-only
    (day-15 imputation on both can manufacture a spurious near-zero duration). Real day-precision
    dates 10 days apart are a genuine short duration and must not be excluded."""
    trials = _m(
        make_study(
            "NCT00000001",
            enrollment=100,
            start="2020-01-01",
            completion="2020-01-11",
        )
    )

    points, excluded = scatter(trials, Measure.DURATION_MONTHS, Measure.ENROLLMENT)

    assert excluded == {}
    assert len(points) == 1


def test_scatter_excludes_short_duration_when_both_dates_are_month_only() -> None:
    """The exclusion still fires when both dates lack day precision (the case it exists for)."""
    trials = _m(
        make_study(
            "NCT00000001",
            enrollment=100,
            start="2020-01",
            completion="2020-01",
        )
    )

    points, excluded = scatter(trials, Measure.DURATION_MONTHS, Measure.ENROLLMENT)

    assert points == []
    assert excluded == {"NCT00000001": "implausible_duration"}


def test_scatter_excludes_a_trial_missing_either_measure() -> None:
    trials = _m(
        make_study("NCT00000001", enrollment=None, start="2017-08", completion="2020-08"),
    )

    points, excluded = scatter(trials, Measure.DURATION_MONTHS, Measure.ENROLLMENT)

    assert points == []
    assert excluded == {"NCT00000001": "missing_enrollment"}


def test_scatter_site_count_measure_uses_the_number_of_distinct_sites() -> None:
    trials = _m(
        make_study(
            "NCT00000001",
            enrollment=100,
            locations=[{"country": "Japan"}, {"country": "France"}],
        )
    )

    points, excluded = scatter(trials, Measure.SITE_COUNT, Measure.ENROLLMENT)

    assert excluded == {}
    assert [(p.nct_id, p.x) for p in points] == [("NCT00000001", 2.0)]


def test_scatter_site_count_measure_excludes_a_trial_with_no_sites() -> None:
    trials = _m(make_study("NCT00000001", enrollment=100, locations=[]))

    points, excluded = scatter(trials, Measure.SITE_COUNT, Measure.ENROLLMENT)

    assert points == []
    assert excluded == {"NCT00000001": "missing_site_count"}
