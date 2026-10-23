"""
curves_survival.py — Kaplan–Meier + Cox（无 lifelines；Cox 用 statsmodels PHReg）
═══════════════════════════════════════════════════════════════════
时间：config.SURVIVAL_TIME_COL
事件：二分类结局 label（1=事件，0=删失）

输出：
  - km_overall.png          全队列
  - km_by_risk_*.png        按风险中位数分层 + log-rank + Cox HR
  - KM 汇总 → SURVIVAL_METRICS；Cox → COX_METRICS
"""

from __future__ import annotations
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy import stats
from statsmodels.duration.hazard_regression import PHReg

from modules import config as cfg

SURVIVAL_METRICS: Dict[str, pd.DataFrame] = {}
COX_METRICS: Dict[str, pd.DataFrame] = {}


def _km_steps(time: np.ndarray, event: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    返回阶梯坐标 (t_plot, s_plot) 与风险集摘要。
    标准乘积极限估计；并列时间按同一 t 合并。
    """
    t = np.asarray(time, dtype=float)
    e = np.asarray(event, dtype=int)
    ok = np.isfinite(t) & (t >= 0) & np.isfinite(e)
    t, e = t[ok], e[ok].astype(int)
    if len(t) == 0:
        return np.array([0.0]), np.array([1.0]), np.array([]), np.array([])

    order = np.argsort(t, kind="mergesort")
    t, e = t[order], e[order]
    uniq = np.unique(t)
    n_risk = []
    n_event = []
    s = 1.0
    times_s = [0.0]
    surv_s = [1.0]
    n = len(t)
    for ut in uniq:
        at = t == ut
        d = int(e[at].sum())
        # 进入该时刻的风险人数
        r = int((t >= ut).sum())
        n_risk.append(r)
        n_event.append(d)
        if r > 0 and d > 0:
            s *= (1.0 - d / r)
        times_s.append(float(ut))
        surv_s.append(float(s))
    return (
        np.asarray(times_s, dtype=float),
        np.asarray(surv_s, dtype=float),
        np.asarray(uniq, dtype=float),
        np.asarray(n_risk, dtype=int),
    )


def _median_survival(t_steps: np.ndarray, s_steps: np.ndarray) -> float:
    """S(t) 首次 ≤ 0.5 的时间；未达到则 nan。"""
    below = np.where(s_steps <= 0.5)[0]
    if len(below) == 0:
        return float("nan")
    return float(t_steps[below[0]])


def logrank_two_groups(
    time: np.ndarray, event: np.ndarray, group: np.ndarray
) -> Tuple[float, float]:
    """两样本 log-rank（Mantel–Haenszel），返回 (chi2, p)。"""
    t = np.asarray(time, dtype=float)
    e = np.asarray(event, dtype=int)
    g = np.asarray(group)
    ok = np.isfinite(t) & (t >= 0) & np.isfinite(e) & pd.notna(g)
    t, e, g = t[ok], e[ok].astype(int), g[ok]
    levels = pd.unique(g)
    if len(levels) != 2 or len(t) < 4:
        return float("nan"), float("nan")
    g0, g1 = levels[0], levels[1]
    # 所有事件时间（有事件发生）
    event_times = np.unique(t[e == 1])
    O1 = 0.0
    E1 = 0.0
    V = 0.0
    for ut in event_times:
        mask_t = t >= ut
        n0 = int(((g == g0) & mask_t).sum())
        n1 = int(((g == g1) & mask_t).sum())
        n = n0 + n1
        if n == 0:
            continue
        d = int(((t == ut) & (e == 1)).sum())
        d1 = int(((t == ut) & (e == 1) & (g == g1)).sum())
        if d == 0 or n <= 1:
            continue
        e1 = d * (n1 / n)
        v = (n0 * n1 * d * (n - d)) / (n * n * (n - 1)) if n > 1 else 0.0
        O1 += d1
        E1 += e1
        V += v
    if V <= 1e-12:
        return float("nan"), float("nan")
    chi2 = (O1 - E1) ** 2 / V
    p = float(1.0 - stats.chi2.cdf(chi2, df=1))
    return float(chi2), p


def _fmt_cox_p(p: float) -> str:
    if not np.isfinite(p):
        return "NA"
    if p < 0.001:
        return "<0.001"
    return f"{p:.3f}"


def _schoenfeld_p(res, time: np.ndarray) -> float:
    """Grambsch–Therneau-style PH check for one covariate.

    Scaled Schoenfeld residual vs rank(time) among event times (Pearson).
    Residuals are defined at event times; censored rows are dropped.
    """
    try:
        sr = np.asarray(res.schoenfeld_residuals, dtype=float).ravel()
    except Exception:
        return float("nan")
    t = np.asarray(time, dtype=float).ravel()
    if sr.size != t.size:
        return float("nan")
    ok = np.isfinite(sr) & np.isfinite(t)
    if int(ok.sum()) < 8:
        return float("nan")
    sr_e, t_e = sr[ok], t[ok]
    try:
        cov = np.asarray(res.cov_params(), dtype=float)
        v = float(cov.ravel()[0]) if cov.size else np.nan
        scaled = sr_e / v if np.isfinite(v) and v > 0 else sr_e
    except Exception:
        scaled = sr_e
    g = stats.rankdata(t_e)
    try:
        _r, p = stats.pearsonr(scaled, g)
    except Exception:
        return float("nan")
    return float(p) if np.isfinite(p) else float("nan")


def fit_cox_ph(
    time: np.ndarray,
    event: np.ndarray,
    x: np.ndarray,
    predictor: str,
    contrast: str,
) -> Dict[str, object]:
    """Univariable Cox PH. Returns HR, 95% CI, Wald p, Schoenfeld p."""
    t = np.asarray(time, dtype=float)
    e = np.asarray(event, dtype=int)
    z = np.asarray(x, dtype=float)
    ok = np.isfinite(t) & (t >= 0) & np.isfinite(e) & np.isfinite(z)
    t, e, z = t[ok], e[ok].astype(int), z[ok]
    n = int(len(t))
    n_ev = int((e == 1).sum())
    row: Dict[str, object] = {
        "Predictor": predictor,
        "Contrast": contrast,
        "HR": np.nan,
        "CI_low": np.nan,
        "CI_high": np.nan,
        "P": np.nan,
        "N": n,
        "Events": n_ev,
        "PH_p": np.nan,
    }
    if n < 10 or n_ev < 5 or np.unique(np.round(z, 12)).size < 2:
        return row
    if np.nanmax(t) - np.nanmin(t) <= 0:
        print(f"  ⚠ Cox skipped ({predictor}): time has no spread")
        return row
    try:
        res = PHReg(t, z.reshape(-1, 1), status=e, ties="breslow").fit(disp=0)
        beta = float(np.asarray(res.params).ravel()[0])
        ci = np.asarray(res.conf_int(), dtype=float)
        lo, hi = float(ci[0, 0]), float(ci[0, 1])
        p = float(np.asarray(res.pvalues).ravel()[0])
        row["HR"] = float(np.exp(beta))
        row["CI_low"] = float(np.exp(lo))
        row["CI_high"] = float(np.exp(hi))
        row["P"] = p
        row["PH_p"] = _schoenfeld_p(res, t)
    except Exception as exc:
        print(f"  ⚠ Cox failed ({predictor}): {exc}")
    return row


def _high_vs_low_dummy(group: np.ndarray, levels: List[str]) -> Tuple[np.ndarray, str]:
    """High (or last level) = 1. Contrast string for the Cox row."""
    high = [lv for lv in levels if "high" in str(lv).lower()]
    if len(high) == 1:
        pos, neg = high[0], [x for x in levels if x != high[0]][0]
    else:
        pos, neg = levels[-1], levels[0]
    dummy = (np.asarray(group) == pos).astype(float)
    return dummy, f"{pos} vs {neg}"


def _store_cox_rows(dataset_label: str, rows: List[dict]) -> None:
    if not rows:
        return
    key = str(dataset_label or "all")
    add = pd.DataFrame(rows)
    prev = COX_METRICS.get(key)
    COX_METRICS[key] = add if prev is None or len(prev) == 0 else pd.concat([prev, add], ignore_index=True)


def _draw_km(
    ax,
    time: np.ndarray,
    event: np.ndarray,
    label: str,
    color: str,
) -> Dict[str, float]:
    t_s, s_s, _, _ = _km_steps(time, event)
    n = int(np.isfinite(time).sum())
    n_ev = int((np.asarray(event)[np.isfinite(time)] == 1).sum()) if n else 0
    med = _median_survival(t_s, s_s)
    med_txt = f"{med:.1f}" if np.isfinite(med) else "NR"
    ax.step(
        t_s, s_s, where="post", color=color, linewidth=2.0,
        label=f"{label} (n={n}, events={n_ev}, median={med_txt})",
    )
    return {"n": n, "events": n_ev, "median_months": med}


def plot_km_overall(
    time: np.ndarray,
    event: np.ndarray,
    dataset_label: str = "all",
    time_unit: str = "months",
    title: Optional[str] = None,
) -> Optional[plt.Figure]:
    """全队列 Kaplan–Meier（复发/无事件生存）。"""
    print(f"\n═══ SURVIVAL KM overall [{dataset_label}] ═══")
    time = np.asarray(time, dtype=float)
    event = np.asarray(event, dtype=int)
    ok = np.isfinite(time) & (time >= 0) & np.isfinite(event)
    time, event = time[ok], event[ok]
    if len(time) < 5:
        print("  ⚠ too few cases with time — skip KM")
        return None

    fig, ax = cfg.style_new_axes(figsize=(7.2, 5.6))
    color = getattr(cfg, "ROC_COLOR_MAP", {}).get("Combined", "#B2182B")
    stats_row = _draw_km(ax, time, event, "All patients", color)
    ax.set_ylim(-0.02, 1.05)
    ax.set_xlim(left=0)
    ax.set_xlabel(f"Time ({time_unit})")
    ax.set_ylabel("Recurrence-free probability")
    ttl = title or f"Kaplan–Meier — Recurrence-free ({dataset_label})"
    ax.set_title(ttl)
    ax.legend(loc="best", frameon=True)
    cfg.apply_clean_theme(ax, grid=True, legend=True)
    fig.tight_layout()

    fn = "km_overall" if dataset_label == "all" else f"km_overall_{dataset_label}"
    if cfg.save_plot(fn, fig, width=7.2, height=5.6, dpi=200):
        print(f"  → PNG/{fn}.png  n={stats_row['n']} events={stats_row['events']} "
              f"median={stats_row['median_months']}")

    SURVIVAL_METRICS[fn] = pd.DataFrame([{
        "Stratum": "All",
        "N": stats_row["n"],
        "Events": stats_row["events"],
        "Median_months": stats_row["median_months"],
        "Logrank_chi2": np.nan,
        "Logrank_p": np.nan,
    }])
    return fig


def plot_km_by_group(
    time: np.ndarray,
    event: np.ndarray,
    group: Sequence,
    dataset_label: str = "all",
    group_name: str = "Group",
    time_unit: str = "months",
    colors: Optional[Dict[str, str]] = None,
    title: Optional[str] = None,
    do_cox: bool = False,
) -> Optional[plt.Figure]:
    """按分组绘制 KM + log-rank（两组）或仅曲线（多组）。"""
    print(f"\n═══ SURVIVAL KM by {group_name} [{dataset_label}] ═══")
    time = np.asarray(time, dtype=float)
    event = np.asarray(event, dtype=int)
    group = pd.Series(group).astype(str).to_numpy()
    ok = np.isfinite(time) & (time >= 0) & np.isfinite(event) & (group != "nan")
    time, event, group = time[ok], event[ok], group[ok]
    levels = [g for g in pd.unique(group) if str(g) not in ("", "nan", "None")]
    if len(time) < 5 or len(levels) < 1:
        print("  ⚠ insufficient data — skip stratified KM")
        return None

    palette = list(getattr(cfg, "ROC_FALLBACK_COLORS", [])) or [
        "#B2182B", "#2166AC", "#D55E00", "#117733", "#332288",
    ]
    if colors is None:
        colors = {}
    for i, lv in enumerate(levels):
        if lv not in colors:
            colors[lv] = palette[i % len(palette)]

    fig, ax = cfg.style_new_axes(figsize=(7.4, 5.8))
    rows = []
    for lv in levels:
        m = group == lv
        row = _draw_km(ax, time[m], event[m], str(lv), colors[lv])
        rows.append({
            "Stratum": str(lv),
            "N": row["n"],
            "Events": row["events"],
            "Median_months": row["median_months"],
        })
        print(f"  {lv}: n={row['n']} events={row['events']} median={row['median_months']}")

    chi2, p = float("nan"), float("nan")
    cox_row = None
    if len(levels) == 2:
        chi2, p = logrank_two_groups(time, event, group)
        p_txt = _fmt_cox_p(p)
        note = f"Log-rank p = {p_txt}"
        if do_cox:
            dummy, contrast = _high_vs_low_dummy(group, levels)
            cox_row = fit_cox_ph(time, event, dummy, group_name, contrast)
            hr = cox_row.get("HR")
            if hr is not None and np.isfinite(float(hr)):
                note += (
                    f"\nCox HR = {float(hr):.2f} "
                    f"({float(cox_row['CI_low']):.2f}–{float(cox_row['CI_high']):.2f}), "
                    f"p = {_fmt_cox_p(float(cox_row['P']))}"
                )
                print(
                    f"  Cox HR={float(hr):.3f} "
                    f"({float(cox_row['CI_low']):.3f}–{float(cox_row['CI_high']):.3f})  "
                    f"p={_fmt_cox_p(float(cox_row['P']))}  PH p={_fmt_cox_p(float(cox_row['PH_p']))}"
                )
        ax.text(
            0.98, 0.05, note,
            transform=ax.transAxes, ha="right", va="bottom", fontsize=10,
            color="#222222",
        )
        print(f"  Log-rank χ²={chi2:.3f}, p={p_txt}")

    ax.set_ylim(-0.02, 1.05)
    ax.set_xlim(left=0)
    ax.set_xlabel(f"Time ({time_unit})")
    ax.set_ylabel("Recurrence-free probability")
    ttl = title or f"Kaplan–Meier by {group_name} ({dataset_label})"
    ax.set_title(ttl)
    ax.legend(loc="best", frameon=True)
    cfg.apply_clean_theme(ax, grid=True, legend=True)
    fig.tight_layout()

    safe = str(group_name).strip().lower().replace(" ", "_")
    fn = f"km_by_{safe}" if dataset_label == "all" else f"km_by_{safe}_{dataset_label}"
    if cfg.save_plot(fn, fig, width=7.4, height=5.8, dpi=200):
        print(f"  → PNG/{fn}.png")

    df = pd.DataFrame(rows)
    df["Logrank_chi2"] = chi2
    df["Logrank_p"] = p
    if cox_row is not None:
        df["Cox_HR"] = cox_row["HR"]
        df["Cox_CI_low"] = cox_row["CI_low"]
        df["Cox_CI_high"] = cox_row["CI_high"]
        df["Cox_p"] = cox_row["P"]
        _store_cox_rows(dataset_label, [cox_row])
    SURVIVAL_METRICS[fn] = df
    return fig


def plot_survival_bundle(
    time: np.ndarray,
    event: np.ndarray,
    risk_score: Optional[np.ndarray] = None,
    risk_name: str = "Combined",
    dataset_label: str = "all",
    time_unit: str = "months",
    median_cut: Optional[float] = None,
    do_cox: bool = False,
) -> Dict[str, Optional[plt.Figure]]:
    """
    一站式：全队列 KM +（可选）按 risk_score 中位数分层。
    median_cut：若提供则用该阈值（建议用 train 中位数），否则用本队列中位数。
    do_cox：控制台 DO_COX；开则 High vs Low 与每 1 SD 单因素 Cox。
    """
    from modules.utils import dry_run as _dry
    if _dry.exit_after_plan("curves_survival", [
        f"dataset={dataset_label} n={len(np.asarray(time))} do_cox={do_cox}",
        "KM / Cox bundle (skipped)",
    ]):
        return {}
    COX_METRICS.pop(str(dataset_label or "all"), None)
    out: Dict[str, Optional[plt.Figure]] = {}
    out["overall"] = plot_km_overall(
        time, event, dataset_label=dataset_label, time_unit=time_unit,
    )
    if risk_score is None:
        return out
    score = np.asarray(risk_score, dtype=float)
    t = np.asarray(time, dtype=float)
    e = np.asarray(event, dtype=int)
    ok = np.isfinite(score) & np.isfinite(t) & (t >= 0) & np.isfinite(e)
    if ok.sum() < 10:
        print("  ⚠ risk score incomplete — skip risk-stratified KM / Cox")
        return out
    cut = float(np.nanmedian(score[ok])) if median_cut is None else float(median_cut)
    strata = np.where(score >= cut, f"High {risk_name}", f"Low {risk_name}")
    print(
        f"  Risk cut ({risk_name}) = {cut:.4f}  (High ≥ cut; "
        "median split is display-only, not a model threshold)"
    )
    out["by_risk"] = plot_km_by_group(
        t, e, strata,
        dataset_label=dataset_label,
        group_name=f"{risk_name}_risk",
        time_unit=time_unit,
        colors={
            f"Low {risk_name}": getattr(cfg, "ROC_COLOR_MAP", {}).get("Clinical", "#2166AC"),
            f"High {risk_name}": getattr(cfg, "ROC_COLOR_MAP", {}).get("RadScore", "#B2182B"),
        },
        title=f"Kaplan–Meier by {risk_name} median split (display only; {dataset_label})",
        do_cox=do_cox,
    )
    if do_cox:
        mu = float(np.nanmean(score[ok]))
        sd = float(np.nanstd(score[ok], ddof=1))
        if np.isfinite(sd) and sd > 0:
            z = (score - mu) / sd
            cont = fit_cox_ph(
                t, e, z, risk_name, "per 1 SD (continuous, centered)",
            )
            _store_cox_rows(dataset_label, [cont])
            if np.isfinite(float(cont["HR"])):
                print(
                    f"  Cox {risk_name} per 1 SD  HR={float(cont['HR']):.3f} "
                    f"({float(cont['CI_low']):.3f}–{float(cont['CI_high']):.3f})  "
                    f"p={_fmt_cox_p(float(cont['P']))}"
                )
    return out
