"""
nri_cir.py — NRI 净重新分类改善指数 + 重分类弦图 (chord diagram)
═══════════════════════════════════════════════════════════════════
功能：提供两个临床预测模型比较工具：
    1. NRI (Net Reclassification Improvement) — 量化新模型相比旧
       模型的重分类改善程度（Pencina 2008 公式）
    2. cir_plot — 弦图可视化新/旧模型的重分类流向
       （matplotlib 手绘：贝塞尔曲线弦 + 圆弧扇区）
═══════════════════════════════════════════════════════════════════
"""

from __future__ import annotations
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.path import Path
import matplotlib.patches as patches
import statsmodels.api as sm

from modules import config as cfg

# ═════════════════════════════════════════════════════════════════
# §1  顶层参数（可覆盖）
# ═════════════════════════════════════════════════════════════════
# 两个内部切点 → 低 / 中 / 高；仅 (0, 0.5, 1) 实际只有两档
NRI_CUTOFF: Sequence[float] = (0.0, 0.3, 0.7, 1.0)
# 原弦图配色；扇区顺序 [PC0, PC1, SC0, SC1]
CIR_GRID_COLORS: Sequence[str] = ("#0073C2FF", "#EFC000FF", "#0073C2FF", "#EFC000FF")
CIR_LINK_COLORS_0: Sequence[str] = ("#E5E5E5", "#5A9367", "#B84A39", "#D9D9D9")  # label=0
CIR_LINK_COLORS_1: Sequence[str] = ("#E5E5E5", "#B84A39", "#5A9367", "#D9D9D9")  # label=1 正误反转
# 90°=正上方起逆时针：前半圈=PC(左)，后半圈=SC(右)
CIR_START_DEGREE: float = 90.0
CIR_TITLE_0: str = "Reclassification (label = 0)"
CIR_TITLE_1: str = "Reclassification (label = 1)"


def _complete_cases(x1: pd.DataFrame, x2: pd.DataFrame, y: np.ndarray):
    """对齐索引，剔除含 NaN/inf 的行，返回干净的 x1,x2,y。"""
    y = np.asarray(y).astype(int)
    n = len(y)
    x1 = x1.apply(pd.to_numeric, errors="coerce").reset_index(drop=True)
    x2 = x2.apply(pd.to_numeric, errors="coerce").reset_index(drop=True)
    if len(x1) != n or len(x2) != n:
        raise ValueError(f"x1/x2/y 长度不一致: {len(x1)}, {len(x2)}, {n}")
    ok = np.isfinite(y.astype(float))
    for df in (x1, x2):
        ok &= np.isfinite(df.to_numpy(dtype=float)).all(axis=1)
    if ok.sum() < 10:
        raise ValueError(f"完整病例不足 (n={int(ok.sum())})，无法计算 NRI/IDI")
    return x1.loc[ok].reset_index(drop=True), x2.loc[ok].reset_index(drop=True), y[ok]


def _fit_prob(X: pd.DataFrame, y: np.ndarray) -> np.ndarray:
    Xc = sm.add_constant(X, has_constant="add")
    # 再剔一次（防御）
    arr = Xc.to_numpy(dtype=float)
    ok = np.isfinite(arr).all(axis=1) & np.isfinite(y.astype(float))
    if ok.sum() < Xc.shape[1] + 2:
        raise ValueError("有效样本不足以拟合 logistic")
    fit = sm.GLM(y[ok], Xc.loc[ok], family=sm.families.Binomial()).fit()
    p = np.full(len(y), np.nan)
    p[ok] = fit.predict(Xc.loc[ok])
    return p


