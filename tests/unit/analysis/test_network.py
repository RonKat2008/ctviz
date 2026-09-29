"""Sponsor/drug/condition relationship graphs, built and pruned the same way as buckets (§10.5)."""

from ctviz.analysis.aggregate import MatchedTrial
from ctviz.analysis.network import Graph, GraphEdge, GraphNode, build_graph
from ctviz.analysis.prune import prune
from ctviz.ctgov.normalize import normalize
from ctviz.schemas.citations import Citation
from ctviz.schemas.enums import NetworkType
from tests.factories import make_study


def _m(*studies: dict) -> list[MatchedTrial]:
    return [MatchedTrial(normalize(s), ()) for s in studies]


def test_sponsor_drug_edge_needs_both_endpoints_in_the_same_trial() -> None:
    trials = _m(
        make_study(
            "NCT00000001",
            sponsor="Merck",
            interventions=[
                {"type": "DRUG", "name": "Pembrolizumab"},
                {"type": "DRUG", "name": "Placebo"},
            ],
        )
    )

    graph = build_graph(trials, NetworkType.SPONSOR_DRUG)

    assert {n.id for n in graph.nodes} == {"sponsor:merck", "drug:pembrolizumab"}
    [edge] = graph.edges
    assert len(edge.citations[0].evidence) >= 1  # second endpoint evidence from the same record


def test_drug_drug_edges_require_the_same_arm() -> None:
    arms = [
        {"label": "A", "interventionNames": ["Drug: Carboplatin", "Drug: Paclitaxel"]},
        {"label": "B", "interventionNames": ["Drug: Docetaxel"]},
    ]
    trials = _m(
        make_study(
            interventions=[
                {"type": "DRUG", "name": n} for n in ("Carboplatin", "Paclitaxel", "Docetaxel")
            ],
            arms=arms,
        )
    )

    graph = build_graph(trials, NetworkType.DRUG_DRUG)

    assert [(e.source, e.target) for e in graph.edges] == [("drug:carboplatin", "drug:paclitaxel")]


def test_drug_drug_edges_canonicalize_regardless_of_arm_order() -> None:
    """HIGH item 6: A-B and B-A (different arm listing order across trials) must merge into one
    undirected edge, with weight counting distinct TRIALS, not one weight-1 edge per direction."""
    trials = _m(
        make_study(
            "NCT00000001",
            interventions=[
                {"type": "DRUG", "name": "Carboplatin"},
                {"type": "DRUG", "name": "Paclitaxel"},
            ],
            arms=[{"label": "A", "interventionNames": ["Carboplatin", "Paclitaxel"]}],
        ),
        make_study(
            "NCT00000002",
            interventions=[
                {"type": "DRUG", "name": "Carboplatin"},
                {"type": "DRUG", "name": "Paclitaxel"},
            ],
            arms=[{"label": "A", "interventionNames": ["Paclitaxel", "Carboplatin"]}],
        ),
    )

    graph = build_graph(trials, NetworkType.DRUG_DRUG)

    [edge] = graph.edges
    assert (edge.source, edge.target) == ("drug:carboplatin", "drug:paclitaxel")
    assert edge.weight == 2


def test_drug_drug_ignores_non_drug_arm_intervention_names() -> None:
    """An arm-group `interventionNames` entry not backed by a DRUG/BIOLOGICAL intervention in
    `trial.interventions` (e.g. an "Other:"-prefixed procedure) is never a drug-drug node."""
    arms = [
        {
            "label": "A",
            "interventionNames": ["Drug: Carboplatin", "Other: Clinical Observation"],
        }
    ]
    trials = _m(
        make_study(
            interventions=[{"type": "DRUG", "name": "Carboplatin"}],
            arms=arms,
        )
    )

    graph = build_graph(trials, NetworkType.DRUG_DRUG)

    assert graph.edges == ()
    assert {n.id for n in graph.nodes} == set()


def test_prune_caps_nodes_and_edges_and_reports_before_after() -> None:
    trials = _m(
        *[
            make_study(
                f"NCT{i:08d}",
                sponsor=f"S{i % 60}",
                interventions=[{"type": "DRUG", "name": f"D{i % 70}"}],
            )
            for i in range(400)
        ]
    )

    pruned = prune(build_graph(trials, NetworkType.SPONSOR_DRUG), max_nodes=50, max_edges=150)

    assert len(pruned.nodes) <= 50 and len(pruned.edges) <= 150
    assert pruned.summary["nodes_before_pruning"] > len(pruned.nodes)


def test_condition_drug_pairs_link_conditions_and_drugs_in_the_same_trial() -> None:
    trials = _m(
        make_study(
            conditions=["Melanoma"],
            interventions=[{"type": "DRUG", "name": "Pembrolizumab"}],
        )
    )

    graph = build_graph(trials, NetworkType.CONDITION_DRUG)

    assert {n.id for n in graph.nodes} == {"condition:melanoma", "drug:pembrolizumab"}
    [edge] = graph.edges
    assert (edge.source, edge.target) == ("condition:melanoma", "drug:pembrolizumab")


