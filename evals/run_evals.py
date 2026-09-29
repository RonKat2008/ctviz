"""Run the live planner + judge evals and write `evals/report.md` (PLAN.md §16.4, §9.6).

Usage: `uv run python -m evals.run_evals --label v2 [--only ID ...] [--skip-judge]`;
`--judge-only` re-runs just the judge suite live and keeps the saved planner results.
Makes real OpenAI / OpenRouter / ClinicalTrials.gov calls; keys come only from the app's own
`get_settings()`, and nothing here prints or saves them. Scored results are saved to
`evals/results.json`; `--render-only` re-renders the report from them (no network) after failure
explanations are added to `evals/failure_notes.yaml`.
"""

import argparse
import dataclasses
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import yaml
from fastapi.testclient import TestClient

from ctviz.api.app import app
from ctviz.config import Settings, get_settings
from evals.cases import EvalCase, load_cases, load_judge_cases
from evals.judge_scoring import JudgeCaseResult, summarize_judge
from evals.live import JudgeFactory, PlannerFactory, live_factories, record_case, run_judge_case
from evals.report import RunInfo, render_report
from evals.scoring import CaseRun, score_case, summarize_cases
from evals.store import load_results, save_results

EVALS_DIR = Path(__file__).resolve().parent
JUDGE_CONCURRENCY = 3
log = logging.getLogger("evals")


def _args(argv: list[str] | None) -> argparse.Namespace:
    """Command-line options."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", default="current", help="prompt version shown in the report")
    parser.add_argument("--only", nargs="*", default=None, help="run only these planner case ids")
    parser.add_argument("--skip-judge", action="store_true", help="skip the judge-case suite")
    parser.add_argument("--out", type=Path, default=EVALS_DIR / "report.md")
    parser.add_argument("--notes", type=Path, default=EVALS_DIR / "failure_notes.yaml")
    parser.add_argument("--results", type=Path, default=EVALS_DIR / "results.json")
    parser.add_argument("--cases", type=Path, default=EVALS_DIR / "cases.yaml")
    parser.add_argument(
        "--judge-only",
        action="store_true",
        help="re-run only the judge cases live; keep the last run's planner results",
    )
    parser.add_argument("--judge-cases", type=Path, default=EVALS_DIR / "judge_cases.yaml")
    parser.add_argument("--render-only", action="store_true", help="re-render saved results")
    parser.add_argument("--debug-out", type=Path, default=None, help="per-case JSON trace dump")
    return parser.parse_args(argv)


def _live_settings() -> Settings:
    """The app's settings, refusing replay mode or a missing key (never printing any value)."""
    settings = get_settings()
    if settings.planner_mode != "live":
        raise SystemExit("evals need PLANNER_MODE=live")
    if settings.openai_api_key is None or settings.openrouter_api_key is None:
        raise SystemExit("evals need both OPENAI_API_KEY and OPENROUTER_API_KEY configured")
    return settings


def _debug_record(run: CaseRun) -> dict[str, Any]:
    """What a failing case needs for diagnosis: plans, validation trace, error (no chart data)."""
    meta = run.payload.get("meta") or {}
    return {
        "id": run.case.id,
        "http_status": run.http_status,
        "error": run.payload.get("error"),
        "raw_plans": [p.model_dump(mode="json") if p else None for p in run.raw_plans],
        "executed_plan": meta.get("plan"),
        "validation": meta.get("validation"),
        "warnings": meta.get("warnings"),
    }


def _run_planner_cases(
    cases: list[EvalCase], make_planner: PlannerFactory, make_judge: JudgeFactory
) -> tuple[list[CaseRun], list[dict[str, Any]]]:
    """Every planner case, sequentially, through the real app (one pooled CtGovClient)."""
    runs, debug = [], []
    with TestClient(app) as http:
        try:
            for case in cases:
                run = record_case(case, http, make_planner, make_judge)
                runs.append(run)
                debug.append(_debug_record(run))
                outcome = score_case(run).actual_outcome
                log.info("case %s -> %s in %.1fs", case.id, outcome, run.latency_s)
        finally:
            app.dependency_overrides.clear()
    return runs, debug


def _load_notes(path: Path) -> dict[str, str]:
    """Hand-written failure explanations keyed by case id (optional file)."""
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {str(k): str(v) for k, v in data.items()}


def _live_run(args: argparse.Namespace) -> tuple[RunInfo, list[CaseRun], list[JudgeCaseResult]]:
    """Run both suites live; save the runs and judge results (and an optional debug dump)."""
    settings = _live_settings()
    make_planner, make_judge = live_factories(settings)
    started = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    cases = [c for c in load_cases(args.cases) if not args.only or c.id in args.only]
    runs, debug = _run_planner_cases(cases, make_planner, make_judge)
    judged = [] if args.skip_judge else _run_judge_cases(args, make_judge)
    info = RunInfo(
        started,
        settings.planner_model,
        settings.judge_model,
        args.label,
        judge_fallback_model=settings.judge_fallback_model,
    )
    save_results(args.results, info, runs, judged)
    if args.debug_out is not None:
        args.debug_out.write_text(json.dumps(debug, indent=2, default=str), encoding="utf-8")
    return info, runs, judged


def _run_judge_cases(args: argparse.Namespace, make_judge: JudgeFactory) -> list[JudgeCaseResult]:
    """Every judge case live (a few in parallel), scored by the code-side decision."""
    judge_cases = load_judge_cases(args.judge_cases)
    today = date.today()
    with ThreadPoolExecutor(max_workers=JUDGE_CONCURRENCY) as pool:
        return list(pool.map(lambda c: run_judge_case(c, make_judge, today), judge_cases))


def _judge_only_run(
    args: argparse.Namespace,
) -> tuple[RunInfo, list[CaseRun], list[JudgeCaseResult]]:
    """`--judge-only`: re-run just the judge cases live, keep the saved planner results (no
    planner is ever built or called), and merge the fresh judge results into the results file."""
    settings = _live_settings()
    info, runs, _old = _saved_run(args)
    _make_planner, make_judge = live_factories(settings)
    judged = _run_judge_cases(args, make_judge)
    info = dataclasses.replace(
        info, judge_model=settings.judge_model, judge_fallback_model=settings.judge_fallback_model
    )
    save_results(args.results, info, runs, judged)
    return info, runs, judged


def _saved_run(args: argparse.Namespace) -> tuple[RunInfo, list[CaseRun], list[JudgeCaseResult]]:
    """`--render-only`: the saved runs, re-bound to the current case file (no network, no LLM)."""
    cases = {c.id: c for c in load_cases(args.cases)}
    return load_results(args.results, cases)


def main(argv: list[str] | None = None) -> int:
    """Run (or re-render) the evals and write the report; return an exit code."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = _args(argv)
    if args.render_only:
        info, runs, judged = _saved_run(args)
    elif args.judge_only:
        info, runs, judged = _judge_only_run(args)
    else:
        info, runs, judged = _live_run(args)
    info = dataclasses.replace(info, notes=_load_notes(args.notes))
    results = [score_case(run) for run in runs]
    report = render_report(info, results, summarize_cases(results), judged, summarize_judge(judged))
    args.out.write_text(report, encoding="utf-8")
    log.info("wrote %s", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
