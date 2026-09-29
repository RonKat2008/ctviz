"""The §9.4 revise loop, one test per arrow of the V3 diagram (PLAN.md Part I), plus the §8.4
NCT fast path. Scripted planner/judge backends + a fake probe client: no network, no LLM."""

import threading
from datetime import date
from typing import Any

import httpx
import openai
import pytest

from ctviz.agent.judge import Judge
from ctviz.agent.judge_backends import JudgeAnswer
from ctviz.agent.orchestrator import (
    JUDGE_TEXT_MAX_CHARS,
    PlanningOutcome,
    is_fast_path,
    orchestrate,
)
from ctviz.agent.planner import Planner
from ctviz.config import JUDGE_REQUEST_BUDGET_S
from ctviz.errors import LLMUnavailableError
from ctviz.schemas.judge import JudgeVerdict
from ctviz.schemas.plan import QueryPlan
from ctviz.schemas.request import VisualizeRequest
from tests.factories import make_plan
from tests.unit.agent.test_judge import FakeClock, ScriptedJudgeBackend, issue, verdict
from tests.unit.agent.test_probe import FakeProbeClient

TODAY = date(2026, 9, 29)
REQUEST = VisualizeRequest(query="Pembrolizumab trials by phase")
BAD_VIZ = {"type": "histogram", "title": "t", "rationale": "r"}  # count_by can't be a histogram
OUT_OF_SCOPE = {
    "answerable": False,
    "out_of_scope_reason": "Efficacy ranking is out of scope.",
    "suggested_reframing": "Count trials by phase instead.",
    "search_terms": [],
    "analysis": None,
    "visualization": None,
}
PASS = verdict()
REVISE = verdict([issue("major")], model_verdict="revise")
UNREACHABLE = openai.APIConnectionError(request=httpx.Request("POST", "https://x.test"))


class ScriptedPlannerBackend:
    """A `PlannerBackend` returning (or raising) each scripted effect in order."""

    def __init__(self, *effects: QueryPlan | Exception) -> None:
        self._effects = list(effects)
        self.user_prompts: list[str] = []
        self.threads: list[str] = []

    def complete(self, system: str, user: str) -> QueryPlan:
        self.user_prompts.append(user)
        self.threads.append(threading.current_thread().name)
        effect = self._effects.pop(0)
        if isinstance(effect, Exception):
            raise effect
        return effect


class _ThreadRecordingJudgeBackend(ScriptedJudgeBackend):
    def __init__(self, *effects: JudgeVerdict | Exception) -> None:
        super().__init__(*effects)
        self.threads: list[str] = []

    def complete(self, system: str, user: str, budget_s: float = 0.0) -> JudgeAnswer:
        self.threads.append(threading.current_thread().name)
        return super().complete(system, user, budget_s)


class _SlowJudgeBackend(ScriptedJudgeBackend):
    """Each call takes `call_s` on a shared fake clock and records the budget it was given."""

    def __init__(self, clock: FakeClock, call_s: float, *effects: JudgeVerdict) -> None:
        super().__init__(*effects)
        self._clock, self._call_s = clock, call_s
        self.budgets: list[float] = []

    def complete(self, system: str, user: str, budget_s: float = 0.0) -> JudgeAnswer:
        self.budgets.append(budget_s)
        self._clock.now += self._call_s
        return super().complete(system, user, budget_s)


async def _run(
    plans: list[QueryPlan | Exception],
    verdicts: list[JudgeVerdict | Exception],
    totals: dict[str, int] | None = None,
    request: VisualizeRequest = REQUEST,
    max_records: int | None = None,
) -> tuple[PlanningOutcome, ScriptedPlannerBackend, ScriptedJudgeBackend, FakeProbeClient]:
    planner_backend = ScriptedPlannerBackend(*plans)
    judge_backend = _ThreadRecordingJudgeBackend(*verdicts)
    client = FakeProbeClient(totals if totals is not None else {"pembrolizumab": 2960})
    if max_records is not None:
        request = request.model_copy(
            update={"options": request.options.model_copy(update={"max_records": max_records})}
        )
    outcome = await orchestrate(
        request,
        planner=Planner(planner_backend, model_name="gpt-5.4-mini"),
        judge=Judge(judge_backend, model_name="google/gemini-2.5-flash-lite"),
        client=client,  # type: ignore[arg-type]
        today=TODAY,
    )
    return outcome, planner_backend, judge_backend, client


