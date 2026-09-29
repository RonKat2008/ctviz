"""Generate the five shipped example runs (PLAN.md §20.2) with the REAL pipeline.

Live network use only: this calls OpenAI (planner), OpenRouter (judge) and ClinicalTrials.gov,
so it needs a filled-in `.env`. Run it with `make examples` (or `uv run python
scripts/generate_examples.py [--only N ...] [--out-dir DIR]`).

For each example `NN` it writes, into `examples/`:

    NN.request.json    the request body
    NN.response.json   the full VisualizeResponse the app returned
    NN.raw.json.gz     every raw study record the run fetched (`{"records": [...]}`)
    NN.verify.txt      the output of `python -m ctviz.citations.verify NN.response.json`

and finally `examples/README_snippets.md` with abridged responses (first 3 rows, 2 citations
per datum, `"_elided": N`) ready to paste into the README. The pure helpers (naming, abridging,
merging, rendering) are unit-tested in `tests/test_generate_examples.py`; `main()` is not.
"""

import argparse
import asyncio
import copy
import gzip
import json
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLES_DIR = REPO_ROOT / "examples"
SNIPPETS_NAME = "README_snippets.md"
KEPT_ROWS = 3
KEPT_CITATIONS = 2
ELIDED_KEY = "_elided"
FILE_KINDS = ("request.json", "response.json", "raw.json.gz", "verify.txt")
META_LISTS: tuple[tuple[str, ...], ...] = (
    ("data_coverage", "excluded_trials"),
    ("provenance", "api_requests"),
    ("validation", "trace"),
)


@dataclass(frozen=True)
class Example:
    """One shipped example: its number, the chart type it demonstrates, and its request body."""

    number: int
    chart_type: str
    request: dict[str, Any]


@dataclass(frozen=True)
class ExamplePaths:
    """The four files one example produces."""

    request: Path
    response: Path
    raw: Path
    verify: Path


# PLAN.md §20.2, in order: five different chart types.
EXAMPLES: tuple[Example, ...] = (
    Example(
        1,
        "time_series",
        {
            "query": "How has the number of trials for this drug changed over time?",
            "drug_name": "Pembrolizumab",
        },
    ),
    Example(
        2,
        "histogram",
        {"query": "Distribution of enrollment sizes for Phase 2 psoriasis trials"},
    ),
    Example(
        3,
        "grouped_bar_chart",
        {"query": "Compare phases for trials involving pembrolizumab vs nivolumab"},
    ),
    Example(
        4,
        "network_graph",
        {"query": "Show a network of sponsors and drugs for glioblastoma trials"},
    ),
    Example(
        5,
        "bar_chart",
        {"query": "Which countries have the most recruiting trials for multiple sclerosis?"},
    ),
)


def example_paths(number: int, out_dir: Path) -> ExamplePaths:
    """The `NN.*` file names for example `number` under `out_dir`."""
    if not 1 <= number <= len(EXAMPLES):
        raise ValueError(f"example number must be 1..{len(EXAMPLES)}, got {number}")
    stem = out_dir / f"{number:02d}"
    return ExamplePaths(*(Path(f"{stem}.{kind}") for kind in FILE_KINDS))


def expected_example_files() -> list[str]:
    """Every file name a complete `make examples` run leaves in `examples/` (no snippets file)."""
    names: list[str] = []
    for example in EXAMPLES:
        paths = example_paths(example.number, Path())
        names += [p.name for p in (paths.request, paths.response, paths.raw, paths.verify)]
    return names


def _abridge_list(items: list[Any], keep: int) -> list[Any]:
    """The first `keep` items, then `{"_elided": N}` when N more were dropped."""
    if len(items) <= keep:
        return items
    return [*items[:keep], {ELIDED_KEY: len(items) - keep}]


def _abridge_row(row: Any) -> Any:
    """A data row/node/edge with its `citations` cut to `KEPT_CITATIONS`."""
    if isinstance(row, dict) and isinstance(row.get("citations"), list):
        return {**row, "citations": _abridge_list(row["citations"], KEPT_CITATIONS)}
    return row


