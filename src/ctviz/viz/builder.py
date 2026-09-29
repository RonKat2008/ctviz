"""Aggregates → visualization models. Deterministic; the only text from the LLM is the title."""

from ctviz.analysis.aggregate import AggregateResult, Bucket
from ctviz.analysis.key_facts import KEY_FACT_COLUMNS, LIST_FACTS, column_of
from ctviz.analysis.network import Graph
from ctviz.analysis.numeric import Point
from ctviz.schemas.enums import Measure, NetworkType
from ctviz.schemas.viz import (
    BarChart,
    Channel,
    GroupedBarChart,
    Histogram,
    Metric,
    NetworkData,
    NetworkEdge,
    NetworkGraph,
    NetworkNode,
    Row,
    ScatterPlot,
    Table,
    TimeSeries,
)

SHARE_RATIO_THRESHOLD = 2.0
MEASURE_TITLES: dict[Measure, str] = {
    Measure.DURATION_MONTHS: "Duration",
    Measure.ENROLLMENT: "Enrollment",
    Measure.SITE_COUNT: "Site count",
}
MEASURE_UNITS: dict[Measure, str] = {
    Measure.DURATION_MONTHS: "months",
    Measure.ENROLLMENT: "participants",
}


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


def build_histogram(title: str, measure_label: str, result: AggregateResult) -> Histogram:
    """One row per bin; `bin_start`/`bin_end` come from each bucket's `in_range` predicate."""
    encoding = {
        "x": Channel(field="bin_start", type="quantitative", title=measure_label, bin=True),
        "x2": Channel(field="bin_end", type="quantitative"),
        "y": Channel(field="trial_count", type="quantitative", title="Trials", unit="trials"),
        "label": Channel(field="bin_label", type="nominal"),
    }
    data = [
        {
            **_row(b, bin_label=b.key),
            "bin_start": b.predicate["value"][0],
            "bin_end": b.predicate["value"][1],
        }
        for b in result.buckets
    ]
    return Histogram(type="histogram", title=title, encoding=encoding, data=data)


def _measure_channel(measure: Measure) -> Channel:
    """A quantitative channel named and titled after `measure`, with its display unit (§12.4)."""
    return Channel(
        field=measure.value,
        type="quantitative",
        title=MEASURE_TITLES.get(measure, measure.value.replace("_", " ").title()),
        unit=MEASURE_UNITS.get(measure),  # type: ignore[arg-type]
    )


def build_scatter(
    title: str, x_measure: Measure, y_measure: Measure, points: list[Point]
) -> ScatterPlot:
    """One row per trial: its two measures keyed by name, the evidence date type, and its own
    citation. Item 2 (§12.4): row keys and encoding fields are the measure names (e.g.
    `duration_months`, `enrollment`), not generic `x`/`y`."""
    encoding = {
        "x": _measure_channel(x_measure),
        "y": _measure_channel(y_measure),
    }
    data = [
        {
            "nct_id": pt.nct_id,
            x_measure.value: pt.x,
            y_measure.value: pt.y,
            "date_type": pt.date_type,
            "citations": list(pt.citations),
        }
        for pt in points
    ]
    return ScatterPlot(type="scatter_plot", title=title, encoding=encoding, data=data)


def build_metric(title: str, result: AggregateResult) -> Metric:
    """One or more headline numbers; always an array, even for a single bucket (§12.4)."""
    encoding = {
        "value": Channel(field="trial_count", type="quantitative", title="Trials", unit="trials")
    }
    data = [_row(b, label=b.key) for b in result.buckets]
    return Metric(type="metric", title=title, encoding=encoding, data=data)


def build_table(title: str, dimension_label: str, result: AggregateResult) -> Table:
    """A row-per-bucket listing, for degenerate charts a guard has downgraded (§10.6)."""
    encoding = {
        "category": Channel(field="category", type="nominal", title=dimension_label),
        "trial_count": Channel(
            field="trial_count", type="quantitative", title="Trials", unit="trials"
        ),
    }
    data = [_row(b, category=b.key) for b in result.buckets]
    return Table(type="table", title=title, encoding=encoding, data=data)


_LIST_COLUMNS = frozenset(column for column, _, _ in LIST_FACTS)


def _key_facts_row(bucket: Bucket) -> Row:
    """One trial's row: each cell's cited text (lists for list facts) plus its pointers."""
    [citation] = bucket.citations
    values: dict[str, list[str]] = {}
    fields: dict[str, list[str]] = {}
    for e in citation.evidence:
        column = column_of(e.field)
        if column is not None:
            values.setdefault(column, []).append(e.excerpt)
            fields.setdefault(column, []).append(e.field)
    cells = {c: v if c in _LIST_COLUMNS else v[0] for c, v in values.items()}
    return {**_row(bucket, nct_id=bucket.key), **cells, "cell_fields": fields}


def build_key_facts_table(title: str, result: AggregateResult) -> Table:
    """§8.4: one row of key facts per trial; every cell's text is a cited raw excerpt."""
    encoding = {"nct_id": Channel(field="nct_id", type="nominal", title="NCT ID")}
    for column in KEY_FACT_COLUMNS:
        title_text = column.replace("_", " ").capitalize()
        encoding[column] = Channel(
            field=column,
            type="nominal",
            title=title_text,
            unit="participants" if column == "enrollment" else None,
        )
    data = [_key_facts_row(b) for b in result.buckets]
    return Table(type="table", title=title, encoding=encoding, data=data)


def build_network(title: str, graph: Graph, network_type: NetworkType) -> NetworkGraph:
    """The pruned node/edge graph as a `NetworkGraph` visualization (§12.5)."""
    nodes = [
        NetworkNode(
            id=n.id,
            label=n.label,
            type=n.type,
            weight=n.weight,
            predicate=n.predicate,
            citations=list(n.citations),
        )
        for n in graph.nodes
    ]
    edges = [
        NetworkEdge(
            id=e.id,
            source=e.source,
            target=e.target,
            type=e.type,
            weight=e.weight,
            predicate=e.predicate,
            citations=list(e.citations),
            flags=list(e.flags),
        )
        for e in graph.edges
    ]
    data = NetworkData(directed=network_type is NetworkType.SPONSOR_DRUG, nodes=nodes, edges=edges)
    encoding = {
        "nodes": Channel(field="nodes", type="nominal"),
        "edges": Channel(field="edges", type="nominal"),
    }
    return NetworkGraph(
        type="network_graph",
        title=title,
        encoding=encoding,
        data=data,
        options=_network_options(graph),
    )


def _network_options(graph: Graph) -> dict[str, object]:
    """Pruning counts for the chart; the per-trial exclusion map lives only in data_coverage."""
    excluded = graph.summary.get("excluded", {})
    options = {k: v for k, v in graph.summary.items() if k != "excluded"}
    options["excluded_count"] = len(excluded) if isinstance(excluded, dict) else 0
    return options
