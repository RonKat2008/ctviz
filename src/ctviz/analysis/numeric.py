"""Numeric analyses: binned histograms and trial-level scatter points (PLAN.md §10.5)."""

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from itertools import pairwise

from ctviz.analysis.aggregate import AggregateResult, Bucket, MatchedTrial
from ctviz.citations.pointer import (
    COMPLETION_DATE,
    COMPLETION_DATE_TYPE,
    ENROLLMENT_COUNT,
    ENROLLMENT_TYPE,
    START_DATE,
    START_DATE_TYPE,
    json_text,
)
from ctviz.ctgov.normalize import PartialDate, Trial
from ctviz.errors import PlanInvalidError
from ctviz.schemas.citations import Citation, Evidence
from ctviz.schemas.enums import Measure

LOG_EDGES = (0, 10, 25, 50, 100, 200, 500, 1000, 2000, 5000)
EN_DASH = chr(0x2013)  # named to avoid an ambiguous-unicode literal in source (RUF001)
GEQ = chr(0x2265)
SKEWNESS_LOG_THRESHOLD = 2.0
MIN_BINS = 8
MAX_BINS = 30
DAY_FOR_MISSING_DATE_PART = 15
DAYS_PER_MONTH = 30.4375
MIN_DURATION_MONTHS = 1.0

_Bin = tuple[float, float | None, str]  # (lo, hi-or-open, label)


@dataclass(frozen=True)
class Point:
    """One scatter point: a single trial's two measures, with its own citation evidence."""

    nct_id: str
    x: float
    y: float
    date_type: str
    citations: tuple[Citation, ...]


@dataclass(frozen=True)
class _MeasureValue:
    """One measure's numeric value for one trial, with the evidence that supports it."""

    value: float
    evidence: tuple[Evidence, ...]
    date_type: str | None = None  # only set when the measure is duration_months


def _skewness(values: list[float]) -> float:
    """Bias-corrected (adjusted Fisher-Pearson) sample skewness (PLAN.md §10.5 bin-selection)."""
    n = len(values)
    if n < 3:
        return 0.0
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / n
    if variance == 0:
        return 0.0
    std = math.sqrt(variance)
    third_moment = sum((v - mean) ** 3 for v in values) / n
    g1 = third_moment / std**3
    return math.sqrt(n * (n - 1)) / (n - 2) * g1


def _percentile(sorted_values: list[float], pct: float) -> float:
    """Linear-interpolation percentile, avoiding a numpy dependency for one small formula."""
    if len(sorted_values) == 1:
        return sorted_values[0]
    k = (len(sorted_values) - 1) * (pct / 100)
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return sorted_values[int(k)]
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (k - lo)


def _fd_bin_count(values: list[float]) -> int:
    """Freedman-Diaconis bin count, clamped to [MIN_BINS, MAX_BINS] (PLAN.md §10.5)."""
    sorted_v = sorted(values)
    iqr = _percentile(sorted_v, 75) - _percentile(sorted_v, 25)
    value_range = sorted_v[-1] - sorted_v[0]
    if iqr <= 0 or value_range <= 0:
        return MIN_BINS
    width = 2 * iqr / (len(sorted_v) ** (1 / 3))
    bins = math.ceil(value_range / width) if width > 0 else MIN_BINS
    return max(MIN_BINS, min(MAX_BINS, bins))


def _log_bins() -> list[_Bin]:
    """The fixed log-spaced edges plus an open last bin, for skewed distributions."""
    bins: list[_Bin] = [(lo, hi, f"{lo}{EN_DASH}{hi - 1}") for lo, hi in pairwise(LOG_EDGES)]
    bins.append((LOG_EDGES[-1], None, f"{GEQ}{LOG_EDGES[-1]}"))
    return bins


def _bin_label(start: float, end: float, integer_measure: bool) -> str:
    """A bin's display label, exactly describing [start, end) membership (MEDIUM item 12)."""
    if integer_measure:
        return f"{int(start)}{EN_DASH}{int(end) - 1}"
    return f"{start:.1f}{EN_DASH}{end:.1f}"


