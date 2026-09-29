"""S7: `run_pipeline` drives the §9.4 revise loop -- meta.validation is real, the loop's
ok:false endings become envelopes, and the NCT fast path answers with a cited, verified table."""

from datetime import date

import httpx
import pytest
import respx

from ctviz.agent.judge import Judge
from ctviz.agent.planner import Planner
from ctviz.citations.pointer import ENROLLMENT_COUNT
from ctviz.config import CTGOV_BASE_URL
from ctviz.ctgov.client import CtGovClient
from ctviz.errors import PlanInvalidError
from ctviz.pipeline import run_pipeline
from ctviz.schemas.request import VisualizeRequest
from tests.factories import make_plan, make_study
from tests.unit.agent.test_judge import ScriptedJudgeBackend, issue, verdict
from tests.unit.agent.test_orchestrator import BAD_VIZ, ScriptedPlannerBackend

TODAY = date(2026, 9, 28)
PEMBRO_IV = [{"type": "DRUG", "name": "Pembrolizumab"}]


def _mock_studies(total: int, studies: list[dict]) -> respx.Route:
    """Every GET /studies answers with this total and page (the probe is cached per params)."""
    return respx.get(f"{CTGOV_BASE_URL}/studies").mock(
        return_value=httpx.Response(200, json={"totalCount": total, "studies": studies})
    )


def _pembro_studies() -> list[dict]:
    return [
        make_study("NCT00000001", phases=["PHASE2"], interventions=PEMBRO_IV),
        make_study("NCT00000002", phases=["PHASE3"], interventions=PEMBRO_IV),
    ]


async def _run(
    plans: list,
    verdicts: list,
    request: VisualizeRequest | None = None,
) -> tuple:
    planner_backend = ScriptedPlannerBackend(*plans)
    judge_backend = ScriptedJudgeBackend(*verdicts)
    async with CtGovClient() as client:
        response = await run_pipeline(
            request or VisualizeRequest(query="Pembrolizumab trials by phase"),
            planner=Planner(planner_backend, model_name="gpt-5.4-mini"),
            judge=Judge(judge_backend, model_name="google/gemini-2.5-flash-lite"),
            client=client,
            today=TODAY,
        )
    return response, planner_backend, judge_backend


@respx.mock
async def test_run_pipeline_populates_meta_validation_from_the_revise_loop() -> None:
    # Arrange
    _mock_studies(2, _pembro_studies())

    # Act
    response, _planner, _judge = await _run(
        [make_plan(), make_plan()], [verdict([issue("major")]), verdict()]
    )

    # Assert
    assert response.ok
    validation = response.meta.validation
    assert validation.executed_attempt == 2
    assert validation.probe == [{"cohort": "pembrolizumab", "total": 2}]
    assert validation.judge.status == "passed_after_revision"
    assert validation.judge.model == "google/gemini-2.5-flash-lite"
    assert validation.judge.same_family is False
    assert validation.judge.issues == []
    assert [e["step"] for e in validation.trace] == [
        "plan",
        "checks",
        "probe",
        "judge",
        "plan",
        "checks",
        "probe",
        "judge",
        "outcome",
    ]


@respx.mock
async def test_run_pipeline_ships_a_flagged_answer_with_the_judges_objections() -> None:
    _mock_studies(2, _pembro_studies())
    objection = verdict([issue("major")])

    response, _planner, _judge = await _run([make_plan(), make_plan()], [objection, objection])

    assert response.ok
    assert response.meta.validation.judge.status == "rejected_after_revision"
    assert len(response.meta.validation.judge.issues) == 1


@respx.mock
async def test_run_pipeline_returns_no_matching_trials_instead_of_an_empty_chart() -> None:
    # Arrange
    route = _mock_studies(0, [])

    # Act
    response, planner, judge = await _run([make_plan(), make_plan()], [])

    # Assert
    assert response.ok is False
    assert response.visualization is None
    assert response.error.code == "NO_MATCHING_TRIALS"
    assert response.error.details["filters_to_drop"] == ["query.intr='pembrolizumab'"]
    assert len(planner.user_prompts) == 2 and judge.calls == []
    assert route.call_count == 1  # attempt 2's identical probe is served from the cache


@respx.mock
async def test_run_pipeline_raises_plan_invalid_when_both_plans_fail_checks() -> None:
    with pytest.raises(PlanInvalidError, match="count_by cannot be shown as histogram"):
        await _run([make_plan(visualization=BAD_VIZ), make_plan(visualization=BAD_VIZ)], [])


