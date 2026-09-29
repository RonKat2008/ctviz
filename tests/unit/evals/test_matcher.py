"""The eval property matcher: plans are checked against *properties*, never exact JSON (§16.4)."""

from typing import Any

from evals.matcher import check_plan

from tests.factories import make_plan


def _plan(**overrides: Any) -> dict[str, Any]:
    return make_plan(**overrides).model_dump(mode="json")


def test_check_plan_passes_when_every_property_holds() -> None:
    expect = {
        "search_terms": [{"param": "query.intr", "value_contains": "pembro"}],
        "analysis.kind": "count_by",
        "analysis.group_by": "phase",
        "visualization.type": ["bar_chart", "grouped_bar_chart"],
    }

    assert check_plan(_plan(), expect) == []


def test_search_terms_value_contains_is_case_insensitive() -> None:
    expect = {"search_terms": [{"param": "query.intr", "value_contains": "PEMBROLIZUMAB"}]}

    assert check_plan(_plan(), expect) == []


def test_search_terms_fails_when_the_entity_is_on_the_wrong_param() -> None:
    expect = {"search_terms": [{"param": "query.cond", "value_contains": "pembrolizumab"}]}

    failures = check_plan(_plan(), expect)

    assert len(failures) == 1
    assert "query.cond" in failures[0]


def test_scalar_property_mismatch_names_expected_and_actual() -> None:
    failures = check_plan(_plan(), {"analysis.kind": "time_trend"})

    assert failures == ["analysis.kind: expected 'time_trend', got 'count_by'"]


def test_allowed_set_rejects_a_viz_type_outside_the_set() -> None:
    failures = check_plan(_plan(), {"visualization.type": ["time_series"]})

    assert len(failures) == 1
    assert "bar_chart" in failures[0]


def test_values_contain_requires_every_value_in_the_comparison() -> None:
    plan = _plan(comparison={"vary_param": "query.intr", "values": ["Pembrolizumab", "Nivo"]})
    expect = {
        "comparison.vary_param": "query.intr",
        "comparison.values_contain": ["pembrolizumab", "nivolumab"],
    }

    failures = check_plan(plan, expect)

    assert len(failures) == 1
    assert "nivolumab" in failures[0]


def test_list_contain_on_filters_matches_enum_members() -> None:
    filters = {
        "phases": ["PHASE2"],
        "overall_statuses": ["RECRUITING"],
        "study_types": None,
        "intervention_types": None,
        "lead_sponsor_classes": None,
        "countries": None,
        "start_year_min": 2015,
        "start_year_max": None,
        "nct_ids": None,
    }
    expect = {
        "filters.phases_contain": ["PHASE2"],
        "filters.overall_statuses_contain": ["recruiting"],
        "filters.start_year_min": 2015,
    }

    assert check_plan(_plan(filters=filters), expect) == []


def test_missing_parent_node_is_a_failure_not_a_crash() -> None:
    failures = check_plan(
        _plan(), {"filters.start_year_min": 2015, "comparison.values_contain": ["a"]}
    )

    assert len(failures) == 2
    assert all("missing" in f for f in failures)


def test_answerable_false_is_checked_as_a_plain_scalar() -> None:
    plan = {"answerable": False, "search_terms": [], "assumptions": []}

    assert check_plan(plan, {"answerable": False}) == []
    assert check_plan(_plan(), {"answerable": False}) == ["answerable: expected False, got True"]


def test_assumptions_nonempty_requires_a_stated_assumption() -> None:
    assert check_plan(_plan(assumptions=[]), {"assumptions_nonempty": True}) != []
    assert (
        check_plan(_plan(assumptions=["recent = since 2021"]), {"assumptions_nonempty": True}) == []
    )


def test_no_plan_at_all_fails_every_case() -> None:
    assert check_plan(None, {"answerable": False}) == ["no plan was produced"]


def test_expected_null_requires_the_slot_to_be_null() -> None:
    assert check_plan(_plan(), {"comparison": None}) == []
    plan = _plan(comparison={"vary_param": "query.intr", "values": ["a", "b"]})
    assert len(check_plan(plan, {"comparison": None})) == 1


def test_present_suffix_requires_a_non_null_value() -> None:
    assert check_plan(_plan(), {"filters.start_year_min_present": True}) == [
        "filters.start_year_min: expected a value, got none"
    ]
    assert check_plan(_plan(), {"analysis.group_by_present": True}) == []


def _filters(**values: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "phases": None,
        "overall_statuses": None,
        "study_types": None,
        "intervention_types": None,
        "lead_sponsor_classes": None,
        "countries": None,
        "start_year_min": None,
        "start_year_max": None,
        "nct_ids": None,
    }
    return base | values


def test_present_suffix_treats_an_explicit_null_as_absent() -> None:
    plan = _plan(filters=_filters(start_year_min=None))

    assert check_plan(plan, {"filters.start_year_min_present": True}) == [
        "filters.start_year_min: expected a value, got none"
    ]


def test_exactly_suffix_rejects_invented_extras_and_accepts_the_stated_set() -> None:
    stated = _plan(filters=_filters(phases=["PHASE3"], overall_statuses=["COMPLETED"]))
    extra = _plan(filters=_filters(phases=["PHASE2", "PHASE3"]))
    expect = {"filters.phases_exactly": ["phase3"]}

    assert check_plan(stated, expect) == []
    assert check_plan(extra, expect) == [
        "filters.phases: expected exactly ['phase3'], got ['PHASE2', 'PHASE3']"
    ]


def test_exactly_suffix_fails_on_a_missing_list() -> None:
    assert check_plan(_plan(), {"filters.overall_statuses_exactly": ["RECRUITING"]}) == [
        "filters.overall_statuses: missing (expected exactly ['RECRUITING'])"
    ]


def test_distinct_requires_the_listed_slots_to_differ() -> None:
    analysis = make_plan().analysis.model_dump()  # type: ignore[union-attr]
    same = _plan(
        analysis=analysis
        | {
            "kind": "scatter",
            "measure_x": "enrollment",
            "measure_y": "enrollment",
            "group_by": None,
        }
    )
    apart = _plan(
        analysis=analysis
        | {
            "kind": "scatter",
            "measure_x": "enrollment",
            "measure_y": "duration_months",
            "group_by": None,
        }
    )
    expect = {"distinct": ["analysis.measure_x", "analysis.measure_y"]}

    assert check_plan(apart, expect) == []
    assert check_plan(same, expect) == [
        "distinct: analysis.measure_x, analysis.measure_y must differ (got 'enrollment' twice)"
    ]


def test_assumptions_state_value_of_requires_the_concrete_year_in_an_assumption() -> None:
    window = _filters(start_year_min=2021)
    stated = _plan(filters=window, assumptions=["Recent = trials starting in or after 2021."])
    vague = _plan(filters=window, assumptions=["Recent means recent trials."])
    unset = _plan(assumptions=["Recent = trials starting in or after 2021."])
    expect = {"assumptions_state_value_of": "filters.start_year_min"}

    assert check_plan(stated, expect) == []
    assert check_plan(vague, expect) == ["assumptions: none states filters.start_year_min = 2021"]
    assert check_plan(unset, expect) == [
        "assumptions: filters.start_year_min is unset, so no assumption can state it"
    ]