def _steps(outcome: PlanningOutcome) -> list[tuple[Any, str]]:
    return [(event.get("attempt"), event["step"]) for event in outcome.trace]


# --- attempt 1 --------------------------------------------------------------------------------


async def test_pass_on_first_attempt() -> None:
    """V3: P1 -> C1 -ok-> R1 -totals ok-> J1 -pass-> EXECUTE (passed)."""
    # Act
    outcome, planner, judge, _client = await _run([make_plan()], [PASS])

    # Assert
    assert outcome.error_code is None
    assert outcome.judge_status == "passed"
    assert outcome.executed_attempt == 1
    assert outcome.plan is not None and outcome.plan.interpretation == make_plan().interpretation
    assert outcome.probe_totals == {"pembrolizumab": 2960}
    assert outcome.judge_model == "google/gemini-2.5-flash-lite"
    assert outcome.same_family is False
    assert len(planner.user_prompts) == 1 and len(judge.calls) == 1
    assert _steps(outcome) == [
        (1, "plan"),
        (1, "checks"),
        (1, "probe"),
        (1, "judge"),
        (None, "outcome"),
    ]


async def test_judge_unavailable_fails_open() -> None:
    """V3: J1 -unavailable-> EXECUTE plan 1 (unavailable)."""
    outcome, planner, _judge, _client = await _run([make_plan()], [UNREACHABLE])

    assert outcome.judge_status == "unavailable"
    assert outcome.executed_attempt == 1
    assert outcome.plan is not None
    assert len(planner.user_prompts) == 1


async def test_out_of_scope_skips_checks_and_judge() -> None:
    """V3: P1 -not answerable-> OUT_OF_SCOPE (judge skipped)."""
    plan = make_plan(**OUT_OF_SCOPE)

    outcome, _planner, judge, client = await _run([plan], [])

    assert outcome.error_code == "OUT_OF_SCOPE"
    assert outcome.judge_status == "skipped"
    assert outcome.plan is not None and outcome.plan.suggested_reframing is not None
    assert judge.calls == [] and client.calls == []
    assert _steps(outcome) == [(1, "plan"), (None, "outcome")]


async def test_planner_unavailable_raises_llm_unavailable() -> None:
    """V3: P1 -unreachable-> 503 LLM_UNAVAILABLE."""
    with pytest.raises(LLMUnavailableError):
        await _run([LLMUnavailableError("down")], [])


# --- attempt 1 fails -> attempt 2 -------------------------------------------------------------


async def test_checks_fail_then_revised_plan_passes() -> None:
    """V3: C1 -fail-> P2 (feedback = errors; judge skipped) -> C2 -ok-> R2 -> J2 -pass->
    EXECUTE (passed_after_revision)."""
    # Arrange
    bad = make_plan(visualization=BAD_VIZ)

    # Act
    outcome, planner, judge, client = await _run([bad, make_plan()], [PASS])

    # Assert
    assert outcome.judge_status == "passed_after_revision"
    assert outcome.executed_attempt == 2
    assert "count_by cannot be shown as histogram" in planner.user_prompts[1]
    assert "Revise your previous plan" in planner.user_prompts[1]
    assert len(judge.calls) == 1 and len(client.calls) == 1  # attempt 1 never probed/judged
    assert _steps(outcome) == [
        (1, "plan"),
        (1, "checks"),
        (2, "plan"),
        (2, "checks"),
        (2, "probe"),
        (2, "judge"),
        (None, "outcome"),
    ]


