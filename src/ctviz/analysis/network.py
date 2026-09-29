"""Sponsor/drug/condition graphs, cited like buckets (§10.5). Q-D: verifier needs `fn` support.

Witness-pair construction (which endpoints a trial witnesses, and with what evidence) lives in
`network_pairs.py` (module budget, LOW item 13); this module folds those pairs into a deduped,
cited `Graph` -- `build_graph()` is the only entry point the rest of the pipeline calls.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Literal

from ctviz.analysis.aggregate import MatchedTrial
from ctviz.analysis.network_pairs import (
    _Pair,
    _trial_drugs,
    condition_drug_pairs,
    drug_drug_pairs,
    sponsor_drug_pairs,
)
from ctviz.citations.pointer import json_text, resolve_pointer
from ctviz.common.names import strip_drug_prefix
from ctviz.ctgov.normalize import Trial
from ctviz.schemas.citations import Citation, Predicate
from ctviz.schemas.enums import NetworkType

NodeType = Literal["sponsor", "drug", "condition"]
EdgeType = Literal["sponsor_drug", "drug_drug", "condition_drug"]
EdgeKey = tuple[str, str]
MAX_NODES_DEFAULT = 50
_EDGE_TYPE: dict[NetworkType, EdgeType] = {
    NetworkType.SPONSOR_DRUG: "sponsor_drug",
    NetworkType.DRUG_DRUG: "drug_drug",
    NetworkType.CONDITION_DRUG: "condition_drug",
}


@dataclass(frozen=True)
class GraphNode:
    """One node (sponsor/drug/condition); weight is the number of distinct trials it appears in."""

    id: str
    label: str
    type: NodeType
    predicate: Predicate
    citations: tuple[Citation, ...]

    @property
    def weight(self) -> int:
        return len(self.citations)


@dataclass(frozen=True)
class GraphEdge:
    """One edge; weight is the number of distinct trials in its witness set (intersection)."""

    id: str
    source: str
    target: str
    type: EdgeType
    predicate: Predicate
    citations: tuple[Citation, ...]
    flags: tuple[str, ...] = ()

    @property
    def weight(self) -> int:
        return len(self.citations)


@dataclass(frozen=True)
class Graph:
    """The full node/edge graph, plus a summary always reported in `meta.network_summary`."""

    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    summary: dict[str, Any] = field(default_factory=dict)


@dataclass
class _FoldState:
    """Mutable accumulators `_fold_pair` fills in; `_assemble` reads them to build the Graph."""

    node_citations: dict[str, list[Citation]] = field(default_factory=lambda: defaultdict(list))
    node_seen: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    node_labels: dict[str, Counter[str]] = field(default_factory=lambda: defaultdict(Counter))
    node_type: dict[str, NodeType] = field(default_factory=dict)
    node_predicate: dict[str, Predicate] = field(default_factory=dict)
    edge_citations: dict[EdgeKey, list[Citation]] = field(default_factory=lambda: defaultdict(list))
    edge_seen: dict[EdgeKey, set[str]] = field(default_factory=lambda: defaultdict(set))
    edge_predicate: dict[EdgeKey, Predicate] = field(default_factory=dict)


def _fold_pair(state: _FoldState, pair: _Pair) -> None:
    """Record one trial's witness pair into `state`, deduping nodes/edges per trial (§10.5)."""
    for endpoint in (pair.a, pair.b):
        state.node_labels[endpoint.key][endpoint.raw_label] += 1
        state.node_type[endpoint.key] = endpoint.kind  # type: ignore[assignment]
        state.node_predicate.setdefault(endpoint.key, endpoint.predicate)
        if pair.nct_id not in state.node_seen[endpoint.key]:
            state.node_seen[endpoint.key].add(pair.nct_id)
            citation = Citation(
                nct_id=pair.nct_id,
                field=endpoint.evidence.field,
                excerpt=endpoint.evidence.excerpt,
                evidence=list(pair.match_evidence),
            )
            state.node_citations[endpoint.key].append(citation)
    edge_key = (pair.a.key, pair.b.key)
    if pair.edge_predicate is not None:
        state.edge_predicate.setdefault(edge_key, pair.edge_predicate)
    if pair.nct_id not in state.edge_seen[edge_key]:
        state.edge_seen[edge_key].add(pair.nct_id)
        citation = Citation(
            nct_id=pair.nct_id,
            field=pair.a.evidence.field,
            excerpt=pair.a.evidence.excerpt,
            evidence=[pair.b.evidence, *pair.match_evidence],
        )
        state.edge_citations[edge_key].append(citation)


