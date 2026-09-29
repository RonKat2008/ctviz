"""The single-trial fast path (PLAN.md §8.4): simple NCT-ID lookups skip both LLMs.

The rule is deterministic: NCT IDs are present (the `nct_ids` field or `NCT\\d{8}` in the query),
the query minus the IDs is at most six words, and it contains none of the analytic cue words.
Such a request gets a plan written by code, never by the planner, and the judge is skipped.
"""

import re

from ctviz.schemas.enums import AnalysisKind, VizType
from ctviz.schemas.plan import Analysis, EnumFilters, QueryPlan, VizChoice
from ctviz.schemas.request import VisualizeRequest

NCT_IN_TEXT = re.compile(r"\bNCT\d{8}\b", re.IGNORECASE)
FAST_PATH_MAX_WORDS = 6
# Short function words match exactly ("please" is not "per"); content words match by stem, so
# "trends"/"trending", "comparing"/"comparison", "distributions" all count (fix H). "similar" and
# "like" ask for OTHER trials, which only the planner can search for.
_ANALYTIC_WORDS = frozenset({"by", "per", "vs", "versus"})
_ANALYTIC_STEMS = ("compar", "network", "distribut", "trend", "similar", "like")
_ANALYTIC_PHRASE = "over time"
_WORD = re.compile(r"[a-z0-9']+")


def nct_ids_in(request: VisualizeRequest) -> list[str]:
    """The request's NCT IDs: the structured field first, then any found in the query text."""
    found = [match.upper() for match in NCT_IN_TEXT.findall(request.query)]
    return list(dict.fromkeys([*(request.nct_ids or []), *found]))


def is_fast_path(request: VisualizeRequest) -> list[str] | None:
    """The NCT IDs to look up directly when the §8.4 rule matches; otherwise `None`."""
    nct_ids = nct_ids_in(request)
    if not nct_ids:
        return None
    words = _WORD.findall(NCT_IN_TEXT.sub(" ", request.query).lower())
    is_short = len(words) <= FAST_PATH_MAX_WORDS
    return nct_ids if is_short and not _is_analytic(words) else None


def _is_analytic(words: list[str]) -> bool:
    """Whether the query (minus its IDs) asks for an analysis rather than a lookup (§8.4)."""
    stemmed = any(word.startswith(_ANALYTIC_STEMS) for word in words)
    exact = bool(_ANALYTIC_WORDS.intersection(words))
    return exact or stemmed or _ANALYTIC_PHRASE in " ".join(words)


def fast_path_plan(nct_ids: list[str]) -> QueryPlan:
    """A code-written plan: a cited table of each trial's key facts (§8.4; no LLM involved)."""
    joined = ", ".join(nct_ids)
    return QueryPlan(
        answerable=True,
        out_of_scope_reason=None,
        suggested_reframing=None,
        interpretation=f"Key facts for {joined}.",
        search_terms=[],
        filters=EnumFilters(
            phases=None,
            overall_statuses=None,
            study_types=None,
            intervention_types=None,
            lead_sponsor_classes=None,
            countries=None,
            start_year_min=None,
            start_year_max=None,
            nct_ids=nct_ids,
        ),
        comparison=None,
        analysis=Analysis(
            kind=AnalysisKind.TRIAL_LOOKUP,
            group_by=None,
            series_by=None,
            phase_mode=None,
            time_field=None,
            granularity=None,
            measure_x=None,
            measure_y=None,
            color_by=None,
            network_type=None,
            top_n=None,
        ),
        visualization=VizChoice(
            type=VizType.TABLE, title=f"Key facts: {joined}", rationale="NCT-ID fast path"
        ),
        assumptions=["Answered by the NCT-ID fast path: no LLM planned or judged this request."],
    )