def _fd_bins(values: list[float]) -> list[_Bin]:
    """Equal-width bins across [min, max], width from Freedman-Diaconis (PLAN.md §10.5).

    Edges are always non-decreasing and the last bin closes exactly at the data's maximum
    (MEDIUM item 12 fix): the FD width floor of 1.0 (never a zero-width bin) used to be applied
    to a bin *count* sized for the un-floored width, which could overshoot the data's actual
    range and produce an inverted final bin (start > end, e.g. "17-14"). The bin count is now
    derived from the floored width instead, so the bins always exactly tile [lo, upper).
    """
    lo, hi = min(values), max(values)
    integer_measure = all(v == int(v) for v in values)
    upper = hi + 1 if integer_measure else hi  # integer edges are [lo, hi] inclusive of hi
    span = max(upper - lo, 0.0)
    target_bins = _fd_bin_count(values)
    raw_width = span / target_bins if target_bins else span
    width = max(raw_width, 1.0)
    n_bins = max(1, math.ceil(span / width)) if span > 0 else 1
    bins: list[_Bin] = []
    for i in range(n_bins):
        start = lo + i * width
        end = upper if i == n_bins - 1 else lo + (i + 1) * width
        bins.append((start, end, _bin_label(start, end, integer_measure)))
    return bins


def _select_bins(values: list[float]) -> list[_Bin]:
    """Log bins when the distribution is skewed, else Freedman-Diaconis (PLAN.md §10.5)."""
    return _log_bins() if _skewness(values) > SKEWNESS_LOG_THRESHOLD else _fd_bins(values)


def _bin_index(value: float, bins: list[_Bin]) -> int:
    """Which bin `value` falls in; the caller guarantees the bins cover every included value."""
    for i, (lo, hi, _label) in enumerate(bins):
        if hi is None:
            if value >= lo:
                return i
        elif lo <= value < hi:
            return i
    return len(bins) - 1


def _approx_date(partial: PartialDate) -> date:
    """A concrete date from a partial one, using day 15 when the day is missing (PLAN.md §10.3)."""
    return date(partial.year, partial.month or 1, partial.day or DAY_FOR_MISSING_DATE_PART)


def _enrollment_value(trial: Trial) -> _MeasureValue | str:
    """Enrollment count + type; withdrawn zero-enrollment trials are an analysis exclusion."""
    if trial.enrollment is None:
        return "missing_enrollment"
    if trial.enrollment == 0 and trial.status == "WITHDRAWN":
        return "withdrawn_zero_enrollment"
    count_excerpt = json_text(trial.enrollment)
    evidence = [Evidence(role="bucket", field=ENROLLMENT_COUNT, excerpt=count_excerpt)]
    if trial.enrollment_type is not None:
        # `context`: the type qualifies the count (§11.5 "the type is always cited") but is not
        # what places the trial in a bin, so it isn't held to the verifier's relevance check.
        type_evidence = Evidence(
            role="context", field=ENROLLMENT_TYPE, excerpt=trial.enrollment_type
        )
        evidence.append(type_evidence)
    return _MeasureValue(float(trial.enrollment), tuple(evidence))


