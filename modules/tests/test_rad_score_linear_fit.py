"""组学 Logistic：入选特征后按 RAD_SCORE_LINEAR 用 logit 还是原始尺度 OLS 定分数。"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from modules.stats import radiomics as rad


def _frame(seed: int = 7, n: int = 90):
    """两列真信号 + 一列噪声；结局由 logit 生成，保证有可入选特征。"""
    rng = np.random.default_rng(seed)
    x1 = rng.normal(size=n)
    x2 = rng.normal(size=n)
    x3 = rng.normal(size=n)
    logit = 1.2 * x1 + 0.9 * x2
    y = (rng.random(n) < 1 / (1 + np.exp(-logit))).astype(int)
    df = pd.DataFrame(
        {"Name": [f"p{i}" for i in range(n)], "f1": x1, "f2": x2, "f3": x3}
    )
    return df, y


@pytest.fixture
def base_cfg(monkeypatch):
    monkeypatch.setattr(rad.cfg, "RAD_FEATURE_SOURCE", "both", raising=False)
    monkeypatch.setattr(rad.cfg, "DO_ICC_FILTER", False, raising=False)
    monkeypatch.setattr(rad.cfg, "ENABLE_CORR_FILTER", False, raising=False)
    monkeypatch.setattr(rad.cfg, "RAD_COR_CUTOFF", 0.95, raising=False)
    monkeypatch.setattr(rad.cfg, "RAD_LOGIT_UNI_P", 0.2, raising=False)
    monkeypatch.setattr(rad.cfg, "RAD_LOGIT_MV_P", 0.2, raising=False)
    monkeypatch.setattr(rad.cfg, "DO_FDR", False, raising=False)
    monkeypatch.setattr(rad.cfg, "DO_FWER", False, raising=False)
    monkeypatch.setattr(rad.cfg, "DO_RAD_FIRTH", False, raising=False)
    monkeypatch.setattr(rad.cfg, "ICC_THRESHOLD", 0.75, raising=False)


def test_linear_fit_switch_is_gone(base_cfg, monkeypatch):
    """线性拟合开关已取消：cfg 里写 False 也照样原始尺度 OLS。"""
    monkeypatch.setattr(rad.cfg, "RAD_SCORE_LINEAR", False, raising=False)
    df, y = _frame()
    res = rad.run_radiomics_logistic(df, y, score_name="RadScore_CTA", group_name="CTA")
    assert res.features, "logistic 应选出特征"
    assert res.score_fit == "ols"
    assert res.ols is not None
    # 原始尺度 OLS：套回同一层必须逐值复现。
    assert np.allclose(rad.apply_locked_radscore(res, df), res.radscore, atol=1e-9)


def test_switch_on_uses_raw_scale_ols(base_cfg, monkeypatch):
    monkeypatch.setattr(rad.cfg, "RAD_SCORE_LINEAR", True, raising=False)
    df, y = _frame()
    res = rad.run_radiomics_logistic(df, y, score_name="RadScore_CTA", group_name="CTA")
    assert res.features, "线性拟合前仍应先选出特征"
    assert res.score_fit == "ols"
    assert res.ols is not None
    # 原始尺度 OLS：套回同一层必须逐值复现。
    assert np.allclose(rad.apply_locked_radscore(res, df), res.radscore, atol=1e-9)
    # RadScore 就是 OLS 的拟合值。
    import statsmodels.api as sm

    X = sm.add_constant(df[res.features], has_constant="add")
    assert np.allclose(res.ols.predict(X), res.radscore, atol=1e-9)
    assert float(res.ols.rsquared) > 0.0


def test_selection_unchanged_by_retired_flag(base_cfg, monkeypatch):
    """旧开关不再影响选择或分数（两次结果完全一致）。"""
    df, y = _frame()
    monkeypatch.setattr(rad.cfg, "RAD_SCORE_LINEAR", False, raising=False)
    off = rad.run_radiomics_logistic(df, y, score_name="RadScore_CTA", group_name="CTA")
    monkeypatch.setattr(rad.cfg, "RAD_SCORE_LINEAR", True, raising=False)
    on = rad.run_radiomics_logistic(df, y, score_name="RadScore_CTA", group_name="CTA")
    assert off.features == on.features
    assert off.score_fit == on.score_fit == "ols"
    assert np.allclose(off.radscore, on.radscore, atol=1e-9)


def test_refit_locked_result_follows_score_fit(base_cfg, monkeypatch):
    monkeypatch.setattr(rad.cfg, "RAD_SCORE_LINEAR", True, raising=False)
    df, y = _frame()
    res = rad.run_radiomics_logistic(df, y, score_name="RadScore_CTA", group_name="CTA")
    refit = rad.refit_locked_result(res, df, y)
    assert np.allclose(refit.radscore, res.radscore, atol=1e-8)


def test_grouped_refit_reestimates_lasso_on_eval_layer():
    """分组 refit：lasso 组按本层重估原始尺度 OLS，分数长度=本层行数（不再沿用训练长度）。"""
    from dataclasses import replace

    from modules.stats.radiomics import RadiomicsResult, score_map
    from modules.stats.val_mode import refit_radscore_ols

    train_df, _ = _frame(seed=7, n=90)
    eval_df, y_eval = _frame(seed=11, n=40)
    group = RadiomicsResult(
        features=["f1", "f2"], coefs=np.array([1.0, -0.5]),
        radscore=np.zeros(len(train_df)), intercept=0.25,
        betas=pd.Series({"f1": 1.0, "f2": -0.5}), ols=None,
        lambda_used=0.05, formula_df=None, formula_text=None,
        method="lasso", score_name="RadScore_CTA", group_name="CTA",
    )
    result = RadiomicsResult(
        features=[], coefs=np.array([]), radscore=None, intercept=None,
        betas=None, ols=None, lambda_used=float("nan"), formula_df=None,
        formula_text=None, method="lasso", groups=[group],
    )

    out = rad.refit_locked_result(result, eval_df, y_eval)
    scores = score_map(out)
    assert set(scores) == {"RadScore_CTA"}
    arr = np.asarray(scores["RadScore_CTA"], dtype=float)
    assert len(arr) == len(eval_df)
    assert np.isfinite(arr).all()
    expected = refit_radscore_ols(eval_df, y_eval, ["f1", "f2"])
    assert expected is not None and np.allclose(arr, expected)
    # 不是把训练公式直接套到本层（refit 必须重估系数）。
    assert not np.allclose(arr, rad.apply_locked_radscore(group, eval_df))

    # 无锁定公式的组：不沿用训练长度分数，给本层全 NaN。
    blank_group = replace(group, features=[], betas=None, intercept=None)
    blank = rad.refit_locked_result(
        replace(result, groups=[blank_group]), eval_df, y_eval,
    )
    blank_arr = np.asarray(score_map(blank)["RadScore_CTA"], dtype=float)
    assert len(blank_arr) == len(eval_df)
    assert np.isnan(blank_arr).all()


def test_block_param_linear_is_retired():
    """组学模块面板的「线性拟合」勾选已取消：旧 ini 里写着也忽略，不再改配置。"""
    from modules.config.blocks import normalize_blocks
    from modules.stats import selectors

    for method in ("logistic", "lasso"):
        spec = normalize_blocks(
            [{"name": "CTA", "method": method, "params": {method: {"linear": True}}}]
        )[0]
        assert "RAD_SCORE_LINEAR" not in selectors._param_settings(spec)
    spec_off = normalize_blocks([{"name": "CTA", "method": "logistic"}])[0]
    assert "RAD_SCORE_LINEAR" not in selectors._param_settings(spec_off)
