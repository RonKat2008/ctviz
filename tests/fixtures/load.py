"""Load recorded ClinicalTrials.gov fixtures (gzipped JSON) for offline integration tests."""

import gzip
import json
from pathlib import Path
from typing import Any

FIXTURES_DIR = Path(__file__).parent / "ctgov"


def load_fixture(name: str) -> list[dict[str, Any]]:
    """Return the recorded `records` list for a named fixture (e.g. 'pembrolizumab')."""
    path = FIXTURES_DIR / f"{name}.json.gz"
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        body: dict[str, Any] = json.load(handle)
    return list(body["records"])
