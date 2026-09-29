"""Saving eval runs as JSON: every raw planner plan, the executed plan and the scoring facts of
each planner case, plus the scored judge cases -- so a pass is auditable and `--render-only` can
re-score and re-render the report without a single LLM call. Chart data, citations and notes are
never stored (notes always come fresh from `failure_notes.yaml`); no key is ever in a payload.
"""

import dataclasses
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ctviz.schemas.plan import QueryPlan
from evals.cases import EvalCase
from evals.judge_scoring import JudgeCaseResult
from evals.report import RunInfo
from evals.scoring import CaseRun, Usage

_JUDGE_TUPLE_FIELDS = ("failed_checks", "issues")
_META_KEPT = ("plan", "validation")


def _scoring_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Only what scoring reads: ok, error, the executed plan, the trace, citations passed."""
    meta = payload.get("meta")
    kept = None
    if isinstance(meta, dict):
        kept = {k: meta.get(k) for k in _META_KEPT}
        check = meta.get("citation_check")
        kept["citation_check"] = {"passed": check.get("passed")} if check else None
    return {"ok": payload.get("ok"), "error": payload.get("error"), "meta": kept}


def _run_record(run: CaseRun) -> dict[str, Any]:
    """One planner case as JSON: its raw plans, executed plan and scoring facts."""
    payload = _scoring_payload(run.payload)
    return {
        "id": run.case.id,
        "http_status": run.http_status,
        "raw_plans": [p.model_dump(mode="json") if p else None for p in run.raw_plans],
        "executed_plan": (payload["meta"] or {}).get("plan"),
        "payload": payload,
        "usages": [dataclasses.asdict(u) for u in run.usages],
        "judge_calls": run.judge_calls,
        "latency_s": run.latency_s,
    }


def save_results(
    path: Path, info: RunInfo, runs: Sequence[CaseRun], judged: Sequence[JudgeCaseResult]
) -> None:
    """Write one run's provenance, planner-case runs and scored judge cases (no notes, no keys)."""
    run_info = {k: v for k, v in dataclasses.asdict(info).items() if k != "notes"}
    data = {
        "run": run_info,
        "runs": [_run_record(r) for r in runs],
        "judge": [dataclasses.asdict(j) for j in judged],
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _case_run(record: dict[str, Any], cases: Mapping[str, EvalCase]) -> CaseRun:
    """A saved record back as a `CaseRun`, bound to the CURRENT definition of its case."""
    case = cases.get(record["id"])
    if case is None:
        raise ValueError(f"saved case {record['id']!r} is not in the case file; re-run it live")
    plans = tuple(QueryPlan.model_validate(p) if p else None for p in record["raw_plans"])
    return CaseRun(
        case=case,
        http_status=record["http_status"],
        payload=record["payload"],
        raw_plans=plans,
        usages=tuple(Usage(**u) for u in record["usages"]),
        judge_calls=record["judge_calls"],
        latency_s=record["latency_s"],
    )


def _judge_result(data: dict[str, Any]) -> JudgeCaseResult:
    """A saved judge result (JSON lists back into the frozen dataclass's tuples)."""
    fixed: dict[str, Any] = {
        k: tuple(v) if k in _JUDGE_TUPLE_FIELDS else v for k, v in data.items()
    }
    return JudgeCaseResult(**fixed)


def load_results(
    path: Path, cases: Mapping[str, EvalCase]
) -> tuple[RunInfo, list[CaseRun], list[JudgeCaseResult]]:
    """Read back what `save_results` wrote; planner runs re-bind to `cases` for re-scoring."""
    data = json.loads(path.read_text(encoding="utf-8"))
    runs = [_case_run(r, cases) for r in data["runs"]]
    return RunInfo(**data["run"]), runs, [_judge_result(j) for j in data["judge"]]
