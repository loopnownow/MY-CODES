"""
mediation.py — 探索性中介分析（bootstrap product-of-coefficients）

二分类 Y：logistic；连续 M/X：线性（或 X 为 0/1）。
报告 ACME (a*b), ADE (c'), total (c), proportion mediated。
表注：associational / exploratory, not causal identification.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import statsmodels.api as sm
import matplotlib.pyplot as plt

from modules import config as cfg


def _as_num(s) -> np.ndarray:
    return pd.to_numeric(pd.Series(s), errors="coerce").to_numpy(dtype=float)


def _fit_ols(y, X_cols: pd.DataFrame):
    X = sm.add_constant(X_cols, has_constant="add")
    return sm.OLS(y, X).fit()


def _fit_logit(y, X_cols: pd.DataFrame):
    X = sm.add_constant(X_cols, has_constant="add")
    # 完整病例
    return sm.GLM(y, X, family=sm.families.Binomial()).fit()


def _is_binary(y: np.ndarray) -> bool:
    u = np.unique(y[np.isfinite(y)])
    return len(u) <= 2


def _effects_once(x: np.ndarray, m: np.ndarray, y: np.ndarray
                  ) -> Optional[Dict[str, float]]:
    """单次样本上的 a, b, c, c', ACME, ADE。"""
    df = pd.DataFrame({"x": x, "m": m, "y": y}).dropna()
    if len(df) < 20:
        return None
    try:
        # a: M ~ X
        fa = _fit_ols(df["m"].to_numpy(), df[["x"]])
        a = float(fa.params["x"])
        # c: Y ~ X
        if _is_binary(df["y"].to_numpy()):
            fc = _fit_logit(df["y"].to_numpy(), df[["x"]])
            fcp = _fit_logit(df["y"].to_numpy(), df[["x", "m"]])
        else:
            fc = _fit_ols(df["y"].to_numpy(), df[["x"]])
            fcp = _fit_ols(df["y"].to_numpy(), df[["x", "m"]])
        c = float(fc.params["x"])
        c_prime = float(fcp.params["x"])
        b = float(fcp.params["m"])
        acme = a * b
        ade = c_prime
        total = c
        prop = acme / total if abs(total) > 1e-12 else np.nan
        binary_y = _is_binary(df["y"].to_numpy())
        return {
            "a": a, "b": b, "c": c, "c_prime": c_prime,
            "ACME": acme, "ADE": ade, "total": total,
            "prop_mediated": prop, "n": float(len(df)),
            "binary_y": float(binary_y),
        }
    except Exception:
        return None


def bootstrap_mediation(x, m, y, n_boot: int = 1000, seed: int = 66666
                        ) -> Optional[Dict[str, Any]]:
    x, m, y = _as_num(x), _as_num(m), _as_num(y)
    mask = np.isfinite(x) & np.isfinite(m) & np.isfinite(y)
    x, m, y = x[mask], m[mask], y[mask]
    if len(x) < 20:
        return None
    point = _effects_once(x, m, y)
    if point is None:
        return None

    rng = np.random.RandomState(seed)
    keys = ["a", "b", "c", "c_prime", "ACME", "ADE", "total", "prop_mediated"]
    store = {k: [] for k in keys}
    n = len(x)
    for _ in range(n_boot):
        idx = rng.randint(0, n, size=n)
        est = _effects_once(x[idx], m[idx], y[idx])
        if est is None:
            continue
        for k in keys:
            v = est[k]
            if np.isfinite(v):
                store[k].append(v)

    out = {
        "n_used": int(point["n"]),
        "n_boot_ok": len(store["ACME"]),
        "binary_y": bool(point.get("binary_y")),
        "note": (
            "Exploratory product-of-coefficients decomposition "
            "(associational, not counterfactual NIE/NDE). "
            "For binary Y, a*b is not generally the natural indirect effect."
            if point.get("binary_y") else
            "Exploratory product-of-coefficients decomposition "
            "(associational, not causal identification)."
        ),
    }
    for k in keys:
        arr = np.asarray(store[k], dtype=float)
        out[k] = float(point[k])
        if len(arr) >= 20:
            out[f"{k}_ci_low"] = float(np.percentile(arr, 2.5))
            out[f"{k}_ci_high"] = float(np.percentile(arr, 97.5))
        else:
            out[f"{k}_ci_low"] = np.nan
            out[f"{k}_ci_high"] = np.nan
    return out


