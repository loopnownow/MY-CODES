from __future__ import annotations

from typing import TYPE_CHECKING, Dict, Optional

import numpy as np
import pandas as pd

from modules import config as cfg
from modules.stats.curves_roc import (
    _resolve_color_map,
    _resolve_n_boot,
    _roc_plot_skipped,
    draw_roc_curves,
    orient_auc_above_chance,
    roc_score_sign,
    roc_split_title,
    roc_time_token,
)

if TYPE_CHECKING:  # 仅用于返回注解（本模块有 from __future__ import annotations）
    from matplotlib.figure import Figure

IPCW_ROC_METRICS: Dict[str, pd.DataFrame] = {}
MIN_RISK_SET = 10
MIN_BOOT_OK = 50


def km_censor_table(time, status) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    time = np.asarray(time, dtype=float)
    status = np.asarray(status, dtype=int)
    uniq, inv = np.unique(time, return_inverse=True)
    counts = np.bincount(inv, minlength=len(uniq))
    at_risk = len(time) - np.concatenate([[0], np.cumsum(counts)[:-1]])
    censored = np.bincount(inv, weights=(status == 0).astype(float), minlength=len(uniq))
    after = np.cumprod(1.0 - censored / at_risk)
    before = np.concatenate([[1.0], after[:-1]])
    return uniq, before, after


def censor_surv_at_events(table, t_events) -> np.ndarray:
    uniq, before, _after = table
    t_events = np.asarray(t_events, dtype=float)
    idx = np.searchsorted(uniq, t_events)
    if np.any(idx >= len(uniq)) or np.any(uniq[np.clip(idx, 0, len(uniq) - 1)] != t_events):
        raise ValueError("事件时间不在删失 KM 表中")
    return before[idx]


def censor_surv_after(table, t: float) -> float:
    uniq, _before, after = table
    k = int(np.searchsorted(uniq, float(t), side="right")) - 1
    return 1.0 if k < 0 else float(after[k])


def weighted_roc_curve(case_score, case_w, ctrl_score, ctrl_w):
    """高分判为阳性。并列分数同时计入病例和对照，梯形面积与 weighted_auc 一致。"""
    case_score = np.asarray(case_score, dtype=float)
    ctrl_score = np.asarray(ctrl_score, dtype=float)
    case_w = np.asarray(case_w, dtype=float)
    ctrl_w = np.asarray(ctrl_w, dtype=float)
    sw = float(case_w.sum())
    ow = float(ctrl_w.sum())
    if not np.isfinite(sw) or not np.isfinite(ow) or sw <= 0 or ow <= 0:
        return np.array([0.0, 1.0]), np.array([0.0, 1.0])
    scores = np.concatenate([case_score, ctrl_score])
    is_case = np.concatenate([
        np.ones(len(case_score), dtype=bool),
        np.zeros(len(ctrl_score), dtype=bool),
    ])
    weights = np.concatenate([case_w, ctrl_w])
    order = np.argsort(-scores, kind="mergesort")
    scores = scores[order]
    is_case = is_case[order]
    weights = weights[order]
    fpr = [0.0]
    tpr = [0.0]
    tp = 0.0
    fp = 0.0
    i = 0
    n = len(scores)
    while i < n:
        j = i + 1
        while j < n and scores[j] == scores[i]:
            j += 1
        tp += float(weights[i:j][is_case[i:j]].sum())
        fp += float(weights[i:j][~is_case[i:j]].sum())
        fpr.append(fp / ow)
        tpr.append(tp / sw)
        i = j
    if fpr[-1] != 1.0 or tpr[-1] != 1.0:
        fpr.append(1.0)
        tpr.append(1.0)
    return np.asarray(fpr, dtype=float), np.asarray(tpr, dtype=float)


def weighted_auc(case_score, case_w, ctrl_score, ctrl_w) -> float:
    case_score = np.asarray(case_score, dtype=float)
    ctrl_score = np.asarray(ctrl_score, dtype=float)
    case_w = np.asarray(case_w, dtype=float)
    ctrl_w = np.asarray(ctrl_w, dtype=float)
    sw = float(case_w.sum())
    ow = float(ctrl_w.sum())
    if not np.isfinite(sw) or not np.isfinite(ow) or sw <= 0 or ow <= 0:
        return float("nan")
    order = np.argsort(ctrl_score, kind="mergesort")
    ctrl_score = ctrl_score[order]
    cumulative = np.concatenate([[0.0], np.cumsum(ctrl_w[order])])
    left = np.searchsorted(ctrl_score, case_score, side="left")
    right = np.searchsorted(ctrl_score, case_score, side="right")
    less = cumulative[left]
    tie = cumulative[right] - less
    return float(((less + 0.5 * tie) * case_w).sum() / (sw * ow))


