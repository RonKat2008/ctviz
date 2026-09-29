import pytest
from pydantic import ValidationError

from ctviz.schemas.response import ErrorInfo, ExcludedTrial, VisualizeResponse


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
