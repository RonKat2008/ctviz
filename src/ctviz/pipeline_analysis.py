"""S5 analysis-kind dispatch, guard application and viz dispatch, split out of `pipeline.py`
(module budget).

`_aggregate` in `pipeline.py` dispatches `count_by`/`time_trend` inline (they return the same
`AggregateResult` shape everything downstream already understood); histogram/scatter/network
return three *different* shapes, so their dispatch and the small adapters that let the rest of
the pipeline (`_records_plotted`, `_data_coverage`, `meta.network_summary`, ...) treat all five
analysis kinds uniformly live here. The §10.6 guard application and the final deterministic
builder dispatch (which visualization function each `VizType` maps to) live here too, since both
consume the same `AnalysisResult` union this module already owns.
"""

from collections.abc import Callable

from ctviz.analysis.aggregate import AggregateResult, MatchedTrial
from ctviz.analysis.guards import GuardResult, apply_guards, graph_to_degree_bar_chart
from ctviz.analysis.key_facts import key_facts
from ctviz.analysis.network import Graph, build_graph
from ctviz.analysis.numeric import Point, histogram, scatter
from ctviz.analysis.prune import MAX_NODES_DEFAULT, prune
from ctviz.errors import PlanInvalidError
from ctviz.schemas.enums import AnalysisKind, Measure, VizType
from ctviz.schemas.plan import Analysis, QueryPlan
from ctviz.schemas.viz import Visualization
from ctviz.viz.builder import (
    build_bar_chart,
    build_grouped_bar,
    build_histogram,
    build_key_facts_table,
    build_metric,
    build_network,
    build_scatter,
    build_table,
    build_time_series,
)

ScatterResult = tuple[list[Point], dict[str, str]]
AnalysisResult = AggregateResult | ScatterResult | Graph


def _aggregate_histogram(trials: list[MatchedTrial], analysis: Analysis) -> AggregateResult:
    """`histogram` requires `measure_x`; dispatched out of `_aggregate` to keep it small."""
    if analysis.measure_x is None:
        raise PlanInvalidError(["histogram requires analysis.measure_x"])
    return histogram(trials, analysis.measure_x)


def _aggregate_scatter(trials: list[MatchedTrial], analysis: Analysis) -> ScatterResult:
    """`scatter` requires both measures; dispatched out of `_aggregate` to keep it small."""
    if analysis.measure_x is None or analysis.measure_y is None:
        raise PlanInvalidError(["scatter requires analysis.measure_x and analysis.measure_y"])
    return scatter(trials, analysis.measure_x, analysis.measure_y)


def _network_exclusions(trials: list[MatchedTrial], raw: Graph, pruned: Graph) -> dict[str, str]:
    """Item 9: every matched trial with no surviving graph element gets a reason, so
    `records_matched - excluded == records_plotted` (§11.6) also holds for network results."""
    matched_ids = {t.trial.nct_id for t in trials}
    witnessed_ids, kept_ids = plotted_ids(raw), plotted_ids(pruned)
    excluded = dict.fromkeys(matched_ids - witnessed_ids, "no_edge_pair")
    excluded.update(dict.fromkeys(witnessed_ids - kept_ids, "not_in_network_after_pruning"))
    return excluded


MIN_RETRY_EDGES = 2
RETRY_MIN_WEIGHT = 1


def _prune_with_retry(raw: Graph, max_nodes: int) -> Graph:
    """§10.6: a network with < 2 edges after the default pruning is re-pruned at min_weight=1
    before the guard ever considers falling back to a degree bar chart."""
    pruned = prune(raw, max_nodes=max_nodes)
    if len(pruned.edges) < MIN_RETRY_EDGES:
        pruned = prune(raw, max_nodes=max_nodes, min_weight=RETRY_MIN_WEIGHT)
    return pruned


def aggregate_network(
    trials: list[MatchedTrial], analysis: Analysis, include_collaborators: bool = False
) -> Graph:
    """`network` requires `network_type`; item 12: honors `analysis.top_n` (node cap) and
    `include_collaborators` (sponsor_drug only -- `build_graph` ignores it for other types)."""
    if analysis.network_type is None:
        raise PlanInvalidError(["network requires analysis.network_type"])
    raw = build_graph(trials, analysis.network_type, include_collaborators=include_collaborators)
    max_nodes = analysis.top_n or MAX_NODES_DEFAULT
    pruned = _prune_with_retry(raw, max_nodes)
    excluded = _network_exclusions(trials, raw, pruned)
    return Graph(pruned.nodes, pruned.edges, {**pruned.summary, "excluded": excluded})


