"""RFC 6901 JSON Pointer build/resolve and exact-text excerpts (PLAN.md §11.4)."""

import pytest

from ctviz.citations.pointer import build_pointer, json_text, resolve_pointer


def test_pointer_round_trip_with_rfc6901_escaping() -> None:
    doc = {"a/b": {"c~d": [10, {"e": "x"}]}}
    pointer = build_pointer("a/b", "c~d", 1, "e")

    assert pointer == "/a~1b/c~0d/1/e"
    assert resolve_pointer(doc, pointer) == "x"


def test_resolve_pointer_raises_on_missing_paths() -> None:
    with pytest.raises((KeyError, IndexError)):
        resolve_pointer({"a": [1]}, "/a/5")


@pytest.mark.parametrize(
    ("value", "text"),
    [(84, "84"), ("PHASE3", "PHASE3"), (True, "true"), (84.5, "84.5"), (84.0, "84.0")],
)
def test_json_text_renders_scalars_like_the_api(value: object, text: str) -> None:
    assert json_text(value) == text
