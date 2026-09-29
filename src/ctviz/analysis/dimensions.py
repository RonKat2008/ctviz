"""Per-dimension extractors: which bucket(s) a trial belongs to, with the exact evidence why."""

from collections.abc import Callable
from dataclasses import dataclass
from types import MappingProxyType

from ctviz.citations import pointer as p
from ctviz.ctgov.normalize import NON_INTERVENTIONAL, Trial
from ctviz.schemas.citations import Evidence, Predicate
from ctviz.schemas.enums import Dimension

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
    }
)


def extract(trial: Trial, dimension: Dimension) -> list[DimensionHit]:
    """Bucket(s) for this trial; [] means the trial lacks the field (an analysis exclusion)."""
    return _EXTRACTORS[dimension](trial)
