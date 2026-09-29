"""Independent citation verifier (PLAN.md §11.6): re-derives every datum's membership from the
raw records using only the predicate grammar, NEVER the aggregation code that produced the
response -- so a bug in that code can't also hide from the check meant to catch it.

Independence rule: this package's verifier modules (`verify*.py`, `witness.py`) import only
`citations.pointer`, `citations.predicates`, `common.names` and `schemas` -- never
`ctviz.analysis` (enforced by a fresh-interpreter `sys.modules` test in `test_verify.py`).

The caller supplies each cohort's population explicitly (`CohortPopulation`: the records it
kept after strict match + filter re-check, and the analysis exclusions it declared); nothing is
derived from the response's own citations. Runs on every response (`pipeline.py` calls it
last); any violation raises `CitationCheckError` -> HTTP 500 `CITATION_CHECK_FAILED` (§12.2).
"""

import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from ctviz.citations.verify_checks import (
    CohortScope,
    RawById,
    check_counts,
    check_membership,
    check_pointers,
    check_relevance,
)
from ctviz.citations.verify_data import (
    INTERNAL,
    STRUCTURE,
    CohortPopulation,
    Datum,
    all_data,
    violation,
)
from ctviz.citations.verify_structure import (
    check_cohort,
    check_data_coverage,
    check_network,
)
from ctviz.citations.witness import WitnessIndex
from ctviz.errors import CitationCheckError
from ctviz.schemas.response import CitationCheck, CohortSummary, Meta, VisualizeResponse

__all__ = ["CohortPopulation", "verify_response"]

_GUARDED_ERRORS = (ValueError, KeyError, TypeError)
MS_PER_SECOND = 1000


def _guarded(name: str, check: Callable[[], list[str]]) -> list[str]:
    """Run one check; an unexpected ValueError/KeyError/TypeError is a violation, not a 500
    with a traceback -- the verifier fails closed with a readable reason (§11.6 step 6)."""
    try:
        return check()
    except _GUARDED_ERRORS as exc:
        return [violation(INTERNAL, f"{name} check raised {type(exc).__name__}: {exc}")]


def _cohort_of(datum: Datum, cohorts: Mapping[str, CohortSummary]) -> CohortSummary | None:
    """A datum's own `cohort` row key, or the response's sole cohort."""
    if datum.cohort_label is not None:
        return cohorts.get(datum.cohort_label)
    return next(iter(cohorts.values())) if len(cohorts) == 1 else None


def _scopes(
    meta: Meta,
    data: list[Datum],
    raw_by_id: RawById,
    populations: Mapping[str, CohortPopulation],
) -> dict[str, CohortScope]:
    """One witness index per cohort, over its population plus every record its data cites."""
    cohorts = {c.label: c for c in meta.cohorts}
    scopes: dict[str, CohortScope] = {}
    for label, summary in cohorts.items():
        population = populations.get(label)
        if population is None:
            continue
        cited = {c.nct_id for d in data if _cohort_of(d, cohorts) is summary for c in d.citations}
        ids = (population.kept_ids | cited) & raw_by_id.keys()
        index = WitnessIndex({i: raw_by_id[i] for i in ids})
        scopes[label] = CohortScope(summary, population, index)
    return scopes


def _membership(
    data: list[Datum], scopes: Mapping[str, CohortScope], raw_by_id: RawById, meta: Meta
) -> tuple[list[str], int]:
    """Soundness + completeness for every datum, within its own cohort's scope."""
    cohorts = {c.label: c for c in meta.cohorts}
    found: list[str] = []
    checked = 0
    for datum in data:
        cohort = _cohort_of(datum, cohorts)
        scope = scopes.get(cohort.label) if cohort else None
        if scope is None:
            found.append(violation(STRUCTURE, f"{datum.label}: no cohort/population resolves"))
            continue
        datum_found, datum_checked = check_membership(datum, scope, raw_by_id)
        found += datum_found
        checked += datum_checked
    return found, checked


def _structure(
    meta: Meta, data: list[Datum], populations: Mapping[str, CohortPopulation]
) -> list[str]:
    """Step 5: per-cohort identities, data_coverage agreement, network shape."""
    cohorts = {c.label: c for c in meta.cohorts}
    dimension = (meta.grouping or {}).get("dimension")
    found: list[str] = []
    for label, summary in cohorts.items():
        mine = [d for d in data if _cohort_of(d, cohorts) is summary]
        found += check_cohort(summary, populations.get(label), mine, dimension)
    if len(cohorts) == 1 and (population := populations.get(meta.cohorts[0].label)):
        found += check_data_coverage(meta.data_coverage, population)
    return [*found, *check_network(data)]


def verify_response(
    response: VisualizeResponse,
    raw_by_id: Mapping[str, Mapping[str, Any]],
    populations: Mapping[str, CohortPopulation],
) -> CitationCheck:
    """Independently re-derive every datum's membership from `raw_by_id` and the supplied
    per-cohort `populations`; raise `CitationCheckError(violations)` on any inconsistency."""
    start = time.monotonic()
    meta = response.meta
    if meta is None or response.visualization is None:
        raise CitationCheckError(["structure: no visualization/meta to verify"])
    data = all_data(response.visualization)
    pointer_found, evidence_checked = check_pointers(raw_by_id, data)
    scopes = _scopes(meta, data, raw_by_id, populations)
    member_found, predicates_checked = _membership(data, scopes, raw_by_id, meta)
    violations = [
        *pointer_found,
        *member_found,
        *_guarded("relevance", lambda: check_relevance(data)),
        *_guarded("count", lambda: check_counts(data)),
        *_guarded("structure", lambda: _structure(meta, data, populations)),
    ]
    if violations:
        raise CitationCheckError(violations)
    return CitationCheck(
        mode=meta.citation_policy.mode,
        passed=True,
        citations_checked=sum(len(d.citations) for d in data),
        evidence_checked=evidence_checked,
        predicates_checked=predicates_checked,
        recount_ok=True,
        ms=int((time.monotonic() - start) * MS_PER_SECOND),
    )


def main(argv: Sequence[str] | None = None) -> int:
    """`python -m ctviz.citations.verify <response.json>` (§11.8); delegates to `verify_cli.py`
    so both `-m ctviz.citations` and `-m ctviz.citations.verify` work without an import cycle."""
    from ctviz.citations.verify_cli import main as cli_main  # local: verify_cli imports us

    return cli_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
