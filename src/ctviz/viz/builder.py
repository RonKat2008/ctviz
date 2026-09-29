"""Aggregates → visualization models. Deterministic; the only text from the LLM is the title."""

from ctviz.analysis.aggregate import AggregateResult, Bucket
from ctviz.schemas.viz import BarChart, Channel, GroupedBarChart, Row, TimeSeries

SHARE_RATIO_THRESHOLD = 2.0


def _row(bucket: Bucket, **keys: object) -> Row:
    """One tidy row: the encoding keys plus count, flags, predicate, citations and fold count."""
    return {
        **keys,
        "trial_count": bucket.trial_count,
        "flags": list(bucket.flags),
        "predicate": bucket.predicate,
        "citations": list(bucket.citations),
        "other_categories": bucket.folded_categories,
    }


def _cohort_size(result: AggregateResult) -> int:
    """Distinct trials plotted in the cohort, used to normalize cross-cohort comparisons.

    Deduped by nct_id rather than summed: a multi-valued dimension can place one trial in
    more than one bucket, and summing bucket.trial_count would double-count it there.
    """
    return len({c.nct_id for b in result.buckets for c in b.citations})


def build_bar_chart(title: str, dimension_label: str, result: AggregateResult) -> BarChart:
    """One bar per bucket, in the aggregator's display order."""
    return BarChart(
        type="bar_chart",
        title=title,
        encoding={
            "x": Channel(field="category", type="nominal", title=dimension_label),
            "y": Channel(field="trial_count", type="quantitative", title="Trials", unit="trials"),
        },
        data=[_row(b, category=b.key) for b in result.buckets],
    )


def build_time_series(title: str, cohorts: dict[str, AggregateResult]) -> TimeSeries:
    """One point per (cohort, year); multi-line when there is more than one cohort."""
    encoding = {
        "x": Channel(field="year", type="temporal", title="Start year", time_unit="year"),
        "y": Channel(
            field="trial_count", type="quantitative", title="Trials started", unit="trials"
        ),
    }
    if len(cohorts) > 1:
        encoding["color"] = Channel(field="cohort", type="nominal", title="Cohort")
    data = [_row(b, year=b.key, cohort=label) for label, r in cohorts.items() for b in r.buckets]
    return TimeSeries(
        type="time_series", title=title, encoding=encoding, data=data, options={"mark": "line"}
    )


def build_grouped_bar(
    title: str, dimension_label: str, cohorts: dict[str, AggregateResult]
) -> GroupedBarChart:
    """Tidy rows per (category, cohort); share of cohort when cohort sizes differ > 2x."""
    sizes = {label: max(_cohort_size(r), 1) for label, r in cohorts.items()}
    use_share = max(sizes.values()) / min(sizes.values()) > SHARE_RATIO_THRESHOLD
    data = [
        {**_row(b, category=b.key, cohort=label), "share": round(b.trial_count / sizes[label], 4)}
        for label, r in cohorts.items()
        for b in r.buckets
    ]
    y = (
        Channel(
            field="share", type="quantitative", title="Share of cohort", unit="share", format=".0%"
        )
        if use_share
        else Channel(field="trial_count", type="quantitative", title="Trials", unit="trials")
    )
    return GroupedBarChart(
        type="grouped_bar_chart",
        title=title,
        data=data,
        encoding={
            "x": Channel(field="category", type="nominal", title=dimension_label),
            "y": y,
            "color": Channel(field="cohort", type="nominal", title="Cohort"),
        },
        options={"stacked": False, "normalize": "share" if use_share else "count"},
    )
