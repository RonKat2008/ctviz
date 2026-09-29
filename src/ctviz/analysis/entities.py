"""Entity resolution: alias discovery by co-reference and the sponsor name census (§10.4)."""

from collections import Counter

from ctviz.analysis.aggregate import MatchedTrial
from ctviz.common.names import normalize_text, text_matches

MIN_ALIAS_TRIALS = 3
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


def discover_aliases(trials: list[MatchedTrial], term: str) -> list[str]:
    """Alias candidates by co-reference: names sharing an intervention object with `term`, seen
    in >= MIN_ALIAS_TRIALS trials (never a name that is its own separate intervention, §10.4)."""
    term_key = normalize_text(term)
    counts: Counter[str] = Counter()
    for matched in trials:
        for iv in matched.trial.interventions:
            names = [iv.name, *iv.other_names]
            if not text_matches(names, term):
                continue
            keys = {normalize_text(n) for n in names} - {term_key}
            counts.update(keys)
    return sorted(key for key, n in counts.items() if n >= MIN_ALIAS_TRIALS)


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