def _normalize_nri_cutoff(cutoff: Sequence[float]) -> np.ndarray:
    arr = np.asarray(list(cutoff), dtype=float)
    arr = arr[np.isfinite(arr)]
    arr = np.unique(np.sort(arr))
    if arr.size < 3:
        raise ValueError(
            f"NRI cutoff {list(cutoff)} 至少需要两端点 + 1 个内部切点；"
            "三分类（低/中/高）需要两个内部切点，例如 (0, 0.3, 0.7, 1)"
        )
    n_internal = int(arr.size - 2)
    if n_internal < 2:
        print(
            f"  ⚠ NRI cutoff {arr.tolist()} 只有 {n_internal} 个内部切点，"
            "实际为两档风险，不能解释为低/中/高。"
        )
    return arr


def _resolve_n_boot(n_boot=None) -> int:
    if n_boot is not None:
        return int(n_boot)
    from modules import config as cfg
    return int(
        getattr(cfg, "BOOTSTRAP_N", None)
        or getattr(cfg, "MEDIATION_BOOTSTRAP", None)
        or 1000
    )


def nri_from_probs(
    p_old,
    p_new,
    y: np.ndarray,
    cutoff: Sequence[float] = NRI_CUTOFF,
    n_boot=None,
    seed: int = 66666,
    *,
    apparent: bool = False,
    layer: str = "",
) -> Dict[str, float]:
    """NRI from locked predicted probabilities (no refit).

    Bootstrap resamples the locked (p_old, p_new, y) triples. This is the
    correct CI when models are already locked (validation/test/external).
    On a development set set apparent=True.
    """
    n_boot = _resolve_n_boot(n_boot)
    p_old = np.asarray(p_old, dtype=float).ravel()
    p_new = np.asarray(p_new, dtype=float).ravel()
    y = np.asarray(y).astype(int).ravel()
    if not (len(p_old) == len(p_new) == len(y)):
        raise ValueError(f"p_old/p_new/y 长度不一致: {len(p_old)}, {len(p_new)}, {len(y)}")
    ok = np.isfinite(p_old) & np.isfinite(p_new) & np.isfinite(y.astype(float))
    p_old, p_new, y = p_old[ok], p_new[ok], y[ok]
    if len(y) < 10:
        raise ValueError(f"完整病例不足 (n={len(y)})，无法计算 NRI")

    cutoff_arr = _normalize_nri_cutoff(cutoff)

    def _nri_from_p(po, pn, yy):
        cat_old = np.digitize(po, cutoff_arr[1:-1], right=False)
        cat_new = np.digitize(pn, cutoff_arr[1:-1], right=False)
        up = cat_new > cat_old
        down = cat_new < cat_old
        events = yy == 1
        nonevents = yy == 0
        ne = (np.mean(up[events]) - np.mean(down[events])) if events.sum() > 0 else np.nan
        nn = (np.mean(down[nonevents]) - np.mean(up[nonevents])) if nonevents.sum() > 0 else np.nan
        return float(ne), float(nn), float(ne + nn) if np.isfinite(ne) and np.isfinite(nn) else np.nan

    nri_events, nri_nonevents, nri_total = _nri_from_p(p_old, p_new, y)

    cat_old = np.digitize(p_old, cutoff_arr[1:-1], right=False)
    cat_new = np.digitize(p_new, cutoff_arr[1:-1], right=False)
    reclass_table = pd.crosstab(
        pd.Series(cat_old, name="Old category"),
        pd.Series(cat_new, name="New category"),
    )

    old_hi = cat_old >= 1
    new_hi = cat_new >= 1
    def _cell(mask_y, old_h, new_h):
        return int(((y == mask_y) & (old_hi == old_h) & (new_hi == new_h)).sum())

    chord_counts = [
        _cell(0, False, False), _cell(0, True, False),
        _cell(0, False, True), _cell(0, True, True),
        _cell(1, False, False), _cell(1, True, False),
        _cell(1, False, True), _cell(1, True, True),
    ]

    rng = np.random.RandomState(seed)
    boots_e, boots_n, boots_t = [], [], []
    n = len(y)
    for _ in range(int(n_boot)):
        idx = rng.randint(0, n, size=n)
        e, nn, t = _nri_from_p(p_old[idx], p_new[idx], y[idx])
        boots_e.append(e)
        boots_n.append(nn)
        boots_t.append(t)

    def _ci(arr):
        a = np.asarray(arr, dtype=float)
        a = a[np.isfinite(a)]
        if len(a) < 20:
            return np.nan, np.nan
        return float(np.percentile(a, 2.5)), float(np.percentile(a, 97.5))

    e_lo, e_hi = _ci(boots_e)
    n_lo, n_hi = _ci(boots_n)
    t_lo, t_hi = _ci(boots_t)
    n_internal = int(cutoff_arr.size - 2)
    return {
        "NRI_events": float(nri_events),
        "NRI_nonevents": float(nri_nonevents),
        "NRI_total": float(nri_total),
        "NRI_events_CI_low": e_lo,
        "NRI_events_CI_high": e_hi,
        "NRI_nonevents_CI_low": n_lo,
        "NRI_nonevents_CI_high": n_hi,
        "NRI_total_CI_low": t_lo,
        "NRI_total_CI_high": t_hi,
        "reclassification_table": reclass_table,
        "p_old": p_old,
        "p_new": p_new,
        "y": y,
        "chord_counts": chord_counts,
        "n_used": int(len(y)),
        "cutoff": [float(x) for x in cutoff_arr],
        "n_risk_bins": int(n_internal + 1),
        "apparent": bool(apparent),
        "layer": str(layer or ""),
        "bootstrap": "locked_predictions",
    }


