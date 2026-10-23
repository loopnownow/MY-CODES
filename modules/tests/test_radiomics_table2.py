"""Radiomics Table 2 mirrors clinical Table 1 format."""
from __future__ import annotations

import numpy as np
import pandas as pd

from modules.stats.radiomics import build_rad_table2


def test_build_rad_table2_matches_table1_columns():
    rng = np.random.RandomState(1)
    n = 40
    y = np.array([0] * 20 + [1] * 20)
    group = pd.Series(["HC"] * 20 + ["SSD"] * 20)
    rad = pd.DataFrame({
        "feat_a": np.concatenate([rng.normal(0, 1, 20), rng.normal(1.5, 1, 20)]),
        "feat_b": rng.normal(0, 1, n),
        "Name": [f"s{i}" for i in range(n)],
    })
    t2 = build_rad_table2(rad, y, group=group)
    assert list(t2.columns)[:4] == ["Variable", "SSD=20", "HC=20", "P value"]
    assert "Significance" in t2.columns
    assert len(t2) >= 2
    assert set(t2["Variable"].astype(str)) >= {"feat_a", "feat_b"}


def test_build_rad_table2_respects_keep_cols():
    rng = np.random.RandomState(2)
    n = 30
    y = np.array([0] * 15 + [1] * 15)
    group = pd.Series(["neg"] * 15 + ["pos"] * 15)
    rad = pd.DataFrame({
        "keep_me": rng.normal(0, 1, n),
        "drop_me": rng.normal(0, 1, n),
    })
    t2 = build_rad_table2(rad, y, group=group, keep_cols=["keep_me"])
    assert list(t2["Variable"].astype(str)) == ["keep_me"]


def test_build_rad_table2_lasso_selected_cols():
    """LASSO Table 2 uses selected feature columns only (original scale)."""
    rng = np.random.RandomState(3)
    n = 30
    y = np.array([0] * 15 + [1] * 15)
    group = pd.Series(["neg"] * 15 + ["pos"] * 15)
    rad = pd.DataFrame({
        "lasso_a": np.concatenate([rng.normal(0, 1, 15), rng.normal(1.2, 1, 15)]),
        "lasso_b": rng.normal(0, 1, n),
        "not_selected": rng.normal(0, 1, n),
    })
    selected = ["lasso_a", "lasso_b"]
    t2 = build_rad_table2(rad, y, group=group, keep_cols=selected)
    assert list(t2["Variable"].astype(str)) == selected
    assert "not_selected" not in set(t2["Variable"].astype(str))
    assert "P value" in t2.columns

