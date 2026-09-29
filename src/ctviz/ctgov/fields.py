"""Which `fields=` pieces each analysis needs. The API projects leaf fields only (PLAN.md §5.5)."""

from ctviz.schemas.enums import AnalysisKind, Dimension, Measure, NetworkType, SearchParam
from ctviz.schemas.plan import QueryPlan

ALWAYS = (
    "NCTId",
    "BriefTitle",
    "StudyType",
    "OverallStatus",
    "StartDate",
    "StartDateType",
    "LastUpdatePostDate",
)
FOR_DIMENSION: dict[Dimension, tuple[str, ...]] = {
    Dimension.PHASE: ("Phase",),
    Dimension.OVERALL_STATUS: (),
    Dimension.STUDY_TYPE: (),
    Dimension.LEAD_SPONSOR_CLASS: ("LeadSponsorClass",),
    Dimension.LEAD_SPONSOR: ("LeadSponsorName",),
    Dimension.INTERVENTION_TYPE: ("InterventionType",),
    Dimension.INTERVENTION: ("InterventionName", "InterventionType"),
    Dimension.CONDITION: ("Condition",),
    Dimension.COUNTRY: ("LocationCountry", "LocationStatus"),
    Dimension.START_YEAR: (),
}
FOR_MEASURE: dict[Measure, tuple[str, ...]] = {
    Measure.ENROLLMENT: ("EnrollmentCount", "EnrollmentType"),
    Measure.DURATION_MONTHS: ("CompletionDate", "CompletionDateType"),
    Measure.SITE_COUNT: ("LocationFacility",),
}
FOR_NETWORK: dict[NetworkType, tuple[str, ...]] = {
    NetworkType.SPONSOR_DRUG: (
        "LeadSponsorName",
        "CollaboratorName",
        "InterventionName",
        "InterventionType",
    ),
    NetworkType.DRUG_DRUG: (
        "InterventionName",
        "InterventionType",
        "ArmGroupLabel",
        "ArmGroupInterventionName",
    ),
    NetworkType.CONDITION_DRUG: ("Condition", "InterventionName", "InterventionType"),
}
FOR_MATCH: dict[SearchParam, tuple[str, ...]] = {
    SearchParam.INTR: (
        "InterventionName",
        "InterventionOtherName",
        "ArmGroupInterventionName",
        "InterventionMeshTerm",
    ),
    SearchParam.LEAD: ("LeadSponsorName",),
    SearchParam.SPONS: ("LeadSponsorName", "CollaboratorName"),
    SearchParam.COND: ("Condition", "Keyword", "ConditionMeshTerm", "ConditionAncestorTerm"),
}
FOR_FILTER = ("Phase", "LeadSponsorClass", "InterventionType", "LocationCountry")
# §8.4 / fix G: every key fact the trial_lookup table cites.
FOR_KEY_FACTS = (
    "Phase",
    "CompletionDate",
    "EnrollmentCount",
    "EnrollmentType",
    "LeadSponsorName",
    "Condition",
    "InterventionName",
)


def fields_for_plan(plan: QueryPlan) -> list[str]:
    """Union of every leaf the plan's aggregation, evidence and filter re-checks will read."""
    pieces: list[str] = [*ALWAYS, *FOR_FILTER]
    analysis = plan.analysis
    if analysis is not None:
        for dim in (analysis.group_by, analysis.series_by, analysis.color_by):
            pieces += FOR_DIMENSION.get(dim, ()) if dim else ()
        for measure in (analysis.measure_x, analysis.measure_y):
            pieces += FOR_MEASURE.get(measure, ()) if measure else ()
        pieces += FOR_NETWORK.get(analysis.network_type, ()) if analysis.network_type else ()
        pieces += FOR_KEY_FACTS if analysis.kind is AnalysisKind.TRIAL_LOOKUP else ()
    params = [t.param for t in plan.search_terms]
    params += [plan.comparison.vary_param] if plan.comparison else []
    for param in params:
        pieces += FOR_MATCH.get(param, ())
    return list(dict.fromkeys(pieces))  # de-duplicate, keep order
