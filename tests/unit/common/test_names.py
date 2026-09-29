"""Name/text normalization: normalize_drug and text_matches (PLAN.md §10.4)."""

import pytest

from ctviz.common.names import normalize_drug, text_matches


@pytest.mark.parametrize(
    ("raw", "key"),
    [
        ("Temozolomide (TMZ)", "temozolomide"),
        ("temozolomide 60 mg x 21 days", "temozolomide"),
        ("Ipilimumab 3mg/kg", "ipilimumab"),
        ("Drug: Lenvatinib Oral Product", "lenvatinib oral product"),
        ("Placebo", None),
        ("Standard of care", None),
    ],
)
def test_normalize_drug_strips_prefix_brackets_and_dose(raw: str, key: str | None) -> None:
    assert normalize_drug(raw) == key


def test_text_matches_is_case_and_space_insensitive_and_accepts_lists() -> None:
    assert text_matches("Pembrolizumab  Injection", "pembrolizumab injection")
    assert text_matches(["Keytruda", "MK-3475"], "mk-3475")
    assert not text_matches("Nivolumab", "pembrolizumab")