def _ipcw_weights(time, status, marker, t: float) -> dict:
    time = np.asarray(time, dtype=float)
    status = np.asarray(status, dtype=float)
    marker = np.asarray(marker, dtype=float)
    ok = np.isfinite(time) & np.isfinite(marker) & np.isin(status, (0.0, 1.0))
    time, status, marker = time[ok], status[ok].astype(int), marker[ok]
    case = (status == 1) & (time <= t)
    ctrl = time > t
    out = {
        "n": int(len(time)),
        "cases": int(case.sum()),
        "controls": int(ctrl.sum()),
        "censored_before": int(((status == 0) & (time <= t)).sum()),
        "ready": False,
    }
    if out["cases"] < 1 or out["controls"] < 1:
        return out
    table = km_censor_table(time, status)
    g_case = censor_surv_at_events(table, time[case])
    g_ctrl = censor_surv_after(table, t)
    if np.any(g_case <= 0) or not np.isfinite(g_ctrl) or g_ctrl <= 0:
        return out
    out["case_score"] = marker[case]
    out["case_w"] = 1.0 / g_case
    out["ctrl_score"] = marker[ctrl]
    out["ctrl_w"] = np.full(out["controls"], 1.0 / g_ctrl)
    out["ready"] = True
    return out


def ipcw_auc_at(time, status, marker, t: float) -> dict:
    parts = _ipcw_weights(time, status, marker, t)
    out = {
        "auc": float("nan"),
        "n": parts["n"],
        "cases": parts["cases"],
        "controls": parts["controls"],
        "censored_before": parts["censored_before"],
        "estimable": 0,
    }
    if not parts["ready"]:
        return out
    auc = weighted_auc(parts["case_score"], parts["case_w"], parts["ctrl_score"], parts["ctrl_w"])
    out["auc"] = auc
    out["estimable"] = int(np.isfinite(auc))
    return out


def ipcw_roc_curve(time, status, marker, t: float):
    """IPCW 累积/动态 ROC。权重与 ipcw_auc_at 相同。"""
    parts = _ipcw_weights(time, status, marker, t)
    if not parts["ready"]:
        return np.array([0.0, 1.0]), np.array([0.0, 1.0])
    return weighted_roc_curve(
        parts["case_score"], parts["case_w"], parts["ctrl_score"], parts["ctrl_w"],
    )


def ipcw_auc_ci(time, status, marker, t: float, n_boot: int, seed: int) -> tuple[float, float]:
    time = np.asarray(time, dtype=float)
    status = np.asarray(status, dtype=float)
    marker = np.asarray(marker, dtype=float)
    rng = np.random.RandomState(seed)
    n = len(time)
    values = []
    for _ in range(int(n_boot)):
        idx = rng.randint(0, n, n)
        got = ipcw_auc_at(time[idx], status[idx], marker[idx], t)
        if got["estimable"]:
            values.append(got["auc"])
    if len(values) < MIN_BOOT_OK:
        return float("nan"), float("nan")
    low, high = np.percentile(np.asarray(values, dtype=float), [2.5, 97.5])
    return float(low), float(high)


