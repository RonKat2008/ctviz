"""§14 golden number: recruiting-site country counts on the `ms_recruiting` fixture."""

from ctviz.analysis.aggregate import MatchedTrial, count_by
from ctviz.analysis.dimensions import country_extractor
from ctviz.schemas.enums import Dimension
from tests.fixtures.load import load_trials


def test_country_recruiting_rule_gives_united_states_157_on_ms_recruiting_fixture() -> None:
    trials = [MatchedTrial(trial, ()) for trial in load_trials("ms_recruiting")]

    result = count_by(trials, Dimension.COUNTRY, extractor=country_extractor(recruiting_only=True))

    counts = {b.key: b.trial_count for b in result.buckets}
    assert counts["United States"] == 157
