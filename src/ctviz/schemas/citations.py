"""Deep-citation data model. A Citation proves why one trial is counted in one datum."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints

JsonPointer = Annotated[str, StringConstraints(pattern=r"^(/[^/]*)+$")]
NctId = Annotated[str, StringConstraints(pattern=r"^NCT\d{8}$")]
Predicate = dict[str, Any]  # grammar enforced by ctviz.citations.predicates


class Evidence(BaseModel):
    """One exact value inside a raw study record, addressed by an RFC 6901 JSON Pointer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: Literal["match", "filter", "bucket", "context"]
    field: JsonPointer
    excerpt: str
    span: tuple[int, int] | None = None


class Citation(BaseModel):
    """{nct_id, field, excerpt} is the primary bucket evidence; `evidence` holds everything else."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    nct_id: NctId
    field: JsonPointer
    excerpt: str
    evidence: list[Evidence]
