"""Aggregates → visualization models: rows named by encoding, always carrying citations."""

from ctviz.analysis.aggregate import AggregateResult, Bucket
from ctviz.analysis.network import Graph, GraphEdge, GraphNode
from ctviz.analysis.numeric import Point
from ctviz.schemas.citations import Citation
from ctviz.schemas.enums import Measure, NetworkType
from ctviz.viz.builder import (
    build_bar_chart,
    build_grouped_bar,
    build_histogram,
    build_metric,
    build_network,
    build_scatter,
    build_table,
    build_time_series,
)


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


def test_histogram_rows_carry_bin_bounds_from_the_in_range_predicate() -> None:
    (cite,) = _cites(1)
    predicate = {"op": "in_range", "path": "/x", "value": [100, 200]}
    result = AggregateResult((Bucket("100–199", predicate, (cite,)),), {})  # noqa: RUF001

    viz = build_histogram("Enrollment", "enrollment", result)

    assert viz.type == "histogram"
    assert viz.data[0]["bin_start"] == 100
    assert viz.data[0]["bin_end"] == 200
    assert viz.data[0]["bin_label"] == "100–199"  # noqa: RUF001
    assert viz.data[0]["trial_count"] == 1


def test_histogram_open_last_bin_has_none_bin_end() -> None:
    predicate = {"op": "in_range", "path": "/x", "value": [5000, None]}
    result = AggregateResult((Bucket("≥5000", predicate, ()),), {})

    viz = build_histogram("Enrollment", "enrollment", result)

    assert viz.data[0]["bin_end"] is None


def test_scatter_rows_use_measure_names_as_field_keys_with_units() -> None:
    """Item 2 (§12.4): rows and encoding are keyed by the measure name, not generic x/y, with
    channel titles and units (months / participants) attached."""
    (cite,) = _cites(1)
    points = [Point("NCT00000001", 12.0, 100.0, "actual", (cite,))]

    viz = build_scatter(
        "Enrollment vs duration", Measure.DURATION_MONTHS, Measure.ENROLLMENT, points
    )

    assert viz.type == "scatter_plot"
    assert viz.data[0] == {
        "nct_id": "NCT00000001",
        "duration_months": 12.0,
        "enrollment": 100.0,
        "date_type": "actual",
        "citations": [cite],
    }
    assert viz.encoding["x"].field == "duration_months"
    assert viz.encoding["y"].field == "enrollment"
    assert viz.encoding["x"].unit == "months"
    assert viz.encoding["y"].unit == "participants"


def test_metric_is_always_a_data_array() -> None:
    (cite,) = _cites(1)
    result = AggregateResult((Bucket("Phase 3", {}, (cite,)),), {})

    viz = build_metric("Phase 3 trial count", result)

    assert viz.type == "metric"
    assert isinstance(viz.data, list) and viz.data[0]["trial_count"] == 1


def test_table_rows_are_one_per_bucket() -> None:
    (cite,) = _cites(1)
    result = AggregateResult((Bucket("Phase 3", {}, (cite,)),), {})

    viz = build_table("Values", "phase", result)

    assert viz.type == "table"
    assert viz.data[0]["category"] == "Phase 3"


def _graph_citation(nct_id: str) -> Citation:
    return Citation(nct_id=nct_id, field="/protocolSection/x", excerpt="v", evidence=[])


def test_build_network_produces_directed_sponsor_drug_graph_with_referentially_sound_edges() -> (
    None
):
    node_a = GraphNode("sponsor:merck", "Merck", "sponsor", {}, (_graph_citation("NCT00000001"),))
    node_b = GraphNode("drug:x", "X", "drug", {}, (_graph_citation("NCT00000001"),))
    edge = GraphEdge(
        "sponsor:merck::drug:x",
        "sponsor:merck",
        "drug:x",
        "sponsor_drug",
        {},
        (_graph_citation("NCT00000001"),),
    )
    graph = Graph((node_a, node_b), (edge,), {"nodes_before_pruning": 2, "edges_before_pruning": 1})

    viz = build_network("Sponsors and drugs", graph, NetworkType.SPONSOR_DRUG)

    assert viz.type == "network_graph"
    assert viz.data.directed is True
    assert {n.id for n in viz.data.nodes} == {"sponsor:merck", "drug:x"}
    assert viz.data.edges[0].weight == 1


def test_build_network_options_carry_counts_not_the_per_trial_exclusion_map() -> None:
    node = GraphNode("drug:x", "X", "drug", {}, (_graph_citation("NCT00000001"),))
    excluded = {f"NCT{i:08d}": "no_edge_pair" for i in range(2, 5)}
    summary = {"nodes_before_pruning": 1, "edges_before_pruning": 0, "excluded": excluded}
    graph = Graph((node,), (), summary)

    viz = build_network("Drugs", graph, NetworkType.DRUG_DRUG)

    assert "excluded" not in viz.options
    assert viz.options["excluded_count"] == 3
    assert "NCT00000002" not in str(viz.options)
