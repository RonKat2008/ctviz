"""Key facts per trial (§8.4 fast path, `trial_lookup`): one table row per trial, every cell cited.

Each trial is one datum whose predicate is its own NCT ID (so the verifier's soundness and
completeness checks hold trivially), and each fact cell is one `context` evidence item holding the
cell's exact raw JSON Pointer and text -- so the verifier's pointer check re-reads every cell from
the raw record. A field the record lacks is simply not cited (and not shown).
"""

import re
from collections.abc import Mapping
from typing import Any

from ctviz.analysis.aggregate import AggregateResult, Bucket, MatchedTrial
from ctviz.citations import pointer as p
from ctviz.schemas.citations import Citation, Evidence

BRIEF_TITLE = "/protocolSection/identificationModule/briefTitle"
# (column, pointer): one scalar cell each.
SCALAR_FACTS: tuple[tuple[str, str], ...] = (
    ("title", BRIEF_TITLE),
    ("overall_status", p.OVERALL_STATUS),
    ("study_type", p.STUDY_TYPE),
    ("start_date", p.START_DATE),
    ("completion_date", p.COMPLETION_DATE),
    ("enrollment", p.ENROLLMENT_COUNT),
    ("enrollment_type", p.ENROLLMENT_TYPE),
    ("lead_sponsor", p.LEAD_SPONSOR_NAME),
)
# (column, list pointer, leaf key or None): one cell per list item.
LIST_FACTS: tuple[tuple[str, str, str | None], ...] = (
    ("phases", p.PHASES, None),
    ("conditions", p.CONDITIONS, None),
    ("interventions", p.INTERVENTIONS, "name"),
)
KEY_FACT_COLUMNS: tuple[str, ...] = (
    *(column for column, _ in SCALAR_FACTS),
    *(column for column, _, _ in LIST_FACTS),
)
_COLUMN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    *((column, re.compile(re.escape(path))) for column, path in SCALAR_FACTS),
    *(
        (column, re.compile(rf"{re.escape(path)}/\d+" + (f"/{leaf}" if leaf else "")))
        for column, path, leaf in LIST_FACTS
    ),
)


def column_of(pointer: str) -> str | None:
    """The key-fact column a cited pointer fills, or None for any other pointer."""
    return next((c for c, pattern in _COLUMN_PATTERNS if pattern.fullmatch(pointer)), None)


def _resolve(raw: Mapping[str, Any], path: str) -> Any:
    """The value at `path`, or None when the record lacks it."""
    try:
        return p.resolve_pointer(raw, path)
    except (KeyError, IndexError):
        return None


def _cell(raw: Mapping[str, Any], path: str) -> Evidence | None:
    """One cell's evidence: its exact pointer and text; None unless a present scalar."""
    value = _resolve(raw, path)
    if not isinstance(value, str | int | float) or isinstance(value, bool) or value == "":
        return None
    return Evidence(role="context", field=path, excerpt=p.json_text(value))


def _fact_pointers(raw: Mapping[str, Any]) -> list[str]:
    """Every key-fact pointer this record could fill, in column order."""
    scalars = [path for _, path in SCALAR_FACTS]
    items: list[str] = []
    for _, path, leaf in LIST_FACTS:
        values = _resolve(raw, path)
        count = len(values) if isinstance(values, list) else 0
        items += [f"{path}/{i}" + (f"/{leaf}" if leaf else "") for i in range(count)]
    return [*scalars, *items]


def _bucket(matched: MatchedTrial) -> Bucket:
    """One trial's row datum: predicate = its NCT ID; evidence = every key fact it has."""
    trial = matched.trial
    cells = [e for path in _fact_pointers(trial.raw) if (e := _cell(trial.raw, path))]
    citation = Citation(
        nct_id=trial.nct_id,
        field=p.NCT_ID,
        excerpt=trial.nct_id,
        evidence=[*cells, *matched.match_evidence],
    )
    predicate = {"op": "equals", "path": p.NCT_ID, "value": trial.nct_id}
    return Bucket(trial.nct_id, predicate, (citation,))


def key_facts(trials: list[MatchedTrial]) -> AggregateResult:
    """One cited key-facts row per trial, in fetch order; nothing is ever excluded."""
    return AggregateResult(tuple(_bucket(matched) for matched in trials), {})
