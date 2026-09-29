"""Network pruning: cap nodes/edges and report the before/after counts (PLAN.md §10.5).

Split out of `analysis/network.py` (LOW item 13) to keep that module under the 400-line budget;
`build_graph()` stays in `network.py`, `prune()` is always called on its result separately.
"""

from ctviz.analysis.network import Graph, GraphEdge, GraphNode

MAX_NODES_DEFAULT = 50
MAX_EDGES_DEFAULT = 150
MIN_EDGE_WEIGHT_DEFAULT = 2


def _weighted_degree(nodes: tuple[GraphNode, ...], edges: list[GraphEdge]) -> dict[str, int]:
    """Sum of incident edge weights per node id, used to rank nodes for the top-N cap."""
    degree: dict[str, int] = dict.fromkeys((n.id for n in nodes), 0)
    for e in edges:
        degree[e.source] = degree.get(e.source, 0) + e.weight
        degree[e.target] = degree.get(e.target, 0) + e.weight
    return degree


def _keep_top_n_nodes(
    nodes: tuple[GraphNode, ...], edges: list[GraphEdge], max_nodes: int
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Step 3: keep the top-N nodes by weighted degree, and the induced subgraph of edges."""
    degree = _weighted_degree(nodes, edges)
    ranked = sorted(nodes, key=lambda n: (-degree.get(n.id, 0), n.id))
    kept_ids = {n.id for n in ranked[:max_nodes]}
    kept_nodes = [n for n in nodes if n.id in kept_ids]
    kept_edges = [e for e in edges if e.source in kept_ids and e.target in kept_ids]
    return kept_nodes, kept_edges


def _drop_isolated(
    nodes: list[GraphNode], edges: list[GraphEdge]
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Step 5: drop nodes with no surviving edge."""
    connected = {e.source for e in edges} | {e.target for e in edges}
    return [n for n in nodes if n.id in connected], edges


def prune(
    graph: Graph,
    max_nodes: int = MAX_NODES_DEFAULT,
    max_edges: int = MAX_EDGES_DEFAULT,
    min_weight: int = MIN_EDGE_WEIGHT_DEFAULT,
) -> Graph:
    """§10.5 pruning order (steps 1-5; step 0 is only for the stretch site/investigator networks).

    Always reported in `meta.network_summary` via the returned graph's `summary`.
    """
    nodes_before, edges_before = len(graph.nodes), len(graph.edges)
    edges = [e for e in graph.edges if e.weight >= min_weight]  # step 2 (step 1 is at construction)
    nodes, edges = _keep_top_n_nodes(graph.nodes, edges, max_nodes)  # step 3
    weight_floor = min_weight
    while len(edges) > max_edges:
        weight_floor += 1
        edges = [e for e in edges if e.weight >= weight_floor]  # step 4
    nodes, edges = _drop_isolated(nodes, edges)  # step 5
    summary = {
        **graph.summary,
        "nodes_before_pruning": nodes_before,
        "edges_before_pruning": edges_before,
        "nodes_after_pruning": len(nodes),
        "edges_after_pruning": len(edges),
        "min_edge_weight": weight_floor,
    }
    return Graph(tuple(nodes), tuple(edges), summary)
