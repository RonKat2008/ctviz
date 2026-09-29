"""Verifier corruptions on comparisons, networks, scatter and multi-valued dims, plus pipeline
MUTANTS (monkeypatched counting code) that the independent recount must still catch (§11.6)."""

import asyncio
from datetime import date
from typing import Any

import httpx
import pytest
import respx

import ctviz.pipeline as pipeline_module
from ctviz.agent.planner import Planner
from ctviz.analysis.aggregate import AggregateResult, Bucket, MatchedTrial
from ctviz.citations.match import MatchOutcome
from ctviz.citations.verify import CohortPopulation, verify_response
from ctviz.config import CTGOV_BASE_URL
from ctviz.ctgov.client import CtGovClient
from ctviz.errors import CitationCheckError
from ctviz.pipeline import run_pipeline
from ctviz.schemas.request import VisualizeRequest
from tests.factories import make_plan, make_study
from tests.unit.agent.test_planner import FakeBackend
from tests.unit.citations.conftest import (
    PEMBRO,
    _raw_by_id,
    _run,
    replace_row,
    row,
    run_small,
    tags_of,
    violations_of,
    with_rows,
)

NIVO = [{"type": "DRUG", "name": "Nivolumab"}]


def _analysis(kind: str, **overrides: Any) -> dict[str, Any]:
    base = dict.fromkeys(
        ("group_by", "series_by", "phase_mode", "time_field", "granularity", "measure_x"), None
    ) | dict.fromkeys(("measure_y", "color_by", "network_type", "top_n"), None)
    return {"kind": kind, **base, **overrides}


def _plan(kind: str, viz: str, **analysis: Any) -> object:
    return make_plan(
        search_terms=[],
        analysis=_analysis(kind, **analysis),
        visualization={"type": viz, "title": "t", "rationale": "r"},
    )


# --- comparisons -----------------------------------------------------------------------------


def _comparison(extra_shared_trial: bool) -> tuple[Any, dict, dict[str, CohortPopulation]]:
    """Pembrolizumab vs nivolumab by phase; optionally a third trial testing BOTH drugs."""
    pembro_only = make_study("NCT00000001", phases=["PHASE3"], interventions=PEMBRO)
    nivo_only = make_study("NCT00000002", phases=["PHASE3"], interventions=NIVO)
    both = make_study("NCT00000003", phases=["PHASE3"], interventions=[*PEMBRO, *NIVO])
    pembro = [pembro_only, both] if extra_shared_trial else [pembro_only]
    nivo = [nivo_only, both] if extra_shared_trial else [nivo_only]
    plan = make_plan(
        search_terms=[],
        comparison={"vary_param": "query.intr", "values": ["Pembrolizumab", "Nivolumab"]},
        analysis=_analysis("count_by", group_by="phase", phase_mode="combined"),
        visualization={"type": "grouped_bar_chart", "title": "Compare", "rationale": "compare"},
    )

    async def _go() -> Any:
        with respx.mock:
            respx.get(f"{CTGOV_BASE_URL}/studies").mock(
                side_effect=[
                    httpx.Response(200, json={"totalCount": len(pembro), "studies": []}),
                    httpx.Response(200, json={"totalCount": len(pembro), "studies": pembro}),
                    httpx.Response(200, json={"totalCount": len(nivo), "studies": []}),
                    httpx.Response(200, json={"totalCount": len(nivo), "studies": nivo}),
                ]
            )
            async with CtGovClient() as client:
                return await run_pipeline(
                    VisualizeRequest(query="Compare by phase"),
                    planner=Planner(FakeBackend(plan)),
                    client=client,
                    today=date(2026, 9, 28),
                )

    response = asyncio.run(_go())
    populations = {
        "Pembrolizumab": CohortPopulation(frozenset(_raw_by_id(pembro)), {}),
        "Nivolumab": CohortPopulation(frozenset(_raw_by_id(nivo)), {}),
    }
    return response, _raw_by_id([*pembro, *nivo]), populations


