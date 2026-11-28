"""B3: pairwise 'vs' parsing; names containing 'vs' must not split."""
from __future__ import annotations

import pytest

from modules.pipeline import _parse_pairwise, _split_pair


@pytest.mark.parametrize(
    "text, expected",
    [
        ("A vs B", ("A", "B")),
        ("A VS B", ("A", "B")),
        ("A Vs B", ("A", "B")),
        ("Canvas vs Control", ("Canvas", "Control")),
        ("A vs. B", ("A", "B")),
        ("AvsB", None),
    ],
)
def test_split_pair_whitespace_boundary(text, expected):
    assert _split_pair(text) == expected


def test_parse_pairwise_accepts_vs_casings_and_skips_substring():
    got = _parse_pairwise([
        "A vs B",
        "A VS B",
        "A Vs B",
        "Canvas vs Control",
        "A vs. B",
        "AvsB",
        ["X", "Y"],
    ])
    assert got == [
        ("A", "B"),
        ("A", "B"),
        ("A", "B"),
        ("Canvas", "Control"),
        ("A", "B"),
        ("X", "Y"),
    ]


def test_parse_pairwise_does_not_split_canvas_or_ravs_alone():
    assert _parse_pairwise(["Canvas", "Ravs", "CanvasRavs"]) == []