def test_sponsor_drug_include_collaborators_adds_collaborator_sponsor_nodes() -> None:
    trials = _m(
        make_study(
            "NCT00000001",
            sponsor="Merck",
            collaborators=["National Cancer Institute"],
            interventions=[{"type": "DRUG", "name": "X"}],
        )
    )

    graph = build_graph(trials, NetworkType.SPONSOR_DRUG, include_collaborators=True)

    assert {n.id for n in graph.nodes} == {
        "sponsor:merck",
        "sponsor:national cancer institute",
        "drug:x",
    }
    assert {(e.source, e.target) for e in graph.edges} == {
        ("sponsor:merck", "drug:x"),
        ("sponsor:national cancer institute", "drug:x"),
    }


def test_sponsor_drug_omits_collaborators_by_default() -> None:
    trials = _m(
        make_study(
            "NCT00000001",
            sponsor="Merck",
            collaborators=["National Cancer Institute"],
            interventions=[{"type": "DRUG", "name": "X"}],
        )
    )

    graph = build_graph(trials, NetworkType.SPONSOR_DRUG)

    assert {n.id for n in graph.nodes} == {"sponsor:merck", "drug:x"}


_SECTION_11_6_OPS = {
    "equals",
    "in",
    "set_equals",
    "contains",
    "exists",
    "year_equals",
    "year_in_range",
    "in_range",
    "any_element",
    "normalizes_to",
    "text_matches",
    "all",
    "any",
    "not",
}


def _collect_ops(predicate: dict) -> set[str]:
    """Every `op` (and combinator key) appearing anywhere inside one predicate, recursively."""
    ops: set[str] = set()
    if "not" in predicate:
        return ops | {"not"} | _collect_ops(predicate["not"])
    for combinator in ("all", "any"):
        if combinator in predicate:
            ops.add(combinator)
            for sub in predicate[combinator]:
                ops |= _collect_ops(sub)
            return ops
    if "op" in predicate:
        ops.add(predicate["op"])
    for sub in predicate.get("where", []):
        ops |= _collect_ops(sub)
    return ops


def test_no_network_predicate_uses_an_op_outside_section_11_6() -> None:
    """Q-D item 4: every node/edge predicate, across all three network types, is built only
    from the §11.6 op set (`any_text_equals` is not one of them)."""
    trials = _m(
        make_study(
            "NCT00000001",
            sponsor="Merck",
            conditions=["Melanoma"],
            interventions=[
                {"type": "DRUG", "name": "Carboplatin"},
                {"type": "DRUG", "name": "Paclitaxel"},
            ],
            arms=[{"label": "A", "interventionNames": ["Carboplatin", "Paclitaxel"]}],
        )
    )

    all_types = (NetworkType.SPONSOR_DRUG, NetworkType.DRUG_DRUG, NetworkType.CONDITION_DRUG)
    for network_type in all_types:
        graph = build_graph(trials, network_type)
        for node in graph.nodes:
            assert _collect_ops(node.predicate) <= _SECTION_11_6_OPS
        for edge in graph.edges:
            assert _collect_ops(edge.predicate) <= _SECTION_11_6_OPS


def _dummy_citations(n: int, start: int) -> tuple[Citation, ...]:
    return tuple(
        Citation(nct_id=f"NCT{i:08d}", field="/x", excerpt="x", evidence=[])
        for i in range(start, start + n)
    )


def _synthetic_graph(weights: list[int]) -> Graph:
    """A graph with one edge per weight in `weights`, each between its own pair of nodes."""
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    counter = 1
    for i, w in enumerate(weights):
        a, b = f"n{i}a", f"n{i}b"
        nodes.append(GraphNode(a, a, "drug", {}, ()))
        nodes.append(GraphNode(b, b, "drug", {}, ()))
        citations = _dummy_citations(w, counter)
        counter += w
        edges.append(GraphEdge(f"{a}::{b}", a, b, "drug_drug", {}, citations))
    return Graph(tuple(nodes), tuple(edges), {})


def test_prune_max_edges_loop_raises_min_weight_by_exactly_one_per_iteration() -> None:
    """M18/M20: the max-edges retry loop must raise `min_edge_weight` by 1 per iteration (not,
    say, 10) until the edge count fits `max_edges`; `network_summary.min_edge_weight` (the
    returned graph's `summary["min_edge_weight"]`) must equal that final threshold exactly."""
    graph = _synthetic_graph(list(range(1, 21)))  # edge weights 1..20

    pruned = prune(graph, max_nodes=100, max_edges=5, min_weight=2)

    # weight>=2 filter keeps weights 2..20 (19 edges); the loop must step the floor from 2 up to
    # 16 (one increment at a time) before only 5 edges (weights 16..20) survive.
    assert pruned.summary["min_edge_weight"] == 16
    assert len(pruned.edges) == 5
    assert {e.weight for e in pruned.edges} == {16, 17, 18, 19, 20}


def test_prune_drops_edges_below_min_weight() -> None:
    trials = _m(
        make_study("NCT00000001", sponsor="Merck", interventions=[{"type": "DRUG", "name": "X"}])
    )

    graph = prune(build_graph(trials, NetworkType.SPONSOR_DRUG), min_weight=2)

    assert graph.edges == ()  # weight 1 < min_weight 2
    assert graph.nodes == ()  # isolated nodes dropped after the edge is pruned
