"""`meta.entity_resolution` shape (item 5): a plain dict, but its keys/shape must stay stable."""

from ctviz.agent.orchestrator import PlanningOutcome
from ctviz.analysis.aggregate import MatchedTrial
from ctviz.ctgov.client import FetchResult
from ctviz.pipeline_meta import _CohortFetch, _entity_resolution, _warnings
from tests.factories import make_plan
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


_PROBE_EMPTY = "cohort 'Nivolumab' has 0 trials on ClinicalTrials.gov; it is shown as zero bars"


def _outcome(totals: dict[str, int], warnings: list[str]) -> PlanningOutcome:
    return PlanningOutcome(
        make_plan(), None, "passed_after_revision", 2, [], probe_totals=totals, warnings=warnings
    )


def test_an_empty_cohort_the_probe_already_disclosed_is_warned_about_once() -> None:
    """Fix J: the probe's zero-cohort disclosure and the fetch's zero-bars note are one fact."""
    pembro = [MatchedTrial(t, ()) for t in load_trials("pembrolizumab")[:3]]
    cohorts = [_cohort("Pembrolizumab", pembro), _cohort("Nivolumab", [])]
    outcome = _outcome({"Pembrolizumab": 3, "Nivolumab": 0}, [_PROBE_EMPTY])

    warnings = _warnings(outcome.plan, cohorts, outcome)  # type: ignore[arg-type]

    assert [w for w in warnings if "Nivolumab" in w] == [_PROBE_EMPTY]


def test_a_cohort_emptied_only_by_strict_match_is_still_warned_about() -> None:
    """Fix J scope: the probe saw trials (>0) but strict match kept none -> still disclosed."""
    pembro = [MatchedTrial(t, ()) for t in load_trials("pembrolizumab")[:3]]
    cohorts = [_cohort("Pembrolizumab", pembro), _cohort("Nivolumab", [])]
    outcome = _outcome({"Pembrolizumab": 3, "Nivolumab": 4}, [])

    warnings = _warnings(outcome.plan, cohorts, outcome)  # type: ignore[arg-type]

    assert warnings == ["cohort 'Nivolumab' matched 0 trials; kept as zero bars, not dropped"]
