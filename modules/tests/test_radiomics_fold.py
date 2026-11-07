"""C-02 / R-01: fold-local preprocess does not use validation-fold statistics."""
from __future__ import annotations

import numpy as np
import pandas as pd

from modules.stats.radiomics import _apply_preprocess, _fit_preprocess, _prefilter_by_auc


def test_preprocess_stats_come_from_train_only():
    rng = np.random.RandomState(0)
    train = pd.DataFrame({
        "f1": rng.normal(0, 1, 80),
        "f2": rng.normal(3, 1, 80),
    })
    val = pd.DataFrame({
        "f1": rng.normal(50, 1, 20),
        "f2": rng.normal(50, 1, 20),
    })
    _xs, params = _fit_preprocess(train, cutoff=1.0, enable_corr=False, verbose=False)
    xv = _apply_preprocess(val, params)
    assert abs(float(params["means"]["f1"])) < 1.0
    # val mean ~50; after train-centering should stay large
    assert float(xv["f1"].mean()) > 30


def test_logistic_screen_locks_train_scale():
    from modules import config as cfg
    from modules.stats import radiomics as radmod
    from modules.stats.radiomics import (
        RadiomicsResult,
        _apply_preprocess,
        apply_locked_radscore,
        refit_radscore_logistic,
        run_radiomics_logistic,
    )
    from modules.stats.report import _radiomics_logistic_html

    keys = [
        "DO_ICC_FILTER", "RAD_FEATURE_SOURCE", "ENABLE_CORR_FILTER", "RAD_COR_CUTOFF",
        "TABLE1_P_THRESHOLD", "ROC_MV_P_THRESHOLD",
        "DO_FIRTH", "RAD_LOGIT_UNI_P", "RAD_LOGIT_MV_P", "DO_RAD_FIRTH",
        "RAD_FEATURE_PREFIX_GROUP",
    ]
    old = {k: getattr(cfg, k) for k in keys}
    rng = np.random.RandomState(0)
    n = 120
    y = np.array([0] * 60 + [1] * 60)
    signal = rng.normal(0, 1, n)
    signal[y == 1] += 3.0
    twin = signal + rng.normal(0, 0.01, n)
    noise = {f"n{i}": rng.normal(0, 1, n) for i in range(4)}
    X = pd.DataFrame({"signal": signal, "twin": twin, **noise})
    try:
        cfg.DO_ICC_FILTER = False
        cfg.RAD_FEATURE_SOURCE = "both"
        cfg.ENABLE_CORR_FILTER = True
        cfg.RAD_COR_CUTOFF = 0.95
        cfg.TABLE1_P_THRESHOLD = 1e-12
        cfg.ROC_MV_P_THRESHOLD = 1e-12
        cfg.DO_FIRTH = True
        cfg.RAD_LOGIT_UNI_P = 0.1
        cfg.RAD_LOGIT_MV_P = 0.05
        cfg.DO_RAD_FIRTH = False
        cfg.RAD_FEATURE_PREFIX_GROUP = {"names": [], "assign": {}}
        res = run_radiomics_logistic(X, y, dataset_label="train")
        html = _radiomics_logistic_html(res, "train")
    finally:
        for k, v in old.items():
            setattr(cfg, k, v)

    assert res.method == "logistic"
    assert not res.groups
    assert res.select_source == "mv_p"
    assert set(res.features) <= {"signal", "twin"}
    assert len(res.features) == 1, res.features
    assert res.radscore is not None and len(res.radscore) == n
    assert np.isfinite(res.radscore).all()
    assert len(set(res.preprocess["keep_cols"]) & {"signal", "twin"}) == 1
    # 线性拟合开关已取消：筛选（按训练集标准化尺度）后一律原始尺度 OLS 定分。
    assert res.score_fit == "ols"
    feat = res.features[0]
    val = X.copy()
    val[feat] = val[feat] + 40
    locked = apply_locked_radscore(res, val)
    raw = (
        float(res.intercept)
        + val[res.features].to_numpy(dtype=float) @ res.betas.reindex(res.features).to_numpy()
    )
    assert np.allclose(locked, raw)
    scaled = _apply_preprocess(val, res.preprocess).reindex(columns=res.features).to_numpy(dtype=float)
    logit_scale = float(res.intercept) + scaled @ res.betas.reindex(res.features).to_numpy(dtype=float)
    assert not np.allclose(locked, logit_scale)
    assert "Radiomics Univariate Logistic" in html
    assert "Radiomics Multivariate Logistic (Train — selected)" in html
    assert "Firth" not in html
    assert "1e-12" not in html
    assert "p < 0.1" in html
    held = float(res.intercept)
    real_fit = radmod._fit_preprocess

    def _boom(*_a, **_k):
        raise AssertionError("refit must keep the training preprocess")

    radmod._fit_preprocess = _boom
    try:
        same = refit_radscore_logistic(res, X, y)
        flipped = refit_radscore_logistic(res, X, 1 - y)
    finally:
        radmod._fit_preprocess = real_fit
    assert same is not None and len(same) == n and np.isfinite(same).all()
    assert flipped is not None and len(flipped) == n and np.isfinite(flipped).all()
    assert not np.allclose(flipped, apply_locked_radscore(res, X))
    assert float(res.intercept) == held
    ols_again = radmod.refit_locked_result(res, X, y)
    assert np.allclose(ols_again.radscore, res.radscore, atol=1e-8)
    blank = RadiomicsResult(
        features=["signal"], coefs=np.array([1.0]), radscore=None,
        intercept=0.0, betas=None, ols=None, lambda_used=np.nan,
        formula_df=None, formula_text=None, method="lasso",
    )
    assert _radiomics_logistic_html(blank, "train") == ""
    assert refit_radscore_logistic(blank, X, y) is None


