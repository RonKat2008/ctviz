from ctviz.schemas.enums import AnalysisKind, Dimension, NetworkType, Phase, SearchParam, VizType


def test_phase_values_match_the_api_exactly() -> None:
    assert [p.value for p in Phase] == [
        "NA",
        "EARLY_PHASE1",
        "PHASE1",
        "PHASE2",
        "PHASE3",
        "PHASE4",
    ]


def test_menus_contain_exactly_the_documented_choices() -> None:
    assert {v.value for v in VizType} == {
        "bar_chart",
        "grouped_bar_chart",
        "time_series",
        "scatter_plot",
        "histogram",
        "network_graph",
        "table",
        "metric",
    }
    assert "query.intr" in {p.value for p in SearchParam}
    assert Dimension.PHASE.value == "phase"
    assert {n.value for n in NetworkType} == {"sponsor_drug", "drug_drug", "condition_drug"}
    assert AnalysisKind.TIME_TREND.value == "time_trend"
