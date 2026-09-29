"""`pipeline._aggregate` dispatch agrees with `plan_checks.EXECUTABLE_KINDS`, and its defensive
branches still raise readable plan errors when called with a plan the checks would reject."""

from datetime import date

import pytest

from ctviz.agent.plan_checks import EXECUTABLE_KINDS
from ctviz.errors import PlanInvalidError
from ctviz.pipeline import _aggregate
from ctviz.schemas.enums import AnalysisKind
from tests.factories import make_plan

TODAY = date(2026, 9, 29)
_REQUIRED = {
    "count_by": {"group_by": "phase"},
    "time_trend": {"time_field": "start_date", "granularity": "year"},
    "histogram": {"measure_x": "enrollment"},
    "scatter": {"measure_x": "enrollment", "measure_y": "duration_months"},
    "network": {"network_type": "sponsor_drug"},
}


def _analysis(kind: str, **fields: object) -> dict[str, object]:
    base = dict.fromkeys(
        (
            "group_by",
            "series_by",
            "phase_mode",
            "time_field",
            "granularity",
            "measure_x",
            "measure_y",
            "color_by",
            "network_type",
            "top_n",
        )
    )
    return {"kind": kind, **base, **fields}


@pytest.mark.parametrize("kind", [k.value for k in AnalysisKind])
def test_aggregate_supports_exactly_the_executable_kinds(kind: str) -> None:
    # Arrange
    plan = make_plan(analysis=_analysis(kind, **_REQUIRED.get(kind, {})))

    # Act / Assert
    if AnalysisKind(kind) in EXECUTABLE_KINDS:
        _aggregate(plan, [], TODAY)
    else:
        with pytest.raises(PlanInvalidError, match="not yet supported"):
            _aggregate(plan, [], TODAY)


def test_aggregate_without_analysis_raises_plan_invalid() -> None:
    with pytest.raises(PlanInvalidError, match=r"plan\.analysis is required"):
        _aggregate(make_plan(analysis=None), [], TODAY)


def test_aggregate_count_by_without_group_by_raises_plan_invalid() -> None:
    with pytest.raises(PlanInvalidError, match=r"count_by requires analysis\.group_by"):
        _aggregate(make_plan(analysis=_analysis("count_by")), [], TODAY)


@pytest.mark.parametrize(
    ("kind", "message"),
    [
        ("histogram", r"histogram requires analysis\.measure_x"),
        ("scatter", "scatter requires"),
        ("network", r"network requires analysis\.network_type"),
    ],
)
def test_aggregate_defensive_branches_raise_readable_plan_errors(kind: str, message: str) -> None:
    with pytest.raises(PlanInvalidError, match=message):
        _aggregate(make_plan(analysis=_analysis(kind)), [], TODAY)


def test_build_bar_chart_with_two_cohorts_raises_plan_invalid() -> None:
    from ctviz.analysis.aggregate import AggregateResult
    from ctviz.pipeline_analysis import _build

    empty = AggregateResult(buckets=[], excluded={})

    with pytest.raises(PlanInvalidError, match="bar_chart requires exactly one cohort, got 2"):
        _build(make_plan(), {"A": empty, "B": empty}, "title")