@respx.mock
async def test_run_pipeline_returns_out_of_scope_with_the_reframing() -> None:
    plan = make_plan(
        answerable=False,
        out_of_scope_reason="Efficacy ranking is out of scope.",
        suggested_reframing="Count trials by phase instead.",
        search_terms=[],
        analysis=None,
        visualization=None,
    )

    response, _planner, judge = await _run([plan], [])

    assert response.error.code == "OUT_OF_SCOPE"
    assert response.error.details == {"suggested_reframing": "Count trials by phase instead."}
    assert judge.calls == []


@respx.mock
async def test_run_pipeline_discloses_digit_guard_adjustments_and_probe_warnings() -> None:
    # Arrange: one compared cohort is empty on both attempts -> zero bars + a warning
    plan = make_plan(
        search_terms=[],
        comparison={"vary_param": "query.intr", "values": ["Pembrolizumab", "Nivolumabb"]},
        visualization={"type": "grouped_bar_chart", "title": "Up 45%", "rationale": "r"},
    )
    respx.get(f"{CTGOV_BASE_URL}/studies", params={"query.intr": "Nivolumabb"}).mock(
        return_value=httpx.Response(200, json={"totalCount": 0, "studies": []})
    )
    _mock_studies(2, _pembro_studies())
    request = VisualizeRequest(query="Compare pembrolizumab vs nivolumabb by phase")

    # Act
    response, *_rest = await _run([plan, plan], [verdict()], request)

    # Assert
    assert response.ok
    assert response.meta.validation.judge.status == "passed_after_revision"
    assert any("unsupported number(s): 45" in note for note in response.meta.adjustments)
    assert (
        "cohort 'Nivolumabb' has 0 trials on ClinicalTrials.gov; it is shown as zero bars"
        in response.meta.warnings
    )


@respx.mock
async def test_run_pipeline_without_a_judge_reports_unavailable() -> None:
    _mock_studies(2, _pembro_studies())

    async with CtGovClient() as client:
        response = await run_pipeline(
            VisualizeRequest(query="Pembrolizumab trials by phase"),
            planner=Planner(ScriptedPlannerBackend(make_plan())),
            client=client,
            today=TODAY,
        )

    assert response.meta.validation.judge.status == "unavailable"
    assert response.meta.validation.judge.model is None


@respx.mock
async def test_run_pipeline_fast_path_answers_an_nct_lookup_with_a_verified_cited_table() -> None:
    # Arrange
    study = make_study("NCT04368728", status="RECRUITING")
    route = _mock_studies(1, [study])

    # Act
    response, planner, judge = await _run([], [], VisualizeRequest(query="Status of NCT04368728?"))

    # Assert
    assert response.ok
    assert response.visualization.type == "table"
    [row] = response.visualization.data
    assert row["trial_count"] == 1 and len(row["citations"]) == 1
    assert response.meta.validation.judge.status == "skipped"
    assert response.meta.citation_check.passed is True
    assert planner.user_prompts == [] and judge.calls == []
    assert route.calls[0].request.url.params["filter.ids"] == "NCT04368728"


@respx.mock
async def test_fast_path_answers_how_many_participants_with_a_cited_key_facts_table() -> None:
    """Fix G (§8.4 "a table of key facts, every cell cited"): enrollment is in the table, each
    cell's pointer resolves to its exact text, and the independent verifier passes."""
    # Arrange
    study = make_study("NCT04368728", enrollment=47079, conditions=["COVID-19"], phases=["PHASE3"])
    _mock_studies(1, [study])

    # Act
    response, planner, judge = await _run(
        [], [], VisualizeRequest(query="How many participants in NCT04368728?")
    )

    # Assert
    assert response.ok and response.visualization.type == "table"
    [row] = response.visualization.data
    assert row["enrollment"] == "47079"
    assert row["cell_fields"]["enrollment"] == [ENROLLMENT_COUNT]
    [citation] = row["citations"]
    cited = {e.field: e.excerpt for e in citation.evidence}
    assert all(cited[f] for column in row["cell_fields"].values() for f in column)
    assert response.meta.citation_check.passed is True
    assert planner.user_prompts == [] and judge.calls == []


@respx.mock
async def test_fast_path_discloses_unknown_ids_in_a_multi_id_lookup() -> None:
    """Fix G: one of two requested IDs doesn't exist -> the table lists the one that does and
    meta.warnings names the missing ID."""
    _mock_studies(1, [make_study("NCT04368728")])

    response, *_rest = await _run(
        [], [], VisualizeRequest(query="Status of NCT04368728 and NCT09999999")
    )

    assert response.ok
    assert [r["nct_id"] for r in response.visualization.data] == ["NCT04368728"]
    assert any("NCT09999999" in w for w in response.meta.warnings)
