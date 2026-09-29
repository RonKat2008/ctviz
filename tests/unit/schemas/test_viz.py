import pytest
from pydantic import TypeAdapter, ValidationError

from ctviz.schemas.viz import NetworkGraph, Visualization

ADAPTER: TypeAdapter[Visualization] = TypeAdapter(Visualization)
ONE_CITATION = [{"nct_id": "NCT00000001", "field": "/a", "excerpt": "x", "evidence": []}]


def test_union_dispatches_on_type() -> None:
    viz = ADAPTER.validate_python(
        {
            "type": "bar_chart",
            "title": "Trials by phase",
            "options": {},
            "encoding": {
                "x": {"field": "phase", "type": "ordinal", "title": "Phase"},
                "y": {"field": "trial_count", "type": "quantitative", "title": "Trials"},
            },
            "data": [
                {"phase": "Phase 3", "trial_count": 1, "predicate": {}, "citations": ONE_CITATION}
            ],
        }
    )

    assert viz.type == "bar_chart"


def test_every_visualization_type_requires_encoding() -> None:
    with pytest.raises(ValidationError, match="encoding"):
        ADAPTER.validate_python({"type": "metric", "title": "t", "data": [], "options": {}})


def test_network_edges_must_join_existing_nodes() -> None:
    with pytest.raises(ValidationError, match="unknown node"):
        NetworkGraph.model_validate(
            {
                "type": "network_graph",
                "title": "t",
                "options": {},
                "encoding": {"node_id": {"field": "id", "type": "nominal"}},
                "data": {
                    "directed": True,
                    "nodes": [
                        {
                            "id": "a",
                            "label": "A",
                            "type": "sponsor",
                            "weight": 1,
                            "predicate": {},
                            "citations": [],
                        }
                    ],
                    "edges": [
                        {
                            "id": "e1",
                            "source": "a",
                            "target": "missing",
                            "type": "sponsor_drug",
                            "weight": 1,
                            "predicate": {},
                            "citations": [],
                        }
                    ],
                },
            }
        )


def test_histogram_open_last_bin_serializes_as_null() -> None:
    viz = ADAPTER.validate_python(
        {
            "type": "histogram",
            "title": "Enrollment",
            "options": {"scale": "log"},
            "encoding": {
                "x": {"field": "bin_start", "type": "quantitative", "bin": True},
                "x2": {"field": "bin_end", "type": "quantitative"},
                "y": {"field": "trial_count", "type": "quantitative"},
            },
            "data": [
                {
                    "bin_start": 5000,
                    "bin_end": None,
                    "bin_label": "≥5000",
                    "trial_count": 0,
                    "predicate": {},
                    "citations": [],
                }
            ],
        }
    )

    assert '"bin_end":null' in viz.model_dump_json()
