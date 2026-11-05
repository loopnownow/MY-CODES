"""DO_DROP_DERIVED：热图与 VIF 表直接剔除完全共线的派生列。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from modules import config as cfg
from modules.stats import correlation, radiomics
from modules.stats.clinical import _univariate_logistic
from modules.stats.vif_analysis import derived_features


def _frame(n: int = 60, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    r = np.round(rng.normal(size=n) * 3 + 8)
    l_ = np.round(rng.normal(size=n) * 3 + 8)
    df = pd.DataFrame({"R_AFC": r, "L_AFC": l_})
    df["AFC_total"] = df["R_AFC"] + df["L_AFC"]  # 精确派生：合计 = 两侧之和
    return df


def test_derived_features_detects_exact_sum():
    df = _frame()
    assert derived_features(df, ["R_AFC", "L_AFC", "AFC_total"]) == ["AFC_total"]


def test_heatmap_drops_derived_columns():
    df = _frame()
    out = correlation.drop_derived_columns(df)
    assert list(out.columns) == ["R_AFC", "L_AFC"]


def test_univariate_vif_drops_derived_rows_when_on(monkeypatch):
    df = _frame()
    y = (np.asarray(df["R_AFC"]) > 8).astype(int)
    uni = _univariate_logistic(df, y)
    monkeypatch.setattr(cfg, "DO_DROP_DERIVED", True, raising=False)
    out = radiomics._attach_univariate_vif(uni, df)
    assert "AFC_total" not in out["Feature"].tolist()
    assert "∞" not in set(out["VIF"].astype(str))


def test_univariate_vif_marks_infinity_when_off(monkeypatch):
    df = _frame()
    y = (np.asarray(df["R_AFC"]) > 8).astype(int)
    uni = _univariate_logistic(df, y)
    monkeypatch.setattr(cfg, "DO_DROP_DERIVED", False, raising=False)
    out = radiomics._attach_univariate_vif(uni, df)
    assert "AFC_total" in out["Feature"].tolist()
    hit = out.loc[out["Feature"].astype(str) == "AFC_total", "VIF"]
    assert str(hit.iloc[0]) == "∞"
