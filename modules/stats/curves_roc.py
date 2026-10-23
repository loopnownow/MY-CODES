"""
modules.stats.curves_roc — ROC 曲线比较
═══════════════════════════════════════════════════════════════════
功能：在同一张图上绘制多个模型的 ROC 曲线，使用 Bootstrap 计算 AUC
      的 95% CI。通过预测值 → logistic 校准 → 概率来生成平滑 ROC。
      VAL_MODE=lock_threshold 时验证层套 Train 校准与 Youden，不重算切点。
指标表写入 ROC_METRICS，供 modules.stats.report 读取。
═══════════════════════════════════════════════════════════════════
"""

from __future__ import annotations
from typing import Dict, Optional
import re

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.stats import norm
from sklearn.metrics import roc_curve, roc_auc_score
import statsmodels.api as sm

from modules import config as cfg
from modules.stats.val_mode import resolve_val_mode

# ═════════════════════════════════════════════════════════════════
# §1  顶层可调参数
# ═════════════════════════════════════════════════════════════════
N_BOOT_AUC = 1000                # Bootstrap AUC CI 默认；运行时优先 cfg.BOOTSTRAP_N

# 每次 plot_roc() 计算出的指标表存放于此，供 report 直接读取。
ROC_METRICS: Dict[str, pd.DataFrame] = {}

# Train 层各模型的 logistic 校准 + Youden，供 lock_threshold 套用。
# {dataset_label: {model: {intercept, slope, threshold}}}
ROC_LOCK: Dict[str, Dict[str, Dict[str, float]]] = {}

# 未知模型名的稳定配色缓存（同一次进程内同名同色）
_FALLBACK_COLOR_CACHE: Dict[str, str] = {}


