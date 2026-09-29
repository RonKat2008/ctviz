from ctviz.schemas.response import ErrorInfo, VisualizeResponse


def test_error_envelope_has_one_parse_path() -> None:
    body = VisualizeResponse.failure(
        ErrorInfo(code="OUT_OF_SCOPE", message="No efficacy data.")
    ).model_dump()

    assert body["ok"] is False
    assert body["visualization"] is None
    assert body["error"]["code"] == "OUT_OF_SCOPE"
    assert body["schema_version"] == "1.0.0"