def _fmt_coef(v: float) -> str:
    """路径系数显示：过大/过小用科学计数，避免图面挤爆。"""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "NA"
    if not np.isfinite(v):
        return "NA"
    av = abs(v)
    if av >= 100 or (av > 0 and av < 0.001):
        return f"{v:.2e}"
    if av >= 10:
        return f"{v:.2f}"
    return f"{v:.3f}"


def _wrap_med_label(s: str, max_chars: int = 14) -> str:
    s = str(s)
    if len(s) <= max_chars:
        return s
    if "_" in s:
        parts = s.split("_")
        lines, cur = [], parts[0]
        for p in parts[1:]:
            if len(cur) + 1 + len(p) <= max_chars:
                cur = cur + "_" + p
            else:
                lines.append(cur)
                cur = p
        lines.append(cur)
        return "\n".join(lines[:3])
    # 硬折
    return "\n".join(s[i:i + max_chars] for i in range(0, min(len(s), max_chars * 2), max_chars))


def plot_mediation_path(res: Dict[str, Any], path_id: str, label: str) -> None:
    """路径系数示意图：X→M→Y 与直接效应 c′（与 DAG 配色一致）。"""
    if res is None:
        return
    from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

    c_x, c_m, c_y = "#6BA3D0", "#7EB6A4", "#E8A060"
    c_path, c_direct = "#475569", "#D64550"
    c_bg, c_edge = "#FAFBFC", "#334155"

    fig, ax = plt.subplots(figsize=(8.4, 4.2))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 5.6)
    ax.axis("off")
    ax.set_facecolor(c_bg)
    fig.patch.set_facecolor(c_bg)

    fs = cfg.FIG_FONT_SIZE
    # 节点中心与半宽/半高
    nodes = {
        "x": (1.7, 2.2, 1.15, 0.55),   # cx, cy, hw, hh
        "m": (5.0, 4.35, 1.25, 0.58),
        "y": (8.3, 2.2, 1.15, 0.55),
    }

    def draw_box(key, text, fc):
        cx, cy, hw, hh = nodes[key]
        ax.add_patch(FancyBboxPatch(
            (cx - hw, cy - hh), 2 * hw, 2 * hh,
            boxstyle="round,pad=0.02,rounding_size=0.08",
            facecolor=fc, edgecolor=c_edge, lw=1.5,
            zorder=3, alpha=0.97,
        ))
        ax.text(cx, cy, text, ha="center", va="center", fontsize=fs,
                fontweight="medium", color="#1F2937", zorder=4, linespacing=1.15)

    def edge_pt(key, toward_key):
        cx, cy, hw, hh = nodes[key]
        tx, ty, _, _ = nodes[toward_key]
        dx, dy = tx - cx, ty - cy
        if abs(dx) < 1e-9 and abs(dy) < 1e-9:
            return cx + hw, cy
        sx = hw / abs(dx) if abs(dx) > 1e-9 else np.inf
        sy = hh / abs(dy) if abs(dy) > 1e-9 else np.inf
        t = min(sx, sy)
        return cx + t * dx, cy + t * dy

    def arrow(src, dst, color, lw=2.0, rad=0.0, z=2):
        p0 = edge_pt(src, dst)
        p1 = edge_pt(dst, src)
        ax.add_patch(FancyArrowPatch(
            p0, p1,
            connectionstyle=f"arc3,rad={rad:.3f}",
            arrowstyle="-|>", mutation_scale=15,
            linewidth=lw, color=color, alpha=0.92, zorder=z,
        ))

    xlab = _wrap_med_label(str(res.get("x", "X")))
    mlab = _wrap_med_label(str(res.get("m", "M")))
    ylab = _wrap_med_label(str(res.get("y", "Y")))

    draw_box("x", xlab, c_x)
    draw_box("m", mlab, c_m)
    draw_box("y", ylab, c_y)

    # a: X→M  b: M→Y  c′: X→Y（直达）
    arrow("x", "m", c_path, lw=2.0, rad=0.0)
    arrow("m", "y", c_path, lw=2.0, rad=0.0)
    arrow("x", "y", c_direct, lw=2.1, rad=0.0)

    a, b = res.get("a", np.nan), res.get("b", np.nan)
    cp, acme = res.get("c_prime", np.nan), res.get("ACME", np.nan)
    # 系数注记放在路径旁，避开节点
    ax.text(3.15, 3.55, f"a = {_fmt_coef(a)}", fontsize=fs, color=c_path,
            ha="center",
            bbox=dict(boxstyle="round,pad=0.18", facecolor="white",
                      edgecolor="#E5E7EB", alpha=0.96), zorder=5)
    ax.text(6.85, 3.55, f"b = {_fmt_coef(b)}", fontsize=fs, color=c_path,
            ha="center",
            bbox=dict(boxstyle="round,pad=0.18", facecolor="white",
                      edgecolor="#E5E7EB", alpha=0.96), zorder=5)
    ax.text(5.0, 1.15, f"c′ = {_fmt_coef(cp)}    ACME = {_fmt_coef(acme)}",
            fontsize=fs, color=c_direct, ha="center",
            bbox=dict(boxstyle="round,pad=0.28", facecolor="#FFF5F5",
                      edgecolor="#FECACA", alpha=0.96), zorder=5)

    title = f"{path_id}: {label}"
    if len(title) > 64:
        title = title[:61] + "…"
    ax.set_title(title, fontsize=fs, color="#111111", fontweight="normal", pad=8)
    fig.tight_layout()
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in path_id)
    wrote = cfg.save_plot(f"mediation_{safe}", fig, width=8.4, height=4.2)
    plt.close(fig)
    if wrote:
        print(f"  mediation_{safe}.png saved")


