"""Probe integration (PLAN.md §9.1): 1-record count per cohort -> ok / revise / no_matches /
too_broad_accept, with feedback the planner can act on. Uses a fake client (no network)."""

from typing import Any

import pytest

from ctviz.agent.probe import ProbeResult, droppable_filters, probe_plan
from ctviz.ctgov.compiler import compile_plan
from tests.factories import make_plan

CAP = 1_000
_COMPARISON = {"vary_param": "query.intr", "values": ["Pembrolizumab", "Nivolumabb"]}
_GROUPED = {"type": "grouped_bar_chart", "title": "t", "rationale": "r"}


class FakeProbeClient:
    """Stands in for `CtGovClient.probe`: the total for a cohort, keyed by its `key` param."""

    def __init__(self, totals: dict[str, int], key: str = "query.intr") -> None:
        self._totals, self._key = totals, key
        self.calls: list[dict[str, str]] = []

    async def probe(self, params: dict[str, str]) -> int:
        self.calls.append(params)
        return self._totals[params.get(self._key, "")]


def _comparison_plan() -> Any:
    return make_plan(search_terms=[], comparison=_COMPARISON, visualization=_GROUPED)


async def test_probe_plan_passes_totals_within_the_cap() -> None:
    # Arrange
    client = FakeProbeClient({"pembrolizumab": 2960})

    # Act
    result = await probe_plan(make_plan(), client, max_records=20_000, attempt=1)

    # Assert
    assert result == ProbeResult(totals={"pembrolizumab": 2960}, feedback=[], verdict="ok")


async def test_probe_plan_probes_each_cohort_with_its_compiled_params() -> None:
    plan = _comparison_plan()
    client = FakeProbeClient({"Pembrolizumab": 5, "Nivolumabb": 7})

    await probe_plan(plan, client, max_records=CAP, attempt=1)

    assert client.calls == [spec.params for spec in compile_plan(plan)]


async def test_probe_plan_zero_on_attempt_one_revises_naming_the_param_and_value() -> None:
    # Arrange
    plan = make_plan(
        search_terms=[
            {"param": "query.cond", "value": "Keytruda", "source": "query_text", "rationale": "x"}
        ]
    )
    client = FakeProbeClient({"Keytruda": 0}, key="query.cond")

    # Act
    result = await probe_plan(plan, client, max_records=CAP, attempt=1)

    # Assert
    assert result.verdict == "revise"
    assert result.totals == {"Keytruda": 0}
    [feedback] = result.feedback
    assert "0 trials for query.cond='Keytruda'" in feedback


async def test_probe_plan_zero_on_attempt_two_is_no_matches() -> None:
    client = FakeProbeClient({"pembrolizumab": 0})

    result = await probe_plan(make_plan(), client, max_records=CAP, attempt=2)

    assert result.verdict == "no_matches"


async def test_probe_plan_zero_feedback_names_the_filters_when_there_are_no_terms() -> None:
    plan = make_plan(
        search_terms=[],
        filters={
            "phases": ["PHASE4"],
            "overall_statuses": None,
            "study_types": None,
            "intervention_types": None,
            "lead_sponsor_classes": None,
            "countries": ["Iceland"],
            "start_year_min": None,
            "start_year_max": None,
            "nct_ids": None,
        },
    )
    client = FakeProbeClient({"": 0})

    result = await probe_plan(plan, client, max_records=CAP, attempt=1)

    assert "filters.phases=['PHASE4']" in result.feedback[0]
    assert "filters.countries=['Iceland']" in result.feedback[0]


async def test_probe_plan_one_empty_cohort_on_attempt_one_asks_to_double_check_it() -> None:
    client = FakeProbeClient({"Pembrolizumab": 5, "Nivolumabb": 0})

    result = await probe_plan(_comparison_plan(), client, max_records=CAP, attempt=1)

    assert result.verdict == "revise"
    assert result.feedback == [
        "0 trials for comparison value query.intr='Nivolumabb'; double-check that value's "
        "spelling and whether it belongs on a different param."
    ]