def _display_label(raw_label: str, node_type: NodeType) -> str:
    """Item 12: strip a drug's arm-listing prefix for display only; evidence stays raw."""
    return strip_drug_prefix(raw_label) if node_type == "drug" else raw_label


def _build_nodes(state: _FoldState) -> tuple[GraphNode, ...]:
    """One `GraphNode` per distinct endpoint key, its most-common raw spelling as the label."""
    return tuple(
        GraphNode(
            key,
            _display_label(state.node_labels[key].most_common(1)[0][0], state.node_type[key]),
            state.node_type[key],
            state.node_predicate[key],
            tuple(state.node_citations[key]),
        )
        for key in state.node_citations
    )


def _edge_predicate(state: _FoldState, a: str, b: str) -> Predicate:
    """A Q-D override when the true witness is narrower, else both endpoints' AND."""
    both = {"all": [state.node_predicate[a], state.node_predicate[b]]}
    return state.edge_predicate.get((a, b), both)


def _build_edges(state: _FoldState, network_type: NetworkType) -> tuple[GraphEdge, ...]:
    """One `GraphEdge` per distinct (source, target) key."""
    edge_type = _EDGE_TYPE[network_type]
    return tuple(
        GraphEdge(f"{a}::{b}", a, b, edge_type, _edge_predicate(state, a, b), tuple(citations))
        for (a, b), citations in state.edge_citations.items()
    )


def _assemble(pairs: list[_Pair], network_type: NetworkType) -> Graph:
    """Fold witness pairs into deduped (per trial) nodes and edges, citations attached once."""
    state = _FoldState()
    for pair in pairs:
        _fold_pair(state, pair)
    nodes, edges = _build_nodes(state), _build_edges(state, network_type)
    summary = {"nodes_before_pruning": len(nodes), "edges_before_pruning": len(edges)}
    return Graph(nodes, edges, summary)


def _excerpt_at(trial: Trial, field_path: str) -> str:
    """The exact raw-record text at `field_path`, for an evidence excerpt."""
    return json_text(resolve_pointer(trial.raw, field_path))


def _expand_drug_drug_node_citations(trials: list[MatchedTrial], graph: Graph) -> Graph:
    """Item 10: re-cite every trial matching a node's own trial-level predicate (§11.5)."""
    node_keys = {n.id.removeprefix("drug:") for n in graph.nodes}
    if not node_keys:
        return graph
    citations: dict[str, list[Citation]] = defaultdict(list)
    seen: dict[str, set[str]] = defaultdict(set)
    for matched in trials:
        trial = matched.trial
        for key, _name, field_path in _trial_drugs(trial):
            if key not in node_keys or trial.nct_id in seen[key]:
                continue
            seen[key].add(trial.nct_id)
            citation = Citation(
                nct_id=trial.nct_id,
                field=field_path,
                excerpt=_excerpt_at(trial, field_path),
                evidence=list(matched.match_evidence),
            )
            citations[key].append(citation)
    nodes = tuple(
        GraphNode(n.id, n.label, n.type, n.predicate, tuple(citations[n.id.removeprefix("drug:")]))
        for n in graph.nodes
    )
    return Graph(nodes, graph.edges, graph.summary)


def build_graph(
    trials: list[MatchedTrial],
    network_type: NetworkType,
    top_n: int = MAX_NODES_DEFAULT,
    include_collaborators: bool = False,
) -> Graph:
    """Build the full (unpruned) node/edge graph for one network type (PLAN.md §10.5)."""
    del top_n  # interface parity with the plan; `prune()` applies the cap as a separate step
    if network_type is NetworkType.SPONSOR_DRUG:
        pairs = sponsor_drug_pairs(trials, include_collaborators)
    elif network_type is NetworkType.DRUG_DRUG:
        pairs = drug_drug_pairs(trials)
        return _expand_drug_drug_node_citations(trials, _assemble(pairs, network_type))
    elif network_type is NetworkType.CONDITION_DRUG:
        pairs = condition_drug_pairs(trials)
    else:
        raise NotImplementedError(f"network type {network_type} is not supported (S5 slice)")
    return _assemble(pairs, network_type)