def resolve_vector(name: str, frame: pd.DataFrame,
                   extras: Optional[Dict[str, np.ndarray]] = None) -> Optional[np.ndarray]:
    """从 frame / extras 解析变量向量。"""
    extras = extras or {}
    key = str(name).strip()
    # 令牌
    low = key.lower().replace(" ", "").replace("_", "")
    outcome = str(getattr(cfg, "GROUP_SOURCE_COL", "") or "").strip()
    if low in ("group", "y", "label") or (outcome and key == outcome):
        if outcome and outcome in frame.columns:
            return _as_num(frame[outcome])
        if "Group" in frame.columns:
            return _as_num(frame["Group"])
        if "__label__" in extras:
            return np.asarray(extras["__label__"], dtype=float)
    if low == "radscore" and "RadScore" in extras:
        return np.asarray(extras["RadScore"], dtype=float)
    if low == "clinicalscore" and "ClinicalScore" in extras:
        return np.asarray(extras["ClinicalScore"], dtype=float)
    if key in frame.columns:
        return _as_num(frame[key])
    # 大小写不敏感
    cmap = {c.lower().replace(" ", ""): c for c in frame.columns}
    if low in cmap:
        return _as_num(frame[cmap[low]])
    if key in extras:
        return np.asarray(extras[key], dtype=float)
    return None


