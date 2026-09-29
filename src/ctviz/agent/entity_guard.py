"""The entity guard: planner prose may not name an entity nothing upstream mentions.

Companion to the digit guard (same shape: `(plan, notes)`). The planner's title, interpretation
and assumptions must not introduce drug/condition/sponsor/country names or NCT ids that appear in
none of: the user's request (query + structured fields), the structured field names, the plan's
own code-checked values, or the catalog vocabulary. Repairs mirror the digit guard: title and
interpretation fall back to code-built text, an offending assumption sentence is dropped, and each
repair is noted for `meta.adjustments`. Deliberately conservative: it flags only NCT ids,
acronyms, drug-suffix words and capitalized mid-sentence words (any capitalized word in a title).
"""

import re
from enum import StrEnum
from functools import lru_cache

from ctviz.agent.digit_guard import (
    _REQUEST_VALUE_FIELDS,
    _SENTENCE_BREAK,
    _entity,
    _measure_and_dimension,
    _plan_texts,
    _request_texts,
)
from ctviz.catalog.loader import load_catalog
from ctviz.schemas import enums
from ctviz.schemas.plan import Analysis, QueryPlan
from ctviz.schemas.request import VisualizeRequest

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]*")
_NCT_LIKE = re.compile(r"NCT\d+", re.IGNORECASE)
_DRUG_SUFFIX = re.compile(
    r"[a-z]{3,}(?:mab|nib|vir|statin|stat|pril|sartan|olol|cillin|mycin|parib|ciclib)",
    re.IGNORECASE,
)
_NEXT_WORD = re.compile(r"\s+([A-Za-z]+)")
_MIN_ACRONYM_LEN = 3
# A sentence-opening unknown word is only an entity when what follows reads like a predicate on
# a named thing ("Zorbinex is ...", "Zorbinex trials ..."); ordinary openers ("Assuming ...") pass.
_ENTITY_FOLLOWERS = frozenset(
    (
        "is",
        "are",
        "was",
        "were",
        "has",
        "have",
        "refers",
        "means",
        "denotes",
        "and",
        "vs",
        "versus",
        "as",
        "trial",
        "trials",
        "study",
        "studies",
        "treatment",
        "therapy",
        "arm",
        "arms",
        "drug",
    )
)
_SENTENCE_END = (".", "!", "?")
_GENERIC_WORDS = frozenset(
    (
        # quantity/summary words that open titles ("Total trials ...", "Overall share ...")
        "total",
        "overall",
        "overview",
        "summary",
        "number",
        "count",
        "counts",
        "share",
        "shares",
        "distribution",
        "landscape",
        "top",
        "all",
        "most",
        "recent",
        "trend",
        "trends",
        "annual",
        "yearly",
        "monthly",
        # quantity/summary words that open titles ("Total trials ...", "Overall share ...")
        "total",
        "overall",
        "overview",
        "summary",
        "number",
        "count",
        "counts",
        "share",
        "shares",
        "distribution",
        "landscape",
        "top",
        "all",
        "most",
        "recent",
        "trend",
        "trends",
        "annual",
        "yearly",
        "monthly",
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "by",
        "for",
        "from",
        "in",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "per",
        "the",
        "to",
        "vs",
        "versus",
        "with",
        "trial",
        "trials",
        "study",
        "studies",
        "count",
        "counts",
        "number",
        "distribution",
        "over",
        "time",
        "across",
        "between",
        "within",
        "assume",
        "assumes",
        "assumed",
        "assuming",
        "interpret",
        "interpreted",
        "interpreting",
        "treated",
        "treat",
        "all",
        "any",
        "each",
        "every",
        "only",
        "both",
        "other",
        "than",
        "that",
        "this",
        "these",
        "those",
        "not",
        "no",
        "yes",
        "year",
        "years",
        "month",
        "months",
        "start",
        "end",
        "date",
        "dates",
        "status",
        "phase",
        "phases",
        "type",
        "types",
        "top",
        "chart",
        "bar",
        "line",
        "network",
        "histogram",
        "scatter",
        "plot",
        "trend",
        "compare",
        "compared",
        "comparison",
        "group",
        "grouped",
        "grouping",
        "include",
        "included",
        "includes",
        "including",
        "exclude",
        "excluded",
        "excludes",
        "excluding",
        "api",
        "nct",
        "mesh",
        "sponsor",
        "sponsors",
        "condition",
        "conditions",
        "drug",
        "drugs",
        "intervention",
        "interventions",
        "country",
        "countries",
        "lead",
        "clinicaltrials",
        "gov",
        "non",
        "anti",
        "pre",
        "post",
    )
)


def _words(text: str) -> set[str]:
    """Lowercased alphanumeric words of `text` (underscores and hyphens split words)."""
    words: set[str] = set()
    for match in _WORD.finditer(text.replace("_", " ")):
        words.add(match.group().lower())
    return words


@lru_cache(maxsize=1)
def _catalog_words() -> frozenset[str]:
    """Words of the catalog text and every schema enum value: the fixed public vocabulary."""
    texts = [load_catalog().render_for_planner()]
    for member in vars(enums).values():
        if isinstance(member, type) and issubclass(member, StrEnum):
            texts += [str(item.value) for item in member]
    return frozenset(_GENERIC_WORDS | _words(" ".join(texts)))


def _allowed_words(plan: QueryPlan, request: VisualizeRequest) -> frozenset[str]:
    """Catalog vocabulary + request text + field names + the plan's own values."""
    countries = plan.filters.countries if plan.filters else None
    texts = [
        *_request_texts(request),
        *_plan_texts(plan),
        *(countries or []),
        *_REQUEST_VALUE_FIELDS,
    ]
    return _catalog_words() | frozenset(_words(" ".join(texts)))


