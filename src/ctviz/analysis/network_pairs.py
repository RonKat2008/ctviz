"""Witness-pair construction for sponsor/drug/condition networks, split out of `network.py`
(module budget). `network.py`'s `build_graph()` calls into `_sponsor_drug_pairs`,
`_drug_drug_pairs` and `_condition_drug_pairs` below, then folds the resulting `_Pair`s into a
`Graph` itself -- pair construction (which endpoints a trial witnesses, and with what evidence)
is a distinct responsibility from graph assembly (deduping and citing those witnesses).
"""

from dataclasses import dataclass
from itertools import combinations

from ctviz.analysis.aggregate import MatchedTrial
from ctviz.citations import pointer as p
from ctviz.citations.pointer import json_text, resolve_pointer
from ctviz.common.names import normalize_drug, normalize_text
from ctviz.ctgov.normalize import Arm, Trial
from ctviz.schemas.citations import Evidence, Predicate

NodeType = str  # "sponsor" | "drug" | "condition" (kept loose here to avoid a network.py import)
DRUG_INTERVENTION_TYPES = ("DRUG", "BIOLOGICAL")


@dataclass(frozen=True)
class _Endpoint:
    """One side of a pair: its node identity, raw label, evidence and node-level predicate."""

    kind: NodeType
    key: str
    raw_label: str
    evidence: Evidence
    predicate: Predicate


@dataclass(frozen=True)
class _Pair:
    """One trial's edge witness; `edge_predicate` (Q-D) overrides a narrower true witness."""

    nct_id: str
    a: _Endpoint
    b: _Endpoint
    match_evidence: tuple[Evidence, ...]
    edge_predicate: Predicate | None = None


def _excerpt_at(trial: Trial, field_path: str) -> str:
    """The exact raw-record text at `field_path`, for an evidence excerpt."""
    return json_text(resolve_pointer(trial.raw, field_path))


def _drug_predicate(key: str) -> Predicate:
    """A drug node/endpoint predicate: any DRUG/BIOLOGICAL intervention normalizing to `key`."""
    return {
        "op": "any_element",
        "path": p.INTERVENTIONS,
        "where": [
            {"op": "in", "path": "/type", "value": list(DRUG_INTERVENTION_TYPES)},
            {"op": "normalizes_to", "path": "/name", "value": key},
        ],
    }


def _trial_drugs(trial: Trial) -> list[tuple[str, str, str]]:
    """This trial's distinct drugs, deduped, as (key, raw_name, field) at first occurrence."""
    seen: dict[str, tuple[str, str]] = {}
    for iv in trial.interventions:
        if iv.type not in DRUG_INTERVENTION_TYPES:
            continue
        key = normalize_drug(iv.name)
        if key is not None:
            seen.setdefault(key, (iv.name, f"{p.INTERVENTIONS}/{iv.index}/name"))
    return [(key, name, field_path) for key, (name, field_path) in seen.items()]


def _trial_conditions(trial: Trial) -> list[tuple[str, str, str]]:
    """This trial's distinct conditions, deduped by normalized text, first occurrence kept."""
    seen: dict[str, tuple[str, str]] = {}
    for i, cond in enumerate(trial.conditions):
        seen.setdefault(normalize_text(cond), (cond, f"{p.CONDITIONS}/{i}"))
    return [(key, text, field_path) for key, (text, field_path) in seen.items()]


def _drug_endpoint(trial: Trial, key: str, name: str, field_path: str) -> _Endpoint:
    """A drug endpoint: id namespaced `drug:<key>`, evidence at the raw intervention name."""
    evidence = Evidence(role="bucket", field=field_path, excerpt=_excerpt_at(trial, field_path))
    return _Endpoint("drug", f"drug:{key}", name, evidence, _drug_predicate(key))


def _sponsor_endpoints(trial: Trial, include_collaborators: bool) -> list[_Endpoint]:
    """This trial's sponsor endpoints: the lead sponsor, plus collaborators if requested."""
    endpoints = []
    if trial.lead_sponsor:
        evidence = Evidence(
            role="bucket",
            field=p.LEAD_SPONSOR_NAME,
            excerpt=_excerpt_at(trial, p.LEAD_SPONSOR_NAME),
        )
        predicate: Predicate = {
            "op": "equals",
            "path": p.LEAD_SPONSOR_NAME,
            "value": trial.lead_sponsor,
        }
        key = normalize_text(trial.lead_sponsor)
        endpoints.append(
            _Endpoint("sponsor", f"sponsor:{key}", trial.lead_sponsor, evidence, predicate)
        )
    if include_collaborators:
        for i, name in trial.collaborators:  # (i is the ORIGINAL array index; item 13)
            field_path = f"{p.COLLABORATORS}/{i}/name"
            evidence = Evidence(
                role="bucket", field=field_path, excerpt=_excerpt_at(trial, field_path)
            )
            predicate = {
                "op": "any_element",
                "path": p.COLLABORATORS,
                "where": [{"op": "equals", "path": "/name", "value": name}],
            }
            key = normalize_text(name)
            endpoints.append(_Endpoint("sponsor", f"sponsor:{key}", name, evidence, predicate))
    return endpoints


