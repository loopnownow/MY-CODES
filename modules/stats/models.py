"""
nomogram.py — 列线图 (Nomogram) 构建
═══════════════════════════════════════════════════════════════════
功能：将 RadScore 与临床特征合并，拟合逻辑回归模型，绘制 Nomogram
      可视化，并计算训练集和测试集的 AUC。

实现要点：
    1. 用 statsmodels 拟合标准 logistic 回归（logit + MLE）；
    2. 对每个变量，在其观测值范围 [min, max] 内，按 |β| 成比例
       换算为 0–100 分的得分尺（最大 |β×range| 的变量记为 100 分）；
    3. 总分 = 各变量得分之和，通过 sigmoid 精确换算为预测概率。
    本实现仅支持线性预测器，不处理样条等非线性项。
═══════════════════════════════════════════════════════════════════
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from sklearn.metrics import roc_auc_score
import statsmodels.api as sm

from modules import config as cfg

# ═════════════════════════════════════════════════════════════════
# §1  顶层可调参数
# ═════════════════════════════════════════════════════════════════
NOMOGRAM_POINTS_MAX = 100     # 得分尺满分
NOMOGRAM_DPI = 300            # 输出分辨率（投稿级）
NOMOGRAM_FIG_WIDTH = 11.5     # 英寸
# 变量显示名与单位（便于临床解读；未列出的保留原名）
FEATURE_DISPLAY: Dict[str, str] = {
    "RadScore": "RadScore",
    "age": "Age (years)",
    "diameter": "Diameter (mm)",
    "max_dia": "Max diameter (mm)",
    "subtype": "Subtype",
    "gender": "Gender",
    "smoking": "Smoking",
    "spiculation": "Spiculation",
    "lobulation": "Lobulation",
    "pleural_retraction": "Pleural retraction",
    "previous_LCH": "Previous LCH",
    "family_LCH": "Family LCH",
    "Fazkas": "Fazekas",
    "Lesion Volume": "Lesion volume",
    "Number of Lesions": "Number of lesions",
}

# 风险分层（用于概率轴着色与解读）
RISK_BANDS = [
    (0.00, 0.30, "#2E7D32", "Low"),       # 绿
    (0.30, 0.70, "#F9A825", "Intermediate"),  # 琥珀
    (0.70, 1.00, "#C62828", "High"),      # 红
]


@dataclass
class NomogramResult:
    model: sm.GLM                       # 拟合的逻辑回归模型
    train_prob: np.ndarray              # 训练集预测概率
    test_prob: Optional[np.ndarray]     # 测试集预测概率（若提供）
    features: List[str]                 # 模型中使用的特征（RadScore + 临床特征）
    df_train: pd.DataFrame
    df_test: Optional[pd.DataFrame]
    points_table: Optional[pd.DataFrame] = None  # 各变量得分换算表
    lp0: float = 0.0                    # 最低风险线性预测子
    scale: float = 1.0                  # 点数 ↔ LP 换算系数
    train_auc: float = np.nan
    test_auc: float = np.nan


def build_nomogram(train_label: np.ndarray, radscore_train: Optional[np.ndarray],
                    train_clin: pd.DataFrame, clin_feat: List[str],
                    radscore_test: Optional[np.ndarray] = None,
                    test_clin: Optional[pd.DataFrame] = None,
                    test_label: Optional[np.ndarray] = None,
                    score_train: Optional[Dict[str, np.ndarray]] = None,
                    score_test: Optional[Dict[str, np.ndarray]] = None) -> NomogramResult:
    """拟合临床特征（可选 + 一个或多个 RadScore）的逻辑回归，绘制点数换算列线图。

    score_train / score_test 给出分类分数。都不传时，radscore_train 仍是单独的 RadScore 列。
    """
    from modules.utils import dry_run as _dry
    print("\n═══ NOMOGRAM ═══")
    if _dry.exit_after_plan("nomogram", [
        f"clin_feat={list(clin_feat)}",
        f"n_train={len(np.asarray(train_label))}",
        "fit logit + plot (skipped)",
    ]):
        return NomogramResult(
            model=None,  # type: ignore[arg-type]
            train_prob=np.asarray([], dtype=float),
            test_prob=None,
            features=[],
            df_train=pd.DataFrame(),
            df_test=None,
        )

    # ── Step 1: 筛选可用特征并构建训练数据 ──
    if score_train is None:
        score_train = {}
        if radscore_train is not None:
            score_train["RadScore"] = np.asarray(radscore_train, dtype=float)
    else:
        score_train = {
            str(k): np.asarray(v, dtype=float)
            for k, v in score_train.items() if v is not None
        }
    if score_test is None:
        score_test = {}
        if radscore_test is not None and "RadScore" in score_train:
            score_test["RadScore"] = np.asarray(radscore_test, dtype=float)
    else:
        score_test = {
            str(k): np.asarray(v, dtype=float)
            for k, v in score_test.items() if v is not None
        }
    available = [f for f in clin_feat if f in train_clin.columns]
    n = len(np.asarray(train_label))
    df_tr = pd.DataFrame(index=range(n))
    for name, vec in score_train.items():
        arr = np.asarray(vec, dtype=float).ravel()
        if len(arr) != n:
            print(f"  ⚠ nomogram skip {name}: length {len(arr)} != {n}")
            continue
        df_tr[name] = arr
    for f in available:
        df_tr[f] = pd.to_numeric(train_clin[f].reset_index(drop=True), errors="coerce")
    df_tr["y"] = np.asarray(train_label)
    df_tr = df_tr.dropna()
    keep_feat: List[str] = []
    for name in score_train:
        if name in df_tr.columns and df_tr[name].nunique(dropna=True) >= 2:
            keep_feat.append(name)
    for f in available:
        s = df_tr[f]
        if s.nunique(dropna=True) >= 2:
            keep_feat.append(f)
        else:
            print(f"  ⚠ drop zero-variance clin after complete-case: {f}")
    all_feat = keep_feat
    if not all_feat:
        raise ValueError("nomogram: no usable features (need clinical columns and/or RadScore)")
    n_score = sum(1 for f in all_feat if f in score_train)
    n_clin = len(all_feat) - n_score
    if n_score == 1 and "RadScore" in all_feat:
        print(f"  Features: {len(all_feat)} (RadScore + {n_clin} clin)")
    elif n_score:
        shown = ", ".join(f for f in all_feat if f in score_train)
        print(f"  Features: {len(all_feat)} ({shown} + {n_clin} clin)")
    else:
        print(f"  Features: {len(all_feat)} (clinical only, no RadScore)")

    # ── Step 2: 拟合逻辑回归（logit + MLE） ──
    X = sm.add_constant(df_tr[all_feat], has_constant="add")
    model = sm.GLM(df_tr["y"], X, family=sm.families.Binomial()).fit()
    design_cols = list(model.params.index)

    # ── Step 3: 绘制并保存点数换算列线图 ──
    plot_feat = [c for c in all_feat if c in design_cols]
    points_table, lp0, scale = _plot_nomogram(model, df_tr[plot_feat], plot_feat)

    # ── Step 4: 评估模型性能 ──
    X_aligned = X.reindex(columns=design_cols, fill_value=0.0)
    train_prob = np.asarray(model.predict(X_aligned), dtype=float)
    train_auc = float(roc_auc_score(df_tr["y"], train_prob))
    print(f"  Train AUC: {train_auc:.3f}")

    # ── Step 5: 测试集验证（仅当提供测试数据时） ──
    test_prob, df_te, test_auc = None, None, np.nan
    score_kept = [f for f in plot_feat if f in score_train]
    want_test = test_label is not None and test_clin is not None
    if want_test and score_kept and any(name not in score_test for name in score_kept):
        want_test = False
    if want_test:
        n_te = len(np.asarray(test_label))
        df_te = pd.DataFrame(index=range(n_te))
        for name in score_kept:
            arr = np.asarray(score_test[name], dtype=float).ravel()
            if len(arr) != n_te:
                print(f"  ⚠ nomogram test skip: {name} length {len(arr)} != {n_te}")
                df_te = None
                want_test = False
                break
            df_te[name] = arr
    if want_test and df_te is not None:
        for f in available:
            df_te[f] = pd.to_numeric(test_clin[f].reset_index(drop=True), errors="coerce")
        df_te["y"] = np.asarray(test_label)
        need = [c for c in plot_feat if c in df_te.columns]
        df_te = df_te.dropna(subset=need + ["y"])
        if len(df_te) == 0:
            print("  ⚠ Test complete-case N=0 — skip test AUC")
        else:
            Xte = sm.add_constant(df_te[plot_feat], has_constant="add")
            Xte = Xte.reindex(columns=design_cols, fill_value=0.0)
            test_prob = np.asarray(model.predict(Xte), dtype=float)
            if df_te["y"].nunique() >= 2:
                test_auc = float(roc_auc_score(df_te["y"], test_prob))
                print(f"  Test AUC:  {test_auc:.3f}")
            else:
                print("  ⚠ Test has single class — skip test AUC")

    return NomogramResult(
        model=model, train_prob=train_prob, test_prob=test_prob,
        features=plot_feat, df_train=df_tr, df_test=df_te,
        points_table=points_table, lp0=lp0, scale=scale,
        train_auc=train_auc, test_auc=test_auc,
    )


# ═════════════════════════════════════════════════════════════════
# §2  内部工具
# ═════════════════════════════════════════════════════════════════
def _display_name(feat: str) -> str:
    return FEATURE_DISPLAY.get(feat, feat.replace("_", " ").title())


def _level_map(feat: str) -> Dict[float, str]:
    """code → 原始类别字符串（data_io.CLIN_LEVEL_MAPS）。"""
    try:
        from modules.stats.data_io import CLIN_LEVEL_MAPS
    except Exception:
        return {}
    raw = (CLIN_LEVEL_MAPS or {}).get(feat) or {}
    out: Dict[float, str] = {}
    for k, lab in raw.items():
        try:
            out[float(k)] = str(lab).strip()
        except (TypeError, ValueError):
            continue
    return out


def _lookup_level(feat: str, v: float) -> Optional[str]:
    m = _level_map(feat)
    if not m:
        return None
    fv = float(v)
    if fv in m:
        return m[fv]
    for k, lab in m.items():
        if abs(k - fv) < 1e-9:
            return lab
    return None


def _wrap_cat_label(s: str, width: int = 22) -> str:
    s = " ".join(str(s).split())
    if len(s) <= width:
        return s
    parts: List[str] = []
    rest = s
    while rest and len(parts) < 2:
        if len(rest) <= width:
            parts.append(rest)
            rest = ""
            break
        cut = rest.rfind(" ", 0, width + 1)
        if cut < 8:
            cut = width
        parts.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    if rest:
        last = (parts[-1] + " " + rest).strip() if parts else rest
        parts[-1] = last if len(last) <= width + 10 else last[: width + 8] + "…"
    return "\n".join(p for p in parts if p)


def _tick_label(feat: str, v: float, discrete: bool) -> str:
    lab = _lookup_level(feat, v)
    if lab:
        return _wrap_cat_label(lab)
    return _fmt_val(v, discrete=discrete)


def _is_discrete(series: pd.Series, feat: str = "", max_unique: int = 8) -> bool:
    """判断是否按离散刻度绘制（分类/序数，或整数水平较少）。"""
    nuniq = int(series.nunique(dropna=True))
    if feat and _level_map(feat) and 2 <= nuniq <= max(max_unique, 12):
        return True
    if nuniq <= max_unique:
        vals = series.dropna().unique()
        if len(vals) and np.allclose(vals, np.round(vals)):
            return True
    return False


def _pretty_ticks(lo: float, hi: float, n_target: int = 6) -> np.ndarray:
    """在 [lo, hi] 上生成美观的连续刻度（含两端）。"""
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        return np.array([lo if np.isfinite(lo) else 0.0])
    span = hi - lo
    # 量级自适应步长
    raw = span / max(n_target - 1, 1)
    exp = np.floor(np.log10(raw)) if raw > 0 else 0
    base = 10 ** exp
    for mult in (1, 2, 2.5, 5, 10):
        step = mult * base
        if span / step <= n_target + 1:
            break
    else:
        step = raw
    start = np.floor(lo / step) * step
    end = np.ceil(hi / step) * step
    ticks = np.arange(start, end + step * 0.5, step)
    ticks = ticks[(ticks >= lo - 1e-9) & (ticks <= hi + 1e-9)]
    if len(ticks) == 0 or abs(ticks[0] - lo) > 1e-9:
        ticks = np.unique(np.concatenate([[lo], ticks]))
    if abs(ticks[-1] - hi) > 1e-9:
        ticks = np.unique(np.concatenate([ticks, [hi]]))
    return ticks


def _fmt_val(v: float, discrete: bool = False) -> str:
    if discrete or abs(v - round(v)) < 1e-9:
        return f"{int(round(v))}"
    av = abs(v)
    if av >= 100:
        return f"{v:.0f}"
    if av >= 10:
        return f"{v:.1f}"
    if av >= 1:
        return f"{v:.2f}"
    return f"{v:.2f}"



def _compute_scales(
    model: sm.GLM, X_feat: pd.DataFrame, all_feat: List[str]
) -> Tuple[Dict[str, Tuple[float, float]], Dict[str, float], float, float, Dict[str, float]]:
    """
    计算点数换算参数。

    返回
    ----
    ranges : {feat: (lo, hi)}
    beta_effect : {feat: |β|×range}  用于排序/报告
    lp0 : 所有变量取最低风险端点时的线性预测子
    scale : points = (LP - lp0) * scale；满分变量 effect → 100 分
    min_contrib : {feat: min(βx)}  各变量最低风险贡献
    """
    betas = model.params
    intercept = float(betas.get("const", 0.0))
    ranges: Dict[str, Tuple[float, float]] = {}
    min_contrib: Dict[str, float] = {}
    max_contrib: Dict[str, float] = {}
    beta_effect: Dict[str, float] = {}

    for f in all_feat:
        lo = float(X_feat[f].min())
        hi = float(X_feat[f].max())
        ranges[f] = (lo, hi)
        b = float(betas[f])
        c_lo, c_hi = b * lo, b * hi
        min_contrib[f] = min(c_lo, c_hi)
        max_contrib[f] = max(c_lo, c_hi)
        beta_effect[f] = max_contrib[f] - min_contrib[f]

    max_effect = max(beta_effect.values()) if beta_effect else 1.0
    if max_effect <= 0:
        max_effect = 1.0
    scale = NOMOGRAM_POINTS_MAX / max_effect
    lp0 = intercept + sum(min_contrib.values())
    return ranges, beta_effect, lp0, scale, min_contrib


def _points_of(x: float, feat: str, betas, min_contrib: Dict[str, float], scale: float) -> float:
    b = float(betas[feat])
    return (b * x - min_contrib[feat]) * scale


def _total_points_to_prob(tp: float, lp0: float, scale: float) -> float:
    lp = lp0 + tp / scale if scale != 0 else lp0
    # 数值稳定 sigmoid
    if lp >= 0:
        z = np.exp(-lp)
        return float(1.0 / (1.0 + z))
    z = np.exp(lp)
    return float(z / (1.0 + z))


def _prob_to_total_points(p: float, lp0: float, scale: float) -> float:
    p = float(np.clip(p, 1e-6, 1 - 1e-6))
    lp = np.log(p / (1 - p))
    return float((lp - lp0) * scale)


def _build_points_table(
    all_feat: List[str],
    X_feat: pd.DataFrame,
    ranges: Dict[str, Tuple[float, float]],
    betas,
    min_contrib: Dict[str, float],
    scale: float,
) -> pd.DataFrame:
    """生成便于查阅的得分换算表（离散：全部水平；连续：美观刻度）。"""
    rows = []
    for f in all_feat:
        lo, hi = ranges[f]
        discrete = _is_discrete(X_feat[f], feat=f)
        if discrete:
            vals = np.sort(X_feat[f].dropna().unique())
        else:
            vals = _pretty_ticks(lo, hi, n_target=8)
        for v in vals:
            fv = float(v)
            pts = _points_of(fv, f, betas, min_contrib, scale)
            lab = _lookup_level(f, fv)
            rows.append({
                "Feature": f,
                "Display": _display_name(f),
                "Value": fv,
                "Label": lab if lab else _fmt_val(fv, discrete=discrete),
                "Points": round(float(pts), 2),
            })
    return pd.DataFrame(rows)


# ═════════════════════════════════════════════════════════════════
# §3  绘图
# ═════════════════════════════════════════════════════════════════
def _thin_labels(xs: List[float], labs: List[str], min_dx: float) -> List[str]:
    """按 x 间距抽稀标签，始终保留首尾非空标签。"""
    if not xs:
        return labs
    out = [""] * len(labs)
    last = -1e18
    # 先放端点
    for i in (0, len(xs) - 1):
        if labs[i]:
            out[i] = labs[i]
            last = xs[i] if i == 0 else last
    # 端点强制保留
    out[0] = labs[0]
    out[-1] = labs[-1]
    last = xs[0]
    for i in range(1, len(xs) - 1):
        if not labs[i]:
            continue
        if xs[i] - last >= min_dx and (xs[-1] - xs[i]) >= min_dx * 0.7:
            out[i] = labs[i]
            last = xs[i]
    return out


def _plot_nomogram(
    model: sm.GLM, X_feat: pd.DataFrame, all_feat: List[str]
) -> Tuple[pd.DataFrame, float, float]:
    """
    绘制发表级点数换算列线图：
      - 顶部 Points 参考尺 (0–100)
      - 各变量得分尺（中间刻度 + 数值标注，方向已按风险对齐）
      - Total Points + Predicted Probability（精确 logit 映射）
      - 风险分层色带（无 How to use / Interpret 注释）
    """
    betas = model.params
    ranges, beta_effect, lp0, scale, min_contrib = _compute_scales(model, X_feat, all_feat)
    points_table = _build_points_table(all_feat, X_feat, ranges, betas, min_contrib, scale)

    n_vars = len(all_feat)
    # 紧凑布局：每行 ~0.85"，底部至风险图例为止（不含 howto）
    fig_h = 1.15 + 0.72 * (n_vars + 4) + 0.45
    fig, ax = plt.subplots(figsize=(NOMOGRAM_FIG_WIDTH, fig_h), facecolor="white")
    ax.set_facecolor("white")

    total_max = sum(beta_effect[f] * scale for f in all_feat)
    canvas = 100.0

    # Y 坐标（从上到下，间距均匀）
    y_title = n_vars + 2.85
    y_points = n_vars + 1.95
    y_vars = {f: float(n_vars - i) for i, f in enumerate(all_feat)}
    y_total = -0.05
    y_prob = -1.05
    y_legend = -1.75

    C_AXIS = "#1a1a1a"
    C_TICK = "#333333"
    C_VAR = "#1565C0"
    C_POINTS = "#212121"
    C_TOTAL = "#37474F"
    C_MUTED = "#616161"
    C_GRID = "#ECEFF1"

    def _draw_axis(y: float, x0: float, x1: float, color: str, lw: float = 1.6) -> None:
        ax.plot([x0, x1], [y, y], color=color, lw=lw, solid_capstyle="butt", zorder=2)

    def _draw_ticks(y: float, xs, labels, color: str, label_above: bool = True,
                    fontsize: float = 7.5, stagger: bool = False) -> None:
        tick_h = 0.12
        for i, (x, lab) in enumerate(zip(xs, labels)):
            ax.plot([x, x], [y - tick_h, y + tick_h], color=color, lw=0.85, zorder=3)
            if not lab:
                continue
            if label_above:
                dy = 0.26 + (0.18 if stagger and i % 2 else 0.0)
                va = "bottom"
            else:
                dy = -0.28 - (0.18 if stagger and i % 2 else 0.0)
                va = "top"
            ax.text(x, y + dy, lab, ha="center", va=va, fontsize=fontsize,
                    color=C_TICK, clip_on=False)

    def _left_label(y: float, text: str, fontsize: float = 9.2) -> None:
        ax.text(-2.8, y, text, ha="right", va="center", fontsize=fontsize,
                fontweight="bold", color=C_AXIS, clip_on=False)

    # ── 标题（不加粗）──
    ax.text(canvas / 2, y_title + 0.12,
            "Nomogram for Individualized Risk Prediction",
            ha="center", va="center", fontsize=13, fontweight="normal", color=C_AXIS)

    # ── Points 参考尺 ──
    _left_label(y_points, "Points")
    _draw_axis(y_points, 0, canvas, C_POINTS, lw=1.9)
    pts_ticks = np.linspace(0, 100, 11)
    _draw_ticks(y_points, pts_ticks, [f"{t:.0f}" for t in pts_ticks], C_POINTS,
                label_above=True, fontsize=8)
    for t in np.linspace(0, 100, 21):
        if t not in pts_ticks:
            ax.plot([t, t], [y_points - 0.06, y_points + 0.06], color=C_POINTS, lw=0.45)
    # 读分辅助虚线（仅覆盖变量区）
    y_var_min = min(y_vars.values())
    for t in pts_ticks:
        ax.plot([t, t], [y_points - 0.28, y_var_min - 0.28],
                color=C_GRID, lw=0.55, zorder=0, linestyle=":")

    # ── 各变量得分尺 ──
    for f in all_feat:
        y = y_vars[f]
        lo, hi = ranges[f]
        discrete = _is_discrete(X_feat[f], feat=f)
        b = float(betas[f])
        max_pts = beta_effect[f] * scale

        _left_label(y, _display_name(f))
        _draw_axis(y, 0, max_pts, C_VAR, lw=2.0)
        ax.plot(0, y, "o", color=C_VAR, ms=4.2, zorder=4,
                markeredgecolor="white", markeredgewidth=0.5)
        ax.plot(max_pts, y, "o", color=C_VAR, ms=4.2, zorder=4,
                markeredgecolor="white", markeredgewidth=0.5)

        # 刻度候选：离散取全部水平；连续按轴长度自适应数量
        if discrete:
            vals = np.sort(X_feat[f].dropna().unique())
        else:
            n_tgt = 5 if max_pts < 25 else (6 if max_pts < 50 else 8)
            vals = _pretty_ticks(lo, hi, n_target=n_tgt)

        xs, labs = [], []
        named = False
        for v in vals:
            p = float(np.clip(_points_of(float(v), f, betas, min_contrib, scale), 0, max_pts))
            xs.append(p)
            lab = _tick_label(f, float(v), discrete=discrete)
            named = named or bool(_lookup_level(f, float(v)))
            labs.append(lab)

        # 分类字符串：尽量保留全部水平；连续短轴才抽稀
        if named:
            stagger = True
            fs = 6.2
        elif max_pts < 6 and len(xs) > 2:
            labs = [labs[0]] + [""] * (len(labs) - 2) + [labs[-1]]
            stagger = False
            fs = 7.0
        else:
            min_dx = 4.0 if max_pts >= 20 else (3.0 if max_pts >= 10 else 2.2)
            labs = _thin_labels(xs, labs, min_dx=min_dx)
            stagger = max_pts < 30 and sum(1 for L in labs if L) >= 5
            fs = 7.0
        _draw_ticks(y, xs, labs, C_VAR, label_above=True, fontsize=fs, stagger=stagger)


    # ── Total Points ──
    def tp_to_x(tp: float) -> float:
        if total_max <= 0:
            return 0.0
        return float(np.clip(tp / total_max * canvas, 0, canvas))

    _left_label(y_total, "Total Points")
    _draw_axis(y_total, 0, canvas, C_TOTAL, lw=1.9)
    if total_max <= 100:
        step = 10
    elif total_max <= 200:
        step = 20
    elif total_max <= 400:
        step = 50
    else:
        step = 100
    tp_ticks = list(np.arange(0, total_max + step * 0.5, step))
    if tp_ticks[-1] < total_max - 1e-6:
        tp_ticks.append(total_max)
    _draw_ticks(
        y_total, [tp_to_x(t) for t in tp_ticks], [f"{t:.0f}" for t in tp_ticks],
        C_TOTAL, label_above=False, fontsize=7.5,
    )

    # ── Predicted Probability（精确 logit 映射）──
    _left_label(y_prob, "Predicted\nProbability", fontsize=8.8)
    p_at_0 = _total_points_to_prob(0, lp0, scale)
    p_at_max = _total_points_to_prob(total_max, lp0, scale)
    p_lo, p_hi = min(p_at_0, p_at_max), max(p_at_0, p_at_max)

    band_y0, band_y1 = y_prob - 0.20, y_prob + 0.20
    for blo, bhi, color, _name in RISK_BANDS:
        a, b_ = max(blo, p_lo), min(bhi, p_hi)
        if b_ <= a:
            continue
        x0 = tp_to_x(_prob_to_total_points(a, lp0, scale))
        x1 = tp_to_x(_prob_to_total_points(b_, lp0, scale))
        if x1 < x0:
            x0, x1 = x1, x0
        ax.add_patch(Rectangle(
            (x0, band_y0), max(x1 - x0, 0.01), band_y1 - band_y0,
            facecolor=color, edgecolor="none", alpha=0.20, zorder=1,
        ))
    _draw_axis(y_prob, 0, canvas, C_AXIS, lw=1.9)

    # 概率刻度：优先临床阈值 (10/30/50/70)，再补两端
    preferred = [0.10, 0.20, 0.30, 0.50, 0.70, 0.80, 0.90]
    must = [0.30, 0.50, 0.70]  # 风险分层边界尽量保留
    cand = [p for p in preferred if p_lo - 1e-6 <= p <= p_hi + 1e-6]
    xs_p, labs_p, must_idx = [], [], set()
    for p in sorted(set(np.round(cand, 4))):
        tp = _prob_to_total_points(float(p), lp0, scale)
        if -1e-6 <= tp <= total_max + 1e-6:
            if any(abs(p - m) < 1e-9 for m in must):
                must_idx.add(len(xs_p))
            xs_p.append(tp_to_x(tp))
            labs_p.append(f"{p:.0%}")
    # 抽稀，但强制保留 must 与端点
    thinned = _thin_labels(xs_p, labs_p, min_dx=6.0)
    for i in must_idx:
        thinned[i] = labs_p[i]
    if thinned:
        thinned[0] = labs_p[0]
        thinned[-1] = labs_p[-1]
    _draw_ticks(y_prob, xs_p, thinned, C_AXIS, label_above=False, fontsize=7.2)

    # 风险分层图例（紧贴概率轴下方，手动定位）
    for i, (lo, hi, c, name) in enumerate(RISK_BANDS):
        x = 18 + i * 28
        ax.plot([x, x + 4], [y_legend, y_legend], color=c, lw=7, alpha=0.55,
                solid_capstyle="butt", clip_on=False)
        ax.text(x + 5.2, y_legend, f"{name}  ({lo:.0%}–{hi:.0%})",
                ha="left", va="center", fontsize=7.5, color=C_TICK, clip_on=False)

    ax.set_xlim(-20, canvas + 24)
    ax.set_ylim(y_legend - 0.55, y_title + 0.40)
    ax.axis("off")

    if not cfg.figures_on():
        plt.close(fig)
        print("  nomogram: DO_FIGURE_PANELS=false — 不写 PNG/TIFF/PDF", flush=True)
        return points_table, lp0, scale

    cfg.ensure_output_dirs()
    png_path = f"{cfg.PNG_DIR}/nomogram.png"
    save_kw = dict(dpi=NOMOGRAM_DPI, bbox_inches="tight",
                   facecolor="white", edgecolor="none", pad_inches=0.20)
    fig.savefig(png_path, **save_kw)
    print(f"  → {png_path}", flush=True)

    # TIFF / PDF 投稿格式（PNG 始终写）
    if cfg.want_tiff():
        tiff_path = f"{cfg.OUT_DIR}/nomogram.tiff"
        try:
            fig.savefig(
                tiff_path, dpi=200, bbox_inches="tight", format="tiff",
                facecolor="white", edgecolor="none", pad_inches=0.20,
            )
            print(f"  → {tiff_path}", flush=True)
        except Exception as e:
            print(f"  ⚠ TIFF skipped: {e}", flush=True)
    if cfg.want_pdf():
        pdf_path = f"{cfg.OUT_DIR}/nomogram.pdf"
        try:
            fig.savefig(
                pdf_path, dpi=200, bbox_inches="tight", format="pdf",
                facecolor="white", edgecolor="none", pad_inches=0.20,
            )
            print(f"  → {pdf_path}", flush=True)
        except Exception as e:
            print(f"  ⚠ PDF skipped: {e}", flush=True)

    plt.close(fig)
    print("  nomogram.png saved (publication style)")
    print(f"  Points scale: 1 point = {1/scale:.4f} log-odds  |  LP0={lp0:.3f}")
    return points_table, lp0, scale
