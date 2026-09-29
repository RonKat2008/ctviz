"""Strict match (Q1=a, PLAN.md §11.3): a trial the API returned via full-text search but which
never actually lists the searched entity is excluded and reported, not silently counted. Also
runs the filter re-check (M3): a record failing an enum filter predicate is dropped too, with its
own stage/reason. Depends on `analysis.aggregate.MatchedTrial` (unlike `verify.py`, this module is
NOT the independent verifier, so it may share types with the aggregation code it feeds)."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from ctviz.analysis.aggregate import MatchedTrial
from ctviz.citations import pointer as p
from ctviz.citations.predicates import evaluate
from ctviz.common.names import text_matches
from ctviz.config import CONDITIONS_STRICT
from ctviz.ctgov.normalize import Trial
from ctviz.schemas.citations import Evidence, Predicate
from ctviz.schemas.enums import SearchParam
from ctviz.schemas.plan import EnumFilters, SearchTerm
from ctviz.schemas.response import ExcludedTrial

MatchPolicy = Literal["strict", "lenient"]
StrictMatchOption = Literal["auto", "all", "off"]

FULLTEXT_ONLY_REASON = "api_fulltext_match_only"
FILTER_MISMATCH_REASON = "api_filter_mismatch"


def strict_match_policy(option: StrictMatchOption) -> Callable[[SearchParam], MatchPolicy]:
    """Q1=c (§22): `request.options.strict_match` -> a per-param policy function. `"auto"` is
    strict for drugs/sponsors, lenient for conditions unless `config.CONDITIONS_STRICT`."""

    def policy(param: SearchParam) -> MatchPolicy:
        if option == "off":
            return "lenient"
        if option == "all":
            return "strict"
        if param is SearchParam.COND:
            return "strict" if CONDITIONS_STRICT else "lenient"
        return "strict"

    return policy


def _resolve_list(trial: Trial, path: str) -> list[Any]:
    """The list at `path` in the raw record, or [] when the section/field is absent."""
    try:
        value = p.resolve_pointer(trial.raw, path)
    except (KeyError, IndexError):
        return []
    return value if isinstance(value, list) else []


def _intervention_name_fields(trial: Trial) -> list[str]:
    return [f"{p.INTERVENTIONS}/{iv.index}/name" for iv in trial.interventions]


def _intervention_other_name_fields(trial: Trial) -> list[str]:
    return [
        f"{p.INTERVENTIONS}/{iv.index}/otherNames/{i}"
        for iv in trial.interventions
        for i in range(len(iv.other_names))
    ]


def _arm_intervention_name_fields(trial: Trial) -> list[str]:
    return [
        f"{p.ARM_GROUPS}/{arm.index}/interventionNames/{i}"
        for arm in trial.arms
        for i in range(len(arm.intervention_names))
    ]


def _intervention_mesh_fields(trial: Trial) -> list[str]:
    meshes = _resolve_list(trial, p.INTERVENTION_MESH)
    return [
        f"{p.INTERVENTION_MESH}/{i}/term"
        for i, m in enumerate(meshes)
        if isinstance(m, dict) and "term" in m
    ]


def _lead_sponsor_fields(trial: Trial) -> list[str]:
    return [p.LEAD_SPONSOR_NAME] if trial.lead_sponsor else []


def _collaborator_fields(trial: Trial) -> list[str]:
    return [f"{p.COLLABORATORS}/{i}/name" for i, _name in trial.collaborators]


def _condition_fields(trial: Trial) -> list[str]:
    return [f"{p.CONDITIONS}/{i}" for i in range(len(trial.conditions))]


def _keyword_fields(trial: Trial) -> list[str]:
    keywords = _resolve_list(trial, p.KEYWORDS)
    return [f"{p.KEYWORDS}/{i}" for i, k in enumerate(keywords) if isinstance(k, str)]


def _condition_mesh_fields(trial: Trial) -> list[str]:
    meshes = _resolve_list(trial, p.CONDITION_MESH)
    return [
        f"{p.CONDITION_MESH}/{i}/term"
        for i, m in enumerate(meshes)
        if isinstance(m, dict) and "term" in m
    ]


def _condition_ancestor_fields(trial: Trial) -> list[str]:
    ancestors = _resolve_list(trial, p.CONDITION_ANCESTORS)
    return [
        f"{p.CONDITION_ANCESTORS}/{i}/term"
        for i, a in enumerate(ancestors)
        if isinstance(a, dict) and "term" in a
    ]


# §11.3 priority order: the first field (in a source, sources tried in this order) that matches
# the term or an alias becomes the `match` evidence. Only these four params get a strict check;
# query.term/titles/outc/locn are "broad_search" and never checked (they're simply absent here).
_MATCH_FIELD_SOURCES: dict[SearchParam, tuple[Callable[[Trial], list[str]], ...]] = {
    SearchParam.INTR: (
        _intervention_name_fields,
        _intervention_other_name_fields,
        _arm_intervention_name_fields,
        _intervention_mesh_fields,
    ),
    SearchParam.LEAD: (_lead_sponsor_fields,),
    SearchParam.SPONS: (_lead_sponsor_fields, _collaborator_fields),
    SearchParam.COND: (
        _condition_fields,
        _keyword_fields,
        _condition_mesh_fields,
        _condition_ancestor_fields,
    ),
}


def _match_evidence(trial: Trial, param: SearchParam, needles: list[str]) -> Evidence | None:
    """The first field (in §11.3 priority order) whose raw text contains the term or an alias."""
    for source in _MATCH_FIELD_SOURCES.get(param, ()):
        for field in source(trial):
            excerpt = p.json_text(p.resolve_pointer(trial.raw, field))
            if any(text_matches(excerpt, needle) for needle in needles):
                return Evidence(role="match", field=field, excerpt=excerpt)
    return None


def _match_predicate_intr(needle: str) -> Predicate:
    return {
        "any": [
            {
                "op": "any_element",
                "path": p.INTERVENTIONS,
                "where": [{"op": "text_matches", "path": "/name", "value": needle}],
            },
            {
                "op": "any_element",
                "path": p.INTERVENTIONS,
                "where": [{"op": "text_matches", "path": "/otherNames", "value": needle}],
            },
            {
                "op": "any_element",
                "path": p.ARM_GROUPS,
                "where": [{"op": "text_matches", "path": "/interventionNames", "value": needle}],
            },
            {
                "op": "any_element",
                "path": p.INTERVENTION_MESH,
                "where": [{"op": "text_matches", "path": "/term", "value": needle}],
            },
        ]
    }


def _match_predicate_lead(needle: str) -> Predicate:
    return {"op": "text_matches", "path": p.LEAD_SPONSOR_NAME, "value": needle}


def _match_predicate_spons(needle: str) -> Predicate:
    return {
        "any": [
            _match_predicate_lead(needle),
            {
                "op": "any_element",
                "path": p.COLLABORATORS,
                "where": [{"op": "text_matches", "path": "/name", "value": needle}],
            },
        ]
    }


def _match_predicate_cond(needle: str) -> Predicate:
    return {
        "any": [
            {"op": "text_matches", "path": p.CONDITIONS, "value": needle},
            {"op": "text_matches", "path": p.KEYWORDS, "value": needle},
            {
                "op": "any_element",
                "path": p.CONDITION_MESH,
                "where": [{"op": "text_matches", "path": "/term", "value": needle}],
            },
            {
                "op": "any_element",
                "path": p.CONDITION_ANCESTORS,
                "where": [{"op": "text_matches", "path": "/term", "value": needle}],
            },
        ]
    }


_MATCH_PREDICATE_BUILDERS: dict[SearchParam, Callable[[str], Predicate]] = {
    SearchParam.INTR: _match_predicate_intr,
    SearchParam.LEAD: _match_predicate_lead,
    SearchParam.SPONS: _match_predicate_spons,
    SearchParam.COND: _match_predicate_cond,
}


def match_predicate(term: SearchTerm, aliases: list[str]) -> Predicate | None:
    """The rule "this record genuinely involves the searched entity" (§11.3); None for broad
    params (query.term/titles/outc/locn), which have no match check at all."""
    builder = _MATCH_PREDICATE_BUILDERS.get(term.param)
    if builder is None:
        return None
    needles = [term.value, *aliases]
    return builder(needles[0]) if len(needles) == 1 else {"any": [builder(n) for n in needles]}


_ENUM_FILTER_CLAUSES: tuple[Callable[[EnumFilters], Predicate | None], ...] = (
    lambda f: (
        {"any": [{"op": "contains", "path": p.PHASES, "value": ph.value} for ph in f.phases]}
        if f.phases
        else None
    ),
    lambda f: (
        {"op": "in", "path": p.OVERALL_STATUS, "value": [s.value for s in f.overall_statuses]}
        if f.overall_statuses
        else None
    ),
    lambda f: (
        {"op": "in", "path": p.STUDY_TYPE, "value": [s.value for s in f.study_types]}
        if f.study_types
        else None
    ),
    lambda f: (
        {
            "op": "any_element",
            "path": p.INTERVENTIONS,
            "where": [
                {"op": "in", "path": "/type", "value": [t.value for t in f.intervention_types]}
            ],
        }
        if f.intervention_types
        else None
    ),
    lambda f: (
        {
            "op": "in",
            "path": p.LEAD_SPONSOR_CLASS,
            "value": [c.value for c in f.lead_sponsor_classes],
        }
        if f.lead_sponsor_classes
        else None
    ),
    lambda f: (
        {
            "op": "any_element",
            "path": p.LOCATIONS,
            "where": [{"op": "in", "path": "/country", "value": f.countries}],
        }
        if f.countries
        else None
    ),
    lambda f: (
        {"op": "year_in_range", "path": p.START_DATE, "value": [f.start_year_min, f.start_year_max]}
        if f.start_year_min or f.start_year_max
        else None
    ),
    lambda f: {"op": "in", "path": p.NCT_ID, "value": f.nct_ids} if f.nct_ids else None,
)


def filter_predicate(filters: EnumFilters | None) -> Predicate | None:
    """The rule "this record satisfies every enum/range filter" (M3), or None when none apply."""
    if filters is None:
        return None
    clauses = [c for build in _ENUM_FILTER_CLAUSES if (c := build(filters)) is not None]
    if not clauses:
        return None
    return clauses[0] if len(clauses) == 1 else {"all": clauses}


@dataclass(frozen=True)
class MatchOutcome:
    """The strict-match/filter re-check result: survivors (with match evidence), the excluded
    list (with stage + reason, §12.6), and the cohort's `base_predicate` (§11.4 item 3)."""

    kept: list[MatchedTrial]
    excluded: list[ExcludedTrial]
    base_predicate: Predicate