def _allowed_nct_ids(plan: QueryPlan, request: VisualizeRequest) -> frozenset[str]:
    """Every NCT-like token in the request or the plan's values, upper-cased."""
    texts = [*_request_texts(request), *_plan_texts(plan)]
    return frozenset(m.upper() for text in texts for m in _NCT_LIKE.findall(text))


def _is_known(word: str, allowed: frozenset[str]) -> bool:
    """True if the word, or its singular/plural form, is in the allowed set."""
    lowered = word.lower()
    return lowered in allowed or lowered.removesuffix("s") in allowed or f"{lowered}s" in allowed


def _is_candidate(word: str, is_initial: bool, next_word: str) -> bool:
    """Would this word read as a named entity (so it must be grounded)?"""
    if _DRUG_SUFFIX.fullmatch(word) or (word.isupper() and len(word) >= _MIN_ACRONYM_LEN):
        return True
    if not word[0].isupper():
        return False
    # A capitalised sentence/title opener is ordinary casing, not an entity, unless an
    # entity-marking word follows it (e.g. 'Merck sponsored ...').
    return not is_initial or next_word in _ENTITY_FOLLOWERS


def _is_sentence_initial(text: str, start: int) -> bool:
    """True if the word at `start` opens the text or a sentence."""
    before = text[:start].rstrip()
    return not before or before.endswith(_SENTENCE_END)


def _unsupported(text: str, allowed: frozenset[str], nct_ids: frozenset[str]) -> list[str]:
    """Entity-like tokens in `text` grounded nowhere, de-duplicated, in order."""
    found = [n for n in _NCT_LIKE.findall(text.upper()) if n not in nct_ids]
    for match in _WORD.finditer(text):
        word = match.group()
        if _NCT_LIKE.fullmatch(word):
            continue
        initial = _is_sentence_initial(text, match.start())
        following = _NEXT_WORD.match(text, match.end())
        next_word = following.group(1).lower() if following else ""
        if _is_candidate(word, initial, next_word) and not _is_known(word, allowed):
            found.append(word)
    return list(dict.fromkeys(found))


def _note(kind: str, bad: list[str]) -> str:
    """The shared "(unsupported entity: ...)" adjustment note."""
    return f"{kind} (unsupported entity: {', '.join(bad)})"


def _guard_title(
    plan: QueryPlan, analysis: Analysis, allowed: frozenset[str], nct_ids: frozenset[str]
) -> tuple[QueryPlan, list[str]]:
    """Swap a title naming an ungrounded entity for `"{Measure} by {dimension}: {entity}"`."""
    viz = plan.visualization
    bad = _unsupported(viz.title, allowed, nct_ids) if viz else []
    if viz is None or not bad:
        return plan, []
    measure, dimension = _measure_and_dimension(analysis)
    title = f"{measure} by {dimension}: {_entity(plan)}"
    new_viz = viz.model_copy(update={"title": title})
    return (
        plan.model_copy(update={"visualization": new_viz}),
        [_note(f"title {viz.title!r} -> {title!r}", bad)],
    )


def _guard_interpretation(
    plan: QueryPlan, analysis: Analysis, allowed: frozenset[str], nct_ids: frozenset[str]
) -> tuple[QueryPlan, list[str]]:
    """Replace an interpretation naming an ungrounded entity with a code-rendered sentence."""
    bad = _unsupported(plan.interpretation, allowed, nct_ids)
    if not bad:
        return plan, []
    measure, dimension = _measure_and_dimension(analysis)
    sentence = f"{measure} by {dimension} for {_entity(plan)}."
    note = _note("interpretation replaced by a code-rendered sentence", bad)
    return plan.model_copy(update={"interpretation": sentence}), [note]


def _guard_assumption(
    assumption: str, allowed: frozenset[str], nct_ids: frozenset[str]
) -> tuple[str, list[str]]:
    """One assumption with its ungrounded-entity sentences dropped ('' if none survive)."""
    kept, notes = [], []
    for sentence in _SENTENCE_BREAK.split(assumption.strip()):
        bad = _unsupported(sentence, allowed, nct_ids)
        if bad:
            notes.append(_note(f"assumption sentence dropped: {sentence!r}", bad))
        else:
            kept.append(sentence)
    return " ".join(kept), notes


def _guard_assumptions(
    plan: QueryPlan, allowed: frozenset[str], nct_ids: frozenset[str]
) -> tuple[QueryPlan, list[str]]:
    """Drop every assumption sentence naming an ungrounded entity (and emptied assumptions)."""
    results = [_guard_assumption(a, allowed, nct_ids) for a in plan.assumptions]
    notes = [note for _text, item_notes in results for note in item_notes]
    if not notes:
        return plan, []
    assumptions = [text for text, _notes in results if text]
    return plan.model_copy(update={"assumptions": assumptions}), notes


def guard_entities(plan: QueryPlan, request: VisualizeRequest) -> tuple[QueryPlan, list[str]]:
    """Make title/interpretation/assumptions entity-safe; return the new plan + adjustment notes."""
    analysis = plan.analysis
    if analysis is None:
        return plan, []
    allowed = _allowed_words(plan, request)
    nct_ids = _allowed_nct_ids(plan, request)
    titled, title_notes = _guard_title(plan, analysis, allowed, nct_ids)
    interpreted, interp_notes = _guard_interpretation(titled, analysis, allowed, nct_ids)
    guarded, assumption_notes = _guard_assumptions(interpreted, allowed, nct_ids)
    return guarded, [*title_notes, *interp_notes, *assumption_notes]
