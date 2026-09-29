import pytest
from pydantic import ValidationError

from ctviz.schemas.citations import Citation, Evidence


def test_citation_matches_the_assignment_shape_plus_evidence() -> None:
    citation = Citation(
        nct_id="NCT06472076",
        field="/protocolSection/designModule/phases/0",
        excerpt="PHASE3",
        evidence=[
            Evidence(
                role="match",
                field="/protocolSection/armsInterventionsModule/interventions/0/name",
                excerpt="Pembrolizumab",
            )
        ],
    )

    dumped = citation.model_dump(exclude_none=True)

    assert (dumped["nct_id"], dumped["excerpt"]) == ("NCT06472076", "PHASE3")
    assert dumped["evidence"][0]["role"] == "match"


def test_citation_rejects_non_pointer_fields() -> None:
    with pytest.raises(ValidationError):
        Citation(
            nct_id="NCT06472076", field="protocolSection.designModule", excerpt="x", evidence=[]
        )
