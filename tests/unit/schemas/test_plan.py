from typing import Any

from ctviz.schemas.plan import QueryPlan

MAX_NESTING = 4
EMPTY_ANALYSIS = {
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


def _walk(schema: dict[str, Any], defs: dict[str, Any], depth: int = 0) -> int:
    """Assert strict-mode rules on every object node; return max object nesting depth."""
    if "$ref" in schema:
        return _walk(defs[schema["$ref"].split("/")[-1]], defs, depth)
    depth = max([depth] + [_walk(b, defs, depth) for b in schema.get("anyOf", [])])
    if schema.get("type") == "object":
        props = schema.get("properties", {})
        assert set(schema.get("required", [])) == set(props), "every field must be required"
        assert schema.get("additionalProperties") is False
        return max([depth + 1] + [_walk(p, defs, depth + 1) for p in props.values()])
    if schema.get("type") == "array":
        return _walk(schema.get("items", {}), defs, depth)
    for banned in ("minLength", "maxLength", "pattern", "minimum", "maximum", "format"):
        assert banned not in schema, f"strict mode forbids {banned}"
    return depth


def test_query_plan_schema_is_strict_structured_output_compatible() -> None:
    schema = QueryPlan.strict_json_schema()

    assert _walk(schema, schema.get("$defs", {})) <= MAX_NESTING


def test_out_of_scope_plan_parses_with_nulls() -> None:
    plan = QueryPlan.model_validate(
        {
            "answerable": False,
            "out_of_scope_reason": "Registry has no efficacy data.",
            "suggested_reframing": "Which drugs have the most Phase 3 cancer trials?",
            "interpretation": "Asks which cancer drug is best.",
            "search_terms": [],
            "filters": None,
            "comparison": None,
            "analysis": None,
            "visualization": None,
            "assumptions": [],
        }
    )

    assert plan.answerable is False
    assert plan.analysis is None


def test_comparison_plan_from_the_spec_parses() -> None:
    plan = QueryPlan.model_validate(
        {
            "answerable": True,
            "out_of_scope_reason": None,
            "suggested_reframing": None,
            "interpretation": "Phases for pembrolizumab vs nivolumab.",
            "search_terms": [],
            "filters": None,
            "comparison": {"vary_param": "query.intr", "values": ["pembrolizumab", "nivolumab"]},
            "analysis": {
                "kind": "count_by",
                "group_by": "phase",
                **EMPTY_ANALYSIS,
                "phase_mode": "combined",
            },
            "visualization": {
                "type": "grouped_bar_chart",
                "title": "Trial Phases",
                "rationale": "Two cohorts, one categorical dimension.",
            },
            "assumptions": [],
        }
    )

    assert plan.comparison is not None
    assert plan.comparison.values == ["pembrolizumab", "nivolumab"]
