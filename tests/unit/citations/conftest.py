"""Small, real pipeline-built responses for the verifier's own unit tests (Task 6.3 Step 1)."""

import asyncio
from collections.abc import Mapping
from datetime import date
from typing import Any

import httpx
import pytest
import respx

from ctviz.agent.planner import Planner
from ctviz.citations.verify import CohortPopulation, verify_response
from ctviz.config import CTGOV_BASE_URL
from ctviz.ctgov.client import CtGovClient
from ctviz.errors import CitationCheckError
from ctviz.pipeline import run_pipeline
from ctviz.schemas.request import VisualizeRequest
from ctviz.schemas.response import VisualizeResponse
from tests.factories import make_plan, make_study
from tests.unit.agent.test_planner import FakeBackend

PEMBRO = [{"type": "DRUG", "name": "Pembrolizumab"}]
SMALL_STUDIES: list[dict[str, Any]] = [
    make_study("NCT00000001", phases=["PHASE1"], interventions=PEMBRO),
    make_study("NCT00000002", phases=["PHASE3"], interventions=PEMBRO),
    make_study("NCT00000003", phases=["PHASE3"], interventions=PEMBRO),
]
RawById = dict[str, Mapping[str, Any]]
Populations = dict[str, CohortPopulation]


def _raw_by_id(studies: list[dict[str, Any]]) -> RawById:
    return {s["protocolSection"]["identificationModule"]["nctId"]: s for s in studies}


async def _run(
    request: VisualizeRequest, plan: object, studies: list[dict[str, Any]]
) -> VisualizeResponse:
    """Run the real pipeline once, with `studies` replayed behind both the probe and the fetch."""
    planner = Planner(FakeBackend(plan))
    with respx.mock:
        respx.get(f"{CTGOV_BASE_URL}/studies").mock(
            side_effect=[
                httpx.Response(200, json={"totalCount": len(studies), "studies": []}),
                httpx.Response(200, json={"totalCount": len(studies), "studies": studies}),
            ]
        )
        async with CtGovClient() as client:
            return await run_pipeline(
                request, planner=planner, client=client, today=date(2026, 9, 28)
            )


def single_cohort_populations(response: VisualizeResponse, raw_by_id: RawById) -> Populations:
    """The population the pipeline itself hands the verifier for one cohort: the fetched records
    minus match/filter drops, with the analysis exclusions it DECLARED (never the citations)."""
    assert response.meta is not None and response.meta.data_coverage is not None
    excluded = response.meta.data_coverage.excluded_trials
    declared = {e.nct_id: e.reason for e in excluded if e.stage == "analysis"}
    dropped = {e.nct_id for e in excluded if e.stage != "analysis"}
    population = CohortPopulation(frozenset(raw_by_id) - dropped, declared)
    return {response.meta.cohorts[0].label: population}


def run_small(
    plan: object, studies: list[dict[str, Any]], query: str = "Trials?", **fields: Any
) -> tuple[VisualizeResponse, RawById, Populations]:
    """A real, verified pipeline response for `studies`, plus the verifier's ground truth."""
    response = asyncio.run(_run(VisualizeRequest(query=query, **fields), plan, studies))
    assert response.ok and response.meta is not None
    raw_by_id = _raw_by_id(studies)
    return response, raw_by_id, single_cohort_populations(response, raw_by_id)


def build_small_response() -> tuple[VisualizeResponse, RawById, Populations]:
    """A real `bar_chart` (phase) response: 1 Phase 1 + 2 Phase 3 trials, all pembrolizumab."""
    return run_small(make_plan(), SMALL_STUDIES, "Trials by phase?", drug_name="Pembrolizumab")


def violations_of(
    response: VisualizeResponse, raw_by_id: RawById, populations: Populations
) -> list[str]:
    """The violations a (corrupted) response raises; fails the test if it verifies cleanly."""
    with pytest.raises(CitationCheckError) as info:
        verify_response(response, raw_by_id, populations)
    return info.value.violations


def tags_of(violations: list[str]) -> set[str]:
    """The stable check prefixes ("pointer", "soundness", ...) of a list of violations."""
    return {v.split(":", 1)[0] for v in violations}


def with_rows(response: VisualizeResponse, rows: list[dict[str, Any]]) -> VisualizeResponse:
    """`response` with its row data replaced (never mutated in place)."""
    assert response.visualization is not None
    viz = response.visualization.model_copy(update={"data": rows})
    return response.model_copy(update={"visualization": viz})


def replace_row(response: VisualizeResponse, category: str, **changes: Any) -> VisualizeResponse:
    """`response` with one category's row updated by `changes`."""
    assert response.visualization is not None
    rows = [
        {**r, **changes} if r.get("category") == category else r
        for r in response.visualization.data  # type: ignore[union-attr]
    ]
    return with_rows(response, rows)


def row(response: VisualizeResponse, category: str) -> dict[str, Any]:
    """The single row for `category`."""
    assert response.visualization is not None
    [found] = [r for r in response.visualization.data if r.get("category") == category]  # type: ignore[union-attr]
    return found