async def test_probe_plan_one_empty_cohort_on_attempt_two_is_ok_with_a_warning() -> None:
    # Arrange
    client = FakeProbeClient({"Pembrolizumab": 5, "Nivolumabb": 0})

    # Act
    result = await probe_plan(_comparison_plan(), client, max_records=CAP, attempt=2)

    # Assert
    assert result.verdict == "ok"
    assert result.feedback == []
    assert result.warnings == [
        "cohort 'Nivolumabb' has 0 trials on ClinicalTrials.gov; it is shown as zero bars"
    ]


async def test_probe_plan_every_cohort_empty_on_attempt_two_is_no_matches() -> None:
    client = FakeProbeClient({"Pembrolizumab": 0, "Nivolumabb": 0})

    result = await probe_plan(_comparison_plan(), client, max_records=CAP, attempt=2)

    assert result.verdict == "no_matches"


async def test_probe_plan_every_cohort_empty_on_attempt_one_revises() -> None:
    client = FakeProbeClient({"Pembrolizumab": 0, "Nivolumabb": 0})

    result = await probe_plan(_comparison_plan(), client, max_records=CAP, attempt=1)

    assert result.verdict == "revise"
    assert len(result.feedback) == 2


async def test_probe_plan_over_the_cap_on_attempt_one_asks_to_narrow_if_implied() -> None:
    client = FakeProbeClient({"pembrolizumab": CAP + 1})

    result = await probe_plan(make_plan(), client, max_records=CAP, attempt=1)

    assert result.verdict == "revise"
    [feedback] = result.feedback
    assert "1,001 trials for query.intr='pembrolizumab' exceed the 1,000-record cap" in feedback
    assert "if the question implies a narrower scope" in feedback


async def test_probe_plan_over_the_cap_on_attempt_two_is_too_broad_accept() -> None:
    client = FakeProbeClient({"pembrolizumab": CAP + 1})

    result = await probe_plan(make_plan(), client, max_records=CAP, attempt=2)

    assert result.verdict == "too_broad_accept"
    assert result.feedback == []


async def test_probe_plan_exactly_at_the_cap_is_ok() -> None:
    client = FakeProbeClient({"pembrolizumab": CAP})

    result = await probe_plan(make_plan(), client, max_records=CAP, attempt=1)

    assert result.verdict == "ok"


async def test_probe_plan_one_empty_and_one_over_cap_on_attempt_two_accepts_with_warning() -> None:
    client = FakeProbeClient({"Pembrolizumab": CAP + 5, "Nivolumabb": 0})

    result = await probe_plan(_comparison_plan(), client, max_records=CAP, attempt=2)

    assert result.verdict == "too_broad_accept"
    assert len(result.warnings) == 1


@pytest.mark.parametrize("attempt", [0, 3])
async def test_probe_plan_rejects_an_attempt_other_than_one_or_two(attempt: int) -> None:
    with pytest.raises(ValueError, match="attempt must be 1 or 2"):
        await probe_plan(make_plan(), FakeProbeClient({}), max_records=CAP, attempt=attempt)


def test_droppable_filters_lists_search_terms_and_every_set_filter() -> None:
    plan = make_plan(
        filters={
            "phases": ["PHASE3"],
            "overall_statuses": ["RECRUITING"],
            "study_types": None,
            "intervention_types": None,
            "lead_sponsor_classes": None,
            "countries": None,
            "start_year_min": 2020,
            "start_year_max": None,
            "nct_ids": None,
        }
    )

    assert droppable_filters(plan) == [
        "query.intr='pembrolizumab'",
        "filters.phases=['PHASE3']",
        "filters.overall_statuses=['RECRUITING']",
        "filters.start_year_min=2020",
    ]


def test_droppable_filters_lists_comparison_values() -> None:
    assert droppable_filters(_comparison_plan()) == [
        "comparison query.intr in ['Pembrolizumab', 'Nivolumabb']"
    ]
