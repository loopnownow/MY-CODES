"""
curves_dca.py — 决策曲线分析 (Decision Curve Analysis, DCA)
═══════════════════════════════════════════════════════════════════
功能：绘制多个模型的决策曲线，展示在不同阈值概率下的净收益
      (Net Benefit)。DCA 帮助评估临床决策策略的价值——相比"全治疗"
      和"全不治疗"，使用模型指导决策能带来多少净收益。

统计方法：
    净收益 NB = Sensitivity × prevalence − (1−Specificity) × (1−prevalence) × pt/(1−pt)
    通过 ROC 在阈值序列上提取灵敏度和特异度。
    参考线："Treat All"（全治）和 "Treat None"（全不治）。
═══════════════════════════════════════════════════════════════════
"""

from __future__ import annotations
from typing import Dict

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from sklearn.metrics import roc_curve

from modules import config as cfg

# ═════════════════════════════════════════════════════════════════
# §1  顶层可调参数
# ═════════════════════════════════════════════════════════════════
DCA_THRESHOLD_MIN = 0.01
DCA_THRESHOLD_MAX = 0.99
DCA_N_POINTS = 99
DEFAULT_MODEL_COLORS = list(getattr(cfg, "ROC_FALLBACK_COLORS", [
    "#B2182B", "#2166AC", "#4DBBD5", "#E64B35",
    "#332288", "#117733", "#44AA99", "#88CCEE",
]))


def _sens_spec_at_thresholds(y: np.ndarray, pred: np.ndarray, pts: np.ndarray):
    """
    在给定的一组阈值概率 pts 上，为每个阈值找到对应的（灵敏度，特异度）。
    以预测分数本身作为阈值刻度（不做额外校准），直接在该刻度上
    切分阳性/阴性预测。
    """
    sens = np.empty_like(pts)
    spec = np.empty_like(pts)
    y_pos = y == 1
    y_neg = y == 0
    n_pos = y_pos.sum()
    n_neg = y_neg.sum()
    for i, pt in enumerate(pts):
        pred_pos = pred >= pt
        tp = np.sum(pred_pos & y_pos)
        tn = np.sum(~pred_pos & y_neg)
        sens[i] = tp / n_pos if n_pos > 0 else np.nan
        spec[i] = tn / n_neg if n_neg > 0 else np.nan
    return sens, spec


def plot_dca(model_list: Dict[str, np.ndarray], y: np.ndarray,
             dataset_label: str = "all") -> plt.Figure:
    """
    多模型决策曲线分析主函数。

    假定 model_list 中的每个预测向量位于 [0, 1] 概率尺度。
    若传入 log-odds 或任意尺度分数，请先转换为概率后再调用。
    """
    from modules.utils import dry_run as _dry
    print(f"\n═══ DCA [{dataset_label}] ═══")
    if _dry.exit_after_plan("curves_dca", [
        f"models={list(model_list.keys())}",
        f"n={len(np.asarray(y))}",
        "DCA plot (skipped)",
    ]):
        return None  # type: ignore[return-value]
    assert len(model_list) >= 1

    y = np.asarray(y).astype(int)
    prev = float(np.mean(y == 1))
    pts = np.linspace(DCA_THRESHOLD_MIN, DCA_THRESHOLD_MAX, DCA_N_POINTS)

    rows = []
    for nm, pred in model_list.items():
        pred = np.asarray(pred, dtype=float)
        sens, spec = _sens_spec_at_thresholds(y, pred, pts)
        nb = sens * prev - (1 - spec) * (1 - prev) * pts / (1 - pts)
        rows.append(pd.DataFrame({"threshold": pts, "NB": nb, "Model": nm}))
    dca_df = pd.concat(rows, ignore_index=True)

    # ── 参考策略：Treat-All 和 Treat-None ──
    nb_all = prev - (1 - prev) * pts / (1 - pts)
    nb_none = np.zeros_like(pts)
    ref_df = pd.concat([
        pd.DataFrame({"threshold": pts, "NB": nb_all, "Model": "Treat All"}),
        pd.DataFrame({"threshold": pts, "NB": nb_none, "Model": "Treat None"}),
    ], ignore_index=True)
    dca_df = pd.concat([dca_df, ref_df], ignore_index=True)

    names = list(model_list.keys())
    # 与 ROC 统一：同一模型名同色（Combined 用独立紫）
    roc_map = dict(getattr(cfg, "ROC_COLOR_MAP", {}) or {})
    model_colors = {}
    for i, nm in enumerate(names):
        if nm in roc_map:
            model_colors[nm] = roc_map[nm]
        else:
            model_colors[nm] = DEFAULT_MODEL_COLORS[i % len(DEFAULT_MODEL_COLORS)]
    model_colors["Treat All"] = "#333333"
    model_colors["Treat None"] = "#B3B3B3"

    fig, ax = cfg.style_new_axes(figsize=(8, 5.8))
    for nm in names:
        sub = dca_df[dca_df["Model"] == nm]
        ax.plot(sub["threshold"], sub["NB"], color=model_colors[nm], linewidth=2.1,
                linestyle="solid", label=nm, solid_capstyle="round", zorder=3)
    sub = dca_df[dca_df["Model"] == "Treat All"]
    ax.plot(sub["threshold"], sub["NB"], color=model_colors["Treat All"], linewidth=1.4,
            linestyle="--", label="Treat All", zorder=2)
    sub = dca_df[dca_df["Model"] == "Treat None"]
    ax.plot(sub["threshold"], sub["NB"], color=model_colors["Treat None"], linewidth=1.4,
            linestyle=":", label="Treat None", zorder=2)

    ymax = dca_df["NB"].max()
    ax.set_xlim(0, 1)
    ax.set_ylim(-0.05, (ymax + 0.06) if np.isfinite(ymax) else 1.0)
    ax.set_xlabel("Threshold Probability")
    ax.set_ylabel("Net Benefit")
    ax.set_title(f"Decision Curve — {dataset_label}")
    fs = cfg.FIG_FONT_SIZE
    ax.legend(loc="upper right", fontsize=fs, framealpha=0.95, borderpad=0.6)
    cfg.apply_clean_theme(ax, grid=True, legend=True)
    fig.tight_layout()

    dca_fn = "dca" if dataset_label == "all" else f"dca_{dataset_label}"
    if cfg.save_plot(dca_fn, fig, width=8, height=5.8, dpi=300):
        print(f"  {dca_fn}.png saved")
    return fig