def _term_evidence_or_failure(
    trial: Trial, strict_terms: list[SearchTerm], aliases_by_term: dict[str, list[str]]
) -> tuple[list[Evidence], SearchTerm | None]:
    """Every strict term's match evidence, or the first term the trial failed to match."""
    evidence: list[Evidence] = []
    for term in strict_terms:
        needles = [term.value, *aliases_by_term.get(term.value, [])]
        hit = _match_evidence(trial, term.param, needles)
        if hit is None:
            return [], term
        evidence.append(hit)
    return evidence, None


def apply_strict_match(
    trials: list[Trial],
    terms: list[SearchTerm],
    aliases_by_term: dict[str, list[str]],
    policy_for: Callable[[SearchParam], MatchPolicy],
    filters: EnumFilters | None = None,
) -> MatchOutcome:
    """Q1=a: exclude trials failing a strict term's match rule or the filter re-check; lenient
    terms are never checked at all (kept with no match evidence, per §11.3)."""
    strict_terms = [
        t for t in terms if t.param in _MATCH_FIELD_SOURCES and policy_for(t.param) == "strict"
    ]
    filt_predicate = filter_predicate(filters)
    kept: list[MatchedTrial] = []
    excluded: list[ExcludedTrial] = []
    for trial in trials:
        evidence, failed_term = _term_evidence_or_failure(trial, strict_terms, aliases_by_term)
        if failed_term is not None:
            excluded.append(
                ExcludedTrial(nct_id=trial.nct_id, stage="match", reason=FULLTEXT_ONLY_REASON)
            )
            continue
        if filt_predicate is not None and not evaluate(filt_predicate, trial.raw):
            excluded.append(
                ExcludedTrial(nct_id=trial.nct_id, stage="filter", reason=FILTER_MISMATCH_REASON)
            )
            continue
        kept.append(MatchedTrial(trial, tuple(evidence)))
    clauses = [match_predicate(t, aliases_by_term.get(t.value, [])) for t in strict_terms]
    clauses = [c for c in clauses if c is not None]
    if filt_predicate is not None:
        clauses.append(filt_predicate)
    return MatchOutcome(kept, excluded, {"all": clauses})
