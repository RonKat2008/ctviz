"""Per-dimension extractors: which bucket(s) a trial belongs to, with the exact evidence why.

Q-D (item 4): condition predicates use `normalizes_to` with `fn="text"` (`normalize_text`,
any-element on the array path), not the non-§11.6 op `any_text_equals`. S6's verifier must
implement `fn` (default `"drug"`) and any-element semantics for an array-valued `path`.
"""

from collections.abc import Callable
from dataclasses import dataclass
from types import MappingProxyType

from ctviz.citations import pointer as p
from ctviz.common.names import normalize_drug, normalize_text
from ctviz.ctgov.normalize import NON_INTERVENTIONAL, Trial
from ctviz.schemas.citations import Evidence, Predicate
from ctviz.schemas.enums import Dimension

DRUG_INTERVENTION_TYPES = ("DRUG", "BIOLOGICAL")

EXCLUSIVE_DIMENSIONS = frozenset(
    {
        Dimension.PHASE,
        Dimension.OVERALL_STATUS,
        Dimension.STUDY_TYPE,
        Dimension.LEAD_SPONSOR_CLASS,
        Dimension.LEAD_SPONSOR,
        Dimension.START_YEAR,
    }
)
NON_INTERVENTIONAL_TYPES = ["OBSERVATIONAL", "EXPANDED_ACCESS"]


@dataclass(frozen=True)
class DimensionHit:
    """One bucket a trial belongs to: its key, the evidence, and the machine-checkable rule."""

    key: str
    evidence: tuple[Evidence, ...]
    predicate: Predicate


def _bucket_from_raw(trial: Trial, field: str) -> Evidence:
    """Bucket evidence read straight from `trial.raw`, so the excerpt is the exact API text.

    The bucket KEY and any predicate `value` still use the normalized (stripped) value; only
    the excerpt must match the untouched raw record exactly (PLAN.md §11.4).
    """
    excerpt = p.json_text(p.resolve_pointer(trial.raw, field))
    return Evidence(role="bucket", field=field, excerpt=excerpt)


def _phase(trial: Trial) -> list[DimensionHit]:
    """Combined phase bucket: multi-phase trials cite every array element (§10.3, §11.5)."""
    if trial.phases:
        fields = (f"{p.PHASES}/{i}" for i in range(len(trial.phases)))
        evidence = tuple(_bucket_from_raw(trial, field) for field in fields)
        predicate: Predicate = {"op": "set_equals", "path": p.PHASES, "value": list(trial.phases)}
        return [DimensionHit(trial.phase_label, evidence, predicate)]
    if trial.study_type is None:
        return []
    evidence = (_bucket_from_raw(trial, p.STUDY_TYPE),)
    types = (
        NON_INTERVENTIONAL_TYPES if trial.phase_label == NON_INTERVENTIONAL else ["INTERVENTIONAL"]
    )
    no_phase_predicate: Predicate = {
        "all": [
            {"not": {"op": "exists", "path": p.PHASES}},
            {"op": "in", "path": p.STUDY_TYPE, "value": types},
        ]
    }
    return [DimensionHit(trial.phase_label, evidence, no_phase_predicate)]


def _scalar(trial: Trial, path: str, value: str | None) -> list[DimensionHit]:
    """A single-valued dimension backed by one scalar field; [] when the trial has no value."""
    if value is None:
        return []
    predicate: Predicate = {"op": "equals", "path": path, "value": value}
    return [DimensionHit(value, (_bucket_from_raw(trial, path),), predicate)]


def _start_year(trial: Trial) -> list[DimensionHit]:
    """Start-year bucket, keyed by the year of the (possibly partial) start date."""
    if trial.start is None:
        return []
    year = str(trial.start.year)
    return [
        DimensionHit(
            year,
            (_bucket_from_raw(trial, p.START_DATE),),
            {"op": "year_equals", "path": p.START_DATE, "value": trial.start.year},
        )
    ]


def _dedup_first(pairs: list[tuple[str, int]]) -> dict[str, int]:
    """First array index seen for each key, in first-seen order (one bucket per trial)."""
    first: dict[str, int] = {}
    for key, index in pairs:
        first.setdefault(key, index)
    return first


def _intervention_type(trial: Trial) -> list[DimensionHit]:
    """Intervention type, deduped per trial: each type counted once, cites the first element."""
    first = _dedup_first([(iv.type, iv.index) for iv in trial.interventions if iv.type is not None])
    hits = []
    for itype, index in first.items():
        field = f"{p.INTERVENTIONS}/{index}/type"
        predicate: Predicate = {
            "op": "any_element",
            "path": p.INTERVENTIONS,
            "where": [{"op": "equals", "path": "/type", "value": itype}],
        }
        hits.append(DimensionHit(itype, (_bucket_from_raw(trial, field),), predicate))
    return hits


