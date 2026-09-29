"""The digit guard (PLAN.md §7.4 check 9, D11): no number in plan prose the user didn't supply.

The planner writes a title, an interpretation and assumptions. Any digit token in them must be
traceable to the request (the query text, a structured field, an NCT id) or to the plan's own
code-checked slots (filter years, phase numbers, search values, the effective top_n). Otherwise
the text is repaired in code -- title -> template, interpretation -> a code-rendered sentence,
assumption sentence -> dropped -- and each repair is noted for `meta.adjustments`. It never
triggers a revise: this is prose hygiene, not a planning error.
"""

import re

from ctviz.analysis.prune import MAX_NODES_DEFAULT
from ctviz.schemas.enums import AnalysisKind, Dimension
from ctviz.schemas.plan import Analysis, QueryPlan
from ctviz.schemas.request import VisualizeRequest

_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+")
_PHASE_BUCKET_DIGITS = frozenset({"1", "2", "3", "4"})
_REQUEST_VALUE_FIELDS = (
    "drug_name",
    "condition",
    "sponsor",
    "country",
    "start_year",
    "end_year",
    "study_type",
    "trial_phase",
    "nct_ids",
)
_NO_ENTITY = "All trials"
_TRIAL_COUNT = "Trial count"


def _numbers(text: str) -> list[str]:
    """Every digit token in `text`, in order of appearance ("COVID-19" -> ["19"])."""
    return _NUMBER.findall(text)


def _request_texts(request: VisualizeRequest) -> list[str]:
    """The query plus every structured field value, rendered as text."""
    texts = [request.query]
    for name in _REQUEST_VALUE_FIELDS:
        value = getattr(request, name)
        if value is not None:
            texts += [str(v) for v in value] if isinstance(value, list) else [str(value)]
    return texts


def _plan_texts(plan: QueryPlan) -> list[str]:
    """Search values, compared values, NCT ids, filter years and phases, as text."""
    texts = [term.value for term in plan.search_terms]
    texts += plan.comparison.values if plan.comparison else []
    filters = plan.filters
    if filters is not None:
        texts += filters.nct_ids or []
        texts += [str(y) for y in (filters.start_year_min, filters.start_year_max) if y]
        texts += [phase.value for phase in filters.phases or []]
    return texts


def _analysis_numbers(analysis: Analysis | None) -> set[str]:
    """Phase bucket labels (when grouped by phase) and the effective top_n."""
    if analysis is None:
        return set()
    allowed: set[str] = set()
    if Dimension.PHASE in (analysis.group_by, analysis.series_by):
        allowed |= _PHASE_BUCKET_DIGITS
    top_n = analysis.top_n
    if top_n is None and analysis.kind is AnalysisKind.NETWORK:
        top_n = MAX_NODES_DEFAULT
    if top_n is not None:
        allowed.add(str(top_n))
    return allowed


def allowed_numbers(plan: QueryPlan, request: VisualizeRequest) -> frozenset[str]:
    """Every digit token plan prose may contain, built exactly as §7.4 check 9 lists them."""
    texts = [*_request_texts(request), *_plan_texts(plan)]
    tokens = {token for text in texts for token in _numbers(text)}
    return frozenset(tokens | _analysis_numbers(plan.analysis))


def _unsupported(text: str, allowed: frozenset[str]) -> list[str]:
    """The digit tokens in `text` that are not allowed, de-duplicated, in order."""
    return list(dict.fromkeys(n for n in _numbers(text) if n not in allowed))


def _label(value: str) -> str:
    """A menu value as display words ("duration_months" -> "duration months")."""
    return value.replace("_", " ")


def _measure_and_dimension(analysis: Analysis) -> tuple[str, str]:
    """The template's {Measure} and {dimension} for this analysis kind."""
    by_kind = {
        AnalysisKind.COUNT_BY: (_TRIAL_COUNT, analysis.group_by),
        AnalysisKind.TIME_TREND: (_TRIAL_COUNT, analysis.time_field),
        AnalysisKind.HISTOGRAM: (_TRIAL_COUNT, analysis.measure_x),
        AnalysisKind.SCATTER: (
            _label(str(analysis.measure_y or "")).capitalize(),
            analysis.measure_x,
        ),
        AnalysisKind.NETWORK: ("Network", analysis.network_type),
    }
    measure, dimension = by_kind.get(analysis.kind, ("Trials", None))
    return measure, _label(str(dimension)) if dimension is not None else "NCT ID"