def nri(x1: pd.DataFrame, x2: pd.DataFrame, y: np.ndarray,
        cutoff: Sequence[float] = NRI_CUTOFF,
        n_boot=None, seed: int = 66666,
        p_old=None, p_new=None, *, apparent: Optional[bool] = None,
        layer: str = "") -> Dict[str, float]:
    """
    净重新分类改善指数 (NRI)。

    优先使用锁定的 p_old/p_new。若未提供，则在同一 y 上重拟合 logistic
    （apparent performance；不用于确认性验证）。
    """
    from modules.utils import dry_run as _dry
    n_boot = _resolve_n_boot(n_boot)
    if _dry.exit_after_plan("nri", [
        f"n={len(np.asarray(y))} n_boot={n_boot} layer={layer or '(auto)'}",
        "NRI bootstrap (skipped)",
    ]):
        return {
            "NRI_events": float("nan"), "NRI_nonevents": float("nan"),
            "NRI_total": float("nan"),
            "NRI_events_CI_low": float("nan"), "NRI_events_CI_high": float("nan"),
            "NRI_nonevents_CI_low": float("nan"), "NRI_nonevents_CI_high": float("nan"),
            "NRI_total_CI_low": float("nan"), "NRI_total_CI_high": float("nan"),
            "layer": layer or "dry-run", "apparent": bool(apparent),
            "bootstrap": "dry-run",
        }
    if p_old is None or p_new is None:
        x1, x2, y = _complete_cases(x1, x2, y)
        p_old = _fit_prob(x1, y)
        p_new = _fit_prob(x2, y)
        if apparent is None:
            apparent = True
        if not layer:
            layer = "refit"
    elif apparent is None:
        apparent = False
    return nri_from_probs(
        p_old, p_new, y, cutoff=cutoff, n_boot=n_boot, seed=seed,
        apparent=bool(apparent), layer=layer,
    )