AGGREGATORS: dict[AnalysisKind, Callable[[list[MatchedTrial], Analysis], AnalysisResult]] = {
    AnalysisKind.HISTOGRAM: _aggregate_histogram,
    AnalysisKind.SCATTER: _aggregate_scatter,
    AnalysisKind.TRIAL_LOOKUP: lambda trials, _analysis: key_facts(trials),
}


def measure_label(measure: Measure | None) -> str:
    """A measure's display label for an axis title, or '' when there is none to show."""
    return measure.value.replace("_", " ") if measure else ""


def plotted_ids(result: AnalysisResult) -> set[str]:
    """Distinct nct_ids actually plotted, regardless of which analysis kind produced `result`."""
    if isinstance(result, AggregateResult):
        return {c.nct_id for b in result.buckets for c in b.citations}
    if isinstance(result, Graph):
        return {c.nct_id for n in result.nodes for c in n.citations}
    points, _excluded = result
    return {pt.nct_id for pt in points}


def excluded_of(result: AnalysisResult) -> dict[str, str]:
    """The per-trial analysis-exclusion reasons, regardless of which analysis kind produced it."""
    if isinstance(result, AggregateResult):
        return result.excluded
    if isinstance(result, Graph):
        return dict(result.summary.get("excluded", {}))
    _points, excluded = result
    return excluded


def _excluded_by_reason(excluded: dict[str, str]) -> dict[str, int]:
    """Per-reason exclusion counts, e.g. `{"no_edge_pair": N, "not_in_network...": M}`."""
    counts: dict[str, int] = {}
    for reason in excluded.values():
        counts[reason] = counts.get(reason, 0) + 1
    return counts


def network_summary(results: dict[str, AnalysisResult]) -> dict[str, object] | None:
    """The single network's pruning report, or None when this response has no network.

    Item 1: the per-trial `excluded` map (one entry per dropped NCT id -- thousands for a large
    cohort) belongs only in `meta.data_coverage.excluded_trials` (via `excluded_of`), never here.
    This reports only aggregate `excluded_count` / `excluded_by_reason` counts.
    """
    for result in results.values():
        if isinstance(result, Graph):
            excluded = result.summary.get("excluded", {})
            summary = {k: v for k, v in result.summary.items() if k != "excluded"}
            summary["excluded_count"] = len(excluded)
            summary["excluded_by_reason"] = _excluded_by_reason(excluded)
            return summary
    return None


def _dimension_label(plan: QueryPlan) -> str:
    """The group-by dimension's display label, for the x-axis title of a categorical chart."""
    dimension = plan.analysis.group_by if plan.analysis else None
    return dimension.value if dimension else ""


def _require_single(results: dict[str, AnalysisResult], viz_type: str) -> AnalysisResult:
    """Unwrap the one cohort a single-cohort-only viz type (histogram/scatter/network) needs."""
    if len(results) != 1:
        raise PlanInvalidError([f"{viz_type} requires exactly one cohort, got {len(results)}"])
    [result] = results.values()
    return result


def _as_aggregate(result: AnalysisResult, viz_type: str) -> AggregateResult:
    """`result` narrowed to `AggregateResult`, or a readable plan error (item 13)."""
    if not isinstance(result, AggregateResult):
        raise PlanInvalidError([f"{viz_type} requires a categorical aggregate result"])
    return result


_BuildFn = Callable[[QueryPlan, dict[str, AnalysisResult], str], Visualization]


def _build_bar_chart_viz(
    plan: QueryPlan, results: dict[str, AnalysisResult], title: str
) -> Visualization:
    """The bar_chart branch of `_build`, split out to keep `_build` under the complexity cap."""
    result = _as_aggregate(_require_single(results, "bar_chart"), "bar_chart")
    return build_bar_chart(title, _dimension_label(plan), result)


def _require_analysis(plan: QueryPlan) -> Analysis:
    """`plan.analysis`, or a readable error instead of an attribute error on `None`."""
    if plan.analysis is None:
        raise PlanInvalidError(["plan.analysis is required to build this chart"])
    return plan.analysis


def _build_histogram_viz(
    plan: QueryPlan, results: dict[str, AnalysisResult], title: str
) -> Visualization:
    """The histogram branch of `_build`, split out to keep `_build` under the complexity cap."""
    result = _as_aggregate(_require_single(results, "histogram"), "histogram")
    return build_histogram(title, measure_label(_require_analysis(plan).measure_x), result)