def _abridge_rows(rows: list[Any]) -> list[Any]:
    """Keep `KEPT_ROWS` rows (each with abridged citations) and mark the remainder elided."""
    return _abridge_list([_abridge_row(r) for r in rows[:KEPT_ROWS]] + rows[KEPT_ROWS:], KEPT_ROWS)


def _abridge_data(data: Any) -> Any:
    """`visualization.data`: a row list, or a network's `{nodes, edges}` (each abridged)."""
    if isinstance(data, list):
        return _abridge_rows(data)
    if isinstance(data, dict):
        return {k: _abridge_rows(v) if k in ("nodes", "edges") else v for k, v in data.items()}
    return data


def _abridge_meta(meta: dict[str, Any]) -> dict[str, Any]:
    """Shorten the long lists inside `meta` (excluded trials, API request log, trace)."""
    out = copy.deepcopy(meta)
    for *parents, leaf in META_LISTS:
        node: Any = out
        for key in parents:
            node = node.get(key) if isinstance(node, dict) else None
        if isinstance(node, dict) and isinstance(node.get(leaf), list):
            node[leaf] = _abridge_list(node[leaf], KEPT_ROWS)
    return out


def abridge_response(response: dict[str, Any]) -> dict[str, Any]:
    """A README-sized copy of a response (never mutates the input): first 3 rows, 2 citations
    per datum, long `meta` lists shortened; every cut is marked `{"_elided": N}`."""
    out = copy.deepcopy(response)
    viz = out.get("visualization")
    if isinstance(viz, dict) and "data" in viz:
        viz["data"] = _abridge_data(viz["data"])
    if isinstance(out.get("meta"), dict):
        out["meta"] = _abridge_meta(out["meta"])
    return out


