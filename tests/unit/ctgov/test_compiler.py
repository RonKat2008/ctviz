"""Compiler tests: plans become request params exactly as the spec verified (PLAN.md §5.3)."""

import pytest

from ctviz.ctgov.compiler import advanced_filter, compile_plan
from ctviz.errors import PlanInvalidError
from ctviz.schemas.plan import EnumFilters
from tests.factories import make_plan

NO_FILTERS = dict.fromkeys(EnumFilters.model_fields)


def test_single_cohort_plan_compiles_search_terms_as_given() -> None:
    [spec] = compile_plan(make_plan())

    assert spec.params["query.intr"] == "pembrolizumab"
    assert spec.params["pageSize"] == "1000"
    assert spec.params["countTotal"] == "true"
    assert "NCTId" in spec.params["fields"].split(",")


def test_multi_word_values_are_not_auto_quoted() -> None:
    plan = make_plan(
        search_terms=[
            {
                "param": "query.cond",
                "value": "breast cancer",
                "source": "query_text",
                "rationale": "condition",
            }
        ]
    )

    [spec] = compile_plan(plan)

    assert spec.params["query.cond"] == "breast cancer"


def test_filters_compile_to_the_verified_essie_expression() -> None:
    filters = EnumFilters(**NO_FILTERS | {"phases": ["PHASE3"], "start_year_min": 2015})

    assert advanced_filter(filters) == "AREA[Phase]PHASE3 AND AREA[StartDate]RANGE[2015-01-01,MAX]"


def test_status_filter_uses_the_typed_parameter() -> None:
    plan = make_plan(filters=NO_FILTERS | {"overall_statuses": ["RECRUITING", "COMPLETED"]})

    [spec] = compile_plan(plan)

    assert spec.params["filter.overallStatus"] == "RECRUITING,COMPLETED"


def test_comparison_produces_one_spec_per_cohort() -> None:
    plan = make_plan(
        search_terms=[],
        comparison={"vary_param": "query.intr", "values": ["pembrolizumab", "nivolumab"]},
    )

    specs = compile_plan(plan)

    assert [s.cohort_label for s in specs] == ["pembrolizumab", "nivolumab"]
    assert [s.params["query.intr"] for s in specs] == ["pembrolizumab", "nivolumab"]


def test_essie_control_syntax_in_values_is_stripped() -> None:
    plan = make_plan(
        search_terms=[
            {
                "param": "query.intr",
                "value": 'drug"]AREA[x',
                "source": "query_text",
                "rationale": "x",
            }
        ]
    )

    [spec] = compile_plan(plan)

    assert "[" not in spec.params["query.intr"] and '"' not in spec.params["query.intr"]


def test_advanced_filter_rejects_country_not_in_catalog() -> None:
    injection = 'Japan" OR AREA[Phase]PHASE1 OR "'
    filters = EnumFilters(**NO_FILTERS | {"countries": [injection]})

    with pytest.raises(PlanInvalidError) as info:
        advanced_filter(filters)

    assert any(injection in e for e in info.value.errors)


def test_advanced_filter_accepts_country_in_catalog() -> None:
    filters = EnumFilters(**NO_FILTERS | {"countries": ["Japan"]})

    assert advanced_filter(filters) == 'AREA[LocationCountry]"Japan"'


def test_compile_plan_rejects_malformed_nct_ids() -> None:
    plan = make_plan(filters=NO_FILTERS | {"nct_ids": ["NCT123", "NCT00000001"]})

    with pytest.raises(PlanInvalidError) as info:
        compile_plan(plan)

    assert any("NCT123" in e for e in info.value.errors)


def test_advanced_filter_renders_every_filter_exactly() -> None:
    filters = EnumFilters(
        **NO_FILTERS
        | {
            "phases": ["PHASE1", "PHASE2"],
            "study_types": ["INTERVENTIONAL"],
            "intervention_types": ["DRUG"],
            "lead_sponsor_classes": ["INDUSTRY"],
            "countries": ["United States"],
            "start_year_min": 2015,
            "start_year_max": 2020,
        }
    )

    assert advanced_filter(filters) == (
        "(AREA[Phase]PHASE1 OR AREA[Phase]PHASE2) AND "
        "AREA[StudyType]INTERVENTIONAL AND "
        "AREA[InterventionType]DRUG AND "
        "AREA[LeadSponsorClass]INDUSTRY AND "
        'AREA[LocationCountry]"United States" AND '
        "AREA[StartDate]RANGE[2015-01-01,2020-12-31]"
    )


def test_compile_plan_puts_filters_and_ids_in_params() -> None:
    plan = make_plan(
        filters=NO_FILTERS
        | {
            "phases": ["PHASE3"],
            "overall_statuses": ["RECRUITING"],
            "nct_ids": ["NCT00000001", "NCT00000002"],
        }
    )

    [spec] = compile_plan(plan)

    assert spec.params["filter.advanced"] == "AREA[Phase]PHASE3"
    assert spec.params["filter.overallStatus"] == "RECRUITING"
    assert spec.params["filter.ids"] == "NCT00000001,NCT00000002"


def test_compile_plan_cleans_comparison_values() -> None:
    plan = make_plan(
        search_terms=[],
        comparison={"vary_param": "query.intr", "values": ['drug"]AREA[x']},
    )

    [spec] = compile_plan(plan)

    assert '"' not in spec.params["query.intr"] and "[" not in spec.params["query.intr"]


def test_compile_plan_rejects_value_that_cleans_to_empty() -> None:
    plan = make_plan(
        search_terms=[
            {
                "param": "query.intr",
                "value": '["]',
                "source": "query_text",
                "rationale": "x",
            }
        ]
    )

    with pytest.raises(PlanInvalidError):
        compile_plan(plan)
