"""Name/text normalization shared by aggregation and verification (so both apply one rule)."""

import re
from functools import lru_cache

_PREFIX = re.compile(r"^(drug|biological|combination product|genetic)\s*:\s*", re.IGNORECASE)
_BRACKETED = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")
_DOSE = re.compile(
    r"\s*\b\d[\d,.]*\s*(mg/m2|mg/kg|mg/day|mg|mcg|µg|g|ml|iu|units?)\b.*$", re.IGNORECASE
)
_NOT_A_DRUG = frozenset(
    {"placebo", "standard of care", "best supportive care", "soc", "saline", "normal saline"}
)


def normalize_text(text: str) -> str:
    """Casefold and collapse whitespace."""
    return " ".join(text.casefold().split())


def text_matches(text: str | list[str], term: str) -> bool:
    """True if the normalized term occurs in the text (or in any item of a list)."""
    needle = normalize_text(term)
    items = text if isinstance(text, list) else [text]
    return any(needle in normalize_text(item) for item in items if isinstance(item, str))


def strip_drug_prefix(raw: str) -> str:
    """Display-only: drop a leading 'Drug: '/'Biological: '-style prefix (item 12); dosage and
    brackets are left alone -- this is for a node LABEL, `normalize_drug` owns the match KEY."""
    return _PREFIX.sub("", raw.strip(), count=1)


@lru_cache(maxsize=8192)
def normalize_drug(raw: str) -> str | None:
    """Canonical drug key: strip type prefix, brackets and doses; None for placebo/SOC.

    Cached (item: S6 performance fix): a pure function of `raw`, called repeatedly with the
    same handful of raw intervention-name strings across every bucket a `count_by`/verifier
    predicate re-evaluates (e.g. the independent verifier's O(buckets x population) completeness
    recount calls this once per intervention per trial per bucket -- caching turns that into a
    cache hit after the first). Cost if wrong: none -- `raw` -> key is a total pure mapping, so
    memoizing it can never return a stale or incorrect result.
    """
    text = _PREFIX.sub("", raw.strip())
    text = _BRACKETED.sub("", text)
    text = _DOSE.sub("", text)
    key = normalize_text(text)
    if not key or key in _NOT_A_DRUG or key.startswith("placebo"):
        return None
    return key