def _intervention_drug(trial: Trial) -> list[DimensionHit]:
    """Drug/biological interventions, keyed by `normalize_drug` (placebo/SOC drop out, §10.4)."""
    candidates = [
        (normalize_drug(iv.name), iv.index)
        for iv in trial.interventions
        if iv.type in DRUG_INTERVENTION_TYPES
    ]
    first = _dedup_first([(key, index) for key, index in candidates if key is not None])
    hits = []
    for key, index in first.items():
        field = f"{p.INTERVENTIONS}/{index}/name"
        predicate: Predicate = {
            "op": "any_element",
            "path": p.INTERVENTIONS,
            "where": [
                {"op": "in", "path": "/type", "value": list(DRUG_INTERVENTION_TYPES)},
                {"op": "normalizes_to", "path": "/name", "value": key},
            ],
        }
        hits.append(DimensionHit(key, (_bucket_from_raw(trial, field),), predicate))
    return hits


def _condition(trial: Trial) -> list[DimensionHit]:
    """Conditions, keyed by `normalize_text`, deduped per trial (lenient match, Q1)."""
    first = _dedup_first([(normalize_text(c), i) for i, c in enumerate(trial.conditions)])
    hits = []
    for key, index in first.items():
        field = f"{p.CONDITIONS}/{index}"
        predicate: Predicate = {
            "op": "normalizes_to",
            "path": p.CONDITIONS,
            "value": key,
            "fn": "text",
        }
        hits.append(DimensionHit(key, (_bucket_from_raw(trial, field),), predicate))
    return hits


def _country_any_site(trial: Trial) -> list[DimensionHit]:
    """One bucket per trial per country with >=1 site there, citing the first matching site."""
    first = _dedup_first([(s.country, s.index) for s in trial.sites if s.country is not None])
    hits = []
    for country, index in first.items():
        field = f"{p.LOCATIONS}/{index}/country"
        predicate: Predicate = {
            "op": "any_element",
            "path": p.LOCATIONS,
            "where": [{"op": "equals", "path": "/country", "value": country}],
        }
        hits.append(DimensionHit(country, (_bucket_from_raw(trial, field),), predicate))
    return hits


def _country_recruiting(trial: Trial) -> list[DimensionHit]:
    """Country buckets requiring a recruiting site there; falls back to trial status when every
    site status is null (§10.3, §11.5)."""
    all_statuses_null = all(s.status is None for s in trial.sites)
    if all_statuses_null:
        candidates = [(s.country, s.index) for s in trial.sites if s.country is not None]
    else:
        candidates = [
            (s.country, s.index)
            for s in trial.sites
            if s.country is not None and s.status == "RECRUITING"
        ]
    first = _dedup_first(candidates)
    hits = []
    for country, index in first.items():
        country_field = f"{p.LOCATIONS}/{index}/country"
        evidence = [_bucket_from_raw(trial, country_field)]
        if all_statuses_null:
            evidence.append(_bucket_from_raw(trial, p.OVERALL_STATUS))
            predicate: Predicate = {
                "all": [
                    {
                        "op": "any_element",
                        "path": p.LOCATIONS,
                        "where": [{"op": "equals", "path": "/country", "value": country}],
                    },
                    {"op": "equals", "path": p.OVERALL_STATUS, "value": "RECRUITING"},
                ]
            }
        else:
            status_field = f"{p.LOCATIONS}/{index}/status"
            evidence.append(_bucket_from_raw(trial, status_field))
            predicate = {
                "op": "any_element",
                "path": p.LOCATIONS,
                "where": [
                    {"op": "equals", "path": "/country", "value": country},
                    {"op": "equals", "path": "/status", "value": "RECRUITING"},
                ],
            }
        hits.append(DimensionHit(country, tuple(evidence), predicate))
    return hits


def country_extractor(recruiting_only: bool) -> Callable[[Trial], list[DimensionHit]]:
    """Country extractor factory: the recruiting rule depends on the plan, not just the trial."""
    return _country_recruiting if recruiting_only else _country_any_site


_EXTRACTORS: MappingProxyType[Dimension, Callable[[Trial], list[DimensionHit]]] = MappingProxyType(
    {
        Dimension.PHASE: _phase,
        Dimension.START_YEAR: _start_year,
        Dimension.OVERALL_STATUS: lambda t: _scalar(t, p.OVERALL_STATUS, t.status),
        Dimension.STUDY_TYPE: lambda t: _scalar(t, p.STUDY_TYPE, t.study_type),
        Dimension.LEAD_SPONSOR_CLASS: (
            lambda t: _scalar(t, p.LEAD_SPONSOR_CLASS, t.lead_sponsor_class)
        ),
        Dimension.LEAD_SPONSOR: lambda t: _scalar(t, p.LEAD_SPONSOR_NAME, t.lead_sponsor),
        Dimension.INTERVENTION_TYPE: _intervention_type,
        Dimension.INTERVENTION: _intervention_drug,
        Dimension.CONDITION: _condition,
        Dimension.COUNTRY: country_extractor(recruiting_only=False),
    }
)


def extract(trial: Trial, dimension: Dimension) -> list[DimensionHit]:
    """Bucket(s) for this trial; [] means the trial lacks the field (an analysis exclusion)."""
    return _EXTRACTORS[dimension](trial)
