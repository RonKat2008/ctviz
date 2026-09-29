"""Deterministic plan checks (§7.4 checks 3-8 + the §7.3 matrix) and the digit guard (check 9)."""

from datetime import date
from typing import Any

import pytest

from ctviz.agent.plan_checks import (
    check_executable,
    check_plan,
    coerce_plan_countries,
    guard_text,
    normalize_plan,
)
from ctviz.schemas.plan import QueryPlan
from ctviz.schemas.request import VisualizeRequest
from tests.factories import make_plan

TODAY = date(2026, 9, 29)
_NO_ANALYSIS_FIELDS: dict[str, Any] = {
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
}
_NO_FILTERS: dict[str, Any] = {
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


def _analysis(kind: str, **fields: Any) -> dict[str, Any]:
    """An `Analysis` dict of `kind` with every optional field null unless given."""
    return {"kind": kind, **_NO_ANALYSIS_FIELDS, **fields}


def _viz(viz_type: str, title: str = "A title") -> dict[str, Any]:
    """A `VizChoice` dict."""
    return {"type": viz_type, "title": title, "rationale": "fits"}


def _filters(**fields: Any) -> dict[str, Any]:
    """An `EnumFilters` dict with every field null unless given."""
    return {**_NO_FILTERS, **fields}


def _term(param: str, value: str, source: str = "query_text") -> dict[str, Any]:
    """A `SearchTerm` dict."""
    return {"param": param, "value": value, "source": source, "rationale": "entity"}


def _time_trend_plan(viz_type: str) -> QueryPlan:
    return make_plan(
        analysis=_analysis("time_trend", time_field="start_date", granularity="year"),
        visualization=_viz(viz_type),
    )


# --- check_plan: happy path -------------------------------------------------------------------


def test_check_plan_returns_no_errors_for_a_valid_count_by_plan() -> None:
    # Arrange
    plan = make_plan()

    # Act
    errors = check_plan(plan, TODAY)

    # Assert
    assert errors == []


# --- §7.3 compatibility matrix ----------------------------------------------------------------


def test_time_trend_requires_time_series_or_bar() -> None:
    # Arrange
    histogram_viz = _time_trend_plan("histogram")

    # Act
    rejected = check_plan(histogram_viz, TODAY)
    accepted = [check_plan(_time_trend_plan(v), TODAY) for v in ("time_series", "bar_chart")]

    # Assert
    assert any("time_trend" in e and "histogram" in e for e in rejected)
    assert accepted == [[], []]


def test_check_plan_requires_time_field_and_granularity_for_time_trend() -> None:
    plan = make_plan(analysis=_analysis("time_trend"), visualization=_viz("time_series"))

    errors = check_plan(plan, TODAY)

    assert "time_trend requires analysis.time_field" in errors
    assert "time_trend requires analysis.granularity" in errors


def test_check_plan_rejects_grouped_bar_without_comparison_or_series_by() -> None:
    plan = make_plan(visualization=_viz("grouped_bar_chart"))

    errors = check_plan(plan, TODAY)

    assert any("grouped_bar_chart" in e for e in errors)


def test_check_plan_accepts_grouped_bar_with_series_by() -> None:
    plan = make_plan(
        analysis=_analysis("count_by", group_by="phase", series_by="overall_status"),
        visualization=_viz("grouped_bar_chart"),
    )

    assert check_plan(plan, TODAY) == []


def test_check_plan_rejects_bar_chart_for_a_comparison() -> None:
    plan = make_plan(
        search_terms=[],
        comparison={"vary_param": "query.intr", "values": ["Pembrolizumab", "Nivolumab"]},
    )

    errors = check_plan(plan, TODAY)

    assert any("bar_chart requires exactly one cohort" in e for e in errors)


def test_check_plan_requires_group_by_for_count_by() -> None:
    plan = make_plan(analysis=_analysis("count_by"))

    assert "count_by requires analysis.group_by" in check_plan(plan, TODAY)


def test_check_plan_requires_measure_x_for_histogram() -> None:
    plan = make_plan(analysis=_analysis("histogram"), visualization=_viz("histogram"))

    assert "histogram requires analysis.measure_x" in check_plan(plan, TODAY)


def test_check_plan_requires_both_measures_for_scatter() -> None:
    plan = make_plan(
        analysis=_analysis("scatter", measure_x="enrollment"), visualization=_viz("scatter_plot")
    )

    assert "scatter requires analysis.measure_y" in check_plan(plan, TODAY)


def test_check_plan_requires_network_type_and_network_graph_for_network() -> None:
    plan = make_plan(analysis=_analysis("network"), visualization=_viz("bar_chart"))

    errors = check_plan(plan, TODAY)

    assert "network requires analysis.network_type" in errors
    assert any("network" in e and "bar_chart" in e for e in errors)


def test_check_plan_rejects_an_unrelated_analysis_field() -> None:
    plan = make_plan(analysis=_analysis("count_by", group_by="phase", measure_x="enrollment"))

    errors = check_plan(plan, TODAY)

    assert errors == ["count_by must leave analysis.measure_x null"]


def test_trial_lookup_requires_nct_ids() -> None:
    # Arrange
    without_ids = make_plan(analysis=_analysis("trial_lookup"), visualization=_viz("table"))
    with_ids = make_plan(
        analysis=_analysis("trial_lookup"),
        visualization=_viz("table"),
        filters=_filters(nct_ids=["NCT04368728"]),
    )

    # Act / Assert
    assert "trial_lookup requires filters.nct_ids" in check_plan(without_ids, TODAY)
    assert check_plan(with_ids, TODAY) == []


def test_check_plan_trial_list_needs_nct_ids_or_a_search_term() -> None:
    no_terms = make_plan(
        search_terms=[], analysis=_analysis("trial_list"), visualization=_viz("table")
    )
    with_term = make_plan(analysis=_analysis("trial_list"), visualization=_viz("table"))

    assert any("trial_list requires" in e for e in check_plan(no_terms, TODAY))
    assert check_plan(with_term, TODAY) == []


def test_check_plan_requires_analysis_and_visualization_when_answerable() -> None:
    plan = make_plan(analysis=None, visualization=None)

    errors = check_plan(plan, TODAY)

    assert "an answerable plan requires analysis" in errors
    assert "an answerable plan requires visualization" in errors


# --- comparison (check 7) ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "values",
    [
        ["Pembrolizumab"],
        ["A", "B", "C", "D", "E"],
        ["Pembrolizumab", "pembrolizumab "],
    ],
    ids=["one", "five", "case-insensitive-duplicate"],
)
def test_comparison_needs_two_to_four_distinct_values(values: list[str]) -> None:
    plan = make_plan(
        search_terms=[],
        comparison={"vary_param": "query.intr", "values": values},
        visualization=_viz("grouped_bar_chart"),
    )

    errors = check_plan(plan, TODAY)

    assert any("2-4 distinct values" in e for e in errors)


