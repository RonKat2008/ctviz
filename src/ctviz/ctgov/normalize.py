"""Raw ClinicalTrials.gov study → Trial. Every rule here is from PLAN.md §10.3 (measured data)."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

PHASE_ORDER = ("EARLY_PHASE1", "PHASE1", "PHASE2", "PHASE3", "PHASE4")
PHASE_NAMES = {
    "EARLY_PHASE1": "Early Phase 1",
    "PHASE1": "Phase 1",
    "PHASE2": "Phase 2",
    "PHASE3": "Phase 3",
    "PHASE4": "Phase 4",
}
NON_INTERVENTIONAL = "Non-interventional"
PHASE_NA = "Phase N/A"
PHASE_NOT_REPORTED = "Phase not reported"
PHASE_DISPLAY_ORDER = (
    "Early Phase 1",
    "Phase 1",
    "Phase 1/Phase 2",
    "Phase 2",
    "Phase 2/Phase 3",
    "Phase 3",
    "Phase 4",
    PHASE_NA,
    NON_INTERVENTIONAL,
    PHASE_NOT_REPORTED,
)


@dataclass(frozen=True)
class PartialDate:
    """A ClinicalTrials.gov date at whatever precision it was actually reported."""

    year: int
    month: int | None
    day: int | None
    raw: str
    type: str | None  # ACTUAL, ESTIMATED, or None (legacy, untyped)


@dataclass(frozen=True)
class Intervention:
    """One arm-interventions-module entry, keeping its original array index for citations."""

    index: int
    name: str
    type: str | None
    other_names: tuple[str, ...]
    arm_labels: tuple[str, ...]


@dataclass(frozen=True)
class Site:
    """One locations-module entry, keeping its original array index for citations."""

    index: int
    facility: str | None
    country: str | None
    status: str | None


@dataclass(frozen=True)
class Trial:
    """The flattened fields every analysis and citation needs, plus the untouched raw record."""

    nct_id: str
    title: str
    phases: tuple[str, ...] | None
    phase_label: str
    status: str | None
    study_type: str | None
    start: PartialDate | None
    completion: PartialDate | None
    enrollment: int | None
    enrollment_type: str | None
    lead_sponsor: str | None
    lead_sponsor_class: str | None
    interventions: tuple[Intervention, ...]
    conditions: tuple[str, ...]
    sites: tuple[Site, ...]
    raw: Mapping[str, Any]


def phase_label(phases: list[str] | tuple[str, ...] | None, study_type: str | None) -> str:
    """One bucket per trial (Q4=combined): 'Phase 1/Phase 2', 'Phase N/A', 'Non-interventional'."""
    if not phases:
        return PHASE_NOT_REPORTED if study_type == "INTERVENTIONAL" else NON_INTERVENTIONAL
    real = sorted((p for p in phases if p != "NA"), key=PHASE_ORDER.index)
    return "/".join(PHASE_NAMES[p] for p in real) if real else PHASE_NA


def parse_partial_date(raw: str, date_type: str | None) -> PartialDate:
    """Parse 'YYYY', 'YYYY-MM' or 'YYYY-MM-DD' without inventing precision."""
    parts = [int(p) for p in raw.split("-")]
    return PartialDate(
        parts[0],
        parts[1] if len(parts) > 1 else None,
        parts[2] if len(parts) > 2 else None,
        raw,
        date_type,
    )


def _date(struct: Mapping[str, Any] | None) -> PartialDate | None:
    """Parse a *DateStruct mapping, or None when the section/date is absent."""
    if not struct or "date" not in struct:
        return None
    return parse_partial_date(struct["date"], struct.get("type"))


def _clean_text(value: str | None) -> str | None:
    """Strip stray surrounding whitespace from a free-text value used for matching/grouping.

    ClinicalTrials.gov data carries whitespace noise (e.g. "Bonaire, Saint Eustatius and Saba ");
    left unstripped it would silently split one real-world group into two buckets.
    """
    return value.strip() if isinstance(value, str) else value


def _interventions(arms_module: Mapping[str, Any]) -> tuple[Intervention, ...]:
    """Flatten the arms-interventions-module entries, keeping their original array indices."""
    return tuple(
        Intervention(
            i,
            _clean_text(item.get("name", "")) or "",
            item.get("type"),
            tuple(item.get("otherNames", [])),
            tuple(item.get("armGroupLabels", [])),
        )
        for i, item in enumerate(arms_module.get("interventions", []))
    )


def _conditions(protocol: Mapping[str, Any]) -> tuple[str, ...]:
    """Flatten the conditions-module list, dropping any cleaned to None."""
    conditions_module = protocol.get("conditionsModule", {})
    cleaned = (_clean_text(c) for c in conditions_module.get("conditions", []))
    return tuple(c for c in cleaned if c is not None)


def _sites(protocol: Mapping[str, Any]) -> tuple[Site, ...]:
    """Flatten the contacts-locations-module entries, keeping their original array indices."""
    locations = protocol.get("contactsLocationsModule", {}).get("locations", [])
    return tuple(
        Site(
            i, _clean_text(loc.get("facility")), _clean_text(loc.get("country")), loc.get("status")
        )
        for i, loc in enumerate(locations)
    )


def normalize(raw: Mapping[str, Any]) -> Trial:
    """Flatten the fields we analyze, keeping array indices so evidence pointers stay exact."""
    protocol = raw.get("protocolSection", {})
    ident = protocol.get("identificationModule", {})
    status = protocol.get("statusModule", {})
    design = protocol.get("designModule", {})
    sponsor = protocol.get("sponsorCollaboratorsModule", {}).get("leadSponsor", {})
    enrollment = design.get("enrollmentInfo", {})
    phases = design.get("phases")
    return Trial(
        nct_id=ident["nctId"],
        title=ident.get("briefTitle", ""),
        phases=tuple(phases) if phases else None,
        phase_label=phase_label(phases, design.get("studyType")),
        status=status.get("overallStatus"),
        study_type=design.get("studyType"),
        start=_date(status.get("startDateStruct")),
        completion=_date(status.get("completionDateStruct")),
        enrollment=enrollment.get("count"),
        enrollment_type=enrollment.get("type"),
        lead_sponsor=_clean_text(sponsor.get("name")),
        lead_sponsor_class=sponsor.get("class"),
        interventions=_interventions(protocol.get("armsInterventionsModule", {})),
        conditions=_conditions(protocol),
        sites=_sites(protocol),
        raw=raw,
    )
