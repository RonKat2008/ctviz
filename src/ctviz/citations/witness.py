"""Witness sets by set algebra (PLAN.md §11.6 cost note: "index records by predicate path").

`WitnessIndex(records).witnesses(predicate)` returns exactly `{id | evaluate(predicate, record)}`
over a fixed record population, but each DISTINCT predicate is computed once (memoized by its
canonical JSON), `all`/`any`/`not` become set intersection/union/complement, and the common leaf
shapes -- `equals`/`in`/`normalizes_to`/`year_equals`, bare or as the clauses of an
`any_element` (keyed per element, so "the SAME element" semantics hold) -- are answered from a
one-pass `(shape) -> {key -> ids}` index. Every other op, and any record whose value can't be
keyed (e.g. a list where a scalar was expected), falls back to `predicates.evaluate` itself, so
the answer is always the evaluator's own.

Imports only `citations.pointer` / `citations.predicates` -- never `ctviz.analysis`."""

import json
from collections import defaultdict
from collections.abc import Hashable, Mapping
from itertools import product
from typing import Any

from ctviz.citations.pointer import resolve_pointer
from ctviz.citations.predicates import evaluate, normalizer_for, strip_operand, year_of
from ctviz.schemas.citations import Predicate

_MISSING = object()
_SCALAR_OPS = frozenset({"equals", "in"})
_INDEXED_OPS = _SCALAR_OPS | {"normalizes_to", "year_equals"}

# One indexable clause: (value path, op family, normalizer name or None).
Clause = tuple[str, str, str | None]
# (container path or None, its clauses): a bare leaf is (None, (clause,)); an `any_element`
# is (container, clauses), keyed per element as the tuple of every clause's key.
IndexShape = tuple[str | None, tuple[Clause, ...]]
_Index = tuple[dict[Hashable, set[str]], frozenset[str]]  # key -> ids, unkeyable-record ids


def _lookup(document: Any, path: str) -> Any:
    """The value at `path`, or `_MISSING` (the evaluator treats absence as "no match")."""
    try:
        return resolve_pointer(document, path)
    except (KeyError, IndexError):
        return _MISSING


def _value_keys(clause: Clause, value: Any) -> list[Hashable] | None:
    """Keys under which `value` satisfies a clause of this op family; None = can't be keyed."""
    _path, family, fn = clause
    if value is _MISSING:
        return []
    if family == "equals":
        key = strip_operand(value)
        return [key] if isinstance(key, Hashable) else None
    if family == "year_equals":
        return [year_of(value)]
    normalize = normalizer_for(fn or "drug")
    items = value if isinstance(value, list) else [value]
    return [normalize(item) for item in items if isinstance(item, str)]


def _clause(leaf: Predicate) -> Clause | None:
    """A bare indexable leaf as a clause, or None; an unknown `fn` raises like `evaluate`."""
    op = leaf.get("op")
    if op not in _INDEXED_OPS:
        return None
    fn = leaf.get("fn", "drug") if op == "normalizes_to" else None
    if fn is not None:
        normalizer_for(fn)  # raises here, as `evaluate` would, even when no record has the path
    return (leaf["path"], "equals" if op in _SCALAR_OPS else op, fn)


def _leaf_shape(leaf: Predicate) -> IndexShape | None:
    """The index shape answering `leaf`, or None when it must be evaluated record by record."""
    if leaf.get("op") != "any_element":
        clause = _clause(leaf)
        return None if clause is None else (None, (clause,))
    clauses = [_clause(w) for w in leaf.get("where", [])]
    if not clauses or any(c is None for c in clauses):
        return None
    return (leaf["path"], tuple(c for c in clauses if c is not None))


def _clause_operands(clause: Predicate) -> list[Hashable]:
    """The keys one clause looks up: its stripped operand(s), or the raw one for other ops."""
    op = clause["op"]
    if op == "in":
        return [strip_operand(v) for v in clause["value"]]
    if op == "equals":
        return [strip_operand(clause["value"])]
    return [clause["value"]]


