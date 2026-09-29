"""Loading the eval case files: planner cases (`cases.yaml`) and labeled judge cases.

Judge cases hold *partial* plans for readability; `plan_from_partial` fills every omitted
nullable slot so the plan validates as the strict `QueryPlan` the planner would have produced.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from ctviz.schemas.plan import Analysis, EnumFilters, QueryPlan

JudgeLabel = Literal["bad", "good"]
_PLAN_DEFAULTS: dict[str, Any] = {
    "answerable": True,
    "out_of_scope_reason": None,
    "suggested_reframing": None,
    "interpretation": "",
    "search_terms": [],
    "filters": None,
    "comparison": None,
    "analysis": None,
    "visualization": None,
    "assumptions": [],
}


@dataclass(frozen=True)
class EvalCase:
    """One planner eval: a request body, plan property assertions and the expected outcome."""

    id: str
    klass: str
    request: dict[str, Any]
    expect: dict[str, Any]
    outcome: str
    planner_calls: tuple[int, ...] | None
    note: str


@dataclass(frozen=True)
class JudgeCase:
    """One labeled judge eval: a request, the planner's raw plan, probe totals and the label."""

    id: str
    category: str
    label: JudgeLabel
    request: dict[str, Any]
    plan: dict[str, Any]
    probe_totals: dict[str, int]
    note: str


def _read_list(path: Path) -> list[dict[str, Any]]:
    """The YAML file's top-level list of mappings."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise ValueError(f"{path}: expected a list of mappings")
    return data


def _require(item: dict[str, Any], keys: tuple[str, ...], path: Path) -> None:
    """Fail fast, naming the case, when a required key is absent."""
    missing = [key for key in keys if key not in item]
    if missing:
        raise ValueError(f"{path}: case {item.get('id', '?')!r} is missing {missing}")


def load_cases(path: Path) -> list[EvalCase]:
    """Every planner eval case in `path`, validated for required keys."""
    cases = []
    for item in _read_list(path):
        _require(item, ("id", "class", "request", "expect", "outcome"), path)
        calls = item.get("planner_calls")
        cases.append(
            EvalCase(
                id=item["id"],
                klass=item["class"],
                request=item["request"],
                expect=item["expect"],
                outcome=item["outcome"],
                planner_calls=tuple(calls) if calls is not None else None,
                note=item.get("note", ""),
            )
        )
    return cases


def load_judge_cases(path: Path) -> list[JudgeCase]:
    """Every labeled judge case in `path`, validated for required keys and label."""
    cases = []
    for item in _read_list(path):
        _require(item, ("id", "category", "label", "request", "plan"), path)
        if item["label"] not in ("bad", "good"):
            raise ValueError(f"{path}: case {item['id']!r} label must be 'bad' or 'good'")
        cases.append(
            JudgeCase(
                id=item["id"],
                category=item["category"],
                label=item["label"],
                request=item["request"],
                plan=item["plan"],
                probe_totals=item.get("probe_totals", {}),
                note=item.get("note", ""),
            )
        )
    return cases


def _filled(node: dict[str, Any] | None, fields: dict[str, Any]) -> dict[str, Any] | None:
    """`node` with every declared field it omits set to null (unknown keys still fail later)."""
    return None if node is None else dict.fromkeys(fields) | node


def plan_from_partial(partial: dict[str, Any]) -> QueryPlan:
    """A strict `QueryPlan` from a partial dict: omitted nullable slots become null."""
    data = _PLAN_DEFAULTS | partial
    terms = [{"source": "query_text", "rationale": ""} | term for term in data["search_terms"]]
    viz = data["visualization"]
    return QueryPlan.model_validate(
        data
        | {
            "search_terms": terms,
            "filters": _filled(data["filters"], EnumFilters.model_fields),
            "analysis": _filled(data["analysis"], Analysis.model_fields),
            "visualization": None if viz is None else {"title": "", "rationale": ""} | viz,
        }
    )
