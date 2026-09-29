"""Entity resolution: alias discovery by co-reference and the sponsor name census (§10.4)."""

import re
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from ctviz.analysis.aggregate import MatchedTrial
from ctviz.common.names import normalize_text
from ctviz.ctgov.normalize import Intervention, Trial

MIN_ALIAS_TRIALS = 3
# Placebo / standard-of-care and drug-CLASS descriptors: never a synonym of one specific drug,
# however often they co-occur with it (an otherNames "Checkpoint inhibitor" would otherwise
# match every other PD-1 antibody trial as if it were the searched drug).
GENERIC_NAME_PATTERN = re.compile(
    r"placebo|standard of care|best supportive care|\bsoc\b|saline|vehicle"
    r"|inhibitor|immunotherap|chemotherap|antibod|immunoglobulin|checkpoint|\banti-",
    re.IGNORECASE,
)
# Q2 = a (PLAN.md §22): known distinct organizations that share a search term, each mapped to
# the lowercase name substrings that identify it in a sponsor name. Item 5 ruling: the warning
# fires only when >= 2 of these DISTINCT ORGS actually appear in the cohort's sponsor census --
# not merely >= 2 differently-spelled sponsor names, which can all be the same org (e.g. "Merck
# Sharp & Dohme LLC" and "...a subsidiary of Merck & Co." are both MSD, not MSD + Merck KGaA).
KNOWN_DISTINCT_ORGS: dict[str, dict[str, tuple[str, ...]]] = {
    "merck": {
        "Merck Sharp & Dohme (MSD)": ("merck sharp", "msd", "merck & co", "merck and co"),
        "Merck KGaA": ("merck kgaa",),
    },
}


@dataclass(frozen=True)
class _AliasEvidence:
    """Per-candidate co-reference trial ids, plus every name vetoed as a separate object."""

    coreferenced: dict[str, frozenset[str]]
    vetoed: frozenset[str]


def _names_of(iv: Intervention) -> tuple[str, ...]:
    return tuple(normalize_text(n) for n in (iv.name, *iv.other_names))


def _trial_alias_evidence(trial: Trial, term_key: str) -> tuple[set[str], set[str]]:
    """(a) this trial's candidates and (c) its vetoes; both empty unless some object's NAME
    contains the term (the same trials (a) learns from -- see the scope ruling below)."""
    named = [iv for iv in trial.interventions if term_key in normalize_text(iv.name)]
    if not named:
        return set(), set()
    bearing = [iv for iv in trial.interventions if any(term_key in n for n in _names_of(iv))]
    candidates = {key for iv in named for key in _names_of(iv)[1:] if term_key not in key}
    vetoed = {key for iv in trial.interventions if iv not in bearing for key in _names_of(iv)}
    return candidates, vetoed


def _collect(trials: Sequence[Trial], term_key: str) -> _AliasEvidence:
    """One pass: which trials co-reference each candidate, and which names are ever vetoed."""
    seen: dict[str, set[str]] = defaultdict(set)
    vetoed: set[str] = set()
    for trial in trials:
        candidates, trial_vetoes = _trial_alias_evidence(trial, term_key)
        for key in candidates:
            seen[key].add(trial.nct_id)
        vetoed |= trial_vetoes
    return _AliasEvidence({k: frozenset(v) for k, v in seen.items()}, frozenset(vetoed))


def _acceptable(key: str, evidence: _AliasEvidence, rivals: Sequence[str]) -> bool:
    """(b) >= MIN_ALIAS_TRIALS trials, (c) never vetoed, never generic, never a rival cohort."""
    return (
        len(evidence.coreferenced[key]) >= MIN_ALIAS_TRIALS
        and key not in evidence.vetoed
        and not GENERIC_NAME_PATTERN.search(key)
        and not any(rival in key for rival in rivals)
    )


def discover_aliases(
    trials: Sequence[Trial], term: str, other_cohort_values: Sequence[str] = ()
) -> list[str]:
    """Co-referenced aliases of `term` (§10.4): otherNames of an object whose NAME contains the
    term, seen in >= 3 trials, never a separate object beside a term-bearing one, never a
    placebo/SOC/drug-class name, never (containing) another compared cohort's value."""
    term_key = normalize_text(term)
    rivals = [r for r in (normalize_text(v) for v in other_cohort_values) if r and r != term_key]
    evidence = _collect(trials, term_key)
    return sorted(key for key in evidence.coreferenced if _acceptable(key, evidence, rivals))


def sponsor_census(trials: list[MatchedTrial], top: int = 10) -> list[tuple[str, int]]:
    """Distinct `LeadSponsorName` spellings and their trial counts, largest first (§10.4)."""
    counts: Counter[str] = Counter(
        matched.trial.lead_sponsor for matched in trials if matched.trial.lead_sponsor
    )
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return ranked[:top]


def _matched_orgs(
    known: dict[str, tuple[str, ...]], census: list[tuple[str, int]]
) -> dict[str, list[tuple[str, int]]]:
    """Which known orgs' name patterns each census entry actually matches (item 5)."""
    matched: dict[str, list[tuple[str, int]]] = {}
    for org, patterns in known.items():
        for name, count in census:
            if any(pattern in name.lower() for pattern in patterns):
                matched.setdefault(org, []).append((name, count))
    return matched


def sponsor_ambiguity_warning(term: str, census: list[tuple[str, int]]) -> str | None:
    """Q2=a: warn only when >= 2 distinct known orgs actually appear in the census (item 5)."""
    known = KNOWN_DISTINCT_ORGS.get(normalize_text(term))
    if known is None:
        return None
    matched = _matched_orgs(known, census)
    if len(matched) < 2:
        return None
    parts = [
        f"{org}: {', '.join(f'{n} ({c})' for n, c in entries)}" for org, entries in matched.items()
    ]
    return (
        f"'{term}' matches multiple distinct organizations that share this name "
        f"({', '.join(matched)}). Results are kept as returned, not auto-split. "
        f"Sponsor name census: {'; '.join(parts)}."
    )
