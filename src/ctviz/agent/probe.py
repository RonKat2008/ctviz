"""The probe (PLAN.md §9.1): one 1-record count per cohort, before any real fetch.

The cheapest, most objective check on a plan: a drug searched as a condition usually returns 0,
and a question matching more than `max_records` trials will be truncated. The §9.1 table decides
what each total means on attempt 1 (revise, with feedback the planner can act on) versus attempt
2 (stop with NO_MATCHING_TRIALS, or proceed and disclose). `CtGovClient` caches the probe, so the
real fetch's own count request later costs nothing.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Literal, Protocol

from ctviz.ctgov.compiler import RequestSpec, compile_plan
from ctviz.schemas.plan import QueryPlan

ProbeVerdict = Literal["ok", "revise", "no_matches", "too_broad_accept"]
FIRST_ATTEMPT, SECOND_ATTEMPT = 1, 2
_NO_FILTERS = "all trials (no search terms or filters)"


class ProbeClient(Protocol):
    """The one `CtGovClient` method the probe needs (a fake in tests)."""

    async def probe(self, params: dict[str, str]) -> int:
        """Total matching trials for these params."""
        ...


@dataclass(frozen=True)
class ProbeResult:
    """Per-cohort totals, revise feedback for the planner, the §9.1 verdict, and disclosures."""

    totals: dict[str, int]
    feedback: list[str]
    verdict: ProbeVerdict
    warnings: list[str] = field(default_factory=list)


def _filter_value(value: object) -> str:
    """A filter value as plain text: enum lists become ['PHASE3'], scalars stay as-is."""
    if isinstance(value, list):
        return repr([str(item) for item in value])
    return str(value)


def droppable_filters(plan: QueryPlan) -> list[str]:
    """Every search term, comparison and set filter -- what a user could drop or loosen."""
    items = [f"{t.param.value}='{t.value}'" for t in plan.search_terms]
    if plan.comparison is not None:
        vary = plan.comparison.vary_param.value
        items.append(f"comparison {vary} in {plan.comparison.values!r}")
    if plan.filters is not None:
        set_filters = plan.filters.model_dump(exclude_none=True)
        items += [f"filters.{name}={_filter_value(v)}" for name, v in set_filters.items()]
    return items


def _describe(plan: QueryPlan, spec: RequestSpec) -> str:
    """How one cohort was searched, in the plan's own terms (for feedback and warnings)."""
    if plan.comparison is not None and spec.cohort_value is not None:
        return f"comparison value {plan.comparison.vary_param.value}='{spec.cohort_value}'"
    return ", ".join(droppable_filters(plan)) or _NO_FILTERS


def _zero_feedback(plan: QueryPlan, spec: RequestSpec) -> str:
    """Attempt-1 feedback for a cohort the API reports 0 trials for."""
    if plan.comparison is not None:
        return (
            f"0 trials for {_describe(plan, spec)}; double-check that value's spelling and "
            "whether it belongs on a different param."
        )
    return (
        f"0 trials for {_describe(plan, spec)}. Check each entity's param (a drug belongs on "
        "query.intr, a condition on query.cond) and its spelling, or drop a filter that is too "
        "narrow."
    )


def _over_cap_feedback(plan: QueryPlan, spec: RequestSpec, total: int, cap: int) -> str:
    """Attempt-1 feedback for a cohort larger than the fetch cap."""
    return (
        f"{total:,} trials for {_describe(plan, spec)} exceed the {cap:,}-record cap; if the "
        "question implies a narrower scope (phase, status, years, country), add that filter, "
        f"otherwise keep the plan (the most recent {cap:,} by start date will be analyzed)."
    )


def _first_attempt(
    plan: QueryPlan, specs: list[RequestSpec], totals: dict[str, int], cap: int
) -> ProbeResult:
    """Attempt 1: any empty or over-cap cohort earns one revise, with feedback for each."""
    feedback = [
        _zero_feedback(plan, spec)
        if totals[spec.cohort_label] == 0
        else _over_cap_feedback(plan, spec, totals[spec.cohort_label], cap)
        for spec in specs
        if totals[spec.cohort_label] == 0 or totals[spec.cohort_label] > cap
    ]
    return ProbeResult(totals, feedback, "revise" if feedback else "ok")


def _second_attempt(totals: dict[str, int], cap: int) -> ProbeResult:
    """Attempt 2: all empty -> no_matches; else proceed, disclosing empty cohorts (zero bars)."""
    empty = [label for label, total in totals.items() if total == 0]
    if len(empty) == len(totals):
        return ProbeResult(totals, [], "no_matches")
    warnings = [
        f"cohort '{label}' has 0 trials on ClinicalTrials.gov; it is shown as zero bars"
        for label in empty
    ]
    too_broad = any(total > cap for total in totals.values())
    return ProbeResult(totals, [], "too_broad_accept" if too_broad else "ok", warnings)


async def probe_plan(
    plan: QueryPlan, client: ProbeClient, max_records: int, attempt: int
) -> ProbeResult:
    """Probe every cohort of `plan` concurrently and apply the §9.1 table for this attempt."""
    if attempt not in (FIRST_ATTEMPT, SECOND_ATTEMPT):
        raise ValueError(f"attempt must be 1 or 2, got {attempt}")
    specs = compile_plan(plan)
    counts = await asyncio.gather(*(client.probe(spec.params) for spec in specs))
    totals = {spec.cohort_label: count for spec, count in zip(specs, counts, strict=True)}
    if attempt == FIRST_ATTEMPT:
        return _first_attempt(plan, specs, totals, max_records)
    return _second_attempt(totals, max_records)
