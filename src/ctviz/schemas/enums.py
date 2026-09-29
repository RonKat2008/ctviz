"""Controlled vocabularies: ClinicalTrials.gov enums (API strings) and our planner menus."""

from enum import StrEnum


class Phase(StrEnum):
    """A trial's clinical phase, per ClinicalTrials.gov's `Phase` enum (§5.6)."""

    NA = "NA"
    EARLY_PHASE1 = "EARLY_PHASE1"
    PHASE1 = "PHASE1"
    PHASE2 = "PHASE2"
    PHASE3 = "PHASE3"
    PHASE4 = "PHASE4"


class OverallStatus(StrEnum):
    """A trial's recruitment status, per ClinicalTrials.gov's `OverallStatus` enum (§5.6)."""

    ACTIVE_NOT_RECRUITING = "ACTIVE_NOT_RECRUITING"
    COMPLETED = "COMPLETED"
    ENROLLING_BY_INVITATION = "ENROLLING_BY_INVITATION"
    NOT_YET_RECRUITING = "NOT_YET_RECRUITING"
    RECRUITING = "RECRUITING"
    SUSPENDED = "SUSPENDED"
    TERMINATED = "TERMINATED"
    WITHDRAWN = "WITHDRAWN"
    AVAILABLE = "AVAILABLE"
    NO_LONGER_AVAILABLE = "NO_LONGER_AVAILABLE"
    TEMPORARILY_NOT_AVAILABLE = "TEMPORARILY_NOT_AVAILABLE"
    APPROVED_FOR_MARKETING = "APPROVED_FOR_MARKETING"
    WITHHELD = "WITHHELD"
    UNKNOWN = "UNKNOWN"


class StudyType(StrEnum):
    """A trial's design category, per ClinicalTrials.gov's `StudyType` enum (§5.6)."""

    INTERVENTIONAL = "INTERVENTIONAL"
    OBSERVATIONAL = "OBSERVATIONAL"
    EXPANDED_ACCESS = "EXPANDED_ACCESS"


class InterventionType(StrEnum):
    """The kind of intervention tested, per ClinicalTrials.gov's `InterventionType` enum (§5.6)."""

    BEHAVIORAL = "BEHAVIORAL"
    BIOLOGICAL = "BIOLOGICAL"
    COMBINATION_PRODUCT = "COMBINATION_PRODUCT"
    DEVICE = "DEVICE"
    DIAGNOSTIC_TEST = "DIAGNOSTIC_TEST"
    DIETARY_SUPPLEMENT = "DIETARY_SUPPLEMENT"
    DRUG = "DRUG"
    GENETIC = "GENETIC"
    PROCEDURE = "PROCEDURE"
    RADIATION = "RADIATION"
    OTHER = "OTHER"


class AgencyClass(StrEnum):
    """A sponsor's organization category, per ClinicalTrials.gov's `AgencyClass` enum (§5.6)."""

    NIH = "NIH"
    FED = "FED"
    OTHER_GOV = "OTHER_GOV"
    INDIV = "INDIV"
    INDUSTRY = "INDUSTRY"
    NETWORK = "NETWORK"
    AMBIG = "AMBIG"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class SearchParam(StrEnum):
    """The `query.*` full-text search parameters the planner may choose from (§5.2, §7.2)."""

    COND = "query.cond"
    INTR = "query.intr"
    LEAD = "query.lead"
    SPONS = "query.spons"
    LOCN = "query.locn"
    TITLES = "query.titles"
    OUTC = "query.outc"
    TERM = "query.term"


class Dimension(StrEnum):
    """The categorical group-by menu the planner may choose from (§7.2, §5.10)."""

    PHASE = "phase"
    OVERALL_STATUS = "overall_status"
    STUDY_TYPE = "study_type"
    LEAD_SPONSOR_CLASS = "lead_sponsor_class"
    LEAD_SPONSOR = "lead_sponsor"
    INTERVENTION_TYPE = "intervention_type"
    INTERVENTION = "intervention"
    CONDITION = "condition"
    COUNTRY = "country"
    START_YEAR = "start_year"


class Measure(StrEnum):
    """The numeric measure menu for histograms and scatter plots (§7.2, §5.10)."""

    ENROLLMENT = "enrollment"
    DURATION_MONTHS = "duration_months"
    SITE_COUNT = "site_count"


class TimeField(StrEnum):
    """Which date field a time-trend analysis buckets by (§7.2, §5.10)."""

    START_DATE = "start_date"
    PRIMARY_COMPLETION_DATE = "primary_completion_date"
    COMPLETION_DATE = "completion_date"


class NetworkType(StrEnum):
    """The core network relationship types the planner may request (§7.2, §5.10)."""

    SPONSOR_DRUG = "sponsor_drug"
    DRUG_DRUG = "drug_drug"
    CONDITION_DRUG = "condition_drug"


class AnalysisKind(StrEnum):
    """The kind of analysis the planner selects; drives the §7.3 compatibility matrix."""

    COUNT_BY = "count_by"
    TIME_TREND = "time_trend"
    HISTOGRAM = "histogram"
    SCATTER = "scatter"
    NETWORK = "network"
    TRIAL_LOOKUP = "trial_lookup"
    TRIAL_LIST = "trial_list"


class VizType(StrEnum):
    """The visualization type the planner selects; must match `Visualization.type` (§12.4)."""

    BAR_CHART = "bar_chart"
    GROUPED_BAR_CHART = "grouped_bar_chart"
    TIME_SERIES = "time_series"
    SCATTER_PLOT = "scatter_plot"
    HISTOGRAM = "histogram"
    NETWORK_GRAPH = "network_graph"
    TABLE = "table"
    METRIC = "metric"
