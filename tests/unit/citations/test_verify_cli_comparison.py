"""Offline comparison populations: a record belongs to the cohort whose fetch returned it.

Regression for the live pembrolizumab-vs-nivolumab example: the raw file is the union of both
cohorts' fetches, and a nivolumab-only fetch returned a trial that also mentions pembrolizumab.
Rebuilding each cohort as "every shipped record satisfying its base predicate" wrongly put that
trial in the pembrolizumab cohort; the live pipeline never did.
"""

from typing import Any

from ctviz.citations.verify_cli import CohortFetch, comparison_populations
from ctviz.schemas.response import CohortSummary

EVERYONE = {"op": "exists", "path": "/protocolSection/identificationModule/nctId"}


def _record(nct_id: str) -> dict[str, Any]:
    return {"protocolSection": {"identificationModule": {"nctId": nct_id}}}


def _cohort(label: str) -> CohortSummary:
    return CohortSummary(
        label=label,
        value=label,
        api_total_count=1,
        records_matched=1,
        records_plotted=1,
        base_predicate=EVERYONE,
    )


def test_comparison_population_uses_the_fetch_that_returned_each_record() -> None:
    raw_by_id = {"NCT00000001": _record("NCT00000001"), "NCT00000002": _record("NCT00000002")}
    fetches = [
        CohortFetch(params={"query.intr": "drug-a"}, nct_ids=frozenset({"NCT00000001"})),
        CohortFetch(params={"query.intr": "drug-b"}, nct_ids=frozenset({"NCT00000002"})),
    ]

    populations = comparison_populations([_cohort("drug-a"), _cohort("drug-b")], raw_by_id, fetches)

    assert populations["drug-a"].kept_ids == frozenset({"NCT00000001"})
    assert populations["drug-b"].kept_ids == frozenset({"NCT00000002"})


def test_comparison_population_falls_back_to_base_predicate_without_fetch_records() -> None:
    raw_by_id = {"NCT00000001": _record("NCT00000001"), "NCT00000002": _record("NCT00000002")}

    populations = comparison_populations([_cohort("drug-a")], raw_by_id, [])

    assert populations["drug-a"].kept_ids == frozenset(raw_by_id)