def test_prefix_groups_fit_separate_logistics():
    from modules import config as cfg
    from modules.stats.radiomics import (
        apply_locked_result,
        run_radiomics_logistic,
        score_map,
        selected_feature_names,
    )
    from modules.stats.report import _radiomics_formula_html, _radiomics_logistic_html

    keys = [
        "DO_ICC_FILTER", "RAD_FEATURE_SOURCE", "ENABLE_CORR_FILTER", "RAD_COR_CUTOFF",
        "TABLE1_P_THRESHOLD", "ROC_MV_P_THRESHOLD",
        "DO_FIRTH", "RAD_LOGIT_UNI_P", "RAD_LOGIT_MV_P", "DO_RAD_FIRTH",
        "RAD_FEATURE_PREFIX_GROUP",
    ]
    old = {k: getattr(cfg, k) for k in keys}
    rng = np.random.RandomState(1)
    n = 120
    y = np.array([0] * 60 + [1] * 60)
    md = rng.normal(0, 1, n)
    md[y == 1] += 3.0
    mk = rng.normal(0, 1, n)
    mk[y == 1] += 2.8
    r2 = rng.normal(0, 1, n)
    r2[y == 1] += 3.0
    X = pd.DataFrame({
        "MD_signal": md,
        "MD_noise": rng.normal(0, 1, n),
        "MK_signal": mk,
        "MK_noise": rng.normal(0, 1, n),
        "R2_signal": r2,
    })
    try:
        cfg.DO_ICC_FILTER = False
        cfg.RAD_FEATURE_SOURCE = "both"
        cfg.ENABLE_CORR_FILTER = True
        cfg.RAD_COR_CUTOFF = 0.95
        cfg.TABLE1_P_THRESHOLD = 1e-12
        cfg.ROC_MV_P_THRESHOLD = 1e-12
        cfg.DO_FIRTH = False
        cfg.RAD_LOGIT_UNI_P = 0.1
        cfg.RAD_LOGIT_MV_P = 0.05
        cfg.DO_RAD_FIRTH = False
        cfg.RAD_FEATURE_PREFIX_GROUP = {
            "names": ["DTI", "DKI", "ASL"],
            "assign": {"MD_": "DTI", "MK_": "DKI", "R2_": "nope"},
        }
        res = run_radiomics_logistic(X, y, dataset_label="train")
        page = _radiomics_logistic_html(res, "train")
        formula = _radiomics_formula_html(res)
    finally:
        for k, v in old.items():
            setattr(cfg, k, v)

    assert res.method == "logistic"
    assert res.select_source == "grouped"
    assert res.features == []
    assert res.radscore is None
    assert res.formula_text is None
    by = {g.group_name: g for g in res.groups}
    assert set(by) == {"DTI", "DKI"}
    assert "MD_signal" in by["DTI"].features
    assert "MK_signal" in by["DKI"].features
    assert all(not str(f).startswith("R2_") for f in selected_feature_names(res))
    assert all(not str(f).startswith("MK_") for f in by["DTI"].features)
    assert all(not str(f).startswith("MD_") for f in by["DKI"].features)
    smap = score_map(res)
    assert set(smap) == {"RadScore_DTI", "RadScore_DKI"}
    assert all(len(v) == n and np.isfinite(v).all() for v in smap.values())
    applied = apply_locked_result(res, X)
    assert applied.radscore is None
    assert set(score_map(applied)) == {"RadScore_DTI", "RadScore_DKI"}
    assert "Radiomics logistic — DTI" in page
    assert "Radiomics logistic — DKI" in page
    assert "Radiomics logistic — ASL" not in page
    # 线性拟合恒为开：结果页出 RadScore Formula（OLS 系数表）。
    assert formula != ""
    assert "RadScore_DTI" in formula or "RadScore" in formula


