"""
vif_analysis.py — Variance Inflation Factor (共线性)
"""
from __future__ import annotations
from typing import Sequence

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from statsmodels.stats.outliers_influence import variance_inflation_factor

from modules import config as cfg


def compute_vif(df: pd.DataFrame, features: Sequence[str],
                warn: float = None, high: float = None) -> pd.DataFrame:
    """对 features 计算 VIF（含截距列的设计矩阵，报告各预测变量 VIF）。

    仅用 VIF_THRESHOLD_WARN 打警告旗；high 参数保留兼容但不再区分高阈值档。
    """
    warn = cfg.VIF_THRESHOLD_WARN if warn is None else warn
    _ = high  # retired high cutoff
    feats = [f for f in features if f in df.columns]
    if len(feats) < 2:
        return pd.DataFrame(columns=["Variable", "VIF", "Flag"])

    X = df[feats].apply(pd.to_numeric, errors="coerce")
    ok = X.notna().all(axis=1)
    X = X.loc[ok].reset_index(drop=True)
    if len(X) < len(feats) + 2:
        return pd.DataFrame(columns=["Variable", "VIF", "Flag"])

    # 常数列 + 标准化减轻尺度影响（VIF 对线性变换不敏感，但数值更稳）
    Xs = X.copy()
    for c in Xs.columns:
        s = Xs[c].std()
        if s > 0:
            Xs[c] = (Xs[c] - Xs[c].mean()) / s
    Xmat = np.column_stack([np.ones(len(Xs)), Xs.to_numpy(dtype=float)])

    rows = []
    for i, name in enumerate(feats):
        try:
            v = float(variance_inflation_factor(Xmat, i + 1))  # skip intercept
        except Exception:
            v = float("nan")
        if not np.isfinite(v):
            flag = "NA"
        elif v >= warn:
            flag = f"warn (≥{warn:g})"
        else:
            flag = "OK"
        rows.append({"Variable": name, "VIF": v, "Flag": flag})

    out = pd.DataFrame(rows).sort_values("VIF", ascending=False).reset_index(drop=True)
    return out


def plot_vif_bar(vif_df: pd.DataFrame, dataset_label: str = "combined",
                 warn: float = None, high: float = None) -> None:
    if vif_df is None or len(vif_df) == 0:
        return
    warn = cfg.VIF_THRESHOLD_WARN if warn is None else warn
    _ = high  # retired high cutoff
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