async def test_zero_probe_then_revised_plan_passes() -> None:
    """V3: R1 -0 results-> P2 (feedback = totals) -> ... -> passed_after_revision."""
    # Arrange
    typo = make_plan(
        search_terms=[
            {
                "param": "query.intr",
                "value": "pembrolizumabb",
                "source": "query_text",
                "rationale": "d",
            }
        ]
    )
    totals = {"pembrolizumabb": 0, "pembrolizumab": 2960}

    # Act
    outcome, planner, judge, _client = await _run([typo, make_plan()], [PASS], totals)

    # Assert
    assert outcome.judge_status == "passed_after_revision"
    assert "0 trials for query.intr='pembrolizumabb'" in planner.user_prompts[1]
    assert len(judge.calls) == 1  # attempt 1 never reached the judge
    assert outcome.probe_totals == {"pembrolizumab": 2960}


async def test_zero_probe_twice_returns_no_matching_trials() -> None:
    """V3: R1 -0-> P2 -> R2 -0 results (every cohort)-> NO_MATCHING_TRIALS (Review Focus #2:
    a misspelled drug revises once, then says so -- never an empty chart)."""
    # Arrange
    typo = make_plan(
        search_terms=[
            {"param": "query.intr", "value": "Keytrudda", "source": "query_text", "rationale": "d"}
        ]
    )

    # Act
    outcome, planner, judge, _client = await _run([typo, typo], [], {"Keytrudda": 0})

    # Assert
    assert outcome.error_code == "NO_MATCHING_TRIALS"
    assert outcome.plan is None
    assert len(planner.user_prompts) == 2
    assert judge.calls == []
    assert outcome.details == {
        "probe": [{"cohort": "Keytrudda", "total": 0}],
        "filters_to_drop": ["query.intr='Keytrudda'"],
    }
    assert "query.intr='Keytrudda'" in outcome.message


async def test_every_comparison_cohort_empty_twice_returns_no_matching_trials() -> None:
    """V3: R2 -0 results (every cohort)-> NO_MATCHING_TRIALS, for a comparison."""
    plan = make_plan(
        search_terms=[],
        comparison={"vary_param": "query.intr", "values": ["Aa", "Bb"]},
        visualization={"type": "grouped_bar_chart", "title": "t", "rationale": "r"},
    )

    outcome, *_rest = await _run([plan, plan], [], {"Aa": 0, "Bb": 0})

    assert outcome.error_code == "NO_MATCHING_TRIALS"


async def test_one_empty_comparison_cohort_on_revise_proceeds_with_a_warning() -> None:
    """§9.1: one empty cohort on attempt 2 -> proceed (zero bars) + meta.warnings."""
    plan = make_plan(
        search_terms=[],
        comparison={"vary_param": "query.intr", "values": ["Aa", "Bb"]},
        visualization={"type": "grouped_bar_chart", "title": "t", "rationale": "r"},
    )

    outcome, *_rest = await _run([plan, plan], [PASS], {"Aa": 3, "Bb": 0})

    assert outcome.judge_status == "passed_after_revision"
    assert outcome.warnings == [
        "cohort 'Bb' has 0 trials on ClinicalTrials.gov; it is shown as zero bars"
    ]


async def test_too_broad_probe_revises_then_proceeds_disclosed() -> None:
    """V3: R1 -too broad-> P2 -> R2 -too broad (disclosed)-> J2 -pass-> passed_after_revision."""
    outcome, planner, _judge, _client = await _run(
        [make_plan(), make_plan()], [PASS], {"pembrolizumab": 2960}, max_records=1000
    )

    assert outcome.judge_status == "passed_after_revision"
    assert "exceed the 1,000-record cap" in planner.user_prompts[1]
    probe_events = [e for e in outcome.trace if e["step"] == "probe"]
    assert [e["verdict"] for e in probe_events] == ["revise", "too_broad_accept"]


async def test_judge_revise_then_pass() -> None:
    """V3: J1 -critical/major issue-> P2 (feedback = issues) -> J2 -pass-> passed_after_revision."""
    outcome, planner, judge, _client = await _run([make_plan(), make_plan()], [REVISE, PASS])

    assert outcome.judge_status == "passed_after_revision"
    assert outcome.executed_attempt == 2
    assert 'set search_terms[0].param = "query.intr"' in planner.user_prompts[1]
    assert len(judge.calls) == 2
    assert outcome.judge_issues == []


