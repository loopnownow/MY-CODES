"""Block categorical descriptor tables (B3): non-numeric block columns stay visible."""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd

from modules.stats.report import (
    _block_cat_columns,
    _block_cat_section,
    _block_cat_stats,
    _cat_cell,
)


def _layer(values: list[str], labels: list[int], extra: dict | None = None) -> SimpleNamespace:
    """一层假数据：blocks={'US': frame} + label。"""
    frame = pd.DataFrame({"Name": [f"p{i}" for i in range(len(values))], "Plaque_echo": values})
    if extra:
        for key, col in extra.items():
            frame[key] = col
    return SimpleNamespace(blocks={"US": frame}, label=np.array(labels))


def test_block_cat_columns_skips_ids_and_numeric():
    frame = pd.DataFrame(
        {
            "Name": ["a", "b"],
            "Patient_ID": ["1", "2"],
            "Plaque_echo": ["Mixed", "Isoechoic"],
            "Thickness": [1.0, 2.0],
            "Ulceration": ["Negative", "Positive"],
        }
    )
    assert _block_cat_columns(frame) == ["Plaque_echo", "Ulceration"]
    assert _block_cat_columns(None) == []


def test_block_cat_stats_counts_and_p():
    frame = pd.DataFrame({"Plaque_echo": ["Mixed", "Isoechoic", "Mixed", "Mixed"]})
    labels = np.array([1, 1, 0, 0])
    levels, counts, p = _block_cat_stats(frame, labels, "Plaque_echo")
    assert levels == ["Isoechoic", "Mixed"]
    assert counts["Mixed"] == (1, 2)
    assert counts["Isoechoic"] == (1, 0)
    assert 0.0 <= p <= 1.0


def test_block_cat_stats_missing_becomes_level():
    frame = pd.DataFrame({"Plaque_echo": ["Mixed", None]})
    levels, counts, _p = _block_cat_stats(frame, np.array([1, 0]), "Plaque_echo")
    assert set(levels) == {"Mixed", "Missing"}
    assert counts["Missing"] == (0, 1)


def test_cat_cell_guards_empty_group():
    assert _cat_cell(3, 4) == "3 (75.0%)"
    assert _cat_cell(0, 0) == "0 (—)"


def test_block_cat_section_renders_three_sets():
    data = SimpleNamespace(
        train=_layer(["Mixed", "Mixed", "Isoechoic"], [1, 0, 0]),
        test=_layer(["Mixed", "Isoechoic"], [1, 0]),
        all=_layer(["Mixed", "Mixed", "Isoechoic", "Mixed"], [1, 0, 0, 1]),
    )
    html = _block_cat_section(data, "Stroke", "non_Stroke")
    assert "B3. Block categorical descriptors" in html
    assert "Plaque_echo (US)" in html
    assert "Development Stroke (n=1)" in html
    assert "All non_Stroke (n=2)" in html
    assert "1 (50.0%)" in html
    assert html.count("<table") == 1


def test_block_cat_section_empty_without_text_columns():
    numeric_only = SimpleNamespace(
        blocks={"CTA": pd.DataFrame({"Name": ["a", "b"], "Max_volume": [1.0, 2.0]})},
        label=np.array([1, 0]),
    )
    data = SimpleNamespace(train=numeric_only, test=None, all=numeric_only)
    assert _block_cat_section(data, "Stroke", "non_Stroke") == ""