def _duration_value(trial: Trial) -> _MeasureValue | str:
    """Duration in months from start/completion; ESTIMATED dates and short durations excluded."""
    if trial.start is None or trial.completion is None:
        return "missing_start_or_completion_date"
    if trial.start.type == "ESTIMATED" or trial.completion.type == "ESTIMATED":
        return "non_actual_duration"
    months = (_approx_date(trial.completion) - _approx_date(trial.start)).days / DAYS_PER_MONTH
    # §10.3: the < 1 month cut exists because day-15 imputation on TWO month-only dates in the
    # same month manufactures a spurious ~0-month duration. When either date carries real day
    # precision, a short duration is a real, reported one and must not be excluded (Q-B).
    both_month_only = trial.start.day is None and trial.completion.day is None
    if both_month_only and months < MIN_DURATION_MONTHS:
        return "implausible_duration"
    date_type = "untyped" if trial.start.type is None or trial.completion.type is None else "actual"
    evidence = [Evidence(role="bucket", field=START_DATE, excerpt=trial.start.raw)]
    if trial.start.type is not None:
        evidence.append(Evidence(role="bucket", field=START_DATE_TYPE, excerpt=trial.start.type))
    evidence.append(Evidence(role="bucket", field=COMPLETION_DATE, excerpt=trial.completion.raw))
    if trial.completion.type is not None:
        evidence.append(
            Evidence(role="bucket", field=COMPLETION_DATE_TYPE, excerpt=trial.completion.type)
        )
    return _MeasureValue(months, tuple(evidence), date_type)


def _site_count_value(trial: Trial) -> _MeasureValue | str:
    """Distinct site count; a trial with no sites has nothing to measure."""
    if not trial.sites:
        return "missing_site_count"
    return _MeasureValue(float(len(trial.sites)), ())


def _measure_value(trial: Trial, measure: Measure) -> _MeasureValue | str:
    """Dispatch to the per-measure extractor: returns a value, or an exclusion reason string."""
    if measure is Measure.ENROLLMENT:
        return _enrollment_value(trial)
    if measure is Measure.DURATION_MONTHS:
        return _duration_value(trial)
    if measure is Measure.SITE_COUNT:
        return _site_count_value(trial)
    raise NotImplementedError(f"measure {measure} is not supported (S5 slice)")


def _histogram_citation(matched: MatchedTrial, value: _MeasureValue) -> Citation:
    """One trial's histogram-bin citation: primary is the measure's own value field."""
    primary, *extra = value.evidence
    return Citation(
        nct_id=matched.trial.nct_id,
        field=primary.field,
        excerpt=primary.excerpt,
        evidence=[*extra, *matched.match_evidence],
    )


def histogram(trials: list[MatchedTrial], measure: Measure) -> AggregateResult:
    """Binned distribution of a numeric measure, with each bin's trials cited (PLAN.md §10.5).

    Ruling (HIGH item 8): PLAN.md §11.5's evidence-recipe table defines a citable "Histogram
    bin" recipe only for `enrollment` (count + type). `site_count` has no evidence recipe of
    its own -- `_site_count_value` deliberately carries no evidence, because as a *scatter*
    measure its point is always co-cited by the other axis (§11.5 "Scatter point"). A site_count
    histogram would have no bucket evidence to cite at all, so it is rejected here rather than
    silently citing nothing (or crashing, as it did before this fix). site_count stays valid for
    scatter plots. Cost if wrong: an uncited chart datum, which the §11.6 verifier must catch
    anyway -- rejecting up front is cheaper and gives the caller an actionable plan error.
    """
    if measure is Measure.SITE_COUNT:
        raise PlanInvalidError(
            [
                "histogram of measure_x=site_count has no citable evidence recipe (§11.5); "
                "use it as a scatter measure instead"
            ]
        )
    if measure is Measure.DURATION_MONTHS:
        # Ruling (MEDIUM item 12): a histogram bin's predicate is `{"op": "in_range", "path":
        # ..., "value": [lo, hi]}`, evaluated by the §11.6 verifier as a numeric range check on
        # ONE raw-record field. duration_months is not a raw field -- it's completion minus
        # start, and no §11.6 op can express that subtraction (no `duration_months_in_range`
        # op was invented, per instructions). §11.5's evidence-recipe table also defines no
        # "Histogram bin" recipe for duration_months, only for enrollment. Bucketing it here
        # would either point `in_range` at START_DATE (comparing a date string to a numeric
        # range -- wrong) or silently ship an unverifiable predicate. Rejected instead; it stays
        # valid as a scatter measure, whose §11.5 recipe cites both dates on the point itself
        # with no range predicate at all. Cost if wrong: a bucket the independent verifier can
        # never confirm, defeating the whole citation guarantee for that chart.
        raise PlanInvalidError(
            [
                "histogram of measure_x=duration_months has no §11.6-expressible predicate "
                "(duration is derived, not a raw field); use it as a scatter measure instead"
            ]
        )
    entries: list[tuple[MatchedTrial, _MeasureValue]] = []
    excluded: dict[str, str] = {}
    for matched in trials:
        result = _measure_value(matched.trial, measure)
        if isinstance(result, str):
            excluded[matched.trial.nct_id] = result
        else:
            entries.append((matched, result))
    if not entries:
        return AggregateResult((), excluded)
    # Bin *shape* (skewness + FD width) reflects only the PLOTTED population, never an
    # analysis-excluded value such as a withdrawn zero-enrollment trial (Q-A ruling): an
    # excluded record must not be able to shift which trials land in which visible bin.
    plotted_values = [value.value for _matched, value in entries]
    bins = _select_bins(plotted_values)
    grouped: dict[int, list[tuple[MatchedTrial, _MeasureValue]]] = defaultdict(list)
    for matched, value in entries:
        grouped[_bin_index(value.value, bins)].append((matched, value))
    path = entries[0][1].evidence[0].field
    buckets = tuple(
        _histogram_bucket(lo, hi, label, grouped.get(i, []), path)
        for i, (lo, hi, label) in enumerate(bins)
    )
    return AggregateResult(buckets, excluded)