def idi_from_probs(
    p_old,
    p_new,
    y: np.ndarray,
    n_boot=None,
    seed: int = 66666,
    *,
    apparent: bool = False,
    layer: str = "",
) -> Dict[str, float]:
    """Continuous IDI from locked predicted probabilities (no refit)."""
    n_boot = _resolve_n_boot(n_boot)
    p_old = np.asarray(p_old, dtype=float).ravel()
    p_new = np.asarray(p_new, dtype=float).ravel()
    y = np.asarray(y).astype(int).ravel()
    if not (len(p_old) == len(p_new) == len(y)):
        raise ValueError(f"p_old/p_new/y 长度不一致: {len(p_old)}, {len(p_new)}, {len(y)}")
    ok = np.isfinite(p_old) & np.isfinite(p_new) & np.isfinite(y.astype(float))
    p_old, p_new, y = p_old[ok], p_new[ok], y[ok]
    if len(y) < 10:
        raise ValueError(f"完整病例不足 (n={len(y)})，无法计算 IDI")

    def _idi(po, pn, yy):
        ev, ne = yy == 1, yy == 0
        if ev.sum() == 0 or ne.sum() == 0:
            return np.nan
        return float(
            (pn[ev].mean() - po[ev].mean()) - (pn[ne].mean() - po[ne].mean())
        )

    idi_val = _idi(p_old, p_new, y)
    is_events = float((p_new[y == 1] - p_old[y == 1]).mean()) if (y == 1).sum() else np.nan
    ip_nonevents = float((p_old[y == 0] - p_new[y == 0]).mean()) if (y == 0).sum() else np.nan

    rng = np.random.RandomState(seed)
    boots = []
    n = len(y)
    for _ in range(n_boot):
        idx = rng.randint(0, n, size=n)
        boots.append(_idi(p_old[idx], p_new[idx], y[idx]))
    boots = np.asarray(boots, dtype=float)
    boots = boots[np.isfinite(boots)]
    if len(boots) >= 20:
        lo, hi = float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))
    else:
        lo, hi = np.nan, np.nan

    return {
        "IDI": idi_val,
        "IDI_CI_low": lo,
        "IDI_CI_high": hi,
        "IS_events": is_events,
        "IP_nonevents": ip_nonevents,
        "p_old": p_old,
        "p_new": p_new,
        "y": y,
        "n_used": int(len(y)),
        "apparent": bool(apparent),
        "layer": str(layer or ""),
        "bootstrap": "locked_predictions",
    }


def idi(x1: pd.DataFrame, x2: pd.DataFrame, y: np.ndarray,
        n_boot=None, seed: int = 66666,
        p_old=None, p_new=None, *, apparent: Optional[bool] = None,
        layer: str = "") -> Dict[str, float]:
    """
    连续 IDI (Pencina)。优先锁定概率；未提供则同 y 重拟合（apparent）。
    """
    n_boot = _resolve_n_boot(n_boot)
    if p_old is None or p_new is None:
        x1, x2, y = _complete_cases(x1, x2, y)
        p_old = _fit_prob(x1, y)
        p_new = _fit_prob(x2, y)
        if apparent is None:
            apparent = True
        if not layer:
            layer = "refit"
    elif apparent is None:
        apparent = False
    return idi_from_probs(
        p_old, p_new, y, n_boot=n_boot, seed=seed,
        apparent=bool(apparent), layer=layer,
    )