def _operand_keys(leaf: Predicate) -> list[Hashable] | None:
    """Every key tuple the leaf looks up (its operands' product), or None if one is unhashable."""
    clauses = leaf["where"] if leaf.get("op") == "any_element" else [leaf]
    operands = [_clause_operands(c) for c in clauses]
    if not all(isinstance(o, Hashable) for group in operands for o in group):
        return None
    return list(product(*operands))


def _record_keys(record: Mapping[str, Any], shape: IndexShape) -> list[Hashable] | None:
    """Every key tuple this record satisfies under `shape` (per element, for a container)."""
    container, clauses = shape
    elements = [record] if container is None else _lookup(record, container)
    if container is not None and not isinstance(elements, list):
        return []
    keys: list[Hashable] = []
    for element in elements:
        if not isinstance(element, Mapping):
            continue
        per_clause = [_value_keys(c, _lookup(element, c[0])) for c in clauses]
        if any(k is None for k in per_clause):
            return None
        keys.extend(product(*(k for k in per_clause if k is not None)))
    return keys


class WitnessIndex:
    """Memoized, indexed `{id | evaluate(predicate, record)}` over one fixed population."""

    def __init__(self, records: Mapping[str, Mapping[str, Any]]) -> None:
        """Index lazily: nothing is computed until a predicate is asked for."""
        self._records = records
        self._universe = frozenset(records)
        self._memo: dict[str, frozenset[str]] = {}
        self._indexes: dict[IndexShape, _Index] = {}

    def witnesses(self, predicate: Predicate) -> frozenset[str]:
        """Ids of every record satisfying `predicate`; raises the evaluator's own errors."""
        return self._within(predicate, self._universe)

    def _within(self, predicate: Predicate, ids: frozenset[str]) -> frozenset[str]:
        """`{i in ids | evaluate(predicate, record_i)}`; whole-population answers are memoized."""
        key = json.dumps(predicate, sort_keys=True, default=str)
        if key in self._memo:
            return self._memo[key] & ids
        result = self._compute(predicate, ids)
        if ids is self._universe:
            self._memo[key] = result
        return result

    def _compute(self, predicate: Predicate, ids: frozenset[str]) -> frozenset[str]:
        """Combinators as set algebra, short-circuiting like `evaluate`: `all` narrows the
        candidates clause by clause, `any` only re-tests records no earlier clause matched."""
        if "all" in predicate:
            for clause in predicate["all"]:
                ids = self._within(clause, ids)
            return ids
        if "any" in predicate:
            hits: frozenset[str] = frozenset()
            for clause in predicate["any"]:
                found = self._within(clause, ids - hits)
                hits |= found
            return hits
        if "not" in predicate:
            return ids - self._within(predicate["not"], ids)
        return self._leaf(predicate, ids)

    def _leaf(self, leaf: Predicate, ids: frozenset[str]) -> frozenset[str]:
        """One leaf: an index lookup (+ scanned unkeyable records), else a scan of `ids`."""
        shape = _leaf_shape(leaf)
        operands = _operand_keys(leaf) if shape is not None else None
        if shape is None or operands is None:
            return self._scan(leaf, ids)
        index, unkeyable = self._index(shape)
        hits = frozenset().union(*(index.get(k, ()) for k in operands))
        return (hits & ids) | self._scan(leaf, unkeyable & ids)

    def _scan(self, leaf: Predicate, ids: frozenset[str]) -> frozenset[str]:
        """The evaluator itself, record by record (the fallback for unindexed shapes)."""
        return frozenset(i for i in ids if evaluate(leaf, self._records[i]))

    def _index(self, shape: IndexShape) -> _Index:
        """Build (once per shape) the key -> ids index and the unkeyable-record set."""
        if shape not in self._indexes:
            index: dict[Hashable, set[str]] = defaultdict(set)
            unkeyable: set[str] = set()
            for nct_id, record in self._records.items():
                keys = _record_keys(record, shape)
                if keys is None:
                    unkeyable.add(nct_id)
                    continue
                for key in keys:
                    index[key].add(nct_id)
            self._indexes[shape] = (index, frozenset(unkeyable))
        return self._indexes[shape]