def sponsor_drug_pairs(trials: list[MatchedTrial], include_collaborators: bool) -> list[_Pair]:
    """Witness pairs for sponsor<->drug: a trial has lead sponsor S and drug D (§10.5)."""
    pairs = []
    for matched in trials:
        trial = matched.trial
        drugs = _trial_drugs(trial)
        for sponsor_endpoint in _sponsor_endpoints(trial, include_collaborators):
            for key, name, field_path in drugs:
                drug_endpoint = _drug_endpoint(trial, key, name, field_path)
                pairs.append(
                    _Pair(trial.nct_id, sponsor_endpoint, drug_endpoint, matched.match_evidence)
                )
    return pairs


def _arm_drug_positions(arm: Arm, drug_keys: set[str]) -> dict[str, int]:
    """First `interventionNames[]` index per key that's also a trial drug intervention."""
    first: dict[str, int] = {}
    for i, name in enumerate(arm.intervention_names):
        key = normalize_drug(name)
        if key is not None and key in drug_keys:
            first.setdefault(key, i)
    return first


def drug_drug_pairs(trials: list[MatchedTrial]) -> list[_Pair]:
    """Witness pairs for drug<->drug: both drugs listed in the same arm (§10.5)."""
    pairs = []
    for matched in trials:
        trial = matched.trial
        drug_keys = {key for key, _name, _field in _trial_drugs(trial)}
        for arm in trial.arms:
            positions = _arm_drug_positions(arm, drug_keys)
            # HIGH item 6: canonicalize the undirected pair by sorted key, not arm appearance
            # order -- otherwise the same two drugs listed as A-then-B in one trial's arm and
            # B-then-A in another's produce two distinct, undercounted edges instead of one.
            for a_key, b_key in combinations(sorted(positions), 2):
                pairs.append(
                    _drug_drug_pair(trial, matched.match_evidence, arm, positions, a_key, b_key)
                )
    return pairs


def _drug_drug_edge_predicate(a_key: str, b_key: str) -> Predicate:
    """Q-D: the true witness is same-arm co-occurrence -- both in ONE armGroup, not just a trial."""
    same_arm: Predicate = {
        "op": "any_element",
        "path": p.ARM_GROUPS,
        "where": [
            {"op": "normalizes_to", "path": "/interventionNames", "value": a_key, "fn": "drug"},
            {"op": "normalizes_to", "path": "/interventionNames", "value": b_key, "fn": "drug"},
        ],
    }
    return {"all": [_drug_predicate(a_key), _drug_predicate(b_key), same_arm]}


def _drug_drug_pair(
    trial: Trial,
    match_evidence: tuple[Evidence, ...],
    arm: Arm,
    positions: dict[str, int],
    a_key: str,
    b_key: str,
) -> _Pair:
    """One same-arm drug pair witness, with the arm label carried as extra match evidence."""
    base = f"{p.ARM_GROUPS}/{arm.index}/interventionNames"
    a_field, b_field = f"{base}/{positions[a_key]}", f"{base}/{positions[b_key]}"
    a = _drug_endpoint(trial, a_key, arm.intervention_names[positions[a_key]], a_field)
    b = _drug_endpoint(trial, b_key, arm.intervention_names[positions[b_key]], b_field)
    extra = match_evidence
    if arm.label is not None:
        label_field = f"{p.ARM_GROUPS}/{arm.index}/label"
        label_evidence = Evidence(
            role="context", field=label_field, excerpt=_excerpt_at(trial, label_field)
        )
        extra = (*match_evidence, label_evidence)
    return _Pair(trial.nct_id, a, b, extra, _drug_drug_edge_predicate(a_key, b_key))


def condition_drug_pairs(trials: list[MatchedTrial]) -> list[_Pair]:
    """Witness pairs for condition<->drug: a trial lists condition C and drug D (§10.5)."""
    pairs = []
    for matched in trials:
        trial = matched.trial
        drugs = _trial_drugs(trial)
        for cond_key, cond_text, cond_field in _trial_conditions(trial):
            cond_evidence = Evidence(
                role="bucket", field=cond_field, excerpt=_excerpt_at(trial, cond_field)
            )
            cond_predicate: Predicate = {
                "op": "normalizes_to",
                "path": p.CONDITIONS,
                "value": cond_key,
                "fn": "text",
            }
            cond_endpoint = _Endpoint(
                "condition", f"condition:{cond_key}", cond_text, cond_evidence, cond_predicate
            )
            for drug_key, drug_name, drug_field in drugs:
                drug_endpoint = _drug_endpoint(trial, drug_key, drug_name, drug_field)
                pairs.append(
                    _Pair(trial.nct_id, cond_endpoint, drug_endpoint, matched.match_evidence)
                )
    return pairs