async def test_revise_prompt_never_leaks_a_structured_field_value() -> None:
    """§8.2: the planner sees structured field NAMES only. On revise it is shown its own
    previous plan (the raw output it wrote, e.g. "pembrolizumab" from the query text) -- never
    the post-overlay plan, which would carry the structured field's VALUE (e.g. "Pembrolizumab",
    the drug_name the request supplied) into a slot the overlay filled in, not the planner."""
    request = VisualizeRequest(query="Trials for this drug by phase", drug_name="Pembrolizumab")
    # The planner's own raw output never mentions the drug -- it only knows "this drug" from
    # the query text; "Pembrolizumab" (the request's structured field value) can only reach the
    # revise prompt via the POST-overlay plan, which is exactly what §8.2 forbids.
    raw_plan = make_plan(
        interpretation="Trials by phase.",
        search_terms=[
            {
                "param": "query.intr",
                "value": "this drug",
                "source": "query_text",
                "rationale": "drug",
            }
        ],
        visualization={"type": "bar_chart", "title": "Trials by phase", "rationale": "categorical"},
    )
    # A structured field's own slot is never raised as a judge issue (it is authoritative), so
    # this objection targets an untouched slot, to actually trigger a revise.
    unrelated_issue = issue("major", "analysis.group_by").model_copy(
        update={
            "explanation": "Grouping by phase doesn't answer the question.",
            "suggested_fix": 'set analysis.group_by = "start_year"',
        }
    )
    revise = verdict([unrelated_issue], model_verdict="revise")

    outcome, planner, _judge, _client = await _run(
        [raw_plan, raw_plan], [revise, PASS], {"Pembrolizumab": 10}, request=request
    )

    assert outcome.judge_status == "passed_after_revision"
    assert "Pembrolizumab" not in planner.user_prompts[1]
    assert "this drug" in planner.user_prompts[1]  # the planner's own raw prior output


async def test_judge_revise_twice_ships_flagged() -> None:
    """V3: J2 -still disagrees-> EXECUTE plan 2, flagged (rejected_after_revision)."""
    outcome, _planner, judge, _client = await _run([make_plan(), make_plan()], [REVISE, REVISE])

    assert outcome.judge_status == "rejected_after_revision"
    assert outcome.executed_attempt == 2
    assert outcome.plan is not None
    assert outcome.judge_issues == [
        "[major] search_terms[0].param: Pembrolizumab is a drug, not a condition. "
        'Fix: set search_terms[0].param = "query.intr"'
    ]
    assert len(judge.calls) == 2  # at most two judge calls: the loop can't spin


async def test_judge_free_text_in_meta_validation_is_one_capped_line() -> None:
    """Fix J: an LLM-authored explanation is stored as one line of at most JUDGE_TEXT_MAX_CHARS."""
    long_issue = issue("major").model_copy(
        update={"explanation": "line one\nline two " + "y" * (JUDGE_TEXT_MAX_CHARS * 4)}
    )
    objection = verdict([long_issue], model_verdict="revise")

    outcome, *_rest = await _run([make_plan(), make_plan()], [objection, objection])

    [stored] = outcome.judge_issues
    assert len(stored) <= JUDGE_TEXT_MAX_CHARS and "\n" not in stored
    judge_events = [e for e in outcome.trace if e["step"] == "judge"]
    assert all(len(text) <= JUDGE_TEXT_MAX_CHARS for e in judge_events for text in e["issues"])


async def test_judge_unavailable_on_revise_executes_the_revised_plan() -> None:
    """V3: J2 -unavailable-> EXECUTE plan 2 (unavailable)."""
    outcome, *_rest = await _run([make_plan(), make_plan()], [REVISE, UNREACHABLE])

    assert outcome.judge_status == "unavailable"
    assert outcome.executed_attempt == 2