def _resolve_color_map(model_names, color_map: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """
    统一 ROC 颜色：优先 config.ROC_COLOR_MAP，再调用方 color_map，
    未知名称从 C_SUB 顺序分配并缓存。
    """
    base = dict(getattr(cfg, "ROC_COLOR_MAP", {}) or {})
    if color_map:
        base.update(color_map)
    palette = list(getattr(cfg, "ROC_FALLBACK_COLORS", [])) or [
        "#332288", "#117733", "#44AA99", "#88CCEE", "#DDCC77",
        "#CC6677", "#AA4499", "#882255", "#6699CC",
    ]
    out = {}
    for nm in model_names:
        if nm in base:
            out[nm] = base[nm]
        elif nm in _FALLBACK_COLOR_CACHE:
            out[nm] = _FALLBACK_COLOR_CACHE[nm]
        else:
            # 避开已用色
            used = set(out.values()) | set(base.values()) | set(_FALLBACK_COLOR_CACHE.values())
            pick = None
            for c in palette:
                if c not in used:
                    pick = c
                    break
            if pick is None:
                pick = palette[len(_FALLBACK_COLOR_CACHE) % len(palette)]
            _FALLBACK_COLOR_CACHE[nm] = pick
            out[nm] = pick
    return out


def _resolve_n_boot(n_boot: Optional[int] = None) -> int:
    if n_boot is not None:
        return int(n_boot)
    return int(getattr(cfg, "BOOTSTRAP_N", None) or N_BOOT_AUC or 1000)


def _boot_auc_ci(y: np.ndarray, prob: np.ndarray, n_boot: Optional[int] = None,
                  seed: Optional[int] = None) -> np.ndarray:
    """Bootstrap AUC 95% 置信区间。"""
    n_boot = _resolve_n_boot(n_boot)
    rng = np.random.RandomState(seed)
    n = len(y)
    aucs = []
    for _ in range(n_boot):
        idx = rng.randint(0, n, n)
        if len(np.unique(y[idx])) < 2:
            continue
        aucs.append(roc_auc_score(y[idx], prob[idx]))
    if len(aucs) == 0:
        return np.array([np.nan, np.nan])
    return np.percentile(aucs, [2.5, 97.5])


def _is_val_dataset(dataset_label: str) -> bool:
    pl = str(dataset_label or "")
    if pl.startswith("sg_"):
        return True
    return pl == "test" or pl.startswith("test") or pl.startswith("ext")


def _sigmoid_lp(lp: np.ndarray) -> np.ndarray:
    lp = np.asarray(lp, dtype=float)
    out = np.empty_like(lp, dtype=float)
    pos = np.isfinite(lp) & (lp >= 0)
    neg = np.isfinite(lp) & ~pos
    out[pos] = 1.0 / (1.0 + np.exp(-lp[pos]))
    ez = np.exp(lp[neg])
    out[neg] = ez / (1.0 + ez)
    out[~np.isfinite(lp)] = np.nan
    return out


def _apply_logit_cal(pred: np.ndarray, intercept: float, slope: float) -> np.ndarray:
    return _sigmoid_lp(float(intercept) + float(slope) * np.asarray(pred, dtype=float))


def _compute_midrank(x: np.ndarray) -> np.ndarray:
    """1-based midranks for DeLong (Sun & Xu 2014)."""
    x = np.asarray(x, dtype=float)
    order = np.argsort(x)
    z = x[order]
    n = len(x)
    t = np.empty(n, dtype=float)
    i = 0
    while i < n:
        j = i
        while j < n and z[j] == z[i]:
            j += 1
        t[i:j] = 0.5 * (i + j - 1) + 1.0
        i = j
    out = np.empty(n, dtype=float)
    out[order] = t
    return out


def delong_roc_test(y: np.ndarray, p1: np.ndarray, p2: np.ndarray) -> float:
    """
    Two-sided paired DeLong test of AUC(p1) vs AUC(p2) on the same cases.
    Returns p-value; nan if the comparison is not identified.
    """
    y = np.asarray(y).astype(int)
    a = np.asarray(p1, dtype=float)
    b = np.asarray(p2, dtype=float)
    ok = np.isfinite(a) & np.isfinite(b) & np.isfinite(y)
    y, a, b = y[ok], a[ok], b[ok]
    if y.size < 4:
        return float("nan")
    n_pos = int((y == 1).sum())
    n_neg = int((y == 0).sum())
    if n_pos < 2 or n_neg < 2:
        return float("nan")
    if np.allclose(a, b, equal_nan=False):
        return 1.0
    order = np.argsort(-y)  # positives first
    preds = np.vstack((a, b))[:, order]
    m, n = n_pos, n_neg
    k = preds.shape[0]
    tx = np.empty((k, m), dtype=float)
    ty = np.empty((k, n), dtype=float)
    tz = np.empty((k, m + n), dtype=float)
    for r in range(k):
        tx[r, :] = _compute_midrank(preds[r, :m])
        ty[r, :] = _compute_midrank(preds[r, m:])
        tz[r, :] = _compute_midrank(preds[r, :])
    aucs = tz[:, :m].sum(axis=1) / m / n - float(m + 1.0) / 2.0 / n
    v01 = (tz[:, :m] - tx) / n
    v10 = 1.0 - (tz[:, m:] - ty) / m
    sx = np.cov(v01, ddof=1)
    sy = np.cov(v10, ddof=1)
    sigma = sx / m + sy / n
    if np.ndim(sigma) == 0:
        var = float(sigma)
    else:
        var = float(sigma[0, 0] + sigma[1, 1] - 2.0 * sigma[0, 1])
    diff = float(aucs[0] - aucs[1])
    if not np.isfinite(var) or var <= 0:
        return 1.0 if abs(diff) < 1e-12 else 0.0
    z = abs(diff) / np.sqrt(var)
    if not np.isfinite(z):
        return float("nan")
    return float(2.0 * norm.sf(z))


def _pairwise_delong(y: np.ndarray, probs: Dict[str, np.ndarray]) -> pd.DataFrame:
    """Square matrix of DeLong p-values; diagonal is nan."""
    names = list(probs.keys())
    mat = pd.DataFrame(np.nan, index=names, columns=names, dtype=float)
    for i, a in enumerate(names):
        mat.loc[a, a] = np.nan
        for b in names[i + 1:]:
            p = delong_roc_test(y, probs[a], probs[b])
            mat.loc[a, b] = p
            mat.loc[b, a] = p
    return mat


def _cls_metrics(y: np.ndarray, pred_bin: np.ndarray) -> Dict[str, float]:
    lab = np.asarray(y).astype(int)
    pred_bin = np.asarray(pred_bin).astype(int)
    TP = int(((pred_bin == 1) & (lab == 1)).sum())
    TN = int(((pred_bin == 0) & (lab == 0)).sum())
    FP = int(((pred_bin == 1) & (lab == 0)).sum())
    FN = int(((pred_bin == 0) & (lab == 1)).sum())
    n = len(lab)
    return {
        "ACC": (TP + TN) / n if n else np.nan,
        "SEN": TP / (TP + FN) if (TP + FN) > 0 else np.nan,
        "SPE": TN / (TN + FP) if (TN + FP) > 0 else np.nan,
        "NPV": TN / (TN + FN) if (TN + FN) > 0 else np.nan,
        "PPV": TP / (TP + FP) if (TP + FP) > 0 else np.nan,
    }


def _roc_plot_exclude_tokens() -> set:
    """ROC 图不画的模型名。未配置时默认 Clinical；显式空列表=都画。表仍保留。"""
    raw = getattr(cfg, "ROC_PLOT_EXCLUDE", None)
    if raw is None:
        raw = ["Clinical"]
    return {
        str(x).strip().lower().replace(" ", "").replace("_", "")
        for x in raw
        if str(x).strip()
    }


def _roc_plot_skipped(name: str) -> bool:
    """名字在 ROC_PLOT_EXCLUDE 里才不画。空列表不再把 Clinical 写死跳过。"""
    tok = str(name).strip().lower().replace(" ", "").replace("_", "")
    return tok in _roc_plot_exclude_tokens()


def roc_time_token(value) -> str:
    """时间点文件名。整数月用 6，小数保留，其余只留字母数字。"""
    try:
        n = float(value)
    except (TypeError, ValueError):
        n = None
    if n is not None and np.isfinite(n):
        return str(int(n)) if float(n).is_integer() else f"{n:g}"
    text = re.sub(r"[^A-Za-z0-9]+", "_", str(value or "")).strip("_")
    return text or "t"


def roc_score_sign(auc: float) -> float:
    """原始 AUC 低于 0.5 时反过来，曲线留在对角线上方。"""
    if np.isfinite(auc) and float(auc) < 0.5:
        return -1.0
    return 1.0


def orient_auc_above_chance(auc, ci_low, ci_high):
    """与 roc_score_sign 同一方向。置信区间跟着变成 1 减原始区间。"""
    auc = float(auc)
    lo = float(ci_low)
    hi = float(ci_high)
    if roc_score_sign(auc) > 0:
        return auc, lo, hi
    lo2 = 1.0 - hi if np.isfinite(hi) else float("nan")
    hi2 = 1.0 - lo if np.isfinite(lo) else float("nan")
    if np.isfinite(lo2) and np.isfinite(hi2) and lo2 > hi2:
        lo2, hi2 = hi2, lo2
    return 1.0 - auc, lo2, hi2


def roc_split_title(kind: str, dataset_label: str, time_label) -> str:
    split = str(dataset_label or "all")
    try:
        n = float(time_label)
    except (TypeError, ValueError):
        n = None
    if n is not None and np.isfinite(n):
        when = f"{int(n)} months" if float(n).is_integer() else f"{n:g} months"
    else:
        when = str(time_label or "").strip() or "time"
    return f"{kind} — {split} ({when})"


def draw_roc_curves(
    curves,
    title: str,
    fname: str,
    color_map: Optional[Dict[str, str]] = None,
) -> Optional[plt.Figure]:
    """与二分类 ROC 相同的坐标、对角线和图例。curves: (图例, 模型名, fpr, tpr)。"""
    if not curves:
        return None
    color_map = _resolve_color_map([nm for _disp, nm, _fpr, _tpr in curves], color_map)
    fig, ax = cfg.style_new_axes(figsize=(7, 6.5))
    ax.plot([0, 1], [0, 1], linestyle="--", color="#888888", linewidth=1.0,
            zorder=1, label="_nolegend_")
    for display_name, nm, fpr, tpr in curves:
        color = color_map.get(nm, "#555555")
        ax.plot(fpr, tpr, linewidth=2.2, color=color, label=display_name,
                solid_capstyle="round", zorder=3)
    ax.set_xlim(-0.02, 1.02)
    ax.set_ylim(-0.02, 1.02)
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlabel("1 − Specificity")
    ax.set_ylabel("Sensitivity")
    ax.set_title(title)
    fs = cfg.FIG_FONT_SIZE
    ax.legend(loc="lower right", fontsize=fs, framealpha=0.95, borderpad=0.6)
    cfg.apply_clean_theme(ax, grid=True, legend=True)
    fig.tight_layout()
    if cfg.save_plot(fname, fig, width=7, height=6.5):
        print(f"  {fname}.png saved")
    return fig


def plot_roc(model_list: Dict[str, np.ndarray], y: np.ndarray, color_map: Optional[Dict[str, str]] = None, dataset_label: str = "all", seed: Optional[int] = None, val_mode: Optional[str] = None, lock_from: Optional[Dict[str, Dict[str, float]]] = None) -> Optional[plt.Figure]:
    """
    多模型 ROC 比较主函数。

    参数
    ----
    model_list : {模型名: 连续预测值向量}
    y          : 二分类结局 (0/1)
    val_mode   : apply_formula / lock_threshold / refit；空则读 config.VAL_MODE
    lock_from  : Train 层 ROC_LOCK 条目；lock_threshold 时用于套校准与 Youden
    """
    from modules.utils import dry_run as _dry
    if seed is None:
        seed = int(getattr(cfg, "SEED", 66666) or 66666)
    mode = resolve_val_mode(val_mode)
    print(f"\n═══ ROC [{dataset_label}]  mode={mode} ═══")
    if _dry.exit_after_plan("curves_roc", [
        f"models={list(model_list.keys())}",
        f"n={len(np.asarray(y))} mode={mode}",
        "ROC plot + metrics (skipped)",
    ]):
        return None
    is_val = _is_val_dataset(dataset_label)
    if is_val and mode == "lock_threshold" and lock_from is None:
        lock_from = ROC_LOCK.get("train") or {}

    assert len(model_list) >= 1
    color_map = _resolve_color_map(list(model_list.keys()), color_map)

    y = np.asarray(y).astype(int)
    metrics_rows = []
    curves = []
    lock_store: Dict[str, Dict[str, float]] = {}
    prob_store: Dict[str, np.ndarray] = {}

    for nm, pred in model_list.items():
        pred = np.asarray(pred, dtype=float)
        ok = np.isfinite(pred)
        if ok.sum() < 10:
            continue

        lab = y[ok]
        if len(np.unique(lab)) < 2:
            continue

        use_lock = (
            is_val and mode == "lock_threshold"
            and isinstance(lock_from, dict) and nm in lock_from
        )
        intercept = slope = np.nan
        thr_source = "this_set"

        if use_lock:
            lk = lock_from[nm]
            intercept = float(lk["intercept"])
            slope = float(lk["slope"])
            thr_y = float(lk["threshold"])
            prob = _apply_logit_cal(pred[ok], intercept, slope)
            fpr, tpr, _thr = roc_curve(lab, prob)
            auc = roc_auc_score(lab, prob)
            ci = _boot_auc_ci(lab, prob, seed=seed)
            pred_bin = (prob >= thr_y).astype(int)
            cm = _cls_metrics(lab, pred_bin)
            sens_y, spec_y = cm["SEN"], cm["SPE"]
            thr_source = "train"
        else:
            if is_val and mode == "lock_threshold":
                print(f"  ⚠ {nm}: no train lock — Youden on this set")
            # Step 1: logistic 校准（预测值 → 概率），使 ROC 平滑
            X = sm.add_constant(pred[ok])
            fit = sm.GLM(lab, X, family=sm.families.Binomial()).fit()
            params = np.asarray(fit.params, dtype=float).ravel()
            intercept = float(params[0])
            slope = float(params[1]) if len(params) > 1 else 0.0
            prob = np.asarray(fit.predict(X), dtype=float)
            fpr, tpr, thresholds = roc_curve(lab, prob)
            auc = roc_auc_score(lab, prob)
            ci = _boot_auc_ci(lab, prob, seed=seed)
            youden = tpr - fpr
            idx_y = int(np.argmax(youden))
            thr_y = float(thresholds[idx_y])
            pred_bin = (prob >= thr_y).astype(int)
            cm = _cls_metrics(lab, pred_bin)
            sens_y, spec_y = float(tpr[idx_y]), float(1.0 - fpr[idx_y])

        lock_store[nm] = {
            "intercept": float(intercept),
            "slope": float(slope),
            "threshold": float(thr_y),
        }

        plot_skip = _roc_plot_skipped(nm)
        tag = "  [table only]" if plot_skip else ""
        thr_tag = "  thr=train" if thr_source == "train" else ""
        print(f"  {nm:<25s} AUC={auc:.3f} ({ci[0]:.3f}-{ci[1]:.3f}){tag}{thr_tag}")

        prob_full = np.full(len(y), np.nan, dtype=float)
        prob_full[ok] = prob
        prob_store[nm] = prob_full

        metrics_rows.append({
            "Model": nm, "Threshold": thr_y, "AUC": auc,
            "AUC_CI_low": ci[0], "AUC_CI_high": ci[1],
            "ACC": cm["ACC"], "SEN": sens_y, "SPE": spec_y,
            "NPV": cm["NPV"], "PPV": cm["PPV"],
            "Threshold_source": thr_source,
            "Val_mode": mode,
        })

        # Clinical 等：只进 ROC summary，不进图
        if plot_skip:
            continue
        display_name = f"{nm}  AUC={auc:.3f} ({ci[0]:.3f}-{ci[1]:.3f})"
        curves.append((display_name, nm, fpr, tpr))

    ROC_LOCK[str(dataset_label)] = lock_store

    roc_fn = "roc" if dataset_label == "all" else f"roc_{dataset_label}"

    if metrics_rows:
        metrics_df = pd.DataFrame(metrics_rows)
        if len(prob_store) >= 2:
            dmat = _pairwise_delong(y, prob_store)
            names = [str(x) for x in metrics_df["Model"].tolist()]
            for other in names:
                col = f"DeLong vs {other}"
                metrics_df[col] = [
                    np.nan if self == other else dmat.loc[self, other]
                    for self in names
                ]
            n_pair = sum(1 for i, a in enumerate(names) for b in names[i + 1:])
            print(f"  DeLong pairwise: {n_pair} tests")
        ROC_METRICS[roc_fn] = metrics_df
        print(f"  metrics stored in ROC_METRICS['{roc_fn}']")

    if len(curves) == 0:
        print("  No curves to plot (all models table-only or invalid) — skip figure.")
        return None

    if not bool(getattr(cfg, "DO_ROC", True)):
        print("  DO_ROC=False — skip ROC figure (metrics kept).")
        return None

    dl = str(dataset_label or "all")
    if dl.endswith("_combined"):
        title = f"ROC — {dl.replace('_combined', '')}"
    elif dl in ("train", "test"):
        title = f"ROC — {dl}"
        if dl == "test" and mode != "apply_formula":
            title = f"ROC — {dl} ({mode})"
    else:
        title = "ROC Curves — Model Comparison"
    return draw_roc_curves(curves, title, roc_fn, color_map)


# 同一套分数在各时间点标签上的 AUC。不是按时间点重开的子结局。
LANDMARK_ROC_METRICS: Dict[str, pd.DataFrame] = {}


def _time_as_number(v) -> Optional[float]:
    try:
        t = float(v)
    except (TypeError, ValueError):
        return None
    return t if np.isfinite(t) else None


def plot_auc_by_time(metrics: pd.DataFrame, fname: str, title: str,
                     ci_low: str = "AUC_CI_low", ci_high: str = "AUC_CI_high") -> Optional[plt.Figure]:
    plot_exclude = _roc_plot_exclude_tokens()
    draw = metrics[np.isfinite(metrics["AUC"].astype(float))].copy()
    draw = draw[~draw["Model"].astype(str).str.lower().str.replace(" ", "").str.replace("_", "").isin(plot_exclude)]
    if draw.empty:
        print(f"  {fname}: nothing to draw (table kept)")
        return None
    times = list(dict.fromkeys(metrics["Time"].tolist()))
    nums = [_time_as_number(t) for t in times]
    numeric = all(n is not None for n in nums)
    xpos = {t: (n if numeric else float(i)) for i, (t, n) in enumerate(zip(times, nums))}
    names = list(dict.fromkeys(draw["Model"].astype(str).tolist()))
    colors = _resolve_color_map(names)
    fig, ax = cfg.style_new_axes(figsize=(7, 5.5))
    for nm in names:
        piece = draw[draw["Model"].astype(str) == nm]
        x = np.array([xpos[t] for t in piece["Time"]], dtype=float)
        order = np.argsort(x, kind="mergesort")
        x = x[order]
        y = piece["AUC"].to_numpy(dtype=float)[order]
        lo = piece[ci_low].to_numpy(dtype=float)[order]
        hi = piece[ci_high].to_numpy(dtype=float)[order]
        ax.plot(x, y, marker="o", linewidth=2.0, color=colors[nm], label=nm)
        ok = np.isfinite(lo) & np.isfinite(hi)
        if ok.any():
            ax.errorbar(x[ok], y[ok], yerr=np.vstack([y[ok] - lo[ok], hi[ok] - y[ok]]),
                        fmt="none", ecolor=colors[nm], elinewidth=1.0, capsize=3)
    ax.axhline(0.5, linestyle="--", color="#888888", linewidth=1.0)
    if numeric:
        ax.set_xticks(sorted({xpos[t] for t in times}))
        ax.set_xlabel("Time (months)")
    else:
        ax.set_xticks([xpos[t] for t in times])
        ax.set_xticklabels([str(t) for t in times])
        ax.set_xlabel("Time")
    ax.set_ylabel("AUC")
    ax.set_ylim(0.0, 1.02)
    ax.set_title(title)
    ax.legend(loc="lower right", fontsize=cfg.FIG_FONT_SIZE, framealpha=0.95)
    cfg.apply_clean_theme(ax, grid=True, legend=True)
    fig.tight_layout()
    if cfg.save_plot(fname, fig, width=7, height=5.5):
        print(f"  {fname}.png saved")
    return fig


def landmark_roc_metrics(
    model_list: Dict[str, np.ndarray],
    landmarks: Dict,
    n_boot: Optional[int] = None,
    seed: Optional[int] = None,
) -> pd.DataFrame:
    """锁定分数对每一对时间列 / 状态列各算一个 AUC。不重估系数。

    分数越高表示建模结局的阳性概率越高。状态列沿用这个方向。
    这不是 IPCW 累积/动态 AUC。
    """
    if not isinstance(landmarks, dict) or "pairs" not in landmarks:
        raise ValueError("Landmark 指标需要时间列和状态列成对，不再按月份标签计算。")
    if seed is None:
        seed = int(getattr(cfg, "SEED", 66666) or 66666)
    rows = []
    for pair in landmarks["pairs"]:
        y = np.asarray(pair["status"], dtype=float)
        for nm, pred in (model_list or {}).items():
            pred = np.asarray(pred, dtype=float)
            if pred.shape[0] != y.shape[0]:
                continue
            ok = np.isfinite(pred) & np.isfinite(y)
            if int(ok.sum()) < 10:
                continue
            lab = y[ok]
            if len(np.unique(lab)) < 2:
                continue
            score = pred[ok]
            auc = float(roc_auc_score(lab, score))
            ci = _boot_auc_ci(lab, score, n_boot=n_boot, seed=seed)
            auc, ci_low, ci_high = orient_auc_above_chance(auc, ci[0], ci[1])
            rows.append({
                "Time": str(pair["time_label"]),
                "Time_col": str(pair["time_col"] or "window month"),
                "Status": str(pair["status_col"]),
                "Model": str(nm),
                "N": int(ok.sum()),
                "Events": int(np.sum(lab == 1)),
                "AUC": auc,
                "AUC_CI_low": ci_low,
                "AUC_CI_high": ci_high,
            })
    return pd.DataFrame(rows)


def plot_landmark_roc(
    model_list: Dict[str, np.ndarray],
    landmarks: Dict,
    dataset_label: str = "all",
    seed: Optional[int] = None,
) -> Optional[plt.Figure]:
    """每一对时间列 / 状态列写一行 AUC，并按二分类 ROC 的画法各出一张曲线。"""
    from modules.utils import dry_run as _dry
    label = str(dataset_label or "all")
    print(f"\n═══ LANDMARK ROC [{label}] ═══")
    pairs = landmarks.get("pairs") if isinstance(landmarks, dict) else None
    if _dry.exit_after_plan("landmark_roc", [
        f"models={list((model_list or {}).keys())}",
        "pairs=" + ", ".join(f"t={p['time_label']} → {p['status_col']}" for p in (pairs or [])),
        f"table landmark_{label} (skipped)",
        f"figure PNG/landmark_roc_*_{label}.png (skipped)",
    ]):
        return None
    metrics = landmark_roc_metrics(model_list, landmarks, seed=seed)
    key = f"landmark_{label}"
    if metrics.empty:
        print(f"  no landmark AUC ({label})")
        LANDMARK_ROC_METRICS.pop(key, None)
        return None
    LANDMARK_ROC_METRICS[key] = metrics
    for _, row in metrics.iterrows():
        print(
            f"  t={row['Time']:<8} {row['Status']:<16} {row['Model']:<16}  "
            f"AUC={row['AUC']:.3f} ({row['AUC_CI_low']:.3f}-{row['AUC_CI_high']:.3f})  "
            f"N={int(row['N'])} events={int(row['Events'])}"
        )
    color_map = _resolve_color_map(list((model_list or {}).keys()))
    fig = None
    for pair in pairs or []:
        y = np.asarray(pair["status"], dtype=float)
        curves = []
        for nm, pred in (model_list or {}).items():
            if _roc_plot_skipped(nm):
                continue
            pred = np.asarray(pred, dtype=float)
            if pred.shape[0] != y.shape[0]:
                continue
            ok = np.isfinite(pred) & np.isfinite(y)
            if int(ok.sum()) < 10 or len(np.unique(y[ok])) < 2:
                continue
            lab = y[ok]
            score = pred[ok]
            hit = metrics[
                (metrics["Time"].astype(str) == str(pair["time_label"]))
                & (metrics["Model"].astype(str) == str(nm))
            ]
            if hit.empty or not np.isfinite(float(hit.iloc[0]["AUC"])):
                continue
            row = hit.iloc[0]
            raw_auc = float(roc_auc_score(lab, score))
            fpr, tpr, _thr = roc_curve(lab, score * roc_score_sign(raw_auc))
            display = (
                f"{nm}  AUC={float(row['AUC']):.3f} "
                f"({float(row['AUC_CI_low']):.3f}-{float(row['AUC_CI_high']):.3f})"
            )
            curves.append((display, str(nm), fpr, tpr))
        if not curves:
            continue
        token = roc_time_token(pair["time_label"])
        fig = draw_roc_curves(
            curves,
            roc_split_title("Landmark ROC", label, pair["time_label"]),
            f"landmark_roc_{token}_{label}",
            color_map,
        )
    if fig is None:
        print("  no landmark ROC curves to draw (table kept).")
    return fig
