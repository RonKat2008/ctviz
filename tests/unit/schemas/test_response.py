import pytest
from pydantic import ValidationError

from ctviz.schemas.response import CitationPolicy, ErrorInfo, ExcludedTrial, VisualizeResponse


def test_error_envelope_has_one_parse_path() -> None:
    body = VisualizeResponse.failure(
        ErrorInfo(code="OUT_OF_SCOPE", message="No efficacy data.")
    ).model_dump()

    assert body["ok"] is False
    assert body["visualization"] is None
    assert body["error"]["code"] == "OUT_OF_SCOPE"
    assert body["schema_version"] == "1.0.0"


def test_excluded_trial_rejects_a_malformed_nct_id() -> None:
    with pytest.raises(ValidationError):
        ExcludedTrial(nct_id="NCT123", stage="match", reason="api_fulltext_match_only")


def test_citation_policy_has_the_documented_shape() -> None:
    policy = CitationPolicy(
        mode="full",
        pointer_format="RFC6901",
        url_template="https://clinicaltrials.gov/study/{nct_id}",
    )

    assert policy.model_dump() == {
        "mode": "full",
        "pointer_format": "RFC6901",
        "url_template": "https://clinicaltrials.gov/study/{nct_id}",
    }


def test_citation_policy_rejects_an_unknown_pointer_format() -> None:
    with pytest.raises(ValidationError):
        CitationPolicy(mode="full", pointer_format="XPATH", url_template="https://x/{nct_id}")
