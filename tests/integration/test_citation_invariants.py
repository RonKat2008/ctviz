"""Every citation resolves, and witness sets are sound, across all recorded fixtures (§11.6)."""

from collections.abc import Iterator

import pytest

from ctviz.analysis.aggregate import AggregateResult, MatchedTrial, count_by, time_trend
from ctviz.citations.pointer import json_text, resolve_pointer
from ctviz.ctgov.normalize import normalize
from ctviz.schemas.citations import Citation
from ctviz.schemas.enums import Dimension
from tests.fixtures.load import load_fixture

FIXTURE_NAMES = (
    "crohns_p3_completed",
    "glioblastoma",
    "ms_recruiting",
    "nivolumab",
    "nsclc",
    "pembrolizumab",
    "psoriasis_p2",
)
COUNT_BY_DIMENSIONS = (
    Dimension.PHASE,
    Dimension.OVERALL_STATUS,
    Dimension.STUDY_TYPE,
    Dimension.LEAD_SPONSOR_CLASS,
    Dimension.LEAD_SPONSOR,
)
TOP_N = 5


def _matched_trials(fixture: str) -> list[MatchedTrial]:
    """Every record in the fixture, normalized, with no match evidence (irrelevant here)."""
    return [MatchedTrial(normalize(record), ()) for record in load_fixture(fixture)]


def _citations(result: AggregateResult) -> Iterator[Citation]:
    """Every citation across every bucket of a result."""
    for bucket in result.buckets:
        yield from bucket.citations


def _assert_pointers_resolve(raw: dict, citation: Citation) -> None:
    """The primary field and every evidence item resolve in `raw` with an exact-text excerpt."""
    assert json_text(resolve_pointer(raw, citation.field)) == citation.excerpt
    for item in citation.evidence:
        assert json_text(resolve_pointer(raw, item.field)) == item.excerpt


def _assert_no_duplicate_nct_ids(result: AggregateResult) -> None:
    """No trial is cited twice within the same bucket."""
    for bucket in result.buckets:
        ids = [c.nct_id for c in bucket.citations]
        assert len(ids) == len(set(ids))


def _assert_exclusive_across_buckets(result: AggregateResult) -> None:
    """For an exclusive dimension, no trial's nct_id appears in more than one bucket."""
    seen: set[str] = set()
    for bucket in result.buckets:
        ids = {c.nct_id for c in bucket.citations}
        assert not (ids & seen)
        seen |= ids


def _assert_trial_count_matches_citations(result: AggregateResult) -> None:
    """`trial_count` is always derived from `len(citations)`, never tracked separately."""
    for bucket in result.buckets:
        assert bucket.trial_count == len(bucket.citations)


def _assert_completeness(
    result: AggregateResult, raw_by_id: dict[str, dict], record_count: int
) -> None:
    """Σ cited + len(excluded) == records, and every pointer resolves for every citation."""
    cited_ids = {c.nct_id for c in _citations(result)}
    assert len(cited_ids) + len(result.excluded) == record_count
    for citation in _citations(result):
        _assert_pointers_resolve(raw_by_id[citation.nct_id], citation)


@pytest.mark.parametrize("fixture", FIXTURE_NAMES)
def test_every_citation_resolves_and_witness_sets_match_on_all_fixtures(fixture: str) -> None:
    records = load_fixture(fixture)
    raw_by_id = {r["protocolSection"]["identificationModule"]["nctId"]: r for r in records}
    trials = _matched_trials(fixture)

    for dimension in COUNT_BY_DIMENSIONS:
        result = count_by(trials, dimension, top_n=TOP_N)
        _assert_no_duplicate_nct_ids(result)
        _assert_exclusive_across_buckets(result)
        _assert_trial_count_matches_citations(result)
        _assert_completeness(result, raw_by_id, len(records))

    time_result = time_trend(trials, today_year=2026)
    _assert_no_duplicate_nct_ids(time_result)
    _assert_exclusive_across_buckets(time_result)
    _assert_trial_count_matches_citations(time_result)
    _assert_completeness(time_result, raw_by_id, len(records))
