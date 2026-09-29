import pytest
from pydantic import ValidationError

from ctviz.schemas.enums import OverallStatus, Phase
from ctviz.schemas.request import VisualizeRequest


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("Phase 3", [Phase.PHASE3]),
        ("phase3", [Phase.PHASE3]),
        ("3", [Phase.PHASE3]),
        ("PHASE3", [Phase.PHASE3]),
        ("Phase 2/3", [Phase.PHASE2, Phase.PHASE3]),
        ("early phase 1", [Phase.EARLY_PHASE1]),
        ("N/A", [Phase.NA]),
        (["Phase 1", "Phase 2"], [Phase.PHASE1, Phase.PHASE2]),
    ],
)
def test_trial_phase_accepts_human_spellings(given: object, expected: list[Phase]) -> None:
    assert VisualizeRequest(query="phases please", trial_phase=given).trial_phase == expected


def test_status_is_case_and_punctuation_insensitive() -> None:
    request = VisualizeRequest(query="q q", status=["recruiting", "Active, not recruiting"])

    assert request.status == [OverallStatus.RECRUITING, OverallStatus.ACTIVE_NOT_RECRUITING]


@pytest.mark.parametrize(("given", "expected"), [("USA", "United States"), ("japan", "Japan")])
def test_country_is_normalized_to_the_api_spelling(given: str, expected: str) -> None:
    assert VisualizeRequest(query="q q", country=given).country == expected


def test_unknown_country_is_rejected_with_a_helpful_message() -> None:
    with pytest.raises(ValidationError, match="Unknown country 'Atlantis'"):
        VisualizeRequest(query="q q", country="Atlantis")


def test_end_year_before_start_year_is_rejected() -> None:
    with pytest.raises(ValidationError, match="end_year"):
        VisualizeRequest(query="q q", start_year=2020, end_year=2015)


def test_query_is_trimmed_and_must_not_be_blank() -> None:
    assert VisualizeRequest(query="  hello  ").query == "hello"
    with pytest.raises(ValidationError):
        VisualizeRequest(query="   ")


def test_nct_ids_must_match_the_registry_format() -> None:
    with pytest.raises(ValidationError):
        VisualizeRequest(query="q q", nct_ids=["NCT123"])


def test_options_default_to_full_citations_and_auto_strict_match() -> None:
    options = VisualizeRequest(query="q q").options

    assert (options.citations, options.strict_match, options.max_records) == (
        "full",
        "auto",
        20_000,
    )