def run_mediation_paths(
    frame: pd.DataFrame,
    paths: Optional[List[dict]] = None,
    extras: Optional[Dict[str, np.ndarray]] = None,
    n_boot: Optional[int] = None,
    seed: Optional[int] = None,
) -> Dict[str, Any]:
    """
    frame: 含临床列与 Group/Recurrence 等
    extras: RadScore, ClinicalScore, __label__
    """
    from modules.utils import dry_run as _dry
    print("═══ MEDIATION (exploratory) ═══")
    paths = paths if paths is not None else list(getattr(cfg, "MEDIATION_PATHS", []))
    n_boot = int(
        n_boot
        if n_boot is not None
        else (
            getattr(cfg, "BOOTSTRAP_N", None)
            or getattr(cfg, "MEDIATION_BOOTSTRAP", None)
            or 1000
        )
    )
    seed = int(seed if seed is not None else cfg.SEED)
    if _dry.exit_after_plan("mediation", [
        f"n_paths={len(paths)} n_boot={n_boot}",
        "bootstrap mediation (skipped)",
    ]):
        return {"paths": [], "dry_run": True}
    extras = extras or {}

    # 清除旧 mediation_M*.png，避免 panel 拼入过期路径图
    try:
        import os
        import re as _re
        png_dir = getattr(cfg, "PNG_DIR", None) or os.path.join(
            getattr(cfg, "OUT_DIR", "") or ".", "PNG"
        )
        if png_dir and os.path.isdir(png_dir):
            for name in os.listdir(png_dir):
                if _re.match(r"mediation_M\d+.*\.png$", name, _re.I):
                    try:
                        os.remove(os.path.join(png_dir, name))
                    except OSError:
                        pass
            print(f"  cleared old mediation_M*.png in {png_dir}")
    except Exception as e:
        print(f"  ⚠ clear old mediation plots: {e}")

    rows = []
    details = {}
    for p in paths:
        pid = str(p.get("id", "path"))
        xlab, mspec, ylab = p.get("x"), p.get("m"), p.get("y")
        x = resolve_vector(str(xlab), frame, extras)
        y = resolve_vector(str(ylab), frame, extras)
        m_list = mspec if isinstance(mspec, (list, tuple)) else [mspec]
        for mname in m_list:
            mid = pid if len(m_list) == 1 else f"{pid}_{mname}"
            m = resolve_vector(str(mname), frame, extras)
            if x is None or m is None or y is None:
                print(f"  ⚠ {mid}: missing variable x={xlab!r} m={mname!r} y={ylab!r} — skip")
                continue
            # 对齐长度
            n = min(len(x), len(m), len(y))
            res = bootstrap_mediation(x[:n], m[:n], y[:n], n_boot=n_boot, seed=seed)
            if res is None:
                print(f"  ⚠ {mid}: estimation failed — skip")
                continue
            res["id"] = mid
            res["label"] = f"{xlab} → {mname} → {ylab}"
            res["x"], res["m"], res["y"] = str(xlab), str(mname), str(ylab)
            details[mid] = res
            try:
                plot_mediation_path(res, mid, res["label"])
            except Exception as e:
                print(f"  ⚠ plot {mid}: {e}")
            rows.append({
                "Path": mid,
                "Label": res["label"],
                "n": res["n_used"],
                "a": res["a"],
                "b": res["b"],
                "c (total)": res["total"],
                "c' (ADE)": res["ADE"],
                "ACME": res["ACME"],
                "ACME 95% CI": f"{res['ACME_ci_low']:.4f}–{res['ACME_ci_high']:.4f}"
                if np.isfinite(res.get("ACME_ci_low", np.nan)) else "NA",
                "Prop. mediated": res["prop_mediated"],
            })
            print(f"  {mid}: ACME={res['ACME']:.4f}  ADE={res['ADE']:.4f}  "
                  f"total={res['total']:.4f}  n={res['n_used']}")

    summary = pd.DataFrame(rows)
    if len(summary):
        print(f"  mediation paths computed: {len(summary)} (no csv)")
    return {
        "summary": summary,
        "details": details,
        "note": (
            "Exploratory product-of-coefficients mediation with bootstrap CIs on the "
            "development set. Binary outcomes use logistic models; continuous mediators "
            "use linear models. Results are associational and do not identify causal effects."
        ),
        "n_boot": n_boot,
    }
