from ctviz.catalog.countries import COUNTRIES
from ctviz.schemas.request import VisualizeRequest


def test_countries_snapshot_has_no_surrounding_whitespace() -> None:
    untrimmed = [name for name in COUNTRIES if name != name.strip()]

    assert untrimmed == []


def test_request_accepts_country_whose_api_spelling_had_trailing_space() -> None:
    request = VisualizeRequest(
        query="trials in Bonaire", country="Bonaire, Saint Eustatius and Saba"
    )

    assert request.country == "Bonaire, Saint Eustatius and Saba"
