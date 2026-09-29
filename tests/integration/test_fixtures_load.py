"""Fixture-load tests: recorded totals become regression assertions (see DEVLOG for any drift)."""

from tests.fixtures.load import load_fixture


def test_recorded_fixtures_have_the_expected_totals() -> None:
    assert len(load_fixture("pembrolizumab")) == 2960
    assert len(load_fixture("ms_recruiting")) == 430
    assert len(load_fixture("psoriasis_p2")) == 512
