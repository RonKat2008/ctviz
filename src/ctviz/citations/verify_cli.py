"""Offline verifier CLI (§11.8): `python -m ctviz.citations.verify <response.json> [--raw PATH]`
re-runs `verify_response` against a shipped example, with no server and no network -- so a
grader never has to trust our code. Exit codes: 0 PASS, 1 FAIL (violations), 2 unusable input.
"""

import argparse
import gzip
import json
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ctviz.citations.predicates import evaluate
from ctviz.citations.verify import CohortPopulation, verify_response
from ctviz.errors import CitationCheckError
from ctviz.schemas.response import CitationCheck, CohortSummary, VisualizeResponse

RAW_SUFFIX = ".raw.json.gz"
RESPONSE_SUFFIX = ".response.json"
EXIT_PASS, EXIT_FAIL, EXIT_BAD_INPUT = 0, 1, 2
RawById = dict[str, Mapping[str, Any]]


class InputError(Exception):
    """An unreadable/malformed input file: reported as one readable line, exit code 2."""


@dataclass(frozen=True)
class CohortFetch:
    """One cohort fetch recorded beside the raw records: its request params and the NCT IDs it
    returned, so an offline run knows which cohort each shipped record actually came from."""

    params: Mapping[str, str]
    nct_ids: frozenset[str]


def _default_raw_path(response_path: Path) -> Path:
    """`NN.response.json` -> `NN.raw.json.gz` beside it (the shipped-example convention)."""
    name = response_path.name
    if name.endswith(RESPONSE_SUFFIX):
        return response_path.with_name(f"{name[: -len(RESPONSE_SUFFIX)]}{RAW_SUFFIX}")
    return response_path.with_suffix(RAW_SUFFIX)


def _read_response(path: Path) -> VisualizeResponse:
    """Parse the response file; unreadable or malformed input is an `InputError`."""
    try:
        return VisualizeResponse.model_validate_json(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise InputError(f"cannot read response file {path}: {exc.strerror}") from exc
    except ValidationError as exc:
        raise InputError(f"{path} is not a valid VisualizeResponse JSON file") from exc


def _read_raw_file(path: Path) -> tuple[RawById, list[CohortFetch]]:
    """A `NN.raw.json.gz` (`{"records": [...], "cohort_fetches": [...]}` or a bare list) as an
    nct_id -> record map plus the recorded per-cohort fetches (empty for older files)."""
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            body = json.load(handle)
        records = body["records"] if isinstance(body, dict) else body
        raw_by_id = {r["protocolSection"]["identificationModule"]["nctId"]: r for r in records}
        fetches = body.get("cohort_fetches", []) if isinstance(body, dict) else []
        return raw_by_id, [CohortFetch(dict(f["params"]), frozenset(f["nct_ids"])) for f in fetches]
    except OSError as exc:
        raise InputError(f"cannot read raw records file {path}: {exc}") from exc
    except (ValueError, KeyError, TypeError) as exc:
        raise InputError(f"{path} is not a gzipped JSON list of study records") from exc


def _single_cohort_population(response: VisualizeResponse, raw_by_id: RawById) -> CohortPopulation:
    """Shipped records minus the match/filter drops `data_coverage` lists; its analysis-stage
    entries are the declared exclusions (exactly what the live pipeline supplied)."""
    assert response.meta is not None and response.meta.data_coverage is not None
    excluded = response.meta.data_coverage.excluded_trials
    dropped = {e.nct_id for e in excluded if e.stage != "analysis"}
    declared = {e.nct_id: e.reason for e in excluded if e.stage == "analysis"}
    return CohortPopulation(frozenset(raw_by_id) - dropped, declared)


def _fetch_for(cohort: CohortSummary, fetches: Iterable[CohortFetch]) -> CohortFetch | None:
    """The recorded fetch whose params carry this cohort's defining value, if one was shipped."""
    wanted = cohort.value.strip().casefold()
    for fetch in fetches:
        if any(str(v).strip().casefold() == wanted for v in fetch.params.values()):
            return fetch
    return None


def _comparison_population(
    cohort: CohortSummary, raw_by_id: RawById, fetches: Sequence[CohortFetch]
) -> CohortPopulation:
    """Records this cohort's own fetch returned that satisfy its base predicate. Older files
    without recorded fetches fall back to every shipped record satisfying the predicate
    (weaker: a record fetched only for another cohort can then leak in)."""
    fetch = _fetch_for(cohort, fetches)
    pool = (
        raw_by_id if fetch is None else {i: raw_by_id[i] for i in fetch.nct_ids if i in raw_by_id}
    )
    kept = frozenset(i for i, raw in pool.items() if evaluate(cohort.base_predicate, raw))
    return CohortPopulation(kept, {})


def comparison_populations(
    cohorts: Sequence[CohortSummary], raw_by_id: RawById, fetches: Sequence[CohortFetch]
) -> dict[str, CohortPopulation]:
    """Per-cohort populations for a multi-cohort response, from the shipped files alone."""
    return {c.label: _comparison_population(c, raw_by_id, fetches) for c in cohorts}


def populations_from_files(
    response: VisualizeResponse, raw_by_id: RawById, fetches: Sequence[CohortFetch] = ()
) -> dict[str, CohortPopulation]:
    """The per-cohort populations an offline run can reconstruct from the shipped files."""
    if response.meta is None:
        return {}
    cohorts = response.meta.cohorts
    if len(cohorts) == 1 and response.meta.data_coverage is not None:
        return {cohorts[0].label: _single_cohort_population(response, raw_by_id)}
    return comparison_populations(cohorts, raw_by_id, fetches)


def _write_report(check: CitationCheck | None, violations: Sequence[str]) -> None:
    """The CLI's PASS/FAIL report; a dedicated print path, not the app's `logging`."""
    if check is not None:
        print(  # noqa: T201
            f"PASS -- {check.citations_checked} citations, {check.evidence_checked} evidence "
            f"items, {check.predicates_checked} predicates checked in {check.ms}ms"
        )
        return
    print(f"FAIL -- {len(violations)} violation(s):")  # noqa: T201
    for line in violations:
        print(f"  - {line}")  # noqa: T201


def _run(args: argparse.Namespace) -> int:
    """Load both files, verify, report; returns the PASS/FAIL exit code."""
    response = _read_response(args.response)
    raw_by_id, fetches = _read_raw_file(args.raw or _default_raw_path(args.response))
    try:
        populations = populations_from_files(response, raw_by_id, fetches)
        check = verify_response(response, raw_by_id, populations)
    except CitationCheckError as exc:
        _write_report(None, exc.violations)
        return EXIT_FAIL
    _write_report(check, [])
    return EXIT_PASS


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point: verify one response file offline, given its raw records."""
    parser = argparse.ArgumentParser(prog="python -m ctviz.citations.verify")
    parser.add_argument("response", type=Path, help="a VisualizeResponse JSON file")
    parser.add_argument(
        "--raw", type=Path, default=None, help="its raw records (defaults to the sibling file)"
    )
    try:
        return _run(parser.parse_args(argv))
    except InputError as exc:
        print(f"error: {exc}", file=sys.stderr)  # noqa: T201
        return EXIT_BAD_INPUT
