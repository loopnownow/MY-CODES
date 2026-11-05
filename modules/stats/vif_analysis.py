"""
vif_analysis.py — Variance Inflation Factor (共线性)

完全共线（某列可由其余列精确线性表出，如 AFC_total = R_AFC + L_AFC、
SA_mean = mean(R_SA_ratio, L_SA_ratio)）时 VIF 不可估。此时先取线性独立基，
只对基内变量算 VIF；共线列记 inf（显示 ∞），不静默丢列、也不整列 NA 混过去。

运行: 由 modules.stats.clinical / modules.stats.radiomics 导入，不单独执行。
"""
from __future__ import annotations
from typing import List, Sequence, Tuple

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from statsmodels.stats.outliers_influence import variance_inflation_factor

from modules import config as cfg


def format_vif(value, decimals: int = 3) -> str:
    """VIF 单元格文本：NA（无值）/ ∞（完全共线）/ 三位小数。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "NA"
    if np.isinf(v):
        return "∞"
    if not np.isfinite(v):
        return "NA"
    return f"{v:.{decimals}f}"


def _independent_basis(mat: np.ndarray, tol: float = 1e-8) -> Tuple[List[int], List[int]]:
    """按原顺序取线性独立基；返回 (基下标, 可由前面列精确表出的下标)。"""
    n, p = mat.shape
    ones = np.ones(n)
    basis: List[int] = []
    dependent: List[int] = []
    for i in range(p):
        col = mat[:, i]
        scale = max(1.0, float(np.max(np.abs(col))) if n else 1.0)
        if not basis:
            if float(np.max(np.abs(col))) <= tol * scale:
                dependent.append(i)
            else:
                basis.append(i)
            continue
        A = np.column_stack([ones] + [mat[:, j] for j in basis])
        coef, *_ = np.linalg.lstsq(A, col, rcond=None)
        resid = col - A @ coef
        if float(np.max(np.abs(resid))) <= tol * scale:
            dependent.append(i)
        else:
            basis.append(i)
    return basis, dependent


def compute_vif(df: pd.DataFrame, features: Sequence[str],
                warn: float = None, high: float = None) -> pd.DataFrame:
    """对 features 计算 VIF（含截距列的设计矩阵，报告各预测变量 VIF）。

    完全共线的变量 VIF 记 inf（显示 ∞）；其余按线性独立基计算。
    仅用 VIF_THRESHOLD_WARN 打警告旗；high 参数保留兼容但不再区分高阈值档。
    """
    warn = cfg.VIF_THRESHOLD_WARN if warn is None else warn
    _ = high
    feats = [f for f in features if f in df.columns]
    if len(feats) < 2:
        return pd.DataFrame(columns=["Variable", "VIF", "Flag"])

    X = df[feats].apply(pd.to_numeric, errors="coerce")
    ok = X.notna().all(axis=1)
    X = X.loc[ok].reset_index(drop=True)
    if len(X) < len(feats) + 2:
        return pd.DataFrame(columns=["Variable", "VIF", "Flag"])

    Xs = X.copy()
    for c in Xs.columns:
        s = Xs[c].std()
        if s > 0:
            Xs[c] = (Xs[c] - Xs[c].mean()) / s
    arr = Xs.to_numpy(dtype=float)
    basis, dependent = _independent_basis(arr)

    values = {i: float("nan") for i in range(len(feats))}
    if len(basis) >= 2:
        Xmat = np.column_stack([np.ones(len(arr))] + [arr[:, j] for j in basis])
        for k, i in enumerate(basis):
            try:
                values[i] = float(variance_inflation_factor(Xmat, k + 1))
            except Exception:
                values[i] = float("nan")
    elif len(basis) == 1:
        values[basis[0]] = 1.0
    for i in dependent:
        values[i] = float("inf")

    rows = []
    for i, name in enumerate(feats):
        v = values[i]
        if np.isinf(v):
            flag = "collinear"
        elif not np.isfinite(v):
            flag = "NA"
        elif v >= warn:
            flag = f"warn (≥{warn:g})"
        else:
            flag = "OK"
        rows.append({"Variable": name, "VIF": v, "Flag": flag})

    out = pd.DataFrame(rows)
    out["_order"] = [
        float("inf") if np.isinf(v) else (-float("inf") if not np.isfinite(v) else float(v))
        for v in out["VIF"]
    ]
    out = out.sort_values("_order", ascending=False).drop(columns=["_order"]).reset_index(drop=True)
    return out


def plot_vif_bar(vif_df: pd.DataFrame, dataset_label: str = "combined",
                 warn: float = None, high: float = None) -> None:
    if vif_df is None or len(vif_df) == 0:
        return
    warn = cfg.VIF_THRESHOLD_WARN if warn is None else warn
    _ = high
    df = vif_df.copy()
    df = df[np.isfinite(pd.to_numeric(df["VIF"], errors="coerce"))]
    if len(df) == 0:
        return
    fig, ax = plt.subplots(figsize=(7, max(2.5, 0.35 * len(df) + 1)))
    y = np.arange(len(df))
    vals = pd.to_numeric(df["VIF"], errors="coerce").to_numpy()
    colors = ["#e67e22" if v >= warn else "#2980b9" for v in vals]
    ax.barh(y, vals, color=colors, edgecolor="none", height=0.7)
    ax.axvline(warn, color="#e67e22", ls="--", lw=1, label=f"VIF={warn:g}")
    ax.set_yticks(y)
    fs = cfg.FIG_FONT_SIZE
    ax.set_yticklabels(df["Variable"].tolist(), fontsize=fs)
    ax.invert_yaxis()
    ax.set_xlabel("VIF", fontsize=fs)
    ax.set_title(f"Variance Inflation Factor ({dataset_label})",
                 fontsize=fs, fontweight="normal")
    ax.legend(loc="lower right", fontsize=fs)
    cfg.apply_clean_theme(ax, grid=True, legend=True)
    ax.set_facecolor("white")
    fig.patch.set_facecolor("white")
    fig.tight_layout()
    wrote = cfg.save_plot(f"vif_{dataset_label}", fig, width=7, height=max(2.5, 0.35 * len(df) + 1))
    plt.close(fig)
    if wrote:
        print(f"  vif_{dataset_label}.png saved")
