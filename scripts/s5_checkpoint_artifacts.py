"""Regenerate the S5 review-fix checkpoint artifacts by running the FIXED pipeline for real.

Offline only: fixtures are replayed via respx, no live network / LLM calls. Run with
`uv run python scripts/s5_checkpoint_artifacts.py <output_dir>`.
"""

import asyncio
import json
import sys
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

TODAY = date(2026, 9, 28)
MAX_CITATIONS_PER_DATUM = 2
MAX_NETWORK_ELEMENTS = 3

HISTOGRAM_ANALYSIS = {
    "kind": "histogram",
    "group_by": None,
    "series_by": None,
    "phase_mode": None,
    "time_field": None,
    "granularity": None,
    "measure_x": "enrollment",
    "measure_y": None,
    "color_by": None,
    "network_type": None,
    "top_n": None,
}
SCATTER_ANALYSIS = {
    "kind": "scatter",
    "group_by": None,
    "series_by": None,
    "phase_mode": None,
    "time_field": None,
    "granularity": None,
    "measure_x": "duration_months",
    "measure_y": "enrollment",
    "color_by": None,
    "network_type": None,
    "top_n": None,
}
NETWORK_ANALYSIS = {
    "kind": "network",
    "group_by": None,
    "series_by": None,
    "phase_mode": None,
    "time_field": None,
    "granularity": None,
    "measure_x": None,
    "measure_y": None,
    "color_by": None,
    "network_type": "sponsor_drug",
    "top_n": None,
}


def _mock(records: list[dict]) -> None:
    respx.get(f"{CTGOV_BASE_URL}/studies").mock(
        side_effect=[
            httpx.Response(200, json={"totalCount": len(records), "studies": []}),
            httpx.Response(200, json={"totalCount": len(records), "studies": records}),
        ]
    )


def _trim_row(row: dict) -> dict:
    """Trim a row's citations to MAX_CITATIONS_PER_DATUM for a compact checkpoint artifact."""
    trimmed = dict(row)
    citations = row.get("citations")
    if isinstance(citations, list):
        trimmed["citations"] = [
            c.model_dump(mode="json") if hasattr(c, "model_dump") else c
            for c in citations[:MAX_CITATIONS_PER_DATUM]
        ]
    return trimmed


async def _run(analysis: dict, viz: dict, condition: str, query: str) -> dict:
    plan = make_plan(analysis=analysis, visualization=viz)
    planner = Planner(FakeBackend(plan))
    request = VisualizeRequest(query=query, condition=condition)
    async with CtGovClient() as client:
        response = await run_pipeline(request, planner=planner, client=client, today=TODAY)
    return response.model_dump(mode="json")


async def _histogram_psoriasis() -> dict:
    with respx.mock:
        _mock(load_fixture("psoriasis_p2"))
        body = await _run(
            HISTOGRAM_ANALYSIS,
            {"type": "histogram", "title": "Enrollment distribution", "rationale": "spread"},
            "Psoriasis",
            "Enrollment distribution?",
        )
    body["visualization"]["data"] = [_trim_row(r) for r in body["visualization"]["data"]]
    return body


async def _scatter_crohns() -> dict:
    with respx.mock:
        _mock(load_fixture("crohns_p3_completed"))
        body = await _run(
            SCATTER_ANALYSIS,
            {"type": "scatter_plot", "title": "Enrollment vs duration", "rationale": "relate"},
            "Crohn's Disease",
            "Enrollment vs duration?",
        )
    body["visualization"]["data"] = [_trim_row(r) for r in body["visualization"]["data"][:5]]
    return body


async def _network_glioblastoma() -> dict:
    with respx.mock:
        _mock(load_fixture("glioblastoma"))
        body = await _run(
            NETWORK_ANALYSIS,
            {"type": "network_graph", "title": "Sponsors and drugs", "rationale": "network"},
            "Glioblastoma",
            "Sponsor-drug network?",
        )
    data = body["visualization"]["data"]
    data["nodes"] = [_trim_row(n) for n in data["nodes"][:MAX_NETWORK_ELEMENTS]]
    data["edges"] = [_trim_row(e) for e in data["edges"][:MAX_NETWORK_ELEMENTS]]
    return body


def _guard_example() -> str:
    """One real guard adjustment note, from a fixture that trips the §10.6 bar_chart->metric
    guard: the pembrolizumab fixture filtered to a single phase produces one bucket."""
    from ctviz.analysis.guards import apply_guards
    from ctviz.analysis.aggregate import AggregateResult, Bucket, MatchedTrial
    from ctviz.ctgov.normalize import normalize

    trials = [MatchedTrial(normalize(r), ()) for r in load_fixture("psoriasis_p2")]
    only_phase2 = [t for t in trials if t.trial.phase_label == "Phase 2"]
    result = AggregateResult((Bucket("Phase 2", {}, tuple(t.trial.nct_id for t in only_phase2)),), {})
    plan = make_plan()
    viz_type, notes = apply_guards(plan, result)
    return (
        f"fixture: psoriasis_p2, {len(only_phase2)} Phase 2 trials -> 1 bucket\n"
        f"requested viz_type: bar_chart\n"
        f"guard decision: {viz_type.value}\n"
        f"meta.adjustments: {notes}\n"
    )