def plot_idi(idi_res: Dict, dataset_label: str = "train",
             group_labels: Optional[Dict[int, str]] = None,
             file_stem: str = "idi") -> None:
    """IDI 图：按实际 group 名标注亚组 Δp 分布 + IDI 标注。"""
    from modules.utils import dry_run as _dry
    if _dry.exit_after_plan("nri_cir.plot_idi", [
        f"dataset={dataset_label} stem={file_stem}",
        "IDI plot (skipped)",
    ]):
        return
    p_old = np.asarray(idi_res["p_old"], dtype=float)
    p_new = np.asarray(idi_res["p_new"], dtype=float)
    y = np.asarray(idi_res["y"]).astype(int)
    delta = p_new - p_old
    gmap = group_labels or {0: "Negative", 1: "Positive"}
    name0 = str(gmap.get(0, "Negative"))
    name1 = str(gmap.get(1, "Positive"))

    fig, ax = cfg.style_new_axes(figsize=(6.8, 5.2))
    data_plot, labels, colors = [], [], []
    if (y == 1).sum() > 0:
        data_plot.append(delta[y == 1])
        labels.append(name1)
        colors.append("#B2182B")
    if (y == 0).sum() > 0:
        data_plot.append(delta[y == 0])
        labels.append(name0)
        colors.append("#2166AC")
    box_kw = dict(
        patch_artist=True, widths=0.5,
        medianprops=dict(color="#111111", linewidth=1.4),
        whiskerprops=dict(color="#444444", linewidth=1.0),
        capprops=dict(color="#444444", linewidth=1.0),
        flierprops=dict(marker="o", markersize=4, markerfacecolor="#666666",
                        markeredgecolor="none", alpha=0.55),
    )
    try:
        bp = ax.boxplot(data_plot, tick_labels=labels, **box_kw)
    except TypeError:
        bp = ax.boxplot(data_plot, labels=labels, **box_kw)
    for patch, c in zip(bp["boxes"], colors):
        patch.set_facecolor(c)
        patch.set_alpha(0.40)
        patch.set_edgecolor(c)
        patch.set_linewidth(1.2)
    ax.axhline(0, color="#888888", linestyle="--", linewidth=1.0, zorder=1)
    idi_v = idi_res.get("IDI", np.nan)
    lo = idi_res.get("IDI_CI_low", np.nan)
    hi = idi_res.get("IDI_CI_high", np.nan)
    fs = cfg.FIG_FONT_SIZE
    ax.set_title("Integrated Discrimination Improvement (IDI)",
                 fontweight="normal", fontsize=fs)
    ax.set_ylabel("Δ Predicted probability", fontsize=fs)
    ci_txt = (
        f"IDI = {idi_v:.4f} (95% CI {lo:.4f}–{hi:.4f})"
        if np.isfinite(lo) and np.isfinite(hi)
        else f"IDI = {idi_v:.4f}"
    )
    ax.text(0.5, 0.98, ci_txt, transform=ax.transAxes, ha="center", va="top",
            fontsize=fs, color="#222222",
            bbox=dict(boxstyle="round,pad=0.25", facecolor="white",
                      edgecolor="#DDDDDD", alpha=0.9))
    cfg.apply_clean_theme(ax, grid=True, legend=False)
    fig.tight_layout()
    stem = str(file_stem or "idi").strip() or "idi"
    if cfg.save_plot(stem, fig, width=6.8, height=5.2, dpi=150):
        print(f"  {stem}.png saved")


# ═════════════════════════════════════════════════════════════════
# §2  弦图 (chord diagram) — 左 PC / 右 SC
#   灰 / 红 / 绿：均为原 circlize 全宽填充弦
#   红 / 绿：额外画 PC→SC 方向箭头（灰带不加）
# ═════════════════════════════════════════════════════════════════
def _sector_arc(ax, theta0: float, theta1: float, r_in: float, r_out: float, color: str, label: str):
    """绘制一个扇区（环形段）并在中点标注文字。"""
    n = 60
    thetas = np.linspace(theta0, theta1, n)
    outer = [(r_out * np.cos(t), r_out * np.sin(t)) for t in thetas]
    inner = [(r_in * np.cos(t), r_in * np.sin(t)) for t in thetas[::-1]]
    poly = patches.Polygon(outer + inner, closed=True, facecolor=color, edgecolor="white", linewidth=1)
    ax.add_patch(poly)
    tm = (theta0 + theta1) / 2
    lx, ly = (r_out + 0.08) * np.cos(tm), (r_out + 0.08) * np.sin(tm)
    ax.text(lx, ly, label, ha="center", va="center", fontsize=cfg.FIG_FONT_SIZE)


