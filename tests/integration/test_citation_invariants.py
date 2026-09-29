"""Every citation resolves, and witness sets are sound, across all recorded fixtures (§11.6)."""

from collections.abc import Iterator

import pytest

from ctviz.analysis.aggregate import AggregateResult, MatchedTrial, count_by, time_trend
from ctviz.analysis.network import Graph, build_graph
from ctviz.analysis.numeric import histogram, scatter
from ctviz.analysis.prune import prune
from ctviz.citations.pointer import json_text, resolve_pointer
from ctviz.schemas.citations import Citation
from ctviz.schemas.enums import Dimension, Measure, NetworkType
from tests.fixtures.load import load_fixture, load_trials

FIXTURE_NAMES = (
    "crohns_p3_completed",
    "glioblastoma",
    "ms_recruiting",
    "nivolumab",
    "nsclc",
    "pembrolizumab",
    "psoriasis_p2",
)
COUNT_BY_DIMENSIONS = (
    Dimension.PHASE,
    Dimension.OVERALL_STATUS,
    Dimension.STUDY_TYPE,
    Dimension.LEAD_SPONSOR_CLASS,
    Dimension.LEAD_SPONSOR,
)
# Multi-valued: a trial may legitimately land in more than one bucket (exempt from exclusivity).
MULTI_VALUED_DIMENSIONS = (
    Dimension.INTERVENTION_TYPE,
    Dimension.INTERVENTION,
    Dimension.CONDITION,
    Dimension.COUNTRY,
)
TOP_N = 5


def _matched_trials(fixture: str) -> list[MatchedTrial]:
    """Every record in the fixture, normalized (cached), no match evidence (irrelevant here)."""
    return [MatchedTrial(trial, ()) for trial in load_trials(fixture)]


def _citations(result: AggregateResult) -> Iterator[Citation]:
    """Every citation across every bucket of a result."""
    for bucket in result.buckets:
        yield from bucket.citations


def _assert_pointers_resolve(raw: dict, citation: Citation) -> None:
    """The primary field and every evidence item resolve in `raw` with an exact-text excerpt."""
    assert json_text(resolve_pointer(raw, citation.field)) == citation.excerpt
    for item in citation.evidence:
        assert json_text(resolve_pointer(raw, item.field)) == item.excerpt


def _assert_no_duplicate_nct_ids(result: AggregateResult) -> None:
    """No trial is cited twice within the same bucket."""
    for bucket in result.buckets:
        ids = [c.nct_id for c in bucket.citations]
        assert len(ids) == len(set(ids))


def _assert_exclusive_across_buckets(result: AggregateResult) -> None:
    """For an exclusive dimension, no trial's nct_id appears in more than one bucket."""
    seen: set[str] = set()
    for bucket in result.buckets:
        ids = {c.nct_id for c in bucket.citations}
        assert not (ids & seen)
        seen |= ids


def _assert_trial_count_matches_citations(result: AggregateResult) -> None:
    """`trial_count` is always derived from `len(citations)`, never tracked separately."""
    for bucket in result.buckets:
        assert bucket.trial_count == len(bucket.citations)


def _assert_completeness(
    result: AggregateResult, raw_by_id: dict[str, dict], record_count: int
) -> None:
    """Σ cited + len(excluded) == records, and every pointer resolves for every citation."""
    cited_ids = {c.nct_id for c in _citations(result)}
    assert len(cited_ids) + len(result.excluded) == record_count
    for citation in _citations(result):
        _assert_pointers_resolve(raw_by_id[citation.nct_id], citation)


@pytest.mark.parametrize("fixture", FIXTURE_NAMES)
def test_every_citation_resolves_and_witness_sets_match_on_all_fixtures(fixture: str) -> None:
    records = load_fixture(fixture)
    raw_by_id = {r["protocolSection"]["identificationModule"]["nctId"]: r for r in records}
    trials = _matched_trials(fixture)

    for dimension in COUNT_BY_DIMENSIONS:
        result = count_by(trials, dimension, top_n=TOP_N)
        _assert_no_duplicate_nct_ids(result)
        _assert_exclusive_across_buckets(result)
        _assert_trial_count_matches_citations(result)
        _assert_completeness(result, raw_by_id, len(records))

    time_result = time_trend(trials, today_year=2026)
    _assert_no_duplicate_nct_ids(time_result)
    _assert_exclusive_across_buckets(time_result)
    _assert_trial_count_matches_citations(time_result)
    _assert_completeness(time_result, raw_by_id, len(records))


