"""Overlay tests: pure function, so these are the most valuable tests in Task 4.1 (§6.1)."""

from ctviz.agent.overlay import FieldOverride, apply_overlay
from ctviz.schemas.plan import Comparison
from ctviz.schemas.request import VisualizeRequest
from tests.factories import make_plan


def test_structured_drug_name_becomes_a_structured_search_term() -> None:
    plan = make_plan(search_terms=[])
    request = VisualizeRequest(query="How has this changed?", drug_name="Pembrolizumab")

    new_plan, overrides = apply_overlay(plan, request)

    [term] = new_plan.search_terms
    assert (term.param, term.value, term.source) == (
        "query.intr",
        "Pembrolizumab",
        "structured_field",
    )
    assert overrides == []
    assert plan.search_terms == []  # input plan is not mutated


def test_structured_field_replaces_llm_value_in_the_same_slot_and_is_logged() -> None:
    plan = make_plan(
        filters={
            "phases": None,
            "overall_statuses": None,
            "study_types": None,
            "intervention_types": None,
            "lead_sponsor_classes": None,
            "countries": None,
            "start_year_min": 2018,
            "start_year_max": None,
            "nct_ids": None,
        }
    )
    request = VisualizeRequest(query="since 2018?", start_year=2015)

    new_plan, overrides = apply_overlay(plan, request)

    assert new_plan.filters is not None and new_plan.filters.start_year_min == 2015
    assert overrides[0].field == "start_year" and overrides[0].text_value == "2018"


def test_sponsor_role_any_maps_to_query_spons() -> None:
    new_plan, _ = apply_overlay(
        make_plan(search_terms=[]),
        VisualizeRequest(query="q q", sponsor="Merck", sponsor_role="any"),
    )

    assert new_plan.search_terms[0].param == "query.spons"


def test_sponsor_role_lead_maps_to_query_lead() -> None:
    """Item 5: `sponsor_role="lead"` (the default) maps to `query.lead`, not `query.spons`."""
    new_plan, _ = apply_overlay(
        make_plan(search_terms=[]),
        VisualizeRequest(query="q q", sponsor="Merck", sponsor_role="lead"),
    )

    assert new_plan.search_terms[0].param == "query.lead"


def test_end_year_maps_to_start_year_max_not_min() -> None:
    """Item 5: `end_year` means the latest START year (§6), so it is `start_year_max`."""
    new_plan, _ = apply_overlay(
        make_plan(filters=None), VisualizeRequest(query="through 2020?", end_year=2020)
    )

    assert new_plan.filters is not None
    assert new_plan.filters.start_year_max == 2020
    assert new_plan.filters.start_year_min is None


def test_trial_phase_override_is_logged_as_a_field_override() -> None:
    """Item 5: a structured `trial_phase` that displaces a different planner phase is logged."""
    plan = make_plan(
        filters={
            "phases": ["PHASE1"],
            "overall_statuses": None,
            "study_types": None,
            "intervention_types": None,
            "lead_sponsor_classes": None,
            "countries": None,
            "start_year_min": None,
            "start_year_max": None,
            "nct_ids": None,
        }
    )
    request = VisualizeRequest(query="phase 3 trials?", trial_phase="Phase 3")

    new_plan, overrides = apply_overlay(plan, request)

    assert new_plan.filters is not None and new_plan.filters.phases == ["PHASE3"]
    assert any(o.field == "trial_phase" for o in overrides)


def test_overlay_replaces_llm_search_term_on_same_param_instead_of_keeping_both() -> None:
    """Item 5: the LLM's own term on the same param is replaced, never kept alongside."""
    plan = make_plan(
        search_terms=[
            {
                "param": "query.intr",
                "value": "pembrolizumab",
                "source": "query_text",
                "rationale": "drug",
            }
        ]
    )
    request = VisualizeRequest(query="q q", drug_name="Pembrolizumab")

    new_plan, _ = apply_overlay(plan, request)

    assert len(new_plan.search_terms) == 1
    [term] = new_plan.search_terms
    assert term.source == "structured_field"


def test_overlay_logs_override_when_structured_term_replaces_different_llm_term() -> None:
    """Item 6: a structured field replacing a *different* LLM value on the same param is logged;
    a pure addition (no prior term on that param) is not (§6.1)."""
    plan = make_plan(
        search_terms=[
            {
                "param": "query.intr",
                "value": "Keytruda",
                "source": "query_text",
                "rationale": "drug synonym",
            }
        ]
    )
    request = VisualizeRequest(query="q q", drug_name="Pembrolizumab")

    new_plan, overrides = apply_overlay(plan, request)

    [term] = new_plan.search_terms
    assert term.value == "Pembrolizumab"
    assert overrides == [
        FieldOverride(field="drug_name", text_value="Keytruda", applied_value="Pembrolizumab")
    ]


def test_overlay_does_not_log_a_pure_search_term_addition() -> None:
    """A structured field filling a slot the planner left empty is never logged as an override."""
    plan = make_plan(search_terms=[])
    request = VisualizeRequest(query="q q", drug_name="Pembrolizumab")

    _, overrides = apply_overlay(plan, request)

    assert overrides == []


def test_structured_field_wins_over_comparison_on_same_param_and_logs_override() -> None:
    """Item 7: a structured field targeting the same param as `comparison.vary_param` wins; the
    comparison is dropped and the override is logged (§6.1 ruling: structured fields always win)."""
    plan = make_plan(
        search_terms=[],
        comparison=Comparison(
            vary_param="query.intr", values=["Pembrolizumab", "Nivolumab"]
        ).model_dump(mode="json"),
    )
    request = VisualizeRequest(query="q q", drug_name="Pembrolizumab")

    new_plan, overrides = apply_overlay(plan, request)

    assert new_plan.comparison is None
    assert any(o.field == "comparison" for o in overrides)


def test_comparison_survives_when_no_structured_field_targets_its_param() -> None:
    plan = make_plan(
        search_terms=[],
        comparison=Comparison(
            vary_param="query.intr", values=["Pembrolizumab", "Nivolumab"]
        ).model_dump(mode="json"),
    )
    request = VisualizeRequest(query="q q", condition="glioblastoma")

    new_plan, overrides = apply_overlay(plan, request)

    assert new_plan.comparison is not None
    assert not any(o.field == "comparison" for o in overrides)


def test_options_top_n_overrides_plan_analysis_top_n_and_logs_override() -> None:
    """Item 7: `request.options.top_n` always wins over `plan.analysis.top_n` (§6)."""
    plan = make_plan()
    assert plan.analysis is not None
    plan = plan.model_copy(update={"analysis": plan.analysis.model_copy(update={"top_n": 15})})
    request = VisualizeRequest(query="q q", options={"top_n": 40})

    new_plan, overrides = apply_overlay(plan, request)

    assert new_plan.analysis is not None and new_plan.analysis.top_n == 40
    assert any(o.field == "options.top_n" for o in overrides)


def test_options_top_n_fills_empty_slot_without_logging_an_override() -> None:
    plan = make_plan()
    assert plan.analysis is not None
    plan = plan.model_copy(update={"analysis": plan.analysis.model_copy(update={"top_n": None})})
    request = VisualizeRequest(query="q q", options={"top_n": 40})

    new_plan, overrides = apply_overlay(plan, request)

    assert new_plan.analysis is not None and new_plan.analysis.top_n == 40
    assert overrides == []
