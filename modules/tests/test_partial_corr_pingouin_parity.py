"""partial_corr 换 pingouin 的对拍（r 与 p 均 1e-12）。

换库依据（提交说明里也有）：逐点对照 dr ≤ 5.6e-16 / dp ≤ 2.3e-28，
含「数值协变量 + 分类协变量（drop_first 哑变量）」与 pearson/spearman 两种方法。

Run: python -B -m pytest modules/tests/test_partial_corr_pingouin_parity.py -q
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pingouin
import pytest

from modules.stats.assoc import partial_corr

TOL = 1e-12
KEYS = {"x", "y", "controls", "method", "r", "lo", "hi", "p", "n", "df", "k"}


def _frame(seed: int = 7, n: int = 120) -> pd.DataFrame:
    rng = np.random.RandomState(seed)
    df = pd.DataFrame({
        "x": rng.normal(size=n),
        "z1": rng.normal(size=n),
        "z2": rng.normal(size=n),
        "sex": rng.choice(["F", "M"], size=n),
    })
    df["y"] = 0.4 * df["x"] + 0.3 * df["z1"] + rng.normal(size=n) * 0.5
    return df


def _pingouin_frame(df: pd.DataFrame, covars: list[str]) -> tuple[pd.DataFrame, list[str]]:
    frame = df[["x", "y"]].copy()
    cov: list[str] = []
    for name in covars:
        if name == "sex":
            frame["sex_M"] = (df["sex"] == "M").astype(float)
            cov.append("sex_M")
        else:
            frame[name] = df[name]
            cov.append(name)
    return frame, cov


@pytest.mark.parametrize("method", ["pearson", "spearman"])
@pytest.mark.parametrize("covars", [["z1"], ["z1", "z2"], ["z1", "sex"]])
def test_r_and_p_match_pingouin(method: str, covars: list[str]) -> None:
    df = _frame()
    got = partial_corr(df, "x", "y", covars, method)
    frame, cov = _pingouin_frame(df, covars)
    want = pingouin.partial_corr(data=frame, x="x", y="y", covar=cov, method=method)
    p_col = next(c for c in want.columns if str(c).lower().startswith("p"))
    assert abs(got["r"] - float(want["r"].iloc[0])) < TOL
    assert abs(got["p"] - float(want[p_col].iloc[0])) < TOL
    assert got["n"] == len(frame)
    assert got["k"] == len(cov)
    assert set(got) == KEYS


def test_method_is_coerced_and_controls_deduped() -> None:
    df = _frame()
    got = partial_corr(df, "x", "y", ["z1", "x", "y"], "kendall")
    assert got["method"] == "spearman"
    assert got["controls"] == ["z1"]


def test_missing_column_raises() -> None:
    df = _frame()
    with pytest.raises(ValueError, match="缺少列"):
        partial_corr(df, "x", "y", ["nope"], "spearman")


def test_too_few_complete_cases_raises() -> None:
    df = _frame(n=120).head(6)
    with pytest.raises(ValueError, match="完整例数"):
        partial_corr(df, "x", "y", ["z1", "z2"], "spearman")


def test_constant_outcome_raises() -> None:
    df = _frame()
    df["x"] = 1.0
    with pytest.raises(ValueError, match="残差没有变异"):
        partial_corr(df, "x", "y", ["z1"], "pearson")
