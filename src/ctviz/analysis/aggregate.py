"""Aggregation where counting and citing are one step: a trial joins a bucket with its evidence."""

from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from ctviz.analysis.dimensions import DimensionHit, extract
from ctviz.citations.pointer import START_DATE
from ctviz.ctgov.normalize import PHASE_DISPLAY_ORDER, Trial
from ctviz.schemas.citations import Citation, Evidence, Predicate
from ctviz.schemas.enums import Dimension

OTHER_KEY = "Other"
DEFAULT_MISSING_REASON = "missing_dimension_value"
MISSING_REASON = {
    Dimension.PHASE: "missing_phase_and_study_type",
    Dimension.OVERALL_STATUS: "missing_overall_status",
    Dimension.STUDY_TYPE: "missing_study_type",
    Dimension.LEAD_SPONSOR_CLASS: "missing_lead_sponsor_class",
    Dimension.LEAD_SPONSOR: "missing_lead_sponsor",
    Dimension.START_YEAR: "missing_start_date",
}


@dataclass(frozen=True)
class MatchedTrial:
    """A trial paired with the evidence that it belongs in the current cohort (§11.3 `match`)."""

    trial: Trial
    match_evidence: tuple[Evidence, ...]


@dataclass(frozen=True)
class Bucket:
    """One datum: its key, the rule a trial must satisfy, and every trial that satisfies it."""

    key: str
    predicate: Predicate
    citations: tuple[Citation, ...]
    flags: tuple[str, ...] = ()
    folded_categories: int = 0

    @property
    def trial_count(self) -> int:
        """Derived from the citations, never tracked separately (§11.2)."""
        return len(self.citations)


@dataclass(frozen=True)
class AggregateResult:
    """The buckets for one dimension, plus per-trial exclusion reasons (nct_id -> reason)."""

    buckets: tuple[Bucket, ...]
    excluded: dict[str, str]


def _citation(matched: MatchedTrial, hit: DimensionHit) -> Citation:
    """Assemble one trial's citation: primary bucket evidence plus every supporting item."""
    primary, *extra = hit.evidence
    return Citation(
        nct_id=matched.trial.nct_id,
        field=primary.field,
        excerpt=primary.excerpt,
        evidence=[*extra, *matched.match_evidence],
    )


def _group(
    trials: list[MatchedTrial],
    dimension: Dimension,
    extractor: Callable[[Trial], list[DimensionHit]] | None = None,
) -> tuple[dict[str, list[Citation]], dict[str, Predicate], dict[str, str]]:
    """Bucket every trial's citations by key, recording each key's predicate once."""
    extract_fn = extractor or (lambda t: extract(t, dimension))
    citations: dict[str, list[Citation]] = defaultdict(list)
    predicates: dict[str, Predicate] = {}
    excluded: dict[str, str] = {}
    for matched in trials:
        hits = extract_fn(matched.trial)
        if not hits:
            excluded[matched.trial.nct_id] = MISSING_REASON.get(dimension, DEFAULT_MISSING_REASON)
        for hit in hits:
            citations[hit.key].append(_citation(matched, hit))
            predicates.setdefault(hit.key, hit.predicate)
    return citations, predicates, excluded


def _count_rank(keys: list[str], counts: dict[str, int]) -> list[str]:
    """Largest-count-first order (ties broken by key); used only to choose top_n survivors."""
    return sorted(keys, key=lambda k: (-counts[k], k))


def _order(keys: list[str], counts: dict[str, int], dimension: Dimension) -> list[str]:
    """Display order: fixed for phase, chronological for start_year, count-desc otherwise."""
    if dimension is Dimension.PHASE:
        return sorted(keys, key=PHASE_DISPLAY_ORDER.index)
    if dimension is Dimension.START_YEAR:
        return sorted(keys, key=int)
    return _count_rank(keys, counts)


def count_by(
    trials: list[MatchedTrial],
    dimension: Dimension,
    top_n: int | None = None,
    extractor: Callable[[Trial], list[DimensionHit]] | None = None,
) -> AggregateResult:
    """Distinct trials per category: top-N LARGEST by count, then a cited 'Other' row, in
    display order. `extractor` overrides the registered one (e.g. the recruiting-site rule)."""
    citations, predicates, excluded = _group(trials, dimension, extractor)
    counts = {k: len(v) for k, v in citations.items()}
    ranked = _count_rank(list(citations), counts)
    keep, rest = (ranked[:top_n], ranked[top_n:]) if top_n else (ranked, [])
    ordered_keep = _order(keep, counts, dimension)
    buckets = [Bucket(k, predicates[k], tuple(citations[k])) for k in ordered_keep]
    if rest:
        other_predicate = {"not": {"any": [predicates[k] for k in keep]}}
        other_citations = _dedupe_by_nct_id(c for k in rest for c in citations[k])
        other = Bucket(OTHER_KEY, other_predicate, other_citations, folded_categories=len(rest))
        buckets.append(other)
    return AggregateResult(tuple(buckets), excluded)


def _dedupe_by_nct_id(citations: Iterable[Citation]) -> tuple[Citation, ...]:
    """First citation per trial, in encounter order: a multi-valued dim can fold the same trial
    into several rolled-up keys, but the 'Other' row must still cite it only once (§11.6)."""
    first: dict[str, Citation] = {}
    for citation in citations:
        first.setdefault(citation.nct_id, citation)
    return tuple(first.values())


def time_trend(trials: list[MatchedTrial], today_year: int) -> AggregateResult:
    """Trials per start year, with empty years filled and current/future years flagged."""
    citations, predicates, excluded = _group(trials, Dimension.START_YEAR)
    if not citations:
        return AggregateResult((), excluded)
    years = range(min(map(int, citations)), max(map(int, citations)) + 1)
    buckets = []
    for year in years:
        key = str(year)
        if year == today_year:
            flags: tuple[str, ...] = ("partial_period",)
        elif year > today_year:
            flags = ("projected",)
        else:
            flags = ()
        predicate = predicates.get(key, {"op": "year_equals", "path": START_DATE, "value": year})
        buckets.append(Bucket(key, predicate, tuple(citations.get(key, [])), flags))
    return AggregateResult(tuple(buckets), excluded)