def test_trial_in_the_wrong_cohort_row_is_a_soundness_violation() -> None:
    response, raw_by_id, populations = _comparison(extra_shared_trial=False)
    rows = response.visualization.data
    [nivo_row] = [r for r in rows if r["cohort"] == "Nivolumab"]
    corrupted = with_rows(
        response,
        [
            {**r, "citations": [*r["citations"], *nivo_row["citations"]], "trial_count": 2}
            if r["cohort"] == "Pembrolizumab"
            else r
            for r in rows
        ],
    )

    violations = violations_of(corrupted, raw_by_id, populations)

    assert any(v.startswith("soundness:") and "NCT00000002" in v for v in violations)


def test_overlapping_cohorts_are_not_a_false_alarm() -> None:
    response, raw_by_id, populations = _comparison(extra_shared_trial=True)

    check = verify_response(response, raw_by_id, populations)

    assert check.passed is True
    cited = [c.nct_id for r in response.visualization.data for c in r["citations"]]
    assert cited.count("NCT00000003") == 2  # once per cohort


# --- multi-valued dims, scatter, networks ----------------------------------------------------


def test_trial_dropped_from_one_multi_valued_bar_is_a_completeness_violation_only() -> None:
    """NCT00000001 has Japan and US sites; dropping it from Japan still leaves it plotted (US),
    so only the per-datum recount can see the hole."""
    studies = [
        make_study("NCT00000001", locations=[{"country": "Japan"}, {"country": "United States"}]),
        make_study("NCT00000002", locations=[{"country": "Japan"}]),
        make_study("NCT00000003", locations=[{"country": "United States"}]),
    ]
    response, raw_by_id, populations = run_small(
        _plan("count_by", "bar_chart", group_by="country"), studies
    )
    kept = [c for c in row(response, "Japan")["citations"] if c.nct_id != "NCT00000001"]

    violations = violations_of(
        replace_row(response, "Japan", citations=kept, trial_count=1), raw_by_id, populations
    )

    assert tags_of(violations) == {"completeness"}
    assert "missing=['NCT00000001']" in violations[0]


def test_scatter_point_silently_dropped_is_a_structure_violation_only() -> None:
    """Scatter points carry no predicate, so only the plotted-population identity sees it."""
    studies = [
        make_study(f"NCT0000000{i}", start="2019-01-10", completion="2021-06-10", enrollment=50 + i)
        for i in range(1, 4)
    ]
    plan = _plan("scatter", "scatter_plot", measure_x="duration_months", measure_y="enrollment")
    response, raw_by_id, populations = run_small(plan, studies)

    violations = violations_of(
        with_rows(response, response.visualization.data[1:]), raw_by_id, populations
    )

    assert tags_of(violations) == {"structure"}


def _drug_drug() -> tuple[Any, dict, dict[str, CohortPopulation]]:
    arms = {"label": "Combo", "interventionNames": ["Pembrolizumab", "Lenvatinib"]}
    studies = [
        make_study(
            "NCT00000001",
            arms=[arms],
            interventions=[*PEMBRO, {"type": "DRUG", "name": "Lenvatinib"}],
        ),
        make_study(
            "NCT00000002",
            arms=[{"label": "Mono", "interventionNames": ["Pembrolizumab"]}],
            interventions=PEMBRO,
        ),
        make_study(
            "NCT00000003",
            arms=[{"label": "C2", "interventionNames": ["Pembrolizumab", "Bevacizumab"]}],
            interventions=[*PEMBRO, {"type": "DRUG", "name": "Bevacizumab"}],
        ),
    ]
    return run_small(_plan("network", "network_graph", network_type="drug_drug"), studies)


def _with_graph(response: Any, **changes: Any) -> Any:
    data = response.visualization.data.model_copy(update=changes)
    viz = response.visualization.model_copy(update={"data": data})
    return response.model_copy(update={"visualization": viz})