async def test_both_reviews_share_one_judge_budget_per_request() -> None:
    """Fix B: J1 burns 11 s of the 12 s request budget -> J2 is not called; plan 2 runs
    flagged unavailable (fail open), so the judge never exceeds its §4.5 share."""
    # Arrange
    clock = FakeClock()
    backend = _SlowJudgeBackend(clock, JUDGE_REQUEST_BUDGET_S - 1.0, REVISE, PASS)
    judge = Judge(backend, model_name="judge", clock=clock)

    # Act
    outcome = await orchestrate(
        REQUEST,
        planner=Planner(ScriptedPlannerBackend(make_plan(), make_plan())),
        judge=judge,
        client=FakeProbeClient({"pembrolizumab": 50}),  # type: ignore[arg-type]
        today=TODAY,
    )

    # Assert
    assert len(backend.calls) == 1
    assert backend.budgets == [JUDGE_REQUEST_BUDGET_S]
    assert (outcome.judge_status, outcome.executed_attempt) == ("unavailable", 2)


async def test_second_plan_invalid_executes_first_plan() -> None:
    """V3: C2 -fail-> F2 (plan 1 passed checks + probe? yes) -> EXECUTE plan 1
    (executed_previous_plan), with plan 1's judge objections attached."""
    # Arrange
    first = make_plan(interpretation="First plan.")
    second = make_plan(visualization=BAD_VIZ)

    # Act
    outcome, *_rest = await _run([first, second], [REVISE])

    # Assert
    assert outcome.judge_status == "executed_previous_plan"
    assert outcome.executed_attempt == 1
    assert outcome.plan is not None and outcome.plan.interpretation == "First plan."
    assert outcome.probe_totals == {"pembrolizumab": 2960}
    assert len(outcome.judge_issues) == 1


async def test_both_plans_invalid() -> None:
    """V3: C1 -fail-> P2 -> C2 -fail-> F2 -no-> PLAN_INVALID."""
    bad = make_plan(visualization=BAD_VIZ)

    outcome, _planner, judge, client = await _run([bad, bad], [])

    assert outcome.error_code == "PLAN_INVALID"
    assert outcome.errors == [
        "count_by cannot be shown as histogram; use one of: bar_chart, grouped_bar_chart, table"
    ]
    assert judge.calls == [] and client.calls == []


async def test_second_plan_invalid_after_a_failed_first_probe_is_plan_invalid() -> None:
    """V3: F2 -no-> PLAN_INVALID also when plan 1 passed its checks but not its probe."""
    typo = make_plan(
        search_terms=[
            {"param": "query.intr", "value": "Keytrudda", "source": "query_text", "rationale": "d"}
        ]
    )

    outcome, *_rest = await _run([typo, make_plan(visualization=BAD_VIZ)], [], {"Keytrudda": 0})

    assert outcome.error_code == "PLAN_INVALID"


_KEYTRUDDA = {"param": "query.intr", "value": "Keytrudda", "source": "query_text", "rationale": "d"}


@pytest.mark.parametrize(
    "second",
    [make_plan(search_terms=[_KEYTRUDDA]), make_plan(visualization=BAD_VIZ)],
    ids=["plan2_probes_zero", "plan2_fails_checks"],
)
async def test_a_too_broad_first_plan_is_executed_when_the_revision_is_worse(
    second: QueryPlan,
) -> None:
    """Fix E: R1 -too broad only-> P2 -> (R2 -0-> | C2 -fail->) EXECUTE plan 1
    (executed_previous_plan), disclosing that it is too broad and was never judged."""
    # Act
    outcome, planner, judge, _client = await _run(
        [make_plan(interpretation="First plan."), second],
        [],
        {"pembrolizumab": 2960, "Keytrudda": 0},
        max_records=1000,
    )

    # Assert
    assert outcome.error_code is None
    assert (outcome.judge_status, outcome.executed_attempt) == ("executed_previous_plan", 1)
    assert outcome.plan is not None and outcome.plan.interpretation == "First plan."
    assert outcome.probe_totals == {"pembrolizumab": 2960}
    assert len(planner.user_prompts) == 2 and judge.calls == []
    assert any("exceed the 1,000-record cap" in w for w in outcome.warnings)
    assert any("not reviewed by the judge" in w for w in outcome.warnings)


