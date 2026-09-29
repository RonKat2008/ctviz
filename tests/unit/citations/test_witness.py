"""`WitnessIndex` is the evaluator, only faster: for every predicate the pipeline emits (and the
awkward synthetic shapes below) its answer equals the brute-force `evaluate` scan exactly."""

from collections.abc import Iterator, Mapping
from typing import Any

import pytest

from ctviz.analysis.aggregate import MatchedTrial, count_by, time_trend
from ctviz.analysis.dimensions import country_extractor
from ctviz.analysis.network import build_graph
from ctviz.citations.match import match_predicate
from ctviz.citations.predicates import evaluate
from ctviz.citations.witness import WitnessIndex
from ctviz.schemas.citations import Predicate
from ctviz.schemas.enums import Dimension, NetworkType
from ctviz.schemas.plan import SearchTerm
from tests.factories import make_study
from tests.fixtures.load import load_fixture, load_trials

LOCATIONS = "/protocolSection/contactsLocationsModule/locations"
PHASES = "/protocolSection/designModule/phases"
STATUS = "/protocolSection/statusModule/overallStatus"
TOP_N = 15


def _records(fixture: str) -> dict[str, Mapping[str, Any]]:
    return {r["protocolSection"]["identificationModule"]["nctId"]: r for r in load_fixture(fixture)}


def _brute(predicate: Predicate, records: Mapping[str, Mapping[str, Any]]) -> frozenset[str]:
    return frozenset(i for i, r in records.items() if evaluate(predicate, r))


def _count_by_predicates(fixture: str) -> Iterator[Predicate]:
    """Country/intervention bars, top-15 + 'Other' (= not-any), as the pipeline emits them."""
    trials = [MatchedTrial(t, ()) for t in load_trials(fixture)]
    for dimension in (Dimension.COUNTRY, Dimension.INTERVENTION):
        yield from (b.predicate for b in count_by(trials, dimension, top_n=TOP_N).buckets)


def _other_predicates(fixture: str) -> Iterator[Predicate]:
    """Phase, recruiting-country, year, and sponsor-drug network predicates."""
    trials = [MatchedTrial(t, ()) for t in load_trials(fixture)]
    yield from (b.predicate for b in count_by(trials, Dimension.PHASE).buckets)
    recruiting = count_by(trials, Dimension.COUNTRY, TOP_N, country_extractor(True))
    yield from (b.predicate for b in recruiting.buckets)
    yield from (b.predicate for b in time_trend(trials, 2026).buckets)
    for network_type in (NetworkType.SPONSOR_DRUG, NetworkType.DRUG_DRUG):
        graph = build_graph(trials, network_type)
        yield from (n.predicate for n in graph.nodes[:TOP_N])
        yield from (e.predicate for e in graph.edges[:TOP_N])  # multi-clause any_element


@pytest.mark.parametrize(
    ("fixture", "predicates"),
    [
        ("ms_recruiting", _other_predicates),
        ("glioblastoma", _other_predicates),
        ("glioblastoma", _count_by_predicates),
        ("nsclc", _count_by_predicates),  # the performance-critical shapes, at full scale
    ],
)
def test_witness_index_equals_brute_force_evaluation_on_real_predicates(
    fixture: str, predicates: Any
) -> None:
    records = _records(fixture)
    index = WitnessIndex(records)

    for predicate in predicates(fixture):
        assert index.witnesses(predicate) == _brute(predicate, records), predicate


SYNTHETIC = {
    "NCT00000001": make_study(
        "NCT00000001",
        phases=["PHASE3"],
        status=" RECRUITING ",
        start="2019-10",
        locations=[{"country": "Japan", "status": "COMPLETED"}, {"country": ["odd"]}],
        interventions=[{"type": "DRUG", "name": "Placebo"}, {"name": "Pembrolizumab 200 mg"}],
    ),
    "NCT00000002": make_study("NCT00000002", phases=["PHASE1", "PHASE2"], start="garbage"),
    "NCT00000003": make_study("NCT00000003", locations=[{"country": "Japan"}]),
}
SYNTHETIC_PREDICATES: list[Predicate] = [
    {"op": "equals", "path": STATUS, "value": "RECRUITING"},  # both sides stripped
    {"op": "in", "path": STATUS, "value": ["RECRUITING ", "COMPLETED"]},
    {"op": "equals", "path": PHASES, "value": ["PHASE3"]},  # list value: not keyable
    {"op": "in", "path": STATUS, "value": [["unhashable"], "COMPLETED"]},
    {
        "op": "year_equals",
        "path": "/protocolSection/statusModule/startDateStruct/date",
        "value": None,
    },
    {
        "op": "any_element",
        "path": "/protocolSection/armsInterventionsModule/interventions",
        "where": [{"op": "normalizes_to", "path": "/name", "value": None}],  # placebo -> None
    },
    {
        "op": "any_element",
        "path": LOCATIONS,
        "where": [{"op": "equals", "path": "/country", "value": "Japan"}],
    },
    {
        "op": "any_element",
        "path": LOCATIONS,
        "where": [
            {"op": "equals", "path": "/country", "value": "Japan"},
            {"op": "equals", "path": "/status", "value": "RECRUITING"},
        ],
    },
    {"not": {"any": [{"op": "contains", "path": PHASES, "value": "PHASE1"}]}},
    {"all": []},
    {"any": []},
]


@pytest.mark.parametrize("predicate", SYNTHETIC_PREDICATES)
def test_witness_index_equals_brute_force_on_awkward_shapes(predicate: Predicate) -> None:
    assert WitnessIndex(SYNTHETIC).witnesses(predicate) == _brute(predicate, SYNTHETIC)


def test_witness_index_raises_like_the_evaluator_for_an_unknown_op() -> None:
    with pytest.raises(ValueError, match="unknown predicate op"):
        WitnessIndex(SYNTHETIC).witnesses({"op": "fuzzy", "path": PHASES})


def test_witness_index_rejects_an_unknown_fn_even_when_no_record_has_the_path() -> None:
    predicate = {"op": "normalizes_to", "path": "/nowhere", "value": "x", "fn": "no-such-fn"}

    with pytest.raises(ValueError, match="unknown normalizes_to fn"):
        WitnessIndex(SYNTHETIC).witnesses(predicate)


def test_witness_index_equals_brute_force_on_a_strict_match_base_predicate() -> None:
    """`any` over 4 fields x (term + aliases), then `all` with a datum predicate: exercises the
    short-circuiting set algebra (later clauses only re-test still-unmatched records)."""
    records = _records("glioblastoma")
    term = SearchTerm(param="query.intr", value="temozolomide", source="query_text", rationale="d")
    base = match_predicate(term, ["temodar", "tmz"])
    phase3 = {"op": "contains", "path": PHASES, "value": "PHASE3"}
    index = WitnessIndex(records)

    for predicate in (base, {"all": [base, phase3]}, {"not": base}, {"any": [phase3, base]}):
        assert index.witnesses(predicate) == _brute(predicate, records)
    assert index.witnesses(base)  # non-vacuous: some glioblastoma trials use temozolomide