def test_check_plan_rejects_a_comparison_varying_a_fixed_search_param() -> None:
    plan = make_plan(
        comparison={"vary_param": "query.intr", "values": ["Pembrolizumab", "Nivolumab"]},
        visualization=_viz("grouped_bar_chart"),
    )

    errors = check_plan(plan, TODAY)

    assert any("query.intr is also a fixed search term" in e for e in errors)


# --- deterministic normalization (fix D) -----------------------------------------------------


def test_normalize_drops_a_fixed_term_that_duplicates_a_compared_value() -> None:
    """Fix D (live: pembro vs nivo revised for "query.intr is also a fixed search term")."""
    plan = make_plan(
        comparison={"vary_param": "query.intr", "values": ["Pembrolizumab", "Nivolumab"]},
        visualization=_viz("grouped_bar_chart"),
    )

    normalized, notes = normalize_plan(plan)

    assert normalized.search_terms == []
    assert check_plan(normalized, TODAY) == []
    assert notes == [
        "search term query.intr='pembrolizumab' dropped: it is one of the compared values "
        "(the comparison varies query.intr)"
    ]
    assert plan.search_terms  # the input plan is never mutated


def test_normalize_keeps_a_genuinely_conflicting_fixed_term_so_the_check_still_fires() -> None:
    plan = make_plan(
        search_terms=[_term("query.intr", "carboplatin")],
        comparison={"vary_param": "query.intr", "values": ["Pembrolizumab", "Nivolumab"]},
        visualization=_viz("grouped_bar_chart"),
    )

    normalized, notes = normalize_plan(plan)

    assert normalized == plan and notes == []
    assert any("query.intr is also a fixed search term" in e for e in check_plan(normalized, TODAY))


def test_normalize_nulls_analysis_fields_irrelevant_to_the_kind() -> None:
    """Fix D (§7.4-6 "unrelated ones are null"): nulled in code, disclosed, no revise."""
    plan = make_plan(analysis=_analysis("count_by", group_by="phase", measure_x="enrollment"))

    normalized, notes = normalize_plan(plan)

    assert normalized.analysis is not None and normalized.analysis.measure_x is None
    assert normalized.analysis.group_by == "phase"
    assert notes == ["analysis.measure_x set to null: count_by does not use it"]
    assert check_plan(normalized, TODAY) == []


