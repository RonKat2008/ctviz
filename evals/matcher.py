"""Property assertions over a plan dict (PLAN.md §16.4): check properties, never exact JSON.

An `expect` mapping holds dotted plan paths. The value's shape picks the check:
- `search_terms: [{param, value_contains}]` -- some term on `param` contains the text;
- a key ending in `_contain` (e.g. `comparison.values_contain`) -- the list at the path minus the
  suffix holds an item containing each expected string;
- a key ending in `_exactly` (e.g. `filters.phases_exactly: [PHASE3]`) -- the list at the path
  minus the suffix is exactly that set: what the question states, no invented extras;
- a key ending in `_present` (e.g. `filters.start_year_min_present: true`) -- the value at the
  path minus the suffix is non-null (an explicit null counts as absent);
- `distinct: [path, path, ...]` -- the values at those paths are pairwise different;
- `assumptions_state_value_of: <path>` -- some assumption contains the value at that path (e.g.
  a vague "recent" must be stated as the concrete start year the plan filters on);
- `assumptions_nonempty: true` -- the plan states at least one assumption;
- a list value -- the actual value is one of the allowed values;
- anything else (including null) -- equality.
String comparisons are case-insensitive throughout, because entity casing is not a plan error.
"""

from collections.abc import Mapping, Sequence
from typing import Any

CONTAIN_SUFFIX = "_contain"
PRESENT_SUFFIX = "_present"
EXACTLY_SUFFIX = "_exactly"
_MISSING = object()


def _norm(value: Any) -> Any:
    """Case-fold strings so "Pembrolizumab" and "pembrolizumab" compare equal."""
    return value.casefold() if isinstance(value, str) else value


def _resolve(plan: Mapping[str, Any], path: str) -> Any:
    """The value at a dotted path, or `_MISSING` when any node on the way is absent or null."""
    node: Any = plan
    for part in path.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return _MISSING
        node = node[part]
    return node


def _contains(items: Sequence[Any], wanted: Any) -> bool:
    """Some item contains `wanted` as a case-insensitive substring (or equals it, non-strings)."""
    target = _norm(wanted)
    for item in items:
        value = _norm(item)
        if value == target or (isinstance(value, str) and str(target) in value):
            return True
    return False


def _check_search_terms(
    plan: Mapping[str, Any], expected: Sequence[Mapping[str, str]]
) -> list[str]:
    """Each expected (param, value_contains) pair is matched by at least one search term."""
    terms = plan.get("search_terms") or []
    failures = []
    for want in expected:
        on_param = [t.get("value", "") for t in terms if t.get("param") == want["param"]]
        if not _contains(on_param, want["value_contains"]):
            actual = [(t.get("param"), t.get("value")) for t in terms]
            failures.append(
                f"search_terms: no {want['param']} term containing "
                f"{want['value_contains']!r} (got {actual})"
            )
    return failures


def _check_contain(plan: Mapping[str, Any], key: str, expected: Sequence[Any]) -> list[str]:
    """`<path>_contain: [...]`: the list at `<path>` holds an item matching every expected one."""
    path = key.removesuffix(CONTAIN_SUFFIX)
    actual = _resolve(plan, path)
    if actual is _MISSING or actual is None:
        return [f"{path}: missing (expected to contain {list(expected)})"]
    missing = [want for want in expected if not _contains(actual, want)]
    return [f"{path}: {actual} does not contain {missing}"] if missing else []


def _check_present(plan: Mapping[str, Any], key: str, expected: bool) -> list[str]:
    """`<path>_present: true`: the value at `<path>` is set (non-null)."""
    path = key.removesuffix(PRESENT_SUFFIX)
    actual = _resolve(plan, path)
    is_present = actual is not _MISSING and actual is not None
    if is_present == expected:
        return []
    return [f"{path}: expected a value, got none" if expected else f"{path}: expected none"]


def _check_exactly(plan: Mapping[str, Any], key: str, expected: Sequence[Any]) -> list[str]:
    """`<path>_exactly: [...]`: the list at `<path>` is that set, case-insensitively."""
    path = key.removesuffix(EXACTLY_SUFFIX)
    actual = _resolve(plan, path)
    if actual is _MISSING or actual is None:
        return [f"{path}: missing (expected exactly {list(expected)})"]
    if {_norm(v) for v in actual} == {_norm(v) for v in expected}:
        return []
    return [f"{path}: expected exactly {list(expected)}, got {list(actual)}"]


def _check_distinct(plan: Mapping[str, Any], paths: Sequence[str]) -> list[str]:
    """`distinct: [...]`: no two of these paths hold the same (set) value."""
    values = [_norm(_resolve(plan, path)) for path in paths]
    repeated = [v for i, v in enumerate(values) if v is not _MISSING and v in values[:i]]
    if not repeated:
        return []
    return [f"distinct: {', '.join(paths)} must differ (got {repeated[0]!r} twice)"]


def _check_assumption_states(plan: Mapping[str, Any], path: str) -> list[str]:
    """`assumptions_state_value_of: <path>`: an assumption quotes the value at `<path>`."""
    value = _resolve(plan, path)
    if value is _MISSING or value is None:
        return [f"assumptions: {path} is unset, so no assumption can state it"]
    if any(str(value) in text for text in plan.get("assumptions") or []):
        return []
    return [f"assumptions: none states {path} = {value}"]


def _check_value(plan: Mapping[str, Any], path: str, expected: Any) -> list[str]:
    """A list is an allowed set; anything else must be equal (case-insensitively for strings)."""
    actual = _resolve(plan, path)
    if actual is _MISSING:
        if expected is None:
            return []
        return [f"{path}: missing (expected {expected!r})"]
    if isinstance(expected, list):
        if _norm(actual) in [_norm(v) for v in expected]:
            return []
        return [f"{path}: expected one of {expected}, got {actual!r}"]
    if _norm(actual) == _norm(expected):
        return []
    return [f"{path}: expected {expected!r}, got {actual!r}"]


def _check_one(plan: Mapping[str, Any], key: str, expected: Any) -> list[str]:
    """Dispatch one `expect` entry to the check its key/value shape selects."""
    if key == "search_terms":
        return _check_search_terms(plan, expected)
    if key == "assumptions_nonempty":
        has_any = bool(plan.get("assumptions"))
        return [] if has_any == expected else [f"assumptions: expected nonempty={expected}"]
    if key == "distinct":
        return _check_distinct(plan, expected)
    if key == "assumptions_state_value_of":
        return _check_assumption_states(plan, expected)
    if key.endswith(CONTAIN_SUFFIX):
        return _check_contain(plan, key, expected)
    if key.endswith(EXACTLY_SUFFIX):
        return _check_exactly(plan, key, expected)
    if key.endswith(PRESENT_SUFFIX):
        return _check_present(plan, key, expected)
    return _check_value(plan, key, expected)


def check_plan(plan: Mapping[str, Any] | None, expect: Mapping[str, Any]) -> list[str]:
    """Every failed property of `plan` against `expect`, as readable lines (empty = correct)."""
    if plan is None:
        return ["no plan was produced"]
    return [failure for key, value in expect.items() for failure in _check_one(plan, key, value)]
