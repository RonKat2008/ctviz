"""Deterministic visualization-shape guards, in place of a second LLM call (§10.6)."""

from ctviz.analysis.aggregate import AggregateResult, Bucket
from ctviz.analysis.guards import apply_guards, graph_to_degree_bar_chart
from ctviz.analysis.network import Graph, GraphEdge, GraphNode
from ctviz.schemas.citations import Citation
from ctviz.schemas.enums import VizType
from tests.factories import make_plan


def test_single_bucket_bar_becomes_metric() -> None:
    viz_type, notes = apply_guards(make_plan(), AggregateResult((Bucket("Phase 3", {}, ()),), {}))

    assert viz_type is VizType.METRIC and "1 category" in notes[0]


def test_short_time_series_becomes_bar_chart() -> None:
    plan = make_plan(visualization={"type": "time_series", "title": "t", "rationale": "r"})
    result = AggregateResult((Bucket("2025", {}, ()), Bucket("2026", {}, ())), {})

    viz_type, _ = apply_guards(plan, result)

    assert viz_type is VizType.BAR_CHART


def test_long_enough_time_series_is_kept() -> None:
    plan = make_plan(visualization={"type": "time_series", "title": "t", "rationale": "r"})
    result = AggregateResult(
        (Bucket("2024", {}, ()), Bucket("2025", {}, ()), Bucket("2026", {}, ())), {}
    )

    viz_type, notes = apply_guards(plan, result)

    assert viz_type is VizType.TIME_SERIES and notes == []


def test_multi_bucket_bar_chart_is_kept() -> None:
    result = AggregateResult((Bucket("Phase 2", {}, ()), Bucket("Phase 3", {}, ())), {})

    viz_type, notes = apply_guards(make_plan(), result)

    assert viz_type is VizType.BAR_CHART and notes == []


def test_degenerate_histogram_becomes_table() -> None:
    plan = make_plan(visualization={"type": "histogram", "title": "t", "rationale": "r"})
    result = AggregateResult((Bucket("0-9", {}, ("c1", "c2")), Bucket("10-19", {}, ("c3",))), {})

    viz_type, notes = apply_guards(plan, result)

    assert viz_type is VizType.TABLE and "3 values" in notes[0]


def test_histogram_with_enough_values_is_kept() -> None:
    plan = make_plan(visualization={"type": "histogram", "title": "t", "rationale": "r"})
    citations = tuple(f"c{i}" for i in range(5))
    result = AggregateResult((Bucket("0-9", {}, citations),), {})

    viz_type, notes = apply_guards(plan, result)

    assert viz_type is VizType.HISTOGRAM and notes == []


def test_multi_cohort_bar_chart_becomes_metric_when_every_cohort_agrees_on_one_category() -> None:
    """Item 3: a comparison is a single "1 category" bar_chart only when EVERY cohort's single
    bucket agrees on the key -- not just when one cohort happens to have one bucket."""
    results = {
        "a": AggregateResult((Bucket("Phase 3", {}, ()),), {}),
        "b": AggregateResult((Bucket("Phase 3", {}, ()),), {}),
    }

    viz_type, notes = apply_guards(make_plan(), results)

    assert viz_type is VizType.METRIC and "Phase 3" in notes[0]


def test_multi_cohort_bar_chart_with_different_single_categories_is_kept() -> None:
    results = {
        "a": AggregateResult((Bucket("Phase 2", {}, ()),), {}),
        "b": AggregateResult((Bucket("Phase 3", {}, ()),), {}),
    }

    viz_type, notes = apply_guards(make_plan(), results)

    assert viz_type is VizType.BAR_CHART and notes == []


def test_multi_cohort_time_series_needs_every_cohort_long_enough() -> None:
    """Item 3: the shortest cohort decides -- one cohort with < 3 years downgrades the whole
    comparison to bar_chart, since a per-cohort mixed shape isn't a valid time_series."""
    plan = make_plan(visualization={"type": "time_series", "title": "t", "rationale": "r"})
    long_cohort = AggregateResult(
        (Bucket("2024", {}, ()), Bucket("2025", {}, ()), Bucket("2026", {}, ())), {}
    )
    short_cohort = AggregateResult((Bucket("2025", {}, ()), Bucket("2026", {}, ())), {})

    viz_type, notes = apply_guards(plan, {"a": long_cohort, "b": short_cohort})

    assert viz_type is VizType.BAR_CHART and "2 time buckets" in notes[0]


def _node(node_id: str, weight_citations: int) -> GraphNode:
    citations = tuple(
        Citation(nct_id=f"NCT{i:08d}", field="/x", excerpt="x", evidence=[])
        for i in range(weight_citations)
    )
    return GraphNode(node_id, node_id, "drug", {}, citations)


def test_sparse_network_below_two_edges_becomes_a_bar_chart_of_node_degrees() -> None:
    """§10.6: a network with < 2 edges (already retried at min_weight=1 by the aggregator, so
    the guard only sees the final count) becomes a bar chart of node degrees."""
    plan = make_plan(visualization={"type": "network_graph", "title": "t", "rationale": "r"})
    a, b = _node("drug:a", 3), _node("drug:b", 1)
    edge = GraphEdge("drug:a::drug:b", "drug:a", "drug:b", "drug_drug", {}, a.citations[:1])
    graph = Graph((a, b), (edge,), {})

    viz_type, notes = apply_guards(plan, graph)

    assert viz_type is VizType.BAR_CHART and "1 edges" in notes[0]
    bar = graph_to_degree_bar_chart(graph)
    assert [b.key for b in bar.buckets] == ["drug:a", "drug:b"]
    assert [b.trial_count for b in bar.buckets] == [3, 1]


def test_network_with_enough_edges_is_kept() -> None:
    plan = make_plan(visualization={"type": "network_graph", "title": "t", "rationale": "r"})
    a, b, c = _node("drug:a", 2), _node("drug:b", 2), _node("drug:c", 1)
    edges = (
        GraphEdge("drug:a::drug:b", "drug:a", "drug:b", "drug_drug", {}, a.citations),
        GraphEdge("drug:a::drug:c", "drug:a", "drug:c", "drug_drug", {}, c.citations),
    )
    graph = Graph((a, b, c), edges, {})

    viz_type, notes = apply_guards(plan, graph)

    assert viz_type is VizType.NETWORK_GRAPH and notes == []