def test_normalize_still_coerces_country_aliases() -> None:
    plan = make_plan(filters=_filters(countries=["USA"]))

    normalized, notes = normalize_plan(plan)

    assert normalized.filters is not None and normalized.filters.countries == ["United States"]
    assert len(notes) == 1 and "United States" in notes[0]


# --- values (check 3) -------------------------------------------------------------------------


@pytest.mark.parametrize("syntax", ["AREA[Phase]PHASE3", "RANGE[2015,MAX]", "expansion[None]x"])
def test_essie_syntax_in_values_is_rejected(syntax: str) -> None:
    # Arrange
    in_term = make_plan(search_terms=[_term("query.cond", syntax)])
    in_comparison = make_plan(
        search_terms=[],
        comparison={"vary_param": "query.cond", "values": ["Asthma", syntax]},
        visualization=_viz("grouped_bar_chart"),
    )

    # Act / Assert
    assert any("Essie syntax" in e for e in check_plan(in_term, TODAY))
    assert any("Essie syntax" in e for e in check_plan(in_comparison, TODAY))


@pytest.mark.parametrize("value", ["   ", "x" * 121])
def test_check_plan_rejects_blank_or_overlong_values(value: str) -> None:
    plan = make_plan(search_terms=[_term("query.cond", value)])

    assert any("1-120 characters" in e for e in check_plan(plan, TODAY))


def test_check_plan_rejects_a_value_the_compiler_would_clean_to_empty() -> None:
    plan = make_plan(search_terms=[_term("query.cond", '"[]"')])

    assert any("cleaned to empty" in e for e in check_plan(plan, TODAY))


# --- years (check 4), countries (check 5), top_n (check 8), nct ids ---------------------------


@pytest.mark.parametrize(
    ("low", "high"),
    [(1899, None), (None, 2032), (2020, 2015)],
    ids=["before-1900", "past-today-plus-5", "min-after-max"],
)
def test_check_plan_rejects_out_of_range_years(low: int | None, high: int | None) -> None:
    plan = make_plan(filters=_filters(start_year_min=low, start_year_max=high))

    assert any("1900 <= start_year_min <= start_year_max" in e for e in check_plan(plan, TODAY))


def test_check_plan_accepts_years_up_to_today_plus_five() -> None:
    plan = make_plan(filters=_filters(start_year_min=1900, start_year_max=2031))

    assert check_plan(plan, TODAY) == []


def test_check_plan_rejects_an_unknown_country() -> None:
    plan = make_plan(filters=_filters(countries=["United States", "Atlantis"]))

    assert check_plan(plan, TODAY) == [
        "unknown country (not a ClinicalTrials.gov spelling): 'Atlantis'"
    ]


@pytest.mark.parametrize(("top_n", "is_ok"), [(2, False), (3, True), (50, True), (51, False)])
def test_check_plan_bounds_top_n_between_3_and_50(top_n: int, is_ok: bool) -> None:
    plan = make_plan(analysis=_analysis("count_by", group_by="phase", top_n=top_n))

    errors = check_plan(plan, TODAY)

    assert (errors == []) is is_ok


def test_check_plan_rejects_a_malformed_nct_id_in_filters() -> None:
    plan = make_plan(filters=_filters(nct_ids=["NCT123"]))

    assert any("NCT123" in e for e in check_plan(plan, TODAY))


def test_coerce_plan_countries_maps_aliases_and_notes_each_change() -> None:
    # Arrange
    plan = make_plan(filters=_filters(countries=["USA", "Atlantis", "France"]))

    # Act
    coerced, notes = coerce_plan_countries(plan)

    # Assert
    assert coerced.filters is not None
    assert coerced.filters.countries == ["United States", "Atlantis", "France"]
    assert notes == ["country 'USA' -> 'United States' (canonical spelling)"]
    assert plan.filters is not None and plan.filters.countries == ["USA", "Atlantis", "France"]


def test_coerce_plan_countries_is_a_no_op_without_countries() -> None:
    plan = make_plan()

    coerced, notes = coerce_plan_countries(plan)

    assert coerced is plan
    assert notes == []


# --- digit guard (check 9) --------------------------------------------------------------------


