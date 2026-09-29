"""Load recorded ClinicalTrials.gov fixtures (gzipped JSON) for offline integration tests.

Both loaders are `functools.cache`d: decompressing/parsing a multi-hundred-record fixture and
re-normalizing every record is pure, deterministic work repeated across many test modules, so
caching it is a straight win. The returned values are immutable (a `tuple`, and `normalize()`
already returns frozen `Trial` dataclasses) -- callers must not mutate them, since the same
objects are shared across every test in the process.
"""

import gzip
import json
from functools import cache
from pathlib import Path
from typing import Any

from ctviz.ctgov.normalize import Trial, normalize

FIXTURES_DIR = Path(__file__).parent / "ctgov"


@cache
def _load_fixture_cached(name: str) -> tuple[dict[str, Any], ...]:
    """Decompress + parse a fixture's `records` list once per process, cached by name."""
    path = FIXTURES_DIR / f"{name}.json.gz"
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        body: dict[str, Any] = json.load(handle)
    return tuple(body["records"])


def load_fixture(name: str) -> list[dict[str, Any]]:
    """Return the recorded `records` list for a named fixture (e.g. 'pembrolizumab').

    A fresh `list` is returned every call (the cached tuple itself is never handed out), so a
    caller mutating the returned list can't corrupt the shared cache -- the individual raw
    record dicts are still shared, though, and must be treated as read-only.
    """
    return list(_load_fixture_cached(name))


@cache
def load_trials(name: str) -> tuple[Trial, ...]:
    """Every record in a named fixture, already normalized to a `Trial`, cached by name.

    Item 4 (S5 test speed): the heavy integration/citation-invariant tests normalize the same
    handful of fixtures over and over; normalization is pure, so caching it once per process
    cuts repeated fixture setup out of the suite. Trials are frozen dataclasses and this returns
    the SAME cached tuple to every caller -- never mutate a returned `Trial`.
    """
    return tuple(normalize(record) for record in _load_fixture_cached(name))
