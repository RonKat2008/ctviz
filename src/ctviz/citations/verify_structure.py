"""§11.6 step 5 structure identities, checked against the pipeline-SUPPLIED populations:

- per cohort, `records_matched - Σ declared analysis exclusions == records_plotted`, and the
  trials the chart cites are exactly that plotted population (no silent drop, no stowaway);
- exclusive dimensions: Σ trial_count == records_plotted, per cohort;
- every edge endpoint is a node, and an edge's witnesses lie in both endpoints' (intersection);
- no non-zero datum lacks citations: enforced by the count check (count == |citations|).

Imports only `citations.verify_data` and `schemas` -- never `ctviz.analysis`."""

from collections.abc import Mapping

from ctviz.citations.verify_data import STRUCTURE, CohortPopulation, Datum, violation
from ctviz.schemas.response import CohortSummary, DataCoverage

# Single-valued dims: every plotted trial lands in exactly one bucket. Multi-valued dims
# (country/intervention/condition/intervention_type) are exempt -- a trial may sit in several.
EXCLUSIVE_DIMENSIONS = frozenset(
    {"phase", "overall_status", "study_type", "lead_sponsor_class", "lead_sponsor", "start_year"}
)
SHOWN_IDS = 5


def _ids(data: list[Datum]) -> set[str]:
    """Every nct_id cited anywhere in `data`."""
    return {c.nct_id for d in data for c in d.citations}


def _identity(cohort: CohortSummary, population: CohortPopulation) -> list[str]:
    """records_matched - declared exclusions == records_plotted, all against the population."""
    found: list[str] = []
    label, kept = cohort.label, population.kept_ids
    if cohort.records_matched != len(kept):
        found.append(
            f"cohort {label!r}: records_matched={cohort.records_matched} != {len(kept)} kept"
        )
    stray = sorted(set(population.excluded) - kept)[:SHOWN_IDS]
    if stray:
        found.append(f"cohort {label!r}: declared exclusions {stray} were never kept")
    plotted = len(population.plotted_ids)
    if cohort.records_plotted != plotted:
        found.append(
            f"cohort {label!r}: records_plotted={cohort.records_plotted} != records_matched "
            f"{len(kept)} - {len(population.excluded)} declared exclusions = {plotted}"
        )
    return [violation(STRUCTURE, v) for v in found]


def _coverage(label: str, population: CohortPopulation, data: list[Datum]) -> list[str]:
    """The cited trials are exactly the plotted population: nothing dropped, nothing extra."""
    cited, plotted = _ids(data), population.plotted_ids
    if cited == plotted:
        return []
    missing, extra = sorted(plotted - cited)[:SHOWN_IDS], sorted(cited - plotted)[:SHOWN_IDS]
    return [
        violation(
            STRUCTURE,
            f"cohort {label!r}: cited trials != plotted population (uncited={missing}, "
            f"not plotted={extra})",
        )
    ]


def _exclusive_sum(label: str, population: CohortPopulation, data: list[Datum]) -> list[str]:
    """Exclusive dims: the sum of witness-set sizes must equal records_plotted."""
    total = sum(len({c.nct_id for c in d.citations}) for d in data)  # witness sets, not lists
    expected = len(population.plotted_ids)
    if total == expected:
        return []
    return [
        violation(
            STRUCTURE,
            f"cohort {label!r}: exclusive-dimension bucket sum {total} != "
            f"records_plotted {expected}",
        )
    ]


def check_cohort(
    cohort: CohortSummary,
    population: CohortPopulation | None,
    data: list[Datum],
    dimension: str | None,
) -> list[str]:
    """Every per-cohort identity for one cohort's slice of the data."""
    if population is None:
        return [violation(STRUCTURE, f"cohort {cohort.label!r}: no population was supplied")]
    found = [*_identity(cohort, population), *_coverage(cohort.label, population, data)]
    if dimension in EXCLUSIVE_DIMENSIONS:
        found += _exclusive_sum(cohort.label, population, data)
    return found


def check_data_coverage(coverage: DataCoverage | None, population: CohortPopulation) -> list[str]:
    """Single-cohort `meta.data_coverage` must report the same counts and exclusions."""
    if coverage is None:
        return []
    declared = {e.nct_id for e in coverage.excluded_trials if e.stage == "analysis"}
    found = []
    if declared != set(population.excluded):
        found.append("data_coverage analysis exclusions differ from the declared exclusions")
    if coverage.records_matched != len(population.kept_ids):
        found.append(f"data_coverage.records_matched={coverage.records_matched} != kept")
    if coverage.records_plotted != len(population.plotted_ids):
        found.append(f"data_coverage.records_plotted={coverage.records_plotted} != plotted")
    return [violation(STRUCTURE, v) for v in found]


def check_network(data: list[Datum]) -> list[str]:
    """Every edge endpoint is a node; an edge's witnesses are in BOTH endpoints (intersection)."""
    nodes: Mapping[str, set[str]] = {
        d.node_id: {c.nct_id for c in d.citations} for d in data if d.kind == "node" and d.node_id
    }
    found: list[str] = []
    for edge in (d for d in data if d.kind == "edge" and d.endpoints):
        missing = [end for end in edge.endpoints or () if end not in nodes]
        if missing:
            found.append(f"{edge.label}: endpoint(s) {missing} are not nodes")
            continue
        source, target = (nodes[end] for end in edge.endpoints or ())
        outside = sorted({c.nct_id for c in edge.citations} - (source & target))[:SHOWN_IDS]
        if outside:
            found.append(f"{edge.label}: witnesses {outside} are not in both endpoint nodes")
    return [violation(STRUCTURE, v) for v in found]
