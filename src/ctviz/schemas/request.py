"""The POST /v1/visualize request body. Inputs are forgiving: human spellings are coerced first."""

import re
from datetime import date
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from ctviz.catalog.countries import COUNTRIES, COUNTRY_ALIASES
from ctviz.config import MAX_RECORDS
from ctviz.schemas.enums import OverallStatus, Phase, StudyType

NctId = Annotated[str, StringConstraints(pattern=r"^NCT\d{8}$")]
MIN_YEAR = 1900
MAX_YEARS_AHEAD = 5
_PHASE_PATTERN = re.compile(r"(phase\s*)?([1-4])(\s*/\s*(phase\s*)?([1-4]))?")
_PHASE_WORDS = {
    "na": Phase.NA,
    "n/a": Phase.NA,
    "not applicable": Phase.NA,
    "early phase 1": Phase.EARLY_PHASE1,
    "early phase1": Phase.EARLY_PHASE1,
}
_COUNTRY_BY_LOWER = {name.lower(): name for name in COUNTRIES}


def _as_list(value: Any) -> list[Any]:
    """Wrap a scalar in a single-item list so coercion always iterates a list."""
    return value if isinstance(value, list) else [value]


def _coerce_one_phase(item: Any) -> list[Phase]:
    """Map one human phase spelling (e.g. 'Phase 2/3') onto one or two `Phase` members."""
    text = str(item).strip().lower()
    if text in _PHASE_WORDS:
        return [_PHASE_WORDS[text]]
    if text.upper() in Phase.__members__:
        return [Phase(text.upper())]
    match = _PHASE_PATTERN.fullmatch(text)
    if match is None:
        raise ValueError(f"Unknown trial phase {item!r}; use e.g. 'Phase 3' or 'Phase 2/3'")
    digits = [d for d in (match.group(2), match.group(5)) if d]
    return [Phase(f"PHASE{d}") for d in digits]


def coerce_phases(value: Any) -> list[Phase]:
    """Map 'Phase 3', '3', 'phase2/3', 'N/A', 'PHASE3' … onto Phase enums."""
    return [phase for item in _as_list(value) for phase in _coerce_one_phase(item)]


def coerce_statuses(value: Any) -> list[OverallStatus]:
    """Map 'recruiting', 'Active, not recruiting' … onto OverallStatus enums."""
    statuses = []
    for item in _as_list(value):
        key = re.sub(r"[^a-z]+", "_", str(item).strip().lower()).strip("_").upper()
        if key not in OverallStatus.__members__:
            raise ValueError(f"Unknown status {item!r}; valid: {', '.join(OverallStatus)}")
        statuses.append(OverallStatus(key))
    return statuses


def canonical_country(value: str) -> str:
    """Return the API's spelling of a country (e.g. 'USA' → 'United States')."""
    lowered = value.strip().lower()
    if lowered in COUNTRY_ALIASES:
        return COUNTRY_ALIASES[lowered]
    if lowered in _COUNTRY_BY_LOWER:
        return _COUNTRY_BY_LOWER[lowered]
    raise ValueError(f"Unknown country {value!r}; use the ClinicalTrials.gov spelling")


class RequestOptions(BaseModel):
    """Tuning knobs; every field has a safe default."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_records: int = Field(default=MAX_RECORDS, ge=100, le=MAX_RECORDS)
    citations: Literal["full", "sample", "none"] = "full"
    top_n: int | None = Field(default=None, ge=3, le=50)
    include_collaborators: bool = False
    strict_match: Literal["auto", "all", "off"] = "auto"


class VisualizeRequest(BaseModel):
    """Only `query` is required; structured fields are applied by code, never copied by the LLM."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    query: str = Field(min_length=3, max_length=500)
    drug_name: str | None = Field(default=None, min_length=1, max_length=100)
    condition: str | None = Field(default=None, min_length=1, max_length=100)
    sponsor: str | None = Field(default=None, min_length=1, max_length=120)
    sponsor_role: Literal["lead", "any"] = "lead"
    trial_phase: list[Phase] | None = None
    status: list[OverallStatus] | None = None
    country: str | None = None
    start_year: int | None = None
    end_year: int | None = None
    study_type: StudyType | None = None
    nct_ids: list[NctId] | None = Field(default=None, max_length=50)
    options: RequestOptions = RequestOptions()

    @field_validator("trial_phase", mode="before")
    @classmethod
    def _coerce_phase(cls, value: Any) -> Any:
        return None if value is None else coerce_phases(value)

    @field_validator("status", mode="before")
    @classmethod
    def _coerce_status(cls, value: Any) -> Any:
        return None if value is None else coerce_statuses(value)

    @field_validator("study_type", mode="before")
    @classmethod
    def _coerce_study_type(cls, value: Any) -> Any:
        return None if value is None else str(value).strip().upper().replace(" ", "_")

    @field_validator("country")
    @classmethod
    def _canonical_country(cls, value: str | None) -> str | None:
        return None if value is None else canonical_country(value)

    @model_validator(mode="after")
    def _check_years(self) -> "VisualizeRequest":
        latest = date.today().year + MAX_YEARS_AHEAD
        for name in ("start_year", "end_year"):
            year = getattr(self, name)
            if year is not None and not MIN_YEAR <= year <= latest:
                raise ValueError(f"{name} must be between {MIN_YEAR} and {latest}")
        if self.start_year and self.end_year and self.end_year < self.start_year:
            raise ValueError("end_year (latest start year) must be ≥ start_year")
        return self
