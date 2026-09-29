"""`meta.entity_resolution` shape (item 5): a plain dict, but its keys/shape must stay stable."""

from ctviz.analysis.aggregate import MatchedTrial
from ctviz.ctgov.client import FetchResult
from ctviz.pipeline_meta import _CohortFetch, _entity_resolution
from tests.fixtures.load import load_trials


def _cohort(label: str, trials: list[MatchedTrial], aliases: tuple[str, ...] = ()) -> _CohortFetch:
    fetch = FetchResult(
        records=[], api_total_count=len(trials), truncated=False, truncation_rule=None
    )
    return _CohortFetch(label, label, trials, fetch, aliases=aliases)


def test_entity_resolution_shape_for_the_pembrolizumab_census() -> None:
    """`entity_resolution` is `{"top_sponsors": [{"name": str, "count": int}, ...]}`, at most 10
    entries, ranked largest-count-first, real (non-uniform) counts."""
    trials = [MatchedTrial(trial, ()) for trial in load_trials("pembrolizumab")]
    cohorts = [_cohort("pembrolizumab", trials)]

    resolution = _entity_resolution(cohorts)

    assert set(resolution) == {"top_sponsors", "aliases"}
    top_sponsors = resolution["top_sponsors"]
    assert isinstance(top_sponsors, list)
    assert 1 <= len(top_sponsors) <= 10
    for entry in top_sponsors:
        assert set(entry) == {"name", "count"}
        assert isinstance(entry["name"], str) and entry["name"]
        assert isinstance(entry["count"], int) and entry["count"] > 0
    counts = [entry["count"] for entry in top_sponsors]
    assert counts == sorted(counts, reverse=True)
    assert len(set(counts)) > 1  # real counts, not every sponsor forced to the same number


def test_entity_resolution_reports_each_cohorts_learned_aliases() -> None:
    """§10.4: "Aliases are reported in meta.entity_resolution" -- per cohort, in order."""
    cohorts = [
        _cohort("Pembrolizumab", [], aliases=("keytruda", "mk-3475")),
        _cohort("Nivolumab", [], aliases=("opdivo",)),
    ]

    resolution = _entity_resolution(cohorts)

    assert resolution["aliases"] == {
        "Pembrolizumab": ["keytruda", "mk-3475"],
        "Nivolumab": ["opdivo"],
    }