def test_edge_evidence_from_another_trial_is_a_pointer_violation() -> None:
    response, raw_by_id, populations = _drug_drug()
    edges = response.visualization.data.edges
    [edge] = [e for e in edges if "lenvatinib" in e.id]
    forged = edge.citations[0].model_copy(update={"nct_id": "NCT00000002"})
    corrupted = _with_graph(
        response,
        edges=[e.model_copy(update={"citations": [forged]}) if e is edge else e for e in edges],
    )

    violations = violations_of(corrupted, raw_by_id, populations)

    assert any(v.startswith("pointer:") and "NCT00000002" in v for v in violations)


def test_edge_whose_endpoint_is_not_a_node_is_a_structure_violation_only() -> None:
    response, raw_by_id, populations = _drug_drug()
    nodes = [n for n in response.visualization.data.nodes if n.id != "drug:lenvatinib"]

    violations = violations_of(_with_graph(response, nodes=nodes), raw_by_id, populations)

    assert tags_of(violations) == {"structure"}
    assert "drug:lenvatinib" in violations[0]


def test_node_weight_that_disagrees_with_its_citations_is_a_count_violation() -> None:
    response, raw_by_id, populations = _drug_drug()
    nodes = [
        n.model_copy(update={"weight": 99}) if n.id == "drug:lenvatinib" else n
        for n in response.visualization.data.nodes
    ]

    violations = violations_of(_with_graph(response, nodes=nodes), raw_by_id, populations)

    assert violations == ["count: node[drug:lenvatinib]: weight=99 but 1 citations"]


# --- pipeline mutants: the counting code itself is wrong ---------------------------------------


def _pipeline_violations(studies: list[dict[str, Any]]) -> list[str]:
    request = VisualizeRequest(query="Trials by phase?", drug_name="Pembrolizumab")
    with pytest.raises(CitationCheckError) as info:
        asyncio.run(_run(request, make_plan(), studies))
    return info.value.violations


def test_pipeline_that_silently_drops_a_trial_fails_the_population_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The mutant drops NCT00000003 from every bucket WITHOUT declaring an exclusion; the
    verifier's population is the kept records, never the (now shorter) citations."""
    real = pipeline_module.count_by

    def dropping(*args: Any, **kwargs: Any) -> AggregateResult:
        result = real(*args, **kwargs)
        buckets = tuple(
            Bucket(b.key, b.predicate, tuple(c for c in b.citations if c.nct_id != "NCT00000003"))
            for b in result.buckets
        )
        return AggregateResult(tuple(b for b in buckets if b.citations), result.excluded)

    monkeypatch.setattr(pipeline_module, "count_by", dropping)
    studies = [
        make_study(f"NCT0000000{i}", phases=["PHASE3"], interventions=PEMBRO) for i in (1, 2, 3)
    ]

    violations = _pipeline_violations(studies)

    assert any(v.startswith("structure:") and "records_plotted=2" in v for v in violations)
    assert any(v.startswith("completeness:") and "NCT00000003" in v for v in violations)


def test_pipeline_that_skips_strict_match_is_caught_by_the_base_predicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = pipeline_module.apply_strict_match

    def keep_everything(trials: list, *args: Any, **kwargs: Any) -> MatchOutcome:
        outcome = real(trials, *args, **kwargs)
        dropped = {e.nct_id for e in outcome.excluded}
        forged = [MatchedTrial(t, ()) for t in trials if t.nct_id in dropped]
        return MatchOutcome([*outcome.kept, *forged], [], outcome.base_predicate)

    monkeypatch.setattr(pipeline_module, "apply_strict_match", keep_everything)
    studies = [
        make_study("NCT00000001", phases=["PHASE3"], interventions=PEMBRO),
        make_study("NCT00000099", phases=["PHASE3"], interventions=NIVO),
    ]

    violations = _pipeline_violations(studies)

    assert tags_of(violations) == {"soundness"}
    assert "NCT00000099" in violations[0]