def _entity(plan: QueryPlan) -> str:
    """The template's {entity}: compared values, else search values, else NCT ids."""
    if plan.comparison is not None:
        return " vs ".join(plan.comparison.values)
    if plan.search_terms:
        return ", ".join(term.value for term in plan.search_terms)
    nct_ids = plan.filters.nct_ids if plan.filters else None
    return ", ".join(nct_ids) if nct_ids else _NO_ENTITY


def _numbers_note(numbers: list[str]) -> str:
    """The shared "(unsupported number(s): ...)" suffix of every adjustment note."""
    return f"(unsupported number(s): {', '.join(numbers)})"


def _guard_title(
    plan: QueryPlan, analysis: Analysis, allowed: frozenset[str]
) -> tuple[QueryPlan, list[str]]:
    """Swap a title carrying an unsupported number for `"{Measure} by {dimension}: {entity}"`."""
    viz = plan.visualization
    bad = _unsupported(viz.title, allowed) if viz else []
    if viz is None or not bad:
        return plan, []
    measure, dimension = _measure_and_dimension(analysis)
    title = f"{measure} by {dimension}: {_entity(plan)}"
    note = f"title {viz.title!r} -> {title!r} {_numbers_note(bad)}"
    new_viz = viz.model_copy(update={"title": title})
    return plan.model_copy(update={"visualization": new_viz}), [note]


def _guard_interpretation(
    plan: QueryPlan, analysis: Analysis, allowed: frozenset[str]
) -> tuple[QueryPlan, list[str]]:
    """Replace an interpretation carrying an unsupported number with a code-rendered sentence."""
    bad = _unsupported(plan.interpretation, allowed)
    if not bad:
        return plan, []
    measure, dimension = _measure_and_dimension(analysis)
    sentence = f"{measure} by {dimension} for {_entity(plan)}."
    note = f"interpretation replaced by a code-rendered sentence {_numbers_note(bad)}"
    return plan.model_copy(update={"interpretation": sentence}), [note]


def _guard_assumption(assumption: str, allowed: frozenset[str]) -> tuple[str, list[str]]:
    """One assumption with its unsupported sentences dropped ('' if none survive), plus notes."""
    kept, notes = [], []
    for sentence in _SENTENCE_BREAK.split(assumption.strip()):
        bad = _unsupported(sentence, allowed)
        if bad:
            notes.append(f"assumption sentence dropped: {sentence!r} {_numbers_note(bad)}")
        else:
            kept.append(sentence)
    return " ".join(kept), notes


def _guard_assumptions(plan: QueryPlan, allowed: frozenset[str]) -> tuple[QueryPlan, list[str]]:
    """Drop every assumption sentence carrying an unsupported number (and emptied assumptions)."""
    results = [_guard_assumption(a, allowed) for a in plan.assumptions]
    notes = [note for _text, item_notes in results for note in item_notes]
    if not notes:
        return plan, []
    assumptions = [text for text, _notes in results if text]
    return plan.model_copy(update={"assumptions": assumptions}), notes


def guard_text(plan: QueryPlan, request: VisualizeRequest) -> tuple[QueryPlan, list[str]]:
    """Make title/interpretation/assumptions number-safe; return the new plan + adjustment notes."""
    analysis = plan.analysis
    if analysis is None:
        return plan, []
    allowed = allowed_numbers(plan, request)
    titled, title_notes = _guard_title(plan, analysis, allowed)
    interpreted, interpretation_notes = _guard_interpretation(titled, analysis, allowed)
    guarded, assumption_notes = _guard_assumptions(interpreted, allowed)
    return guarded, [*title_notes, *interpretation_notes, *assumption_notes]