def merge_raw_records(fetches: Iterable[Iterable[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Every fetched study once (by NCT ID, first seen wins), across all cohorts of one run."""
    seen: dict[str, dict[str, Any]] = {}
    for records in fetches:
        for record in records:
            nct_id = record["protocolSection"]["identificationModule"]["nctId"]
            seen.setdefault(nct_id, record)
    return list(seen.values())


def verify_argv(paths: ExamplePaths, python: str = sys.executable) -> list[str]:
    """The offline verifier invocation for one example (§11.8)."""
    return [python, "-m", "ctviz.citations.verify", str(paths.response), "--raw", str(paths.raw)]


def write_json(path: Path, body: Any) -> None:
    """UTF-8, 2-space-indented JSON with a trailing newline (stable, diff-friendly)."""
    path.write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_raw(
    path: Path,
    records: list[dict[str, Any]],
    cohort_fetches: Sequence[dict[str, Any]] = (),
) -> None:
    """The gzipped `{"records": [...], "cohort_fetches": [...]}` file the offline verifier reads;
    `cohort_fetches` ({params, nct_ids} per fetch) lets it rebuild each comparison cohort."""
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(
            {"records": records, "cohort_fetches": list(cohort_fetches)}, handle, ensure_ascii=False
        )


def render_snippets(entries: Sequence[tuple[Example, dict[str, Any], str | None]]) -> str:
    """Markdown for `examples/README_snippets.md`: per example the request, the verify line
    and the abridged response, linking to the full files."""
    parts = [
        "<!-- Generated by scripts/generate_examples.py; "
        "paste into the README examples section. -->\n"
    ]
    for example, response, verify_text in entries:
        p = example_paths(example.number, Path("examples"))
        verdict = (verify_text or "not verified").strip().splitlines()[0:1]
        parts.append(
            f"## Example {example.number} -- `{example.chart_type}`\n\n"
            f"Request (`{p.request.name}`):\n\n"
            f"```json\n{json.dumps(example.request, indent=2)}\n```\n\n"
            f"Offline verifier (`{p.verify.name}`): `{verdict[0] if verdict else 'no output'}`\n\n"
            f"Abridged response (full: `{p.response.name}`, raw records: `{p.raw.name}`):\n\n"
            "```json\n"
            f"{json.dumps(abridge_response(response), indent=2, ensure_ascii=False)}\n```\n"
        )
    return "\n".join(parts)


# --- live part (untested: needs real keys and network) -------------------------------------


def _recording_client_class() -> type:
    """`CtGovClient` subclass that remembers every record `fetch_all` returns."""
    from ctviz.ctgov.client import CtGovClient, FetchResult

    class RecordingCtGovClient(CtGovClient):
        """Captures each cohort's fetched records so they can ship as `NN.raw.json.gz`."""

        def __init__(self) -> None:
            super().__init__()
            self.fetches: list[list[dict[str, Any]]] = []
            self.cohort_fetches: list[dict[str, Any]] = []

        async def fetch_all(self, params: dict[str, str], max_records: int) -> FetchResult:
            result = await super().fetch_all(params, max_records)
            self.fetches.append(list(result.records))
            ids = [r["protocolSection"]["identificationModule"]["nctId"] for r in result.records]
            self.cohort_fetches.append({"params": dict(params), "nct_ids": ids})
            return result

    return RecordingCtGovClient


async def _run_one(
    example: Example, planner: Any, judge: Any
) -> tuple[dict[str, Any], list[Any], list[dict[str, Any]]]:
    """Run the real pipeline for one request; returns (response dict, raw records, fetches)."""
    from ctviz.pipeline import run_pipeline
    from ctviz.schemas.request import VisualizeRequest

    client = _recording_client_class()()
    try:
        body = VisualizeRequest.model_validate(example.request)
        today = datetime.now(UTC).date()
        response = await run_pipeline(
            body, planner=planner, judge=judge, client=client, today=today
        )
    finally:
        await client.aclose()
    records = merge_raw_records(client.fetches)
    return response.model_dump(mode="json"), records, client.cohort_fetches


def _verify(paths: ExamplePaths) -> str:
    """Run the offline verifier CLI and return its output plus the exit code."""
    done = subprocess.run(
        verify_argv(paths), capture_output=True, text=True, check=False, cwd=REPO_ROOT
    )
    return f"{done.stdout}{done.stderr}exit code: {done.returncode}\n"


def _load_entries(out_dir: Path) -> list[tuple[Example, dict[str, Any], str | None]]:
    """Every example already on disk (so a partial `--only` run keeps the whole snippets file)."""
    entries: list[tuple[Example, dict[str, Any], str | None]] = []
    for example in EXAMPLES:
        paths = example_paths(example.number, out_dir)
        if paths.response.exists():
            verify = paths.verify.read_text(encoding="utf-8") if paths.verify.exists() else None
            entries.append((example, json.loads(paths.response.read_text("utf-8")), verify))
    return entries


async def _generate(numbers: Sequence[int], out_dir: Path) -> int:
    """Generate the chosen examples; returns the number of failed ones."""
    from ctviz.agent.judge import build_judge
    from ctviz.agent.planner import build_planner
    from ctviz.config import get_settings

    settings = get_settings()
    if settings.planner_mode != "live":
        print("PLANNER_MODE must be 'live' to generate examples", file=sys.stderr)  # noqa: T201
        return len(numbers)
    planner, judge = build_planner(settings), build_judge(settings)
    out_dir.mkdir(parents=True, exist_ok=True)
    failures = 0
    for example in (e for e in EXAMPLES if e.number in numbers):
        paths = example_paths(example.number, out_dir)
        print(f"[{example.number}] {example.chart_type}: {example.request['query']}")  # noqa: T201
        response, records, cohort_fetches = await _run_one(example, planner, judge)
        write_json(paths.request, example.request)
        write_json(paths.response, response)
        write_raw(paths.raw, records, cohort_fetches)
        verify_text = _verify(paths)
        paths.verify.write_text(verify_text, encoding="utf-8")
        got = (response.get("visualization") or {}).get("type")
        healthy = response["ok"] and got == example.chart_type and "exit code: 0" in verify_text
        failures += 0 if healthy else 1
        print(f"    ok={response['ok']} type={got} {verify_text.splitlines()[0]}")  # noqa: T201
    (out_dir / SNIPPETS_NAME).write_text(render_snippets(_load_entries(out_dir)), encoding="utf-8")
    return failures


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: exit 0 only if every requested example is ok, the right type, and verifies."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--only", type=int, nargs="+", default=[e.number for e in EXAMPLES])
    parser.add_argument("--out-dir", type=Path, default=EXAMPLES_DIR)
    args = parser.parse_args(argv)
    return 1 if asyncio.run(_generate(args.only, args.out_dir)) else 0


if __name__ == "__main__":
    raise SystemExit(main())
