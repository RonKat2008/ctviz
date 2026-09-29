"""§14 golden network numbers: glioblastoma sponsor<->drug caps, NSCLC arm-vs-trial-level pairs."""

from itertools import combinations

from ctviz.analysis.aggregate import MatchedTrial
from ctviz.analysis.network import build_graph
from ctviz.analysis.prune import prune
from ctviz.common.names import normalize_drug
from ctviz.ctgov.normalize import normalize
from ctviz.pipeline_analysis import aggregate_network, excluded_of, network_summary, plotted_ids
from ctviz.schemas.enums import AnalysisKind, NetworkType
from ctviz.schemas.plan import Analysis
from tests.factories import make_study
from tests.fixtures.load import load_trials


def _matched_trials(fixture: str) -> list[MatchedTrial]:
    return [MatchedTrial(trial, ()) for trial in load_trials(fixture)]


def test_glioblastoma_sponsor_drug_network_stays_within_caps() -> None:
    trials = _matched_trials("glioblastoma")

    graph = prune(build_graph(trials, NetworkType.SPONSOR_DRUG))

    assert len(graph.nodes) <= 50
    assert len(graph.edges) <= 150
    assert all(e.weight >= 2 for e in graph.edges)
    assert graph.summary["nodes_before_pruning"] >= len(graph.nodes)


def _trial_level_drug_pairs(fixture: str) -> set[frozenset[str]]:
    """Every distinct drug pair co-occurring anywhere in the same trial (ignoring arms)."""
    pairs: set[frozenset[str]] = set()
    for matched in _matched_trials(fixture):
        keys = {
            normalize_drug(iv.name)
            for iv in matched.trial.interventions
            if iv.type in ("DRUG", "BIOLOGICAL") and normalize_drug(iv.name) is not None
        }
        pairs |= {frozenset(pair) for pair in combinations(sorted(keys), 2)}
    return pairs


def test_nsclc_drug_drug_arm_level_has_fewer_edges_than_trial_level_co_occurrence() -> None:
    trials = _matched_trials("nsclc")

    graph = build_graph(trials, NetworkType.DRUG_DRUG)
    arm_level_pairs = {frozenset((e.source, e.target)) for e in graph.edges}
    trial_level_pairs = _trial_level_drug_pairs("nsclc")

    assert len(arm_level_pairs) < len(trial_level_pairs)


def test_nsclc_network_exclusions_account_for_every_matched_trial() -> None:
    """Item 9: `records_matched - excluded == records_plotted` (§11.6) must hold for a network
    result too -- every trial that produced no surviving graph element needs a reason."""
    trials = _matched_trials("nsclc")
    analysis = Analysis(
        kind=AnalysisKind.NETWORK,
        group_by=None,
        series_by=None,
        phase_mode=None,
        time_field=None,
        granularity=None,
        measure_x=None,
        measure_y=None,
        color_by=None,
        network_type=NetworkType.DRUG_DRUG,
        top_n=None,
    )

    result = aggregate_network(trials, analysis)

    matched_ids = {t.trial.nct_id for t in trials}
    plotted = plotted_ids(result)
    excluded = excluded_of(result)
    assert plotted | set(excluded) == matched_ids
    assert not (plotted & set(excluded))  # disjoint: never both plotted and excluded
    assert set(excluded.values()) <= {"no_edge_pair", "not_in_network_after_pruning"}


def test_glioblastoma_network_summary_has_no_nct_ids_only_counts() -> None:
    """Item 1: `meta.network_summary` must never carry the per-trial `excluded` map (thousands
    of NCT ids for glioblastoma) -- only aggregate counts. The per-trial detail must still be
    reachable via `excluded_of` for `data_coverage.excluded_trials`."""
    trials = _matched_trials("glioblastoma")
    analysis = Analysis(
        kind=AnalysisKind.NETWORK,
        group_by=None,
        series_by=None,
        phase_mode=None,
        time_field=None,
        granularity=None,
        measure_x=None,
        measure_y=None,
        color_by=None,
        network_type=NetworkType.SPONSOR_DRUG,
        top_n=None,
    )

    result = aggregate_network(trials, analysis)
    summary = network_summary({"only": result})
    excluded = excluded_of(result)

    assert summary is not None
    assert "excluded" not in summary
    for value in summary.values():
        assert not isinstance(value, dict) or not any(k.startswith("NCT") for k in value)
    assert excluded  # glioblastoma has thousands of exclusions -- the fixture must exercise this
    reasons: dict[str, int] = {}
    for reason in excluded.values():
        reasons[reason] = reasons.get(reason, 0) + 1
    assert summary["excluded_count"] == len(excluded)
    assert summary["excluded_by_reason"] == reasons


def test_aggregate_network_honors_analysis_top_n_as_the_node_cap() -> None:
    """MEDIUM item 12: `analysis.top_n` becomes the pruning node cap, not just the default 50."""
    trials = _matched_trials("glioblastoma")
    analysis = Analysis(
        kind=AnalysisKind.NETWORK,
        group_by=None,
        series_by=None,
        phase_mode=None,
        time_field=None,
        granularity=None,
        measure_x=None,
        measure_y=None,
        color_by=None,
        network_type=NetworkType.SPONSOR_DRUG,
        top_n=5,
    )

    result = aggregate_network(trials, analysis)

    assert len(result.nodes) <= 5


def test_aggregate_network_honors_include_collaborators() -> None:
    """MEDIUM item 12: `options.include_collaborators` reaches `build_graph` for sponsor_drug."""
    study = make_study(
        "NCT00000001",
        sponsor="Merck",
        collaborators=["National Cancer Institute"],
        interventions=[{"type": "DRUG", "name": "X"}],
    )
    trials = [MatchedTrial(normalize(study), ())]
    analysis = Analysis(
        kind=AnalysisKind.NETWORK,
        group_by=None,
        series_by=None,
        phase_mode=None,
        time_field=None,
        granularity=None,
        measure_x=None,
        measure_y=None,
        color_by=None,
        network_type=NetworkType.SPONSOR_DRUG,
        top_n=None,
    )

    without = aggregate_network(trials, analysis, include_collaborators=False)
    with_collab = aggregate_network(trials, analysis, include_collaborators=True)

    assert "sponsor:national cancer institute" not in {n.id for n in without.nodes}
    assert "sponsor:national cancer institute" in {n.id for n in with_collab.nodes}