def _chord_ribbon(
    ax,
    theta_a0: float, theta_a1: float,
    theta_b0: float, theta_b1: float,
    r_in: float, color: str,
):
    """PC→SC 全宽填充弦（与灰色相同的原 circlize 风格）。"""
    pa0 = (r_in * np.cos(theta_a0), r_in * np.sin(theta_a0))
    pa1 = (r_in * np.cos(theta_a1), r_in * np.sin(theta_a1))
    pb0 = (r_in * np.cos(theta_b0), r_in * np.sin(theta_b0))
    pb1 = (r_in * np.cos(theta_b1), r_in * np.sin(theta_b1))
    verts = [pa0, (0.0, 0.0), pb1, pb0, (0.0, 0.0), pa1, pa0]
    codes = [
        Path.MOVETO, Path.CURVE3, Path.CURVE3,
        Path.LINETO, Path.CURVE3, Path.CURVE3, Path.CLOSEPOLY,
    ]
    path = Path(verts, codes)
    ax.add_patch(patches.PathPatch(
        path, facecolor=color, edgecolor="none", alpha=0.65, zorder=1,
    ))


def _lighten_hex(color: str, amount: float = 0.5) -> str:
    """将颜色与白色按 amount 混合（0=原色，1=纯白）。"""
    c = str(color).strip()
    if c.startswith("#") and len(c) >= 7:
        r = int(c[1:3], 16)
        g = int(c[3:5], 16)
        b = int(c[5:7], 16)
    else:
        return c
    a = float(np.clip(amount, 0.0, 1.0))
    r = int(round(r + (255 - r) * a))
    g = int(round(g + (255 - g) * a))
    b = int(round(b + (255 - b) * a))
    return f"#{r:02X}{g:02X}{b:02X}"


def _chord_direction_arrow(
    ax,
    theta_a0: float, theta_a1: float,
    theta_b0: float, theta_b1: float,
    r_in: float, color: str,
):
    """在弦带中线上画 PC→SC 方向箭头（不改变弦带填充外形）。"""
    ta = 0.5 * (theta_a0 + theta_a1)
    tb = 0.5 * (theta_b0 + theta_b1)
    p_src = np.array([r_in * np.cos(ta), r_in * np.sin(ta)])
    p_tgt = np.array([r_in * np.cos(tb), r_in * np.sin(tb)])
    # 中线二次贝塞尔（控制点圆心）：取中后段作为箭头
    def _bez(t: float) -> np.ndarray:
        u = 1.0 - t
        return (u * u) * p_src + (t * t) * p_tgt

    q0, q1 = _bez(0.42), _bez(0.78)
    # 箭头颜色淡 50%（与白色 1:1 混合）
    arrow_color = _lighten_hex(color, 0.5)
    ax.add_patch(patches.FancyArrowPatch(
        q0, q1,
        arrowstyle="-|>",
        mutation_scale=16,
        linewidth=1.6,
        color=arrow_color,
        alpha=0.95,
        zorder=3,
        shrinkA=0,
        shrinkB=0,
    ))


def _is_gray_link(color: str) -> bool:
    """灰/浅灰重分类带：无变化，不加方向箭头。"""
    c = str(color).strip().upper()
    if len(c) == 9 and c.startswith("#"):
        c = c[:7]
    gray = {
        "#E5E5E5", "#D9D9D9", "#CCCCCC", "#B0B8C1", "#BDBDBD",
        "#EEEEEE", "#F0F0F0", "#C0C0C0", "#A0A0A0",
    }
    return c in gray


