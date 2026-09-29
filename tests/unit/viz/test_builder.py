"""Aggregates → visualization models: rows named by encoding, always carrying citations."""

from ctviz.analysis.aggregate import AggregateResult, Bucket
from ctviz.schemas.citations import Citation
from ctviz.viz.builder import build_bar_chart, build_grouped_bar, build_time_series


def _cite(nct_id: str) -> Citation:
    """A citation for a distinct synthetic trial, e.g. 'NCT00000001'."""
    return Citation(
        nct_id=nct_id,
        field="/protocolSection/designModule/phases/0",
        excerpt="PHASE3",
        evidence=[],
    )


def _cites(*n: int) -> tuple[Citation, ...]:
    """Citations for distinct synthetic trials NCT00000001, NCT00000002, ... (given indices)."""
    return tuple(_cite(f"NCT{i:08d}") for i in n)


def test_bar_chart_rows_are_named_by_encoding_and_carry_citations() -> None:
    (cite,) = _cites(1)
    result = AggregateResult((Bucket("Phase 3", {"op": "x"}, (cite,)),), {})

    viz = build_bar_chart("Pembrolizumab trials by phase", "Phase", result)

    assert viz.encoding["x"].field == "category" and viz.encoding["y"].field == "trial_count"
    assert viz.data[0] == {
        "category": "Phase 3",
        "trial_count": 1,
        "flags": [],
        "predicate": {"op": "x"},
        "citations": [cite],
        "other_categories": 0,
    }


def test_bar_chart_row_carries_folded_category_count_for_the_other_bucket() -> None:
    (cite,) = _cites(1)
    result = AggregateResult((Bucket("Other", {}, (cite,), folded_categories=4),), {})

    viz = build_bar_chart("dim", "dim", result)

    assert viz.data[0]["other_categories"] == 4


def test_grouped_bar_uses_share_when_cohorts_differ_more_than_twofold() -> None:
    big = AggregateResult((Bucket("INDUSTRY", {}, _cites(1, 2, 3, 4, 5, 6)),), {})
    small = AggregateResult((Bucket("INDUSTRY", {}, _cites(7, 8)),), {})

    viz = build_grouped_bar(
        "Sponsor class", "Sponsor class", {"Lung cancer": big, "Melanoma": small}
    )

    assert viz.encoding["y"].field == "share"
    assert {r["cohort"] for r in viz.data} == {"Lung cancer", "Melanoma"}


def test_grouped_bar_cohort_size_counts_distinct_trials_not_citations() -> None:
    """A trial cited in two buckets (a multi-valued dimension) must count once for cohort size."""
    shared, only_in_b = _cites(1, 2)
    multi = AggregateResult(
        (
            Bucket("A", {}, (shared,)),
            Bucket("B", {}, (shared, only_in_b)),
        ),
        {},
    )
    small = AggregateResult((Bucket("A", {}, _cites(10, 11, 12, 13, 14)),), {})

    viz = build_grouped_bar("dim", "dim", {"Multi": multi, "Small": small})

    multi_rows = {r["category"]: r["share"] for r in viz.data if r["cohort"] == "Multi"}
    assert multi_rows == {"A": 0.5, "B": 1.0}


def test_share_boundary_exactly_two_x_uses_trial_count_not_share() -> None:
    big = AggregateResult((Bucket("A", {}, _cites(1, 2, 3, 4)),), {})
    small = AggregateResult((Bucket("A", {}, _cites(10, 11)),), {})

    viz = build_grouped_bar("d", "d", {"Big": big, "Small": small})

    assert viz.encoding["y"].field == "trial_count"
    assert viz.options["normalize"] == "count"


def test_share_boundary_greater_than_two_x_uses_share() -> None:
    big = AggregateResult((Bucket("A", {}, _cites(1, 2, 3, 4, 5)),), {})
    small = AggregateResult((Bucket("A", {}, _cites(10, 11)),), {})

    viz = build_grouped_bar("d", "d", {"Big": big, "Small": small})

    assert viz.encoding["y"].field == "share"


def test_share_value_is_trial_count_over_cohort_size_rounded_to_4dp() -> None:
    big = AggregateResult(
        (Bucket("A", {}, _cites(1)), Bucket("B", {}, _cites(2, 3, 4, 5, 6, 7))), {}
    )
    small = AggregateResult((Bucket("A", {}, _cites(20, 21)),), {})

    viz = build_grouped_bar("d", "d", {"Big": big, "Small": small})

    row_a = next(r for r in viz.data if r["cohort"] == "Big" and r["category"] == "A")
    assert row_a["share"] == 0.1429


def test_time_series_encoding_and_rows_and_color_for_multiple_cohorts() -> None:
    result_a = AggregateResult((Bucket("2024", {}, _cites(1), flags=("partial_period",)),), {})
    result_b = AggregateResult((Bucket("2024", {}, _cites(2)),), {})

    viz = build_time_series("trend", {"A": result_a, "B": result_b})

    assert (viz.encoding["x"].field, viz.encoding["x"].type) == ("year", "temporal")
    assert viz.encoding["x"].time_unit == "year"
    assert viz.encoding["y"].type == "quantitative"
    assert "color" in viz.encoding
    row_a = next(r for r in viz.data if r["cohort"] == "A")
    assert (row_a["year"], row_a["flags"]) == ("2024", ["partial_period"])


def test_time_series_omits_color_channel_for_a_single_cohort() -> None:
    result = AggregateResult((Bucket("2024", {}, _cites(1)),), {})

    viz = build_time_series("trend", {"Only": result})

    assert "color" not in viz.encoding
