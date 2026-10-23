"""
curves_calibration.py — 校准曲线 (Calibration Curve)
═══════════════════════════════════════════════════════════════════
功能：绘制模型的校准曲线，评估预测概率与实际观察率之间的一致性。
      理想校准下，曲线应接近对角线 (y=x)。

统计方法：
    1. 用预测值对结局做 logistic 回归获得校准后的预测概率（recalibrate=True）
    2. 按预测概率分位数分成 n_bins 组
    3. 每组内计算平均预测概率 (x轴) 和实际阳性比例 (y轴)
    4. Hosmer-Lemeshow 拟合优度（与曲线同一分位数箱；df = G−2）写入 HL_METRICS / HTML，不画进图
═══════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import statsmodels.api as sm
from scipy.stats import chi2 as chi2_dist

from modules import config as cfg

# ═════════════════════════════════════════════════════════════════
# §1  顶层可调参数
# ═════════════════════════════════════════════════════════════════
DEFAULT_N_BINS = 8   # 默认分箱数（按预测概率分位数分组）
HL_DENOM_MIN = 1e-12

# dataset_label → {chi2, df, p, n_groups, n}
HL_METRICS: Dict[str, dict] = {}


def _quantile_bins(pr: np.ndarray, n_bins: int) -> pd.Series:
    """与校准曲线共用的分位数切分；箱数不足时返回全 NA。"""
    quantiles = np.quantile(pr, np.linspace(0, 1, n_bins + 1))
    quantiles[0] -= 1e-9  # pd.cut 左开右闭，保证最小值落入第一箱
    edges = np.unique(quantiles)
    if len(edges) < 3:
        return pd.Series([pd.NA] * len(pr))
    return pd.cut(pr, bins=edges, include_lowest=True)


def hosmer_lemeshow_test(
    y: np.ndarray,
    pr: np.ndarray,
    bins: Optional[pd.Series] = None,
    n_bins: int = DEFAULT_N_BINS,
) -> dict:
    """分位数箱 Hosmer-Lemeshow。

    χ² = Σ (O_g − E_g)² / [n_g · p̄_g · (1 − p̄_g)]
    df = G − 2（截距 + 斜率）。期望为 0/1 的箱不进入 G。
    """
    y = np.asarray(y).astype(int)
    pr = np.asarray(pr, dtype=float)
    if bins is None:
        bins = _quantile_bins(pr, n_bins)
    df = pd.DataFrame({"pr": pr, "y": y, "bin": bins})
    df = df.dropna(subset=["bin"])
    out = {"n": int(len(df)), "n_groups": 0, "chi2": np.nan, "df": np.nan, "p": np.nan}
    if len(df) < 3:
        return out
    g = df.groupby("bin", observed=True).agg(
        n=("y", "size"),
        obs=("y", "sum"),
        exp=("pr", "sum"),
    )
    g = g[g["n"] > 0]
    if g.empty:
        return out
    pbar = g["exp"] / g["n"]
    denom = g["n"] * pbar * (1.0 - pbar)
    use = denom > HL_DENOM_MIN
    n_groups = int(use.sum())
    out["n_groups"] = n_groups
    out["n"] = int(g["n"].sum())
    if n_groups < 3:
        return out
    g_use = g.loc[use]
    stat = float((((g_use["obs"] - g_use["exp"]) ** 2) / denom[use]).sum())
    dof = int(n_groups - 2)
    if dof < 1 or not np.isfinite(stat):
        return out
    pval = float(chi2_dist.sf(stat, dof))
    out.update({"chi2": stat, "df": dof, "p": pval})
    return out


def _fmt_hl_p(p: float) -> str:
    if p is None or not np.isfinite(p):
        return "NA"
    if p < 0.001:
        return "<0.001"
    return f"{p:.3f}"


def plot_calibration(pred: np.ndarray, y: np.ndarray, n_bins: int = DEFAULT_N_BINS,
                      model_name: str = "Model", dataset_label: str = "all",
                      line_color: str = None,
                      recalibrate: bool = False) -> plt.Figure:
    """绘制校准曲线并保存。

    recalibrate=False（默认）：用传入概率直接分箱，评价原模型校准。
    recalibrate=True：在本层用 pred 再拟合一次 logistic（展示用 recalibration，
    不是原模型校准）。lock_threshold / refit 验证层必须为 False。
    """
    from modules.utils import dry_run as _dry
    kind = "recalibrated" if recalibrate else "raw"
    print(f"\n═══ CALIBRATION [{dataset_label}]  {kind} ═══")
    if _dry.exit_after_plan("curves_calibration", [
        f"model={model_name} n={len(np.asarray(y))} kind={kind}",
        "calibration plot (skipped)",
    ]):
        return None  # type: ignore[return-value]
    if line_color is None:
        line_color = getattr(cfg, "ROC_COLOR_MAP", {}).get("Combined", "#000000")
    pred = np.asarray(pred, dtype=float)
    y = np.asarray(y).astype(int)
    ok = np.isfinite(pred)

    # ── Step 1: 可选：用 pred 对 y 拟合 logistic 获得校准概率 ──
    if recalibrate:
        X = sm.add_constant(pred[ok])
        fit = sm.GLM(y[ok], X, family=sm.families.Binomial()).fit()
        pr = np.asarray(fit.predict(X), dtype=float)
    else:
        pr = pred[ok].astype(float)
    y_ok = y[ok]

    # ── Step 2: 按预测概率分位数分组 ──
    bins = _quantile_bins(pr, n_bins)

    # ── Step 3: 组内汇总 ──
    df = pd.DataFrame({"pr": pr, "y": y_ok, "bin": bins})
    cal = df.groupby("bin", observed=True).agg(mean_pred=("pr", "mean"),
                                                 frac_pos=("y", "mean")).dropna()

    hl = hosmer_lemeshow_test(y_ok, pr, bins=bins, n_bins=n_bins)
    HL_METRICS[str(dataset_label)] = dict(hl)
    if np.isfinite(hl.get("p", np.nan)):
        print(
            f"  Hosmer-Lemeshow: χ²={hl['chi2']:.2f}, df={int(hl['df'])}, "
            f"P={_fmt_hl_p(hl['p'])}  (G={hl['n_groups']}, n={hl['n']})"
        )
    else:
        print("  Hosmer-Lemeshow: not estimable")

    # ── 绘图（发表级；HL 数字只进 HTML / 日志，不画进图）──
    fig, ax = cfg.style_new_axes(figsize=(6.2, 5.8))
    ax.plot([0, 1], [0, 1], linestyle="--", color="#888888", linewidth=1.0, zorder=1)
    ax.plot(cal["mean_pred"], cal["frac_pos"], marker="o", markersize=7.5,
            markerfacecolor="white", markeredgewidth=1.6, markeredgecolor=line_color,
            color=line_color, linewidth=2.0, zorder=3)
    ax.set_xlim(-0.02, 1.02)
    ax.set_ylim(-0.02, 1.02)
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlabel("Predicted Probability")
    ax.set_ylabel("Observed Proportion")
    suffix = " (recalibrated)" if recalibrate else " (raw)"
    if dataset_label and dataset_label not in ("all", "demo"):
        ax.set_title(f"Calibration — {dataset_label}{suffix}")
    else:
        ax.set_title(f"Calibration — {model_name}{suffix}")
    cfg.apply_clean_theme(ax, grid=True, legend=False)
    fig.tight_layout()

    cal_fn = "calibration" if dataset_label == "all" else f"calibration_{dataset_label}"
    if cfg.save_plot(cal_fn, fig, width=6.2, height=5.8):
        print(f"  {cal_fn}.png saved")
    return fig