@pytest.mark.parametrize("fixture", FIXTURE_NAMES)
@pytest.mark.parametrize("dimension", MULTI_VALUED_DIMENSIONS)
def test_multi_valued_dimension_citations_resolve_and_dedupe_per_bucket(
    fixture: str, dimension: Dimension
) -> None:
    """Multi-valued dims are exempt from cross-bucket exclusivity, but never duplicate within
    a bucket, and every citation still resolves exactly (§11.5, §11.6 structure exemption)."""
    records = load_fixture(fixture)
    raw_by_id = {r["protocolSection"]["identificationModule"]["nctId"]: r for r in records}
    trials = _matched_trials(fixture)

    result = count_by(trials, dimension, top_n=TOP_N)

    _assert_no_duplicate_nct_ids(result)
    _assert_trial_count_matches_citations(result)
    for citation in _citations(result):
        _assert_pointers_resolve(raw_by_id[citation.nct_id], citation)


@pytest.mark.parametrize("fixture", FIXTURE_NAMES)
def test_histogram_citations_resolve_on_all_fixtures(fixture: str) -> None:
    records = load_fixture(fixture)
    raw_by_id = {r["protocolSection"]["identificationModule"]["nctId"]: r for r in records}
    trials = _matched_trials(fixture)

    result = histogram(trials, Measure.ENROLLMENT)

    _assert_no_duplicate_nct_ids(result)
    _assert_trial_count_matches_citations(result)
    cited_ids = {c.nct_id for c in _citations(result)}
    assert len(cited_ids) + len(result.excluded) == len(records)
    for citation in _citations(result):
        _assert_pointers_resolve(raw_by_id[citation.nct_id], citation)


@pytest.mark.parametrize("fixture", FIXTURE_NAMES)
def test_scatter_citations_resolve_on_all_fixtures(fixture: str) -> None:
    records = load_fixture(fixture)
    raw_by_id = {r["protocolSection"]["identificationModule"]["nctId"]: r for r in records}
    trials = _matched_trials(fixture)

    points, excluded = scatter(trials, Measure.DURATION_MONTHS, Measure.ENROLLMENT)

    ids = [pt.nct_id for pt in points]
    assert len(ids) == len(set(ids))  # no duplicate scatter points
    assert len(ids) + len(excluded) == len(records)
    for pt in points:
        for citation in pt.citations:
            _assert_pointers_resolve(raw_by_id[citation.nct_id], citation)


@pytest.mark.parametrize("fixture", FIXTURE_NAMES)
@pytest.mark.parametrize("network_type", list(NetworkType))
def test_network_citations_resolve_and_edges_join_known_nodes(
    fixture: str, network_type: NetworkType
) -> None:
    records = load_fixture(fixture)
    raw_by_id = {r["protocolSection"]["identificationModule"]["nctId"]: r for r in records}
    trials = _matched_trials(fixture)

    graph = prune(build_graph(trials, network_type))

    _assert_network_structure_sound(graph, raw_by_id)


def _assert_network_structure_sound(graph: Graph, raw_by_id: dict) -> None:
    """No non-zero node/edge lacks citations; edges join known nodes; every pointer resolves."""
    node_ids = {n.id for n in graph.nodes}
    for edge in graph.edges:
        assert edge.source in node_ids and edge.target in node_ids
        assert edge.citations  # every edge has a non-empty witness set
        for citation in edge.citations:
            _assert_pointers_resolve(raw_by_id[citation.nct_id], citation)
    for node in graph.nodes:
        assert node.citations
        for citation in node.citations:
            _assert_pointers_resolve(raw_by_id[citation.nct_id], citation)