def _histogram_bucket(
    lo: float,
    hi: float | None,
    label: str,
    items: list[tuple[MatchedTrial, _MeasureValue]],
    path: str,
) -> Bucket:
    """One histogram bin: its citations plus the actual/estimated split flags."""
    citations = tuple(_histogram_citation(m, v) for m, v in items)
    type_counts: dict[str, int] = defaultdict(int)
    for _matched, value in items:
        for item in value.evidence:
            if item.field == ENROLLMENT_TYPE:
                type_counts[item.excerpt] += 1
    actual, estimated = type_counts.get("ACTUAL", 0), type_counts.get("ESTIMATED", 0)
    # Item 3: a trial with no enrollment-type evidence (no `enrollmentInfo.type` on the raw
    # record) is neither actual nor estimated -- it must still be counted, or
    # actual + estimated < trial_count for that bin.
    untyped = len(items) - actual - estimated
    flags = (f"actual:{actual}", f"estimated:{estimated}", f"untyped:{untyped}")
    predicate = {"op": "in_range", "path": path, "value": [lo, hi]}
    return Bucket(label, predicate, citations, flags)


def scatter(
    trials: list[MatchedTrial], x: Measure, y: Measure
) -> tuple[list[Point], dict[str, str]]:
    """One point per trial with both measures present; ESTIMATED durations excluded (§11.5)."""
    points: list[Point] = []
    excluded: dict[str, str] = {}
    for matched in trials:
        trial = matched.trial
        x_result = _measure_value(trial, x)
        if isinstance(x_result, str):
            excluded[trial.nct_id] = x_result
            continue
        y_result = _measure_value(trial, y)
        if isinstance(y_result, str):
            excluded[trial.nct_id] = y_result
            continue
        points.append(_scatter_point(trial.nct_id, x_result, y_result, matched.match_evidence))
    return points, excluded


def _scatter_point(
    nct_id: str, x_val: _MeasureValue, y_val: _MeasureValue, match_evidence: tuple[Evidence, ...]
) -> Point:
    """Assemble one scatter point's citation from both measures' evidence (same record)."""
    date_type = x_val.date_type or y_val.date_type or "n/a"
    evidence = [*x_val.evidence, *y_val.evidence]
    primary, *extra = evidence
    citation = Citation(
        nct_id=nct_id,
        field=primary.field,
        excerpt=primary.excerpt,
        evidence=[*extra, *match_evidence],
    )
    return Point(nct_id, x_val.value, y_val.value, date_type, (citation,))