def _build_scatter_viz(
    plan: QueryPlan, results: dict[str, AnalysisResult], title: str
) -> Visualization:
    """The scatter_plot branch of `_build`, split out to keep `_build` under the complexity cap."""
    points, _excluded = _require_single(results, "scatter_plot")  # type: ignore[misc]
    analysis = _require_analysis(plan)
    if analysis.measure_x is None or analysis.measure_y is None:
        raise PlanInvalidError(["scatter_plot requires analysis.measure_x and analysis.measure_y"])
    return build_scatter(title, analysis.measure_x, analysis.measure_y, points)


def _build_network_viz(
    plan: QueryPlan, results: dict[str, AnalysisResult], title: str
) -> Visualization:
    """The network_graph branch of `_build`, split out to keep `_build` under the complexity cap."""
    result = _require_single(results, "network_graph")
    if not isinstance(result, Graph):
        raise PlanInvalidError(["network_graph requires a network graph result"])
    network_type = _require_analysis(plan).network_type
    if network_type is None:
        raise PlanInvalidError(["network_graph requires analysis.network_type"])
    return build_network(title, result, network_type)


def _build_metric_viz(
    plan: QueryPlan, results: dict[str, AnalysisResult], title: str
) -> Visualization:
    """The metric branch of `_build`, split out to keep `_build` under the complexity cap."""
    result = _as_aggregate(_require_single(results, "metric"), "metric")
    return build_metric(title, result)


def _build_table_viz(
    plan: QueryPlan, results: dict[str, AnalysisResult], title: str
) -> Visualization:
    """The table branch of `_build`, split out to keep `_build` under the complexity cap."""
    result = _as_aggregate(_require_single(results, "table"), "table")
    if plan.analysis is not None and plan.analysis.kind is AnalysisKind.TRIAL_LOOKUP:
        return build_key_facts_table(title, result)
    return build_table(title, _dimension_label(plan), result)


_VIZ_BUILDERS: dict[VizType, _BuildFn] = {
    VizType.TIME_SERIES: lambda _plan, r, t: build_time_series(t, r),  # type: ignore[arg-type]
    VizType.BAR_CHART: _build_bar_chart_viz,
    VizType.GROUPED_BAR_CHART: (
        lambda plan, r, t: build_grouped_bar(t, _dimension_label(plan), r)  # type: ignore[arg-type]
    ),
    VizType.HISTOGRAM: _build_histogram_viz,
    VizType.SCATTER_PLOT: _build_scatter_viz,
    VizType.NETWORK_GRAPH: _build_network_viz,
    VizType.METRIC: _build_metric_viz,
    VizType.TABLE: _build_table_viz,
}


def _build(plan: QueryPlan, results: dict[str, AnalysisResult], title: str) -> Visualization:
    """Dispatch to the deterministic builder the plan's visualization type selects."""
    viz = plan.visualization
    if viz is None:
        raise PlanInvalidError(["plan.visualization is required to build a chart"])
    builder = _VIZ_BUILDERS.get(viz.type)
    if builder is None:
        raise PlanInvalidError([f"visualization.type={viz.type} is not yet supported (S5 slice)"])
    return builder(plan, results, title)


def _guard_input(results: dict[str, AnalysisResult]) -> GuardResult:
    """Item 3: what `apply_guards` inspects -- the single result for a single-cohort viz type,
    or the AggregateResult-only comparison dict for a multi-cohort bar_chart/time_series."""
    if len(results) == 1:
        [only] = results.values()
        return only  # type: ignore[return-value]  # a ScatterResult: no §10.6 rule governs it
    return {label: r for label, r in results.items() if isinstance(r, AggregateResult)}


def _rebuild_plan_for_viz(plan: QueryPlan, viz_type: VizType) -> QueryPlan:
    """Item 3: `apply_guards` decides the final type; rebuild the plan via `model_copy` so
    `_build` and `meta.plan` reflect what was actually delivered, not what was requested."""
    if plan.visualization is None or plan.visualization.type is viz_type:
        return plan
    visualization = plan.visualization.model_copy(update={"type": viz_type})
    return plan.model_copy(update={"visualization": visualization})


def apply_viz_guards(
    plan: QueryPlan, results: dict[str, AnalysisResult]
) -> tuple[QueryPlan, dict[str, AnalysisResult], list[str]]:
    """Item 3: run the §10.6 guards, swap in a degree bar chart for a downgraded network, and
    rebuild the plan for whichever visualization type is actually delivered."""
    viz_type, notes = apply_guards(plan, _guard_input(results))
    if viz_type is VizType.BAR_CHART:
        results = {
            label: graph_to_degree_bar_chart(r) if isinstance(r, Graph) else r
            for label, r in results.items()
        }
    return _rebuild_plan_for_viz(plan, viz_type), results, notes