def ipcw_roc_metrics(
    model_list: Dict[str, np.ndarray],
    ipcw: Dict,
    n_boot: Optional[int] = None,
    seed: Optional[int] = None,
) -> pd.DataFrame:
    if not isinstance(ipcw, dict) or "time" not in ipcw or "status" not in ipcw:
        raise ValueError("IPCW 指标需要时间列、状态列和评估月。")
    horizons = [float(h) for h in (ipcw.get("horizons") or [])]
    if not horizons:
        raise ValueError("IPCW 没有评估月 TD_IPCW_HORIZONS。")
    if seed is None:
        seed = int(getattr(cfg, "SEED", 66666) or 66666)
    n_boot = _resolve_n_boot(n_boot)
    time = np.asarray(ipcw["time"], dtype=float)
    status = np.asarray(ipcw["status"], dtype=float)
    rows = []
    for nm, pred in (model_list or {}).items():
        marker = np.asarray(pred, dtype=float)
        if marker.shape[0] != time.shape[0]:
            continue
        for t in horizons:
            got = ipcw_auc_at(time, status, marker, t)
            low, high = float("nan"), float("nan")
            if got["estimable"] and n_boot > 0:
                low, high = ipcw_auc_ci(time, status, marker, t, n_boot, seed)
            auc, low, high = orient_auc_above_chance(got["auc"], low, high)
            small = (not got["estimable"]) or got["cases"] < MIN_RISK_SET or got["controls"] < MIN_RISK_SET
            rows.append({
                "Time": t,
                "Model": str(nm),
                "N": got["n"],
                "Cases": got["cases"],
                "Controls": got["controls"],
                "Censored_before": got["censored_before"],
                "AUC": auc,
                "AUC_CI_low": low,
                "AUC_CI_high": high,
                "Risk_set": "small" if small else "ok",
            })
    return pd.DataFrame(rows)


def plot_ipcw_roc(
    model_list: Dict[str, np.ndarray],
    ipcw: Dict,
    dataset_label: str = "all",
    seed: Optional[int] = None,
) -> Optional[Figure]:
    from modules.utils import dry_run as _dry
    label = str(dataset_label or "all")
    print(f"\n═══ IPCW TIME-DEPENDENT ROC [{label}] ═══")
    if _dry.exit_after_plan("ipcw_roc", [
        f"models={list((model_list or {}).keys())}",
        f"time={ipcw.get('time_col')}  status={ipcw.get('status_col')}  horizons={ipcw.get('horizons')}",
        f"table ipcw_{label} (skipped)",
        f"figure PNG/ipcw_roc_*_{label}.png (skipped)",
    ]):
        return None
    metrics = ipcw_roc_metrics(model_list, ipcw, seed=seed)
    key = f"ipcw_{label}"
    if metrics.empty:
        print(f"  no IPCW AUC ({label})")
        IPCW_ROC_METRICS.pop(key, None)
        return None
    IPCW_ROC_METRICS[key] = metrics
    for _, row in metrics.iterrows():
        auc = "NA" if not np.isfinite(row["AUC"]) else f"{row['AUC']:.3f}"
        print(
            f"  t={row['Time']:g}  {row['Model']:<16}  AUC={auc}  "
            f"cases={int(row['Cases'])} controls={int(row['Controls'])} risk_set={row['Risk_set']}"
        )
    time = np.asarray(ipcw["time"], dtype=float)
    status = np.asarray(ipcw["status"], dtype=float)
    color_map = _resolve_color_map(list((model_list or {}).keys()))
    fig = None
    horizons = list(dict.fromkeys(float(h) for h in (ipcw.get("horizons") or [])))
    for t in horizons:
        curves = []
        for nm, pred in (model_list or {}).items():
            if _roc_plot_skipped(nm):
                continue
            marker = np.asarray(pred, dtype=float)
            if marker.shape[0] != time.shape[0]:
                continue
            hit = metrics[
                (metrics["Model"].astype(str) == str(nm))
                & np.isclose(metrics["Time"].astype(float), t)
            ]
            if hit.empty or not np.isfinite(float(hit.iloc[0]["AUC"])):
                continue
            row = hit.iloc[0]
            raw_auc = ipcw_auc_at(time, status, marker, t)["auc"]
            fpr, tpr = ipcw_roc_curve(time, status, marker * roc_score_sign(raw_auc), t)
            display = (
                f"{nm}  AUC={float(row['AUC']):.3f} "
                f"({float(row['AUC_CI_low']):.3f}-{float(row['AUC_CI_high']):.3f})"
            )
            curves.append((display, str(nm), fpr, tpr))
        if not curves:
            continue
        fig = draw_roc_curves(
            curves,
            roc_split_title("Time-dependent ROC", label, t),
            f"ipcw_roc_{roc_time_token(t)}_{label}",
            color_map,
        )
    if fig is None:
        print("  no IPCW ROC curves to draw (table kept).")
    return fig
