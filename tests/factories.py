"""Readable builders for fake ClinicalTrials.gov records and QueryPlans (unit tests only)."""

from typing import Any

from ctviz.schemas.plan import QueryPlan


def make_study(
    nct_id: str = "NCT00000001",
    *,
    phases: list[str] | None = None,
    study_type: str = "INTERVENTIONAL",
    start: str | None = None,
    start_type: str | None = "ACTUAL",
    status: str = "COMPLETED",
    sponsor: str = "Merck Sharp & Dohme LLC",
    sponsor_class: str = "INDUSTRY",
    interventions: list[dict[str, Any]] | None = None,
    arms: list[dict[str, Any]] | None = None,
    conditions: list[str] | None = None,
    locations: list[dict[str, Any]] | None = None,
    enrollment: int | None = None,
    enrollment_type: str = "ACTUAL",
    mesh_interventions: list[str] | None = None,
) -> dict[str, Any]:
    """Build a raw study shaped exactly like GET /studies output; omitted parts stay absent."""
    design: dict[str, Any] = {"studyType": study_type}
    if phases is not None:
        design["phases"] = phases
    if enrollment is not None:
        design["enrollmentInfo"] = {"count": enrollment, "type": enrollment_type}
    status_module: dict[str, Any] = {"overallStatus": status}
    if start is not None:
        status_module["startDateStruct"] = {
            "date": start,
            **({"type": start_type} if start_type else {}),
        }
    protocol: dict[str, Any] = {
        "identificationModule": {"nctId": nct_id, "briefTitle": f"Study {nct_id}"},
        "statusModule": status_module,
        "sponsorCollaboratorsModule": {"leadSponsor": {"name": sponsor, "class": sponsor_class}},
        "designModule": design,
    }
    if interventions is not None or arms is not None:
        protocol["armsInterventionsModule"] = {
            "interventions": interventions or [],
            "armGroups": arms or [],
        }
    if conditions is not None:
        protocol["conditionsModule"] = {"conditions": conditions}
    if locations is not None:
        protocol["contactsLocationsModule"] = {"locations": locations}
    study: dict[str, Any] = {"protocolSection": protocol}
    if mesh_interventions is not None:
        study["derivedSection"] = {
            "interventionBrowseModule": {
                "meshes": [{"id": f"D{i:06d}", "term": t} for i, t in enumerate(mesh_interventions)]
            }
        }
    return study


def make_plan(**overrides: Any) -> QueryPlan:
    """A valid answerable plan (pembrolizumab trials by phase) with selective overrides."""
    base: dict[str, Any] = {
        "answerable": True,
        "out_of_scope_reason": None,
        "suggested_reframing": None,
        "interpretation": "Pembrolizumab trials by phase.",
        "search_terms": [
            {
                "param": "query.intr",
                "value": "pembrolizumab",
                "source": "query_text",
                "rationale": "drug",
            }
        ],
        "filters": None,
        "comparison": None,
        "analysis": {
            "kind": "count_by",
            "group_by": "phase",
            "series_by": None,
            "phase_mode": "combined",
            "time_field": None,
            "granularity": None,
            "measure_x": None,
            "measure_y": None,
            "color_by": None,
            "network_type": None,
            "top_n": None,
        },
        "visualization": {
            "type": "bar_chart",
            "title": "Pembrolizumab trials by phase",
            "rationale": "categorical distribution",
        },
        "assumptions": [],
    }
    return QueryPlan.model_validate(base | overrides)
