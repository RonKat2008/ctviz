"""D4: payload size (§11.7) -- measured on the shipped example and a large fixture-scale
response, against the 1 MB gzipped budget that triggers `citation_field` compaction."""

import gzip
import logging
from datetime import date
from pathlib import Path

import httpx
import respx

from ctviz.agent.planner import Planner
from ctviz.config import CTGOV_BASE_URL
from ctviz.ctgov.client import CtGovClient
from ctviz.pipeline import run_pipeline
from ctviz.schemas.request import VisualizeRequest
from tests.factories import make_plan
from tests.fixtures.load import load_fixture
from tests.unit.agent.test_planner import FakeBackend

log = logging.getLogger(__name__)
EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "examples"
GZIP_BUDGET_BYTES = 1_000_000  # §11.7: `citation_field` compaction only needed past this
NSCLC_GZIP_BOUND_BYTES = 250_000  # measured 144,275 B; a regression guard, not the §11.7 budget
COUNTRY_ANALYSIS = {
    "kind": "count_by",
    "group_by": "country",
    "series_by": None,
    "phase_mode": None,
    "time_field": None,
    "granularity": None,
    "measure_x": None,
    "measure_y": None,
    "color_by": None,
    "network_type": None,
    "top_n": None,
}


def _gzip_size(data: bytes) -> int:
    """Bytes after gzip compression -- the same measure §11.7's table is written against."""
    return len(gzip.compress(data))


def test_shipped_pembrolizumab_example_stays_under_the_1mb_gzip_budget() -> None:
    """The shipped `examples/01_draft.response.json` (2,960-trial time series, full citations)."""
    raw = (EXAMPLES_DIR / "01_draft.response.json").read_bytes()
    gzipped = _gzip_size(raw)

    assert gzipped < GZIP_BUDGET_BYTES
    log.info("[D4] 01_draft.response.json: raw=%sB gzip=%sB", f"{len(raw):,}", f"{gzipped:,}")


async def test_nsclc_scale_country_chart_stays_under_the_1mb_gzip_budget() -> None:
    """D4: the largest recorded fixture (NSCLC, 8,572 records), every country its own bucket,
    full citations on every bar -- the closest offline proxy to the 20k-record-cap worst case."""
    records = load_fixture("nsclc")
    plan = make_plan(
        search_terms=[],
        analysis=COUNTRY_ANALYSIS,
        visualization={"type": "bar_chart", "title": "NSCLC trials by country", "rationale": "r"},
    )
    planner = Planner(FakeBackend(plan))
    request = VisualizeRequest(query="NSCLC trials by country")

    with respx.mock:
        respx.get(f"{CTGOV_BASE_URL}/studies").mock(
            side_effect=[
                httpx.Response(200, json={"totalCount": len(records), "studies": []}),
                httpx.Response(200, json={"totalCount": len(records), "studies": records}),
            ]
        )
        respx.get(f"{CTGOV_BASE_URL}/version").mock(
            return_value=httpx.Response(200, json={"apiVersion": "2.0.5", "dataTimestamp": "t"})
        )
        async with CtGovClient() as client:
            response = await run_pipeline(
                request, planner=planner, client=client, today=date(2026, 9, 28)
            )

    assert response.ok, response.error
    raw = response.model_dump_json().encode("utf-8")
    gzipped = _gzip_size(raw)
    log.info(
        "[D4] NSCLC country chart (%s records): raw=%sB gzip=%sB",
        f"{len(records):,}",
        f"{len(raw):,}",
        f"{gzipped:,}",
    )
    assert gzipped < NSCLC_GZIP_BOUND_BYTES