def test_guard_text_keeps_a_phase_number_from_the_phase_filter() -> None:
    # Arrange
    plan = make_plan(
        search_terms=[_term("query.lead", "Merck")],
        filters=_filters(phases=["PHASE3"]),
        analysis=_analysis("count_by", group_by="overall_status"),
        visualization=_viz("bar_chart", "Merck Phase 3 Trials by Status"),
    )
    request = VisualizeRequest(query="How are Merck's late-stage trials split by status?")

    # Act
    guarded, notes = guard_text(plan, request)

    # Assert
    assert guarded.visualization is not None
    assert guarded.visualization.title == "Merck Phase 3 Trials by Status"
    assert notes == []


def test_guard_text_keeps_a_number_inside_a_search_term_value() -> None:
    plan = make_plan(
        search_terms=[_term("query.cond", "Type 2 Diabetes")],
        analysis=_analysis("count_by", group_by="intervention_type"),
        visualization=_viz("bar_chart", "Intervention Types in Type 2 Diabetes Trials"),
    )
    request = VisualizeRequest(query="What intervention types are tested for T2D?")

    guarded, notes = guard_text(plan, request)

    assert guarded == plan
    assert notes == []


def test_guard_text_keeps_a_number_that_appears_verbatim_in_the_query() -> None:
    plan = make_plan(
        search_terms=[_term("query.cond", "coronavirus")],
        visualization=_viz("bar_chart", "COVID-19 Vaccine Trials"),
    )
    request = VisualizeRequest(query="COVID-19 vaccine trials by phase")

    guarded, notes = guard_text(plan, request)

    assert guarded == plan
    assert notes == []


def test_guard_text_replaces_a_title_with_an_unsupported_number_by_the_template() -> None:
    # Arrange
    plan = make_plan(
        filters=_filters(start_year_min=2015),
        visualization=_viz("bar_chart", "Trials rose 45% since 2015"),
    )
    request = VisualizeRequest(query="Pembrolizumab trials by phase since 2015")

    # Act
    guarded, notes = guard_text(plan, request)

    # Assert
    assert guarded.visualization is not None
    assert guarded.visualization.title == "Trial count by phase: pembrolizumab"
    assert notes == [
        "title 'Trials rose 45% since 2015' -> 'Trial count by phase: pembrolizumab' "
        "(unsupported number(s): 45)"
    ]


def test_guard_text_replaces_an_interpretation_with_a_code_rendered_sentence() -> None:
    plan = make_plan(interpretation="About 300 pembrolizumab trials, by phase.")
    request = VisualizeRequest(query="Pembrolizumab trials by phase")

    guarded, notes = guard_text(plan, request)

    assert guarded.interpretation == "Trial count by phase for pembrolizumab."
    assert notes == [
        "interpretation replaced by a code-rendered sentence (unsupported number(s): 300)"
    ]


def test_guard_text_drops_only_the_assumption_sentence_with_an_unsupported_number() -> None:
    # Arrange
    plan = make_plan(
        assumptions=[
            "Recent means since 2019. Roughly 12 trials are withdrawn.",
            "Phases are combined.",
        ]
    )
    request = VisualizeRequest(query="Recent pembrolizumab trials since 2019 by phase")

    # Act
    guarded, notes = guard_text(plan, request)

    # Assert
    assert guarded.assumptions == ["Recent means since 2019.", "Phases are combined."]
    assert notes == [
        "assumption sentence dropped: 'Roughly 12 trials are withdrawn.' "
        "(unsupported number(s): 12)"
    ]


def test_guard_text_drops_an_assumption_whose_every_sentence_is_unsupported() -> None:
    plan = make_plan(assumptions=["About 40% are recruiting."])
    request = VisualizeRequest(query="Pembrolizumab trials by phase")

    guarded, _notes = guard_text(plan, request)

    assert guarded.assumptions == []


def test_guard_text_allows_structured_values_nct_ids_top_n_and_phase_labels() -> None:
    # Arrange
    plan = make_plan(
        interpretation="Top 12 conditions for NCT04368728 in France, 2018 onward.",
        filters=_filters(nct_ids=["NCT04368728"], start_year_min=2018, countries=["France"]),
        analysis=_analysis("count_by", group_by="phase", top_n=12),
        assumptions=["Phase 1/2 trials count toward both Phase 1 and Phase 2."],
    )
    request = VisualizeRequest(query="Conditions for this trial", start_year=2018)

    # Act
    guarded, notes = guard_text(plan, request)

    # Assert
    assert guarded == plan
    assert notes == []