def test_auc_prefilter_topk():
    rng = np.random.RandomState(1)
    n = 60
    y = np.array([0] * 30 + [1] * 30)
    good = rng.normal(0, 1, n)
    good[y == 1] += 2.0
    noise = {f"n{i}": rng.normal(0, 1, n) for i in range(8)}
    X = pd.DataFrame({"signal": good, **noise})
    kept = _prefilter_by_auc(X, y, topk=3, verbose=False)
    assert kept.shape[1] == 3
    assert "signal" in kept.columns


def test_normalize_switch_controls_scaling_only(monkeypatch):
    """RAD_NORMALIZE=False 只去掉 z-score；插补、零方差、NZV 与保留列不变。"""
    from modules.stats import radiomics as radmod

    rng = np.random.RandomState(0)
    train = pd.DataFrame({
        "f1": rng.normal(5, 2, 80),
        "f2": rng.normal(0, 1, 80),
        "const": np.ones(80),
    })
    val = pd.DataFrame({
        "f1": rng.normal(7, 2, 20),
        "f2": rng.normal(0, 1, 20),
        "const": np.ones(20),
    })
    monkeypatch.setattr(radmod.cfg, "RAD_NORMALIZE", True, raising=False)
    x_on, p_on = _fit_preprocess(train, cutoff=1.0, enable_corr=False, verbose=False)
    monkeypatch.setattr(radmod.cfg, "RAD_NORMALIZE", False, raising=False)
    x_off, p_off = _fit_preprocess(train, cutoff=1.0, enable_corr=False, verbose=False)

    assert p_on["normalize"] is True
    assert p_off["normalize"] is False
    assert list(x_on.columns) == list(x_off.columns) == ["f1", "f2"]
    assert np.allclose(x_on.mean(), 0.0, atol=1e-12)
    assert np.allclose(x_on.std(ddof=1), 1.0, atol=1e-12)
    assert np.allclose(x_off.to_numpy(), train[list(x_off.columns)].to_numpy())
    assert (p_off["means"] == 0.0).all()
    assert (p_off["stds"] == 1.0).all()
    assert np.allclose(p_on["means"], train[["f1", "f2"]].mean())

    xv_on = _apply_preprocess(val, p_on)
    xv_off = _apply_preprocess(val, p_off)
    assert np.allclose(xv_off.to_numpy(), val[list(xv_off.columns)].to_numpy())
    assert not np.allclose(xv_on.to_numpy(), val[list(xv_on.columns)].to_numpy())
    # 旧缓存/旧结果的 params 没有 normalize 键：仍按存下的 means/stds 套用。
    legacy = {k: v for k, v in p_on.items() if k != "normalize"}
    assert np.allclose(_apply_preprocess(val, legacy).to_numpy(), xv_on.to_numpy())


def test_rad_normalize_default_and_lasso_cache_key():
    """RAD_NORMALIZE 默认开、属预处理分节；LASSO 缓存键包含该开关。"""
    from modules.config.defaults import DEFAULT_SETTINGS
    from modules.config.ini_io import INI_SECTIONS
    from modules.stats.radiomics import (
        _LASSO_CACHE_VERSION,
        _lasso_cache_digest,
        _lasso_cache_params,
    )

    assert DEFAULT_SETTINGS["RAD_NORMALIZE"] is True
    sec = {k: sid for sid, _t, ks in INI_SECTIONS for k in ks}
    assert sec["RAD_NORMALIZE"] == "radprep"

    base = dict(
        alpha=1.0, nfolds=5, n_cs=3, seed=1, max_iter=100, topk=0,
        c_min=1e-3, c_max=50.0, tol=1e-3, cutoff=0.95, enable_corr=True,
    )
    on = _lasso_cache_params(normalize=True, **base)
    off = _lasso_cache_params(normalize=False, **base)
    assert on["normalize"] is True and off["normalize"] is False
    assert on["cache_version"] == off["cache_version"] == _LASSO_CACHE_VERSION
    assert _LASSO_CACHE_VERSION >= 2
    X = pd.DataFrame({"f1": [0.0, 1.0, 2.0], "f2": [3.0, 2.0, 1.0]})
    y = np.array([0, 1, 1])
    assert _lasso_cache_digest(X, y, on) != _lasso_cache_digest(X, y, off)