async def test_a_zero_first_plan_is_not_a_fallback_when_the_revision_also_probes_zero() -> None:
    """Fix E scope: only a TOO-BROAD plan 1 is a fallback; a zero plan 1 stays NO_MATCHING."""
    typo = make_plan(search_terms=[_KEYTRUDDA])

    outcome, *_rest = await _run([typo, typo], [], {"Keytrudda": 0}, max_records=1000)

    assert outcome.error_code == "NO_MATCHING_TRIALS"


async def test_out_of_scope_on_revise_returns_out_of_scope() -> None:
    """V3: P2 -not answerable-> OUT_OF_SCOPE."""
    outcome, *_rest = await _run([make_plan(visualization=BAD_VIZ), make_plan(**OUT_OF_SCOPE)], [])

    assert outcome.error_code == "OUT_OF_SCOPE"
    assert outcome.judge_status == "skipped"


async def test_planner_unavailable_on_revise_raises_llm_unavailable() -> None:
    """V3: P2 -unreachable-> 503 LLM_UNAVAILABLE."""
    with pytest.raises(LLMUnavailableError):
        await _run([make_plan(visualization=BAD_VIZ), LLMUnavailableError("down")], [])


# --- cross-cutting ----------------------------------------------------------------------------


async def test_orchestrate_runs_planner_and_judge_off_the_event_loop() -> None:
    _outcome, planner, judge, _client = await _run([make_plan()], [PASS])

    main = threading.main_thread().name
    assert planner.threads and main not in planner.threads
    assert judge.threads and main not in judge.threads  # type: ignore[attr-defined]


async def test_a_comparison_duplicating_its_fixed_term_is_normalized_not_revised() -> None:
    """Fix D (live K4): pembro vs nivo + a fixed query.intr='pembrolizumab' -> the duplicated
    term is dropped in code (meta.adjustments + trace), one planner call, no revise."""
    # Arrange
    plan = make_plan(
        comparison={"vary_param": "query.intr", "values": ["pembrolizumab", "nivolumab"]},
        visualization={"type": "grouped_bar_chart", "title": "t", "rationale": "r"},
    )

    # Act
    outcome, planner, _judge, _client = await _run(
        [plan], [PASS], {"pembrolizumab": 50, "nivolumab": 40}
    )

    # Assert
    assert len(planner.user_prompts) == 1
    assert (outcome.judge_status, outcome.executed_attempt) == ("passed", 1)
    assert outcome.plan is not None and outcome.plan.search_terms == []
    note = outcome.adjustments[0]
    assert note.startswith("search term query.intr='pembrolizumab' dropped")
    [checks] = [e for e in outcome.trace if e["step"] == "checks"]
    assert checks["ok"] is True and checks["adjustments"] == [note]


async def test_a_genuinely_conflicting_fixed_term_still_revises() -> None:
    """Fix D: a fixed query.intr='carboplatin' is NOT a compared value -- check 7 still fires."""
    plan = make_plan(
        search_terms=[
            {
                "param": "query.intr",
                "value": "carboplatin",
                "source": "query_text",
                "rationale": "d",
            }
        ],
        comparison={"vary_param": "query.intr", "values": ["pembrolizumab", "nivolumab"]},
        visualization={"type": "grouped_bar_chart", "title": "t", "rationale": "r"},
    )

    _outcome, planner, _judge, _client = await _run(
        [plan, make_plan()], [PASS], {"pembrolizumab": 50, "nivolumab": 40, "carboplatin": 9}
    )

    assert len(planner.user_prompts) == 2
    assert "query.intr is also a fixed search term" in planner.user_prompts[1]


async def test_irrelevant_analysis_fields_are_nulled_not_revised() -> None:
    """Fix D (§7.4-6): count_by with a stray measure_x -> nulled in code, disclosed, no revise."""
    analysis = {**make_plan().model_dump(mode="json")["analysis"], "measure_x": "enrollment"}

    outcome, planner, _judge, _client = await _run([make_plan(analysis=analysis)], [PASS])

    assert len(planner.user_prompts) == 1
    assert outcome.plan is not None and outcome.plan.analysis is not None
    assert outcome.plan.analysis.measure_x is None
    assert "analysis.measure_x set to null: count_by does not use it" in outcome.adjustments


