"""Record ClinicalTrials.gov pages for the PLAN.md §14 queries once, gzipped, for offline tests.

Live network use only; run manually with `uv run python scripts/record_fixtures.py`.
"""

import asyncio
import gzip
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ctviz.config import MAX_RECORDS
from ctviz.ctgov.client import CtGovClient
from ctviz.ctgov.fields import (
    ALWAYS,
    FOR_DIMENSION,
    FOR_FILTER,
    FOR_MATCH,
    FOR_MEASURE,
    FOR_NETWORK,
)

FIXTURES_DIR = Path(__file__).parent.parent / "tests" / "fixtures" / "ctgov"

# One query per named fixture, params exactly as in PLAN.md §14 (unquoted values, per D15).
QUERIES: dict[str, dict[str, str]] = {
    "pembrolizumab": {"query.intr": "pembrolizumab"},
    "nivolumab": {"query.intr": "nivolumab"},
    "ms_recruiting": {"query.cond": "multiple sclerosis", "filter.overallStatus": "RECRUITING"},
    "glioblastoma": {"query.cond": "glioblastoma"},
    "psoriasis_p2": {"query.cond": "psoriasis", "filter.advanced": "AREA[Phase]PHASE2"},
    "crohns_p3_completed": {
        "query.cond": "Crohn's Disease",
        "filter.advanced": "AREA[Phase]PHASE3",
        "filter.overallStatus": "COMPLETED",
    },
    "nsclc": {"query.cond": "non-small cell lung cancer"},
}


def _all_fields() -> list[str]:
    """Union of every fields.py piece, so one fixture serves every analysis kind."""
    pieces: list[str] = [*ALWAYS, *FOR_FILTER]
    for table in (FOR_DIMENSION, FOR_MEASURE, FOR_NETWORK, FOR_MATCH):
        for values in table.values():
            pieces += values
    return list(dict.fromkeys(pieces))


async def _record_one(client: CtGovClient, name: str, params: dict[str, str]) -> None:
    """Fetch one named query and write it as gzipped JSON; skip if already recorded."""
    path = FIXTURES_DIR / f"{name}.json.gz"
    if path.exists():
        print(f"{name}: already recorded, skipping -> {path}")  # noqa: T201
        return
    full_params = params | {"fields": ",".join(_all_fields())}
    started = time.monotonic()
    result = await client.fetch_all(full_params, max_records=MAX_RECORDS)
    elapsed_s = time.monotonic() - started
    body: dict[str, Any] = {
        "total": result.api_total_count,
        "fetched_at": datetime.now(UTC).isoformat(),
        "records": result.records,
    }
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(body, handle)
    print(  # noqa: T201
        f"{name}: total={result.api_total_count} fetched={len(result.records)} "
        f"truncated={result.truncated} elapsed={elapsed_s:.2f}s -> {path}"
    )


async def main() -> None:
    """Record every fixture in QUERIES, sequentially, against the live API."""
    async with CtGovClient(backoff_base_s=2.0) as client:
        for name, params in QUERIES.items():
            await _record_one(client, name, params)
            await asyncio.sleep(2)  # be polite to the public API, avoid 429s across fixtures


if __name__ == "__main__":
    asyncio.run(main())