def _draw_chord_diagram(ax, sector_names: List[str], sector_sizes: Dict[str, float],
                         links: List[tuple], grid_colors: Sequence[str],
                         link_colors: Sequence[str], start_degree: float, title: str):
    """
    4 扇区弦图。start_degree=90° → PC 左、SC 右。
    灰/红/绿均为全宽弦；红/绿加 PC→SC 方向箭头。

    links : [(from_sector, to_sector, value, color_idx), ...]
    """
    total = sum(sector_sizes.values())
    if total <= 0:
        ax.text(0, 0, "(no data)", ha="center", va="center")
        ax.set_title(title)
        return

    gap = 0.03 * 2 * np.pi / max(len(sector_names), 1)
    angle = np.deg2rad(start_degree)
    bounds = {}
    for name in sector_names:
        span = (sector_sizes[name] / total) * (2 * np.pi - gap * len(sector_names))
        bounds[name] = (angle, angle + span)
        angle += span + gap

    for i, name in enumerate(sector_names):
        t0, t1 = bounds[name]
        _sector_arc(ax, t0, t1, r_in=0.95, r_out=1.0,
                    color=grid_colors[i % len(grid_colors)], label=name)

    cursor = {name: bounds[name][0] for name in sector_names}
    for frm, to, val, color_idx in links:
        if val <= 0:
            continue
        span_frm = (bounds[frm][1] - bounds[frm][0]) * (val / sector_sizes[frm]) if sector_sizes[frm] > 0 else 0
        span_to = (bounds[to][1] - bounds[to][0]) * (val / sector_sizes[to]) if sector_sizes[to] > 0 else 0
        a0, a1 = cursor[frm], cursor[frm] + span_frm
        b0, b1 = cursor[to], cursor[to] + span_to
        cursor[frm] = a1
        cursor[to] = b1
        col = link_colors[color_idx % len(link_colors)]
        _chord_ribbon(ax, a0, a1, b0, b1, r_in=0.95, color=col)
        # 红/绿：标 PC→SC 方向；灰带不加
        if (int(color_idx) % 4) in (1, 2) and not _is_gray_link(col):
            _chord_direction_arrow(ax, a0, a1, b0, b1, r_in=0.95, color=col)

    ax.set_xlim(-1.3, 1.3)
    ax.set_ylim(-1.3, 1.3)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_title(title, fontsize=cfg.FIG_FONT_SIZE, fontweight="normal")


def cir_plot(data: Sequence[float], a: str, b: str, c: str, d: str,
             grid_colors: Sequence[str] = CIR_GRID_COLORS,
             link_colors_0: Sequence[str] = CIR_LINK_COLORS_0,
             link_colors_1: Sequence[str] = CIR_LINK_COLORS_1,
             start_degree: float = CIR_START_DEGREE,
             title_0: str = CIR_TITLE_0, title_1: str = CIR_TITLE_1,
             dataset_label: str = "reclassification") -> plt.Figure:
    """
    重分类弦图：8 个数值 = [label=0 的 4 种重分类组合, label=1 的 4 种]。
    组合顺序：(a→c, b→c, a→d, b→d)。

    a, b : previous classified = 标签0 / 标签1 的扇区名
    c, d : subsequent classified = 标签0 / 标签1 的扇区名
    """
    from modules.utils import dry_run as _dry
    if _dry.exit_after_plan("nri_cir.cir_plot", [
        f"dataset={dataset_label}",
        "reclassification chord (skipped)",
    ]):
        return None  # type: ignore[return-value]
    assert len(data) == 8, "data 必须含 8 个数值（前4个 label=0，后4个 label=1）"
    d0 = data[:4]
    d1 = data[4:]

    fig, axes = plt.subplots(1, 2, figsize=(12, 6))

    for ax, vals, link_colors, title in [(axes[0], d0, link_colors_0, title_0),
                                          (axes[1], d1, link_colors_1, title_1)]:
        sector_names = [a, b, c, d]
        sector_sizes = {
            a: vals[0] + vals[2],  # a→c + a→d
            b: vals[1] + vals[3],  # b→c + b→d
            c: vals[0] + vals[1],  # a→c + b→c
            d: vals[2] + vals[3],  # a→d + b→d
        }
        links = [(a, c, vals[0], 0), (b, c, vals[1], 1), (a, d, vals[2], 2), (b, d, vals[3], 3)]
        _draw_chord_diagram(ax, sector_names, sector_sizes, links,
                             grid_colors, link_colors, start_degree, title)

    fig.tight_layout()
    if cfg.save_plot(f"chord_{dataset_label}", fig, width=12, height=6, dpi=300):
        print(f"  chord_{dataset_label}.png saved")
    return fig