async def test_orchestrate_applies_the_digit_guard_and_country_coercion_to_the_executed_plan() -> (
    None
):
    # Arrange
    plan = make_plan(
        filters={
            "phases": None,
            "overall_statuses": None,
            "study_types": None,
            "intervention_types": None,
            "lead_sponsor_classes": None,
            "countries": ["USA"],
            "start_year_min": None,
            "start_year_max": None,
            "nct_ids": None,
        },
        visualization={"type": "bar_chart", "title": "Up 45%", "rationale": "r"},
    )

    # Act
    outcome, *_rest = await _run([plan], [PASS])

    # Assert
    assert outcome.plan is not None and outcome.plan.visualization is not None
    assert outcome.plan.visualization.title == "Trial count by phase: pembrolizumab"
    assert outcome.plan.filters is not None and outcome.plan.filters.countries == ["United States"]
    assert outcome.adjustments == [
        "country 'USA' -> 'United States' (canonical spelling)",
        "title 'Up 45%' -> 'Trial count by phase: pembrolizumab' (unsupported number(s): 45)",
    ]


async def test_orchestrate_applies_the_overlay_and_reports_overrides() -> None:
    request = VisualizeRequest(query="Trials for this drug by phase", drug_name="Keytruda")

    outcome, *_rest = await _run([make_plan()], [PASS], {"Keytruda": 10}, request=request)

    assert outcome.plan is not None
    assert [t.value for t in outcome.plan.search_terms] == ["Keytruda"]
    assert outcome.overrides[0].applied_value == "Keytruda"


async def test_orchestrate_without_a_judge_reports_unavailable() -> None:
    outcome = await orchestrate(
        REQUEST,
        planner=Planner(ScriptedPlannerBackend(make_plan())),
        judge=None,
        client=FakeProbeClient({"pembrolizumab": 5}),  # type: ignore[arg-type]
        today=TODAY,
    )

    assert outcome.judge_status == "unavailable"
    assert outcome.judge_model is None


async def test_orchestrate_flags_a_same_family_judge() -> None:
    outcome = await orchestrate(
        REQUEST,
        planner=Planner(ScriptedPlannerBackend(make_plan()), model_name="gpt-5.4-mini"),
        judge=Judge(ScriptedJudgeBackend(PASS, model="gpt-4.1-nano"), model_name="gpt-4.1-nano"),
        client=FakeProbeClient({"pembrolizumab": 5}),  # type: ignore[arg-type]
        today=TODAY,
    )

    assert outcome.same_family is True


# --- §8.4 fast path ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "nct_ids", "expected"),
    [
        ("Status of NCT04368728?", None, ["NCT04368728"]),
        ("status of nct04368728 and NCT04470427", None, ["NCT04368728", "NCT04470427"]),
        ("What is this trial's status?", ["NCT04368728"], ["NCT04368728"]),
        ("Compare NCT04368728 vs NCT04470427", None, None),
        ("NCT04368728 enrollment over time", None, None),
        ("Tell me all about the design of NCT04368728 please", None, None),  # 7 words
        ("Status of pembrolizumab trials?", None, None),
        ("Sponsors per trial NCT04368728", None, None),
        ("NCT04368728 trends", None, None),  # fix H: stems, not exact words (kills M24)
        ("NCT04368728 trending?", None, None),
        ("Comparing NCT04368728 and NCT04470427", None, None),
        ("NCT04368728 site distributions", None, None),
        ("Trials similar to NCT04368728", None, None),  # fix H: "similar"/"like" -> planner
        ("Trials like NCT04368728", None, None),
        ("Details on NCT04368728, please", None, ["NCT04368728"]),  # "please" is not "per"
    ],
)
def test_is_fast_path_applies_the_deterministic_rule(
    query: str, nct_ids: list[str] | None, expected: list[str] | None
) -> None:
    request = VisualizeRequest(query=query, nct_ids=nct_ids)

    assert is_fast_path(request) == expected


