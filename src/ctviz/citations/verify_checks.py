"""Per-datum verifier checks (§11.6 steps 1-4, plus the count rule of §11.2): pointer/excerpt,
soundness against `base_predicate ∧ datum.predicate`, relevance of bucket evidence, the
independent witness-set recount, and `trial_count`/`weight` == |citations| == |distinct ids|.

Imports only `citations.*` and `schemas` -- never `ctviz.analysis`."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ctviz.citations.pointer import json_text, resolve_pointer
from ctviz.citations.predicates import referenced_paths
from ctviz.citations.verify_data import (
    COMPLETENESS,
    COUNT,
    POINTER,
    PREDICATE,
    RELEVANCE,
    SOUNDNESS,
    CohortPopulation,
    Datum,
    violation,
)
from ctviz.citations.witness import WitnessIndex
from ctviz.schemas.citations import Citation, Predicate
from ctviz.schemas.response import CohortSummary

RawById = Mapping[str, Mapping[str, Any]]
PREDICATE_ERRORS = (ValueError, KeyError, TypeError)
SHOWN_IDS = 5  # how many offending ids a completeness violation lists


def _check_field(label: str, nct_id: str, path: str, excerpt: str, raw: Mapping[str, Any]) -> str:
    """'' when `path` resolves in `raw` to exactly `excerpt`, else a pointer violation."""
    try:
        text = json_text(resolve_pointer(raw, path))
    except (KeyError, IndexError, TypeError) as exc:
        return violation(POINTER, f"{label}: {nct_id} field {path!r} does not resolve: {exc}")
    if text != excerpt:
        return violation(
            POINTER, f"{label}: {nct_id} field {path!r} = {text!r}, cited as {excerpt!r}"
        )
    return ""


def check_pointers(raw_by_id: RawById, data: list[Datum]) -> tuple[list[str], int]:
    """Step 1: every field/evidence pointer resolves, and its exact text equals the excerpt."""
    found: list[str] = []
    checked = 0
    for datum in data:
        for c in datum.citations:
            raw = raw_by_id.get(c.nct_id)
            if raw is None:
                found.append(violation(POINTER, f"{datum.label}: {c.nct_id} has no raw record"))
                continue
            items = [(c.field, c.excerpt), *((e.field, e.excerpt) for e in c.evidence)]
            found += [v for f, x in items if (v := _check_field(datum.label, c.nct_id, f, x, raw))]
            checked += len(items)
    return found, checked


def _under(path: str, roots: set[str]) -> bool:
    """True when `path` is `root` itself or lies beneath it, for some root."""
    return any(path == root or path.startswith(f"{root}/") for root in roots)


def _bucket_fields(citation: Citation) -> list[str]:
    """The primary field plus every `bucket`-role item: the claims of membership in this datum
    (`match`/`filter`/`context` items support other things and are not held to relevance)."""
    return [citation.field, *(e.field for e in citation.evidence if e.role == "bucket")]


def check_relevance(data: list[Datum]) -> list[str]:
    """Step 3: every bucket pointer lies under a path the datum's predicate reads."""
    found: list[str] = []
    for datum in data:
        if datum.predicate is None:
            continue
        try:
            roots = referenced_paths(datum.predicate)
        except PREDICATE_ERRORS as exc:
            found.append(violation(PREDICATE, f"{datum.label}: predicate is malformed: {exc}"))
            continue
        for c in datum.citations:
            found += [
                violation(
                    RELEVANCE,
                    f"{datum.label}: {c.nct_id} bucket field {f!r} is not "
                    "under any path the predicate references",
                )
                for f in _bucket_fields(c)
                if not _under(f, roots)
            ]
    return found


def check_counts(data: list[Datum]) -> list[str]:
    """§11.2: a stated count equals |citations|, and no trial is cited twice in one datum."""
    found: list[str] = []
    for d in data:
        n, distinct = len(d.citations), len({c.nct_id for c in d.citations})
        if d.count is not None and d.count != n:
            stated = "trial_count" if d.kind == "row" else "weight"
            found.append(violation(COUNT, f"{d.label}: {stated}={d.count} but {n} citations"))
        if distinct != n:
            found.append(
                violation(COUNT, f"{d.label}: {n} citations but {distinct} distinct trials")
            )
    return found


@dataclass(frozen=True)
class CohortScope:
    """One cohort's verification scope: its summary, supplied population, and witness index
    over population + every record its data cites (so forged citations are evaluated too)."""

    summary: CohortSummary
    population: CohortPopulation
    index: WitnessIndex


def _soundness(datum: Datum, scope: CohortScope, raw_by_id: RawById) -> tuple[list[str], int]:
    """Step 2: each cited record satisfies base_predicate ∧ datum.predicate (raw record)."""
    rule: Predicate = scope.summary.base_predicate
    if datum.predicate is not None:
        rule = {"all": [rule, datum.predicate]}
    sound = scope.index.witnesses(rule)
    cited = [c.nct_id for c in datum.citations if c.nct_id in raw_by_id]
    return [
        violation(SOUNDNESS, f"{datum.label}: {i} does not satisfy its cohort/datum predicate")
        for i in cited
        if i not in sound
    ], len(cited)


def _completeness(datum: Datum, scope: CohortScope) -> list[str]:
    """Step 4: the witness set over the PLOTTED population equals the cited ids exactly."""
    if datum.predicate is None:
        return []
    witness = scope.index.witnesses(datum.predicate) & scope.population.plotted_ids
    cited = {c.nct_id for c in datum.citations}
    if witness == cited:
        return []
    missing, extra = sorted(witness - cited)[:SHOWN_IDS], sorted(cited - witness)[:SHOWN_IDS]
    return [
        violation(
            COMPLETENESS, f"{datum.label}: witness set mismatch (missing={missing}, extra={extra})"
        )
    ]


def check_membership(datum: Datum, scope: CohortScope, raw_by_id: RawById) -> tuple[list[str], int]:
    """Steps 2 + 4 for one datum; a malformed predicate is a violation, never a crash."""
    try:
        sound, checked = _soundness(datum, scope, raw_by_id)
        return [*sound, *_completeness(datum, scope)], checked
    except PREDICATE_ERRORS as exc:
        return [violation(PREDICATE, f"{datum.label}: predicate cannot be evaluated: {exc}")], 0
