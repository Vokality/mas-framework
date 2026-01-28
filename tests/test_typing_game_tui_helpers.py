from __future__ import annotations

import pytest

from examples.typing_game.tui.formatting import (
    display_glyph_for_character,
    format_target_word,
)
from examples.typing_game.tui.input import normalize_typed_character


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("a", "a"),
        ("A", "a"),
        (" ", " "),
        ("\n", None),
        ("\t", None),
        ("1", None),
        ("-", None),
        (None, None),
    ],
)
def test_normalize_typed_character(raw: str | None, expected: str | None) -> None:
    assert normalize_typed_character(raw) == expected


def test_display_glyph_for_character() -> None:
    assert display_glyph_for_character("a") == "A"
    assert display_glyph_for_character(" ") == "␠"


def test_format_target_word_spans_are_stable() -> None:
    text = format_target_word("ab", 1)
    assert text.plain.replace(" ", "") == "AB"