async def _merck_census() -> str:
    from ctviz.analysis.aggregate import MatchedTrial
    from ctviz.analysis.entities import sponsor_ambiguity_warning, sponsor_census
    from ctviz.ctgov.normalize import normalize

    trials = [MatchedTrial(normalize(r), ()) for r in load_fixture("pembrolizumab")]
    census = sponsor_census(trials)
    warning = sponsor_ambiguity_warning("Merck", census)
    synthetic_census = [
        ("Merck Sharp & Dohme LLC", 10),
        ("Merck KGaA, Darmstadt, Germany", 5),
    ]
    synthetic_warning = sponsor_ambiguity_warning("Merck", synthetic_census)
    lines = ["real pembrolizumab fixture top-10 sponsor census:"]
    lines += [f"  {name}: {count}" for name, count in census]
    lines.append(f"multi-org warning fires: {warning is not None}")
    if warning:
        lines.append(f"  warning: {warning}")
    lines.append("")
    lines.append("synthetic two-org census (MSD + Merck KGaA):")
    lines += [f"  {name}: {count}" for name, count in synthetic_census]
    lines.append(f"multi-org warning fires: {synthetic_warning is not None}")
    if synthetic_warning:
        lines.append(f"  warning: {synthetic_warning}")
    return "\n".join(lines) + "\n"


async def _golden_summary() -> str:
    with respx.mock:
        psoriasis = load_fixture("psoriasis_p2")
        _mock(psoriasis)
        hist = await _run(
            HISTOGRAM_ANALYSIS,
            {"type": "histogram", "title": "t", "rationale": "r"},
            "Psoriasis",
            "Enrollment distribution query",
        )
    plotted = sum(row["trial_count"] for row in hist["visualization"]["data"])
    excluded = hist["meta"]["data_coverage"]["excluded"]["analysis"]

    with respx.mock:
        crohns = load_fixture("crohns_p3_completed")
        _mock(crohns)
        scatter = await _run(
            SCATTER_ANALYSIS,
            {"type": "scatter_plot", "title": "t", "rationale": "r"},
            "Crohn's Disease",
            "Enrollment vs duration query",
        )
    scatter_points = len(scatter["visualization"]["data"])

    with respx.mock:
        ms = load_fixture("ms_recruiting")
        _mock(ms)
        country_plan = make_plan(
            filters={
                "phases": None,
                "overall_statuses": ["RECRUITING"],
                "study_types": None,
                "intervention_types": None,
                "lead_sponsor_classes": None,
                "countries": None,
                "start_year_min": None,
                "start_year_max": None,
                "nct_ids": None,
            },
            analysis={
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
            },
            visualization={"type": "bar_chart", "title": "t", "rationale": "r"},
        )
        planner = Planner(FakeBackend(country_plan))
        request = VisualizeRequest(query="Recruiting countries query", condition="Multiple Sclerosis")
        async with CtGovClient() as client:
            response = await run_pipeline(request, planner=planner, client=client, today=TODAY)
        us_count = next(
            row["trial_count"] for row in response.visualization.model_dump(mode="json")["data"]
            if row["category"] == "United States"
        )

    return (
        "S5 golden numbers (fixed code, re-derived 2026-09-29):\n"
        f"  psoriasis_p2 histogram: {len(psoriasis)} total, {plotted} plotted, "
        f"{sum(excluded.values())} excluded ({excluded})\n"
        f"  crohns_p3_completed scatter: {len(crohns)} total, {scatter_points} usable points\n"
        f"  ms_recruiting country (recruiting rule): United States = {us_count}\n"
    )


async def main(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "s5_histogram_psoriasis.json").write_text(
        json.dumps(await _histogram_psoriasis(), indent=2)
    )
    (out_dir / "s5_scatter_crohns.json").write_text(json.dumps(await _scatter_crohns(), indent=2))
    (out_dir / "s5_network_glioblastoma.json").write_text(
        json.dumps(await _network_glioblastoma(), indent=2)
    )
    (out_dir / "s5_merck_census.txt").write_text(await _merck_census())
    (out_dir / "s5_golden.txt").write_text(await _golden_summary())
    (out_dir / "s5_guard_example.txt").write_text(_guard_example())
    print(f"wrote checkpoint artifacts to {out_dir}")


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1])))