async def test_fast_path_skips_llms_for_simple_nct_lookup() -> None:
    # Arrange
    request = VisualizeRequest(query="Status of NCT04368728?")
    client = FakeProbeClient({"": 1})

    # Act
    outcome = await orchestrate(
        request,
        planner=Planner(ScriptedPlannerBackend()),  # raises IndexError if ever called
        judge=Judge(ScriptedJudgeBackend()),
        client=client,  # type: ignore[arg-type]
        today=TODAY,
    )

    # Assert
    assert outcome.error_code is None
    assert outcome.judge_status == "skipped"
    assert outcome.judge_model is None
    assert outcome.plan is not None and outcome.plan.filters is not None
    assert outcome.plan.filters.nct_ids == ["NCT04368728"]
    assert client.calls[0]["filter.ids"] == "NCT04368728"
    assert [e["step"] for e in outcome.trace] == ["fast_path", "probe", "outcome"]


async def test_fast_path_for_an_unknown_nct_id_returns_no_matching_trials() -> None:
    outcome = await orchestrate(
        VisualizeRequest(query="Status of NCT99999999?"),
        planner=Planner(ScriptedPlannerBackend()),
        judge=None,
        client=FakeProbeClient({"": 0}),  # type: ignore[arg-type]
        today=TODAY,
    )

    assert outcome.error_code == "NO_MATCHING_TRIALS"


async def test_nct_question_with_compare_goes_to_planner() -> None:
    # Arrange
    request = VisualizeRequest(query="Compare NCT04368728 vs NCT04470427 by phase")
    planner_backend = ScriptedPlannerBackend(make_plan(search_terms=[]))

    # Act
    outcome = await orchestrate(
        request,
        planner=Planner(planner_backend),
        judge=Judge(ScriptedJudgeBackend(PASS)),
        client=FakeProbeClient({"": 2}),  # type: ignore[arg-type]
        today=TODAY,
    )

    # Assert: the planner ran, and the IDs were pre-filled as a structured filter
    assert len(planner_backend.user_prompts) == 1
    assert "nct_ids" in planner_backend.user_prompts[0]
    assert outcome.plan is not None and outcome.plan.filters is not None
    assert outcome.plan.filters.nct_ids == ["NCT04368728", "NCT04470427"]
    assert outcome.judge_status == "passed"


async def test_nct_ids_found_in_the_query_text_are_not_authoritative_for_the_judge() -> None:
    """Fix H: IDs pre-filled from the query TEXT help the planner, but only a structured
    `nct_ids` field is authoritative -- a judge issue on filters.nct_ids still revises."""
    # Arrange
    request = VisualizeRequest(query="Compare NCT04368728 vs NCT04470427 by phase")
    planner_backend = ScriptedPlannerBackend(make_plan(search_terms=[]), make_plan(search_terms=[]))
    judge_backend = ScriptedJudgeBackend(
        verdict([issue("major", "filters.nct_ids")], model_verdict="revise"), PASS
    )

    # Act
    outcome = await orchestrate(
        request,
        planner=Planner(planner_backend),
        judge=Judge(judge_backend),
        client=FakeProbeClient({"": 2}),  # type: ignore[arg-type]
        today=TODAY,
    )

    # Assert
    assert outcome.judge_status == "passed_after_revision"
    structured = judge_backend.calls[0][1].split("Structured fields (authoritative):", 1)[1]
    assert "nct_ids" not in structured.split("Field overrides", 1)[0]


async def test_unexecutable_kind_revises_like_a_failed_check() -> None:
    """V3: C1 -fail-> P2 also covers a plan the executor can't run (trial_list)."""
    # Arrange
    trial_list = make_plan(
        analysis={
            "kind": "trial_list",
            "group_by": None,
            "series_by": None,
            "phase_mode": None,
            "time_field": None,
            "granularity": None,
            "measure_x": None,
            "measure_y": None,
            "color_by": None,
            "network_type": None,
            "top_n": None,
        },
        visualization={"type": "table", "title": "t", "rationale": "r"},
    )

    # Act
    outcome, planner, _judge, _client = await _run([trial_list, make_plan()], [PASS])

    # Assert
    assert outcome.judge_status == "passed_after_revision"
    assert "trial_list is not executable yet" in planner.user_prompts[1]
