"""Deterministic visualization-shape adjustments, in place of a second LLM call (PLAN.md §10.6)."""

from ctviz.analysis.aggregate import AggregateResult, Bucket
from ctviz.analysis.network import Graph, GraphNode
from ctviz.schemas.enums import VizType
from ctviz.schemas.plan import QueryPlan

MIN_TIME_SERIES_BUCKETS = 3
MIN_HISTOGRAM_VALUES = 5
MIN_GRAPH_EDGES = 2

GuardResult = AggregateResult | dict[str, AggregateResult] | Graph


def _total_trials(result: AggregateResult) -> int:
    """Trials actually plotted across every bucket (buckets may double-count multi-valued dims)."""
    return sum(b.trial_count for b in result.buckets)


def _bucket_count(result: AggregateResult | dict[str, AggregateResult]) -> int:
    """Item 3: the bucket count guards act on -- the single result's, or the smallest cohort's
    (a comparison is only "long enough" for time_series when every cohort is)."""
    if isinstance(result, AggregateResult):
        return len(result.buckets)
    return min((len(r.buckets) for r in result.values()), default=0)


def _values_count(result: AggregateResult | dict[str, AggregateResult]) -> int:
    """Item 3: the value count guards act on -- the single result's, or the largest cohort's."""
    if isinstance(result, AggregateResult):
        return _total_trials(result)
    return max((_total_trials(r) for r in result.values()), default=0)


def _single_category_key(result: AggregateResult | dict[str, AggregateResult]) -> str | None:
    """The one category name, if every cohort has exactly one (and they all agree); else None."""
    results = [result] if isinstance(result, AggregateResult) else list(result.values())
    if not results or any(len(r.buckets) != 1 for r in results):
        return None
    keys = {r.buckets[0].key for r in results}
    return keys.pop() if len(keys) == 1 else None


def _node_degree_bucket(node: GraphNode) -> Bucket:
    """One node's own (already-cited) membership, reused as a bar-chart row (§10.6)."""
    return Bucket(node.label, node.predicate, node.citations)


def graph_to_degree_bar_chart(graph: Graph) -> AggregateResult:
    """§10.6: a persistently sparse network (< 2 edges even at min_weight=1) becomes a bar chart
    of node degrees, largest first; every row keeps the node's own citations (still cited)."""
    ranked = sorted(graph.nodes, key=lambda n: (-n.weight, n.label))
    return AggregateResult(tuple(_node_degree_bucket(n) for n in ranked), {})


def _apply_graph_guard(plan: QueryPlan, graph: Graph) -> tuple[VizType, list[str]]:
    """§10.6: too few edges even after the min_weight=1 retry (done by the aggregator) becomes
    a bar chart of node degrees instead of a network graph."""
    requested = plan.visualization.type if plan.visualization else VizType.NETWORK_GRAPH
    if requested is not VizType.NETWORK_GRAPH or len(graph.edges) >= MIN_GRAPH_EDGES:
        return requested, []
    note = f"network_graph with {len(graph.edges)} edges (after min_weight=1 retry) -> bar_chart"
    return VizType.BAR_CHART, [note]


def apply_guards(plan: QueryPlan, result: GuardResult) -> tuple[VizType, list[str]]:
    """The final viz type plus adjustment notes for `meta.adjustments` (PLAN.md §10.6 table).

    `result` is the single cohort's result for histogram/scatter/network (single-cohort-only viz
    types), or a `{cohort_label: AggregateResult}` dict for a bar_chart/time_series comparison.
    """
    if isinstance(result, Graph):
        return _apply_graph_guard(plan, result)
    requested = plan.visualization.type if plan.visualization else VizType.BAR_CHART
    if not isinstance(result, AggregateResult | dict):
        return requested, []  # a scatter result: no §10.6 rule governs it
    bucket_count = _bucket_count(result)
    if requested is VizType.TIME_SERIES and bucket_count < MIN_TIME_SERIES_BUCKETS:
        note = f"time_series with {bucket_count} time buckets -> bar_chart"
        return VizType.BAR_CHART, [note]
    single_category = _single_category_key(result)
    if requested is VizType.BAR_CHART and single_category is not None:
        note = f"bar_chart with 1 category ({single_category}) -> metric"
        return VizType.METRIC, [note]
    if requested is VizType.HISTOGRAM and _values_count(result) < MIN_HISTOGRAM_VALUES:
        note = f"histogram with {_values_count(result)} values -> table"
        return VizType.TABLE, [note]
    return requested, []
