"""The verifier's inputs, flattened (§11.6): every visualized datum as one chart-shape-agnostic
`Datum`, each cohort's independently supplied `CohortPopulation`, and the stable violation tags.

Imports only `schemas` -- never `ctviz.analysis` (see `verify.py`'s independence rule)."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from ctviz.schemas.citations import Citation, Predicate
from ctviz.schemas.viz import NetworkData, NetworkGraph, Visualization

# Stable violation prefixes: every violation string starts with one of these plus ": ".
POINTER = "pointer"
SOUNDNESS = "soundness"
RELEVANCE = "relevance"
COMPLETENESS = "completeness"
STRUCTURE = "structure"
COUNT = "count"
PREDICATE = "predicate"
DISPLAY = "display"
INTERNAL = "internal"

DatumKind = Literal["row", "node", "edge"]


def violation(tag: str, message: str) -> str:
    """One violation line, `"<tag>: <message>"`, so callers can assert on the failing check."""
    return f"{tag}: {message}"


@dataclass(frozen=True)
class CohortPopulation:
    """What the PIPELINE kept for one cohort (post strict-match, post filter re-check) and which
    of those it declared excluded at the analysis stage -- supplied explicitly, never derived
    from the response's own citations (that would make the recount circular)."""

    kept_ids: frozenset[str]
    excluded: Mapping[str, str] = field(default_factory=dict)

    @property
    def plotted_ids(self) -> frozenset[str]:
        """The records the chart must account for: kept minus declared exclusions."""
        return self.kept_ids - frozenset(self.excluded)


@dataclass(frozen=True)
class Datum:
    """One visualized datum (bar/bin/time bucket/point/node/edge), normalized for the checks.

    `count` is the datum's own stated number (`trial_count` / `weight`), or None when the row
    shape carries none (a scatter point); `endpoints` is set only for edges. `row` is the raw
    tabular row this datum came from (row kind only) -- kept so `check_display` can compare its
    own displayed cell values (top-level keys named by `cell_fields`) against the excerpt each
    cell cites; `None` for node/edge data, which carry no such cells."""

    label: str
    kind: DatumKind
    predicate: Predicate | None
    citations: tuple[Citation, ...]
    count: int | None
    cohort_label: str | None
    node_id: str | None = None
    endpoints: tuple[str, str] | None = None
    row: Mapping[str, Any] | None = None


def _as_citation(value: Citation | Mapping[str, Any]) -> Citation:
    """A row's citation: already a `Citation` live, or a plain dict after a JSON round-trip
    (`Row = dict[str, Any]` carries no per-key schema for `model_validate_json` to use)."""
    return value if isinstance(value, Citation) else Citation.model_validate(value)


def _row_datum(row: Mapping[str, Any], index: int, kind: str) -> Datum:
    """One tabular row (bar/bin/time bucket/metric/table cell/scatter point) as a `Datum`."""
    name = row.get("category", row.get("year", row.get("label", row.get("nct_id", ""))))
    count = row.get("trial_count")
    return Datum(
        label=f"{kind}[{index}] {name}",
        kind="row",
        predicate=row.get("predicate"),
        citations=tuple(_as_citation(c) for c in row.get("citations", ())),
        count=int(count) if count is not None else None,
        cohort_label=row.get("cohort"),
        row=row,
    )


def _network_data(data: NetworkData) -> list[Datum]:
    """Nodes then edges; a network is always single-cohort, so no cohort label is carried."""
    nodes = [
        Datum(f"node[{n.id}]", "node", n.predicate, tuple(n.citations), n.weight, None, n.id)
        for n in data.nodes
    ]
    edges = [
        Datum(
            f"edge[{e.id}]",
            "edge",
            e.predicate,
            tuple(e.citations),
            e.weight,
            None,
            e.id,
            (e.source, e.target),
        )
        for e in data.edges
    ]
    return [*nodes, *edges]


def all_data(viz: Visualization) -> list[Datum]:
    """Every checkable datum in `viz`, in one flat, chart-shape-agnostic list."""
    if isinstance(viz, NetworkGraph):
        return _network_data(viz.data)
    return [_row_datum(row, i, viz.type) for i, row in enumerate(viz.data)]
