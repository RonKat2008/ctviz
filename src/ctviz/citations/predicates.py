"""Predicate evaluator: the boolean rule a raw record must satisfy (PLAN.md §11.6). Pure;
imports only `common/names.py` and `citations/pointer.py`, so both the aggregators and the
independent verifier can share one definition of "does this trial belong here"."""

from collections.abc import Callable, Mapping
from typing import Any

from ctviz.citations.pointer import resolve_pointer
from ctviz.common.names import normalize_drug, normalize_text, text_matches
from ctviz.schemas.citations import Predicate

_MISSING = object()


def _lookup(document: Mapping[str, Any], path: str) -> Any:
    """Resolve `path` in `document`; a sentinel (never an exception) when it does not exist."""
    try:
        return resolve_pointer(document, path)
    except (KeyError, IndexError):
        return _MISSING


def strip_operand(value: Any) -> Any:
    """Strip a string operand; leave anything else untouched (raw API text carries whitespace)."""
    return value.strip() if isinstance(value, str) else value


_strip = strip_operand


def _op_equals(predicate: Predicate, document: Mapping[str, Any]) -> bool:
    value = _lookup(document, predicate["path"])
    return value is not _MISSING and _strip(value) == _strip(predicate["value"])


def _op_in(predicate: Predicate, document: Mapping[str, Any]) -> bool:
    value = _lookup(document, predicate["path"])
    if value is _MISSING:
        return False
    return _strip(value) in [_strip(v) for v in predicate["value"]]


def _op_set_equals(predicate: Predicate, document: Mapping[str, Any]) -> bool:
    value = _lookup(document, predicate["path"])
    if value is _MISSING or not isinstance(value, list):
        return False
    return {_strip(v) for v in value} == {_strip(v) for v in predicate["value"]}


def _op_contains(predicate: Predicate, document: Mapping[str, Any]) -> bool:
    value = _lookup(document, predicate["path"])
    if value is _MISSING or not isinstance(value, list):
        return False
    operand = _strip(predicate["value"])
    return any(_strip(v) == operand for v in value)


def _op_exists(predicate: Predicate, document: Mapping[str, Any]) -> bool:
    value = _lookup(document, predicate["path"])
    if value is _MISSING or value is None:
        return False
    return not (isinstance(value, list) and not value)


def year_of(value: Any) -> int | None:
    """The year prefix of a partial-date string ('2019-10' -> 2019), or None if unparseable."""
    if not isinstance(value, str) or not value:
        return None
    head = value.split("-", 1)[0]
    return int(head) if head.isdigit() else None


def _op_year_equals(predicate: Predicate, document: Mapping[str, Any]) -> bool:
    value = _lookup(document, predicate["path"])
    return value is not _MISSING and year_of(value) == predicate["value"]


def _op_year_in_range(predicate: Predicate, document: Mapping[str, Any]) -> bool:
    value = _lookup(document, predicate["path"])
    if value is _MISSING:
        return False
    year = year_of(value)
    if year is None:
        return False
    lo, hi = predicate["value"]
    return (lo is None or year >= lo) and (hi is None or year <= hi)


def _op_in_range(predicate: Predicate, document: Mapping[str, Any]) -> bool:
    value = _lookup(document, predicate["path"])
    if value is _MISSING or not isinstance(value, int | float) or isinstance(value, bool):
        return False
    lo, hi = predicate["value"]
    return value >= lo and (hi is None or value < hi)


def _op_any_element(predicate: Predicate, document: Mapping[str, Any]) -> bool:
    value = _lookup(document, predicate["path"])
    if value is _MISSING or not isinstance(value, list):
        return False
    clause: Predicate = {"all": predicate["where"]}
    return any(evaluate(clause, el) for el in value if isinstance(el, Mapping))


_NORMALIZERS: dict[str, Callable[[str], str | None]] = {
    "drug": normalize_drug,
    "text": normalize_text,
}


def normalizer_for(fn: str) -> Callable[[str], str | None]:
    """The named pure normalizer; an unknown name is a malformed predicate, never a fallback."""
    normalizer = _NORMALIZERS.get(fn)
    if normalizer is None:
        raise ValueError(f"unknown normalizes_to fn: {fn!r}")
    return normalizer


def _normalizes_one(normalizer: Callable[[str], str | None], raw: Any, key: Any) -> bool:
    """One raw string's normalized form equals `key` (non-strings never match)."""
    return isinstance(raw, str) and normalizer(raw) == key


def _op_normalizes_to(predicate: Predicate, document: Mapping[str, Any]) -> bool:
    normalizer = normalizer_for(predicate.get("fn", "drug"))
    value = _lookup(document, predicate["path"])
    if value is _MISSING:
        return False
    key = predicate["value"]
    if isinstance(value, list):
        return any(_normalizes_one(normalizer, v, key) for v in value)
    return _normalizes_one(normalizer, value, key)


def _op_text_matches(predicate: Predicate, document: Mapping[str, Any]) -> bool:
    value = _lookup(document, predicate["path"])
    if value is _MISSING or not isinstance(value, str | list):
        return False
    return text_matches(value, predicate["value"])


_OPS: dict[str, Any] = {
    "equals": _op_equals,
    "in": _op_in,
    "set_equals": _op_set_equals,
    "contains": _op_contains,
    "exists": _op_exists,
    "year_equals": _op_year_equals,
    "year_in_range": _op_year_in_range,
    "in_range": _op_in_range,
    "any_element": _op_any_element,
    "normalizes_to": _op_normalizes_to,
    "text_matches": _op_text_matches,
}


def evaluate(predicate: Predicate, document: Mapping[str, Any]) -> bool:
    """True when `document` satisfies `predicate`; unknown ops raise rather than default False."""
    if "all" in predicate:
        return all(evaluate(p, document) for p in predicate["all"])
    if "any" in predicate:
        return any(evaluate(p, document) for p in predicate["any"])
    if "not" in predicate:
        return not evaluate(predicate["not"], document)
    op = predicate.get("op")
    handler = _OPS.get(op)  # type: ignore[arg-type]
    if handler is None:
        raise ValueError(f"unknown predicate op: {op!r}")
    result: bool = handler(predicate, document)
    return result


def _sub_paths(container: str, where: list[Predicate]) -> set[str]:
    """Absolute paths reachable through an `any_element` clause's relative sub-predicates."""
    return {container + rel for sub in where for rel in referenced_paths(sub)}


def referenced_paths(predicate: Predicate) -> set[str]:
    """Every JSON Pointer this predicate reads, for the verifier's relevance check (§11.6)."""
    if "all" in predicate:
        return {path for p in predicate["all"] for path in referenced_paths(p)}
    if "any" in predicate:
        return {path for p in predicate["any"] for path in referenced_paths(p)}
    if "not" in predicate:
        return referenced_paths(predicate["not"])
    paths = {predicate["path"]}
    if "where" in predicate:
        paths |= _sub_paths(predicate["path"], predicate["where"])
    return paths
