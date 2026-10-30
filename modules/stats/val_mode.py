"""
val_mode.py — 验证策略（不重新筛选）
═══════════════════════════════════════════════════════════════════
VAL_MODE 三选一（特征集一律锁定 Train；LASSO / stepwise 不再跑）：

    lock_threshold  锁定阈值（独立验证默认）
                    套 Train 系数 + Train 的 logistic 校准与 Youden
    apply_formula   套公式
                    验证集套 Train 系数；Youden 在本层重算
    refit           重新拟合 / 模型更新（须显式 opt-in）
                    同一特征集在评估层用该层结局重估系数；Youden 在本层重算
                    不得当作独立测试 / 外部验证性能上报

非法值：raise ValueError（不静默回退）。
配置：settings.ini  [switch] VAL_MODE
入口：resolve_val_mode()
═══════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd
import statsmodels.api as sm

from modules import config as cfg

VAL_MODE_ALIASES: Dict[str, str] = {
    "apply_formula": "apply_formula",
    "apply": "apply_formula",
    "formula": "apply_formula",
    "applyformula": "apply_formula",
    "套公式": "apply_formula",
    "lock_threshold": "lock_threshold",
    "lock": "lock_threshold",
    "locked": "lock_threshold",
    "lockthreshold": "lock_threshold",
    "锁定阈值": "lock_threshold",
    "refit": "refit",
    "refit_coefs": "refit",
    "refitcoefs": "refit",
    "重新拟合": "refit",
}

VAL_MODE_LABELS: Dict[str, str] = {
    "apply_formula": "套公式（锁定特征与系数，验证集重算 Youden）",
    "lock_threshold": "锁定阈值（锁定特征、系数与 Train Youden；独立验证）",
    "refit": "重新拟合/模型更新（锁定特征，评估层用结局重估系数；非独立验证）",
}


def resolve_val_mode(raw=None) -> str:
    """把配置值规范成 apply_formula / lock_threshold / refit。

    默认（raw is None 且配置缺键）为 lock_threshold。
    非法字符串直接 ValueError，不回退 refit。
    """
    if raw is None:
        raw = getattr(cfg, "VAL_MODE", "lock_threshold")
    key = str(raw if raw is not None else "lock_threshold").strip()
    if not key:
        key = "lock_threshold"
    tok = key.lower().replace(" ", "").replace("-", "_")
    mode = VAL_MODE_ALIASES.get(key) or VAL_MODE_ALIASES.get(tok)
    if mode is None:
        allowed = "apply_formula | lock_threshold | refit"
        raise ValueError(
            f"VAL_MODE={raw!r} 无效；允许: {allowed}（refit 须显式 opt-in，不得静默回退）"
        )
    return mode


def val_mode_label(mode: Optional[str] = None) -> str:
    m = resolve_val_mode(mode)
    return VAL_MODE_LABELS.get(m, m)


def _safe_predict_glm(model, X: pd.DataFrame, n: int) -> Optional[np.ndarray]:
    """用已拟合 GLM 在可能含缺失的设计阵上预测概率。"""
    if model is None or X is None or len(X) == 0:
        return None
    cols = [c for c in model.params.index if c in X.columns]
    if not cols:
        return None
    arr = X[cols].to_numpy(dtype=float)
    ok = np.isfinite(arr).all(axis=1)
    if ok.sum() < 1:
        return None
    out = np.full(n, np.nan, dtype=float)
    out[ok] = np.asarray(model.predict(X.loc[ok, cols]), dtype=float)
    return out


def refit_clinical_prob(
    clin_df: pd.DataFrame,
    y: np.ndarray,
    features: Sequence[str],
) -> Optional[np.ndarray]:
    """锁定临床特征，在本层重拟合 logit，返回预测概率。"""
    feats = [f for f in features if f in clin_df.columns]
    if not feats:
        return None
    from modules.stats.clinical import _fit_multivar_glm

    model, _lp, _mv = _fit_multivar_glm(clin_df, np.asarray(y).astype(int), feats)
    if model is None:
        return None
    X = sm.add_constant(
        clin_df[feats].apply(pd.to_numeric, errors="coerce"),
        has_constant="add",
    )
    return _safe_predict_glm(model, X, len(clin_df))


def refit_radscore_ols(
    rad_df: pd.DataFrame,
    y: np.ndarray,
    features: Sequence[str],
) -> Optional[np.ndarray]:
    """锁定 LASSO 入选特征，在本层重估原始尺度 OLS RadScore。"""
    use = [f for f in features if f in rad_df.columns]
    if not use:
        return None
    y = np.asarray(y).astype(int)
    df = rad_df[use].apply(pd.to_numeric, errors="coerce").copy()
    df["y"] = y
    sub = df.dropna()
    if len(sub) < len(use) + 2 or int(sub["y"].nunique()) < 2:
        return None
    Xd = sm.add_constant(sub[use], has_constant="add")
    try:
        ols = sm.OLS(sub["y"], Xd).fit()
    except Exception:
        return None
    intercept = float(ols.params.get("const", 0.0))
    betas = ols.params.drop(labels=["const"], errors="ignore")
    Xfull = rad_df[list(betas.index)].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    return intercept + Xfull @ betas.to_numpy(dtype=float)


def refit_combined_prob(
    clin_df: pd.DataFrame,
    y: np.ndarray,
    clin_feat: Sequence[str],
    radscore,
) -> Optional[np.ndarray]:
    """锁定 Combined 特征（临床 + 一个或多个 RadScore），在本层重拟合 logit。"""
    n = len(y)
    parts: List[pd.Series] = []
    for f in clin_feat:
        if f in clin_df.columns:
            parts.append(
                pd.to_numeric(clin_df[f].reset_index(drop=True), errors="coerce").rename(f)
            )
    if isinstance(radscore, dict):
        for name, arr in radscore.items():
            rs = np.asarray(arr, dtype=float).ravel()
            if len(rs) != n:
                return None
            parts.append(pd.Series(rs, name=str(name)))
    else:
        rs = np.asarray(radscore, dtype=float).ravel()
        if len(rs) != n:
            return None
        parts.append(pd.Series(rs, name="RadScore"))
    df = pd.concat(parts, axis=1)
    df["y"] = np.asarray(y).astype(int)
    keep = [c for c in df.columns if c != "y" and df[c].nunique(dropna=True) >= 2]
    if not keep:
        return None
    sub = df[keep + ["y"]].dropna()
    if len(sub) < len(keep) + 2 or int(sub["y"].nunique()) < 2:
        return None
    X = sm.add_constant(sub[keep], has_constant="add")
    try:
        model = sm.GLM(sub["y"], X, family=sm.families.Binomial()).fit()
    except Exception:
        return None
    Xf = sm.add_constant(df[keep], has_constant="add")
    return _safe_predict_glm(model, Xf, n)