def test_guard_text_template_names_a_comparison_and_never_mutates_its_input() -> None:
    # Arrange
    plan = make_plan(
        search_terms=[],
        comparison={"vary_param": "query.intr", "values": ["Pembrolizumab", "Nivolumab"]},
        visualization=_viz("grouped_bar_chart", "7 key differences"),
    )
    before = plan.model_dump()
    request = VisualizeRequest(query="Compare pembrolizumab vs nivolumab by phase")

    # Act
    guarded, _notes = guard_text(plan, request)

    # Assert
    assert guarded.visualization is not None
    assert guarded.visualization.title == "Trial count by phase: Pembrolizumab vs Nivolumab"
    assert plan.model_dump() == before


@pytest.mark.parametrize(
    ("analysis", "expected"),
    [
        (
            _analysis("time_trend", time_field="start_date", granularity="year"),
            "Trial count by start date: All trials",
        ),
        (_analysis("histogram", measure_x="enrollment"), "Trial count by enrollment: All trials"),
        (
            _analysis("scatter", measure_x="enrollment", measure_y="duration_months"),
            "Duration months by enrollment: All trials",
        ),
        (_analysis("network", network_type="sponsor_drug"), "Network by sponsor drug: All trials"),
        (_analysis("trial_lookup"), "Trials by NCT ID: All trials"),
    ],
    ids=["time_trend", "histogram", "scatter", "network", "trial_lookup"],
)
def test_guard_text_template_covers_every_analysis_kind(
    analysis: dict[str, Any], expected: str
) -> None:
    plan = make_plan(
        search_terms=[], analysis=analysis, visualization=_viz("table", "Up 99 percent")
    )
    request = VisualizeRequest(query="Show me the landscape")

    guarded, _notes = guard_text(plan, request)

    assert guarded.visualization is not None
    assert guarded.visualization.title == expected


def test_guard_text_leaves_a_plan_without_visualization_or_analysis_alone() -> None:
    plan = make_plan(analysis=None, visualization=None, interpretation="Nothing numeric here.")
    request = VisualizeRequest(query="Which drug is best?")

    guarded, notes = guard_text(plan, request)

    assert guarded == plan
    assert notes == []


# --- executability (beyond §7.4: what this build's executor can actually run) -----------------


@pytest.mark.parametrize("kind", ["trial_list"])
def test_check_executable_rejects_a_kind_the_executor_cannot_run_yet(kind: str) -> None:
    # Arrange
    plan = make_plan(
        analysis=_analysis(kind),
        visualization=_viz("table"),
        filters=_filters(nct_ids=["NCT04368728"]),
    )

    # Act
    errors = check_executable(plan)

    # Assert
    assert check_plan(plan, TODAY) == []  # the §7.3 matrix allows it...
    assert errors == [  # ...but the executor can't run it, so the planner is asked to revise
        f"analysis.kind={kind} is not executable yet; use count_by with a table instead "
        "(e.g. group_by=overall_status)"
    ]


def test_check_executable_accepts_every_kind_the_pipeline_dispatches() -> None:
    from ctviz.agent.plan_checks import EXECUTABLE_KINDS

    plans = [
        make_plan(
            analysis=_analysis(kind),
            visualization=_viz("table" if kind == "trial_lookup" else "bar_chart"),
        )
        for kind in sorted(EXECUTABLE_KINDS)
    ]

    assert [check_executable(p) for p in plans] == [[] for _ in plans]
    assert check_executable(make_plan(analysis=None)) == []


def test_trial_lookup_executes_as_the_key_facts_table_but_never_as_a_metric() -> None:
    """Fix G: §8.4's key-facts table is the one trial_lookup shape this build can run."""
    lookup = {"analysis": _analysis("trial_lookup"), "filters": _filters(nct_ids=["NCT04368728"])}

    assert check_executable(make_plan(**lookup, visualization=_viz("table"))) == []
    assert check_executable(make_plan(**lookup, visualization=_viz("metric"))) == [
        "trial_lookup is executable as a table only"
    ]


def test_coerce_plan_countries_returns_the_same_plan_when_every_country_is_canonical() -> None:
    plan = make_plan(filters=_filters(countries=["United States", "France"]))

    coerced, notes = coerce_plan_countries(plan)

    assert coerced is plan
    assert notes == []


def test_allowed_numbers_without_analysis_uses_only_request_and_plan_values() -> None:
    from ctviz.agent.digit_guard import allowed_numbers

    plan = make_plan(analysis=None, filters=_filters(start_year_min=2015))
    request = VisualizeRequest(query="COVID-19 trials")

    assert allowed_numbers(plan, request) == frozenset({"19", "2015"})
