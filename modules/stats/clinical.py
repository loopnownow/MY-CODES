"""
modules.stats.clinical — Table1 + 临床特征选择（单变量 → 多变量 logistic）
═══════════════════════════════════════════════════════════════════
功能：对临床特征执行完整的筛选流程：
    Step 1 — Table1 基线表（两组：正态性 → t / Mann-Whitney；≥3 组：ANOVA + Fisher LSD）
    Step 2 — 单变量 Logistic 回归（OR + 95%CI + p 值）
    Step 3 — 单因素 p + VIF 候选池全模型多因素表 + AIC stepwise（仅报告，不入模）
    Step 4 — Selected = FORCE_MODEL_FEATURES（手动优先）否则多因素 p < ROC_MV_P_THRESHOLD → ClinicalScore

当 fixed_features 不为 None 时（TEST / 外部验证层），跳过筛选，
强制使用给定特征集并仅拟合对应模型。

入口：pipeline 中 import modules.stats.clinical as clin_select
═══════════════════════════════════════════════════════════════════
"""

from __future__ import annotations
import re
from dataclasses import dataclass
from typing import List, Optional, Sequence

import numpy as np
import pandas as pd
from scipy import stats
import statsmodels.api as sm

from modules import config as cfg

# ═════════════════════════════════════════════════════════════════
# §1  顶层可调参数（可在调用 select_clinical() 时覆盖）
# ═════════════════════════════════════════════════════════════════
DEFAULT_P_THRESHOLD = 0.1  # Table1 初筛 p 值阈值；调用时以 cfg.TABLE1_P_THRESHOLD 为准
MIN_GROUP_N_FOR_CONTINUOUS = 3                 # 连续变量最小组内样本量（低于此值跳过该变量）
MIN_UNIVARIATE_N = 10                          # 单变量回归最小样本量


@dataclass
class ClinSelectResult:
    features: List[str]                 # Selected：手动 FORCE_MODEL，否则多因素 p 默认
    uni_res: pd.DataFrame                # 单变量分析结果（可含 VIF 列）
    table1: pd.DataFrame                 # 基线描述表
    clinscore: Optional[np.ndarray]      # 最终模型的线性预测值 (log-odds)
    model: Optional[sm.GLM]              # 最终 statsmodels GLM（Selected 特征）
    mv_df: Optional[pd.DataFrame] = None  # 候选池全模型多因素表
    mv_note: Optional[str] = None         # 候选池表注（含 AIC stepwise）
    candidates: Optional[List[str]] = None       # 单因素筛后的候选池
    stepwise_features: Optional[List[str]] = None  # AIC stepwise 保留（仅报告）
    mv_selected: Optional[pd.DataFrame] = None  # Selected 终模型多因素表
    select_source: Optional[str] = None  # "manual" | "mv_p"
    firth_uni: Optional[pd.DataFrame] = None
    firth_mv: Optional[pd.DataFrame] = None


def _sig_stars(p: float) -> str:
    """显著性标注：*** p<0.001, ** p<0.01, * p<0.05。"""
    if p is None or (isinstance(p, float) and np.isnan(p)):
        return ""
    if p < 0.001:
        return "***"
    if p < 0.01:
        return "**"
    if p < 0.05:
        return "*"
    return ""


def _normalize_name(x: str) -> str:
    """归一化变量名用于模糊匹配（忽略大小写/首尾空格/标点）。"""
    s = str(x).strip().lower()
    s = re.sub(r"[^0-9a-z]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# ═════════════════════════════════════════════════════════════════
# Step 1: 构建 Table1 基线表
#   对每个变量自动判断类型：
#   - 分类变量（非数值 或 唯一值 ≤ 5）→ 卡方检验 或 Fisher 精确检验
#   - 连续变量 → Shapiro-Wilk 正态性检验
#     · 两组均正态 → t 检验，显示 mean±SD
#     · 非正态     → Mann-Whitney U 检验，显示 median [IQR]
# ═════════════════════════════════════════════════════════════════
def _chi2_monte_carlo(table: np.ndarray, n_resamples: int = 2000, seed: int = 66666) -> float:
    """Freeman-Halton-style Monte Carlo P for sparse RxC tables (fixed margins)."""
    table = np.asarray(table, dtype=int)
    obs = float(stats.chi2_contingency(table, correction=False)[0])
    rs = table.sum(axis=1)
    cs = table.sum(axis=0)
    row_ids = np.repeat(np.arange(len(rs)), rs)
    col_ids = np.repeat(np.arange(len(cs)), cs)
    rng = np.random.RandomState(seed)
    ge = 0
    n_ok = 0
    for i in range(int(n_resamples)):
        shuffled = col_ids.copy()
        rng.shuffle(shuffled)
        sim = np.zeros_like(table)
        for r, c in zip(row_ids, shuffled):
            sim[r, c] += 1
        try:
            chi = float(stats.chi2_contingency(sim, correction=False)[0])
        except Exception:
            continue
        n_ok += 1
        if chi >= obs:
            ge += 1
    if n_ok < 20:
        return float("nan")
    return float((ge + 1) / (n_ok + 1))


def _categorical_p(table_use: np.ndarray) -> float:
    """Pearson χ² if all expected ≥ 5; Fisher for 2×2 sparse; Monte Carlo otherwise."""
    chi2, p_chi, _dof, expected = stats.chi2_contingency(table_use, correction=False)
    sparse = np.any(np.asarray(expected) < 5)
    if not sparse:
        return float(p_chi)
    if table_use.shape == (2, 2):
        return float(stats.fisher_exact(table_use)[1])
    p_mc = _chi2_monte_carlo(table_use)
    return float(p_mc) if np.isfinite(p_mc) else float(p_chi)


def _level_label(col: str, code) -> str:
    """将编码后的分类水平还原为原文标签（如 Positive / male）。"""
    try:
        from modules.stats import data_io as _dio
        maps = getattr(_dio, "CLIN_LEVEL_MAPS", {}) or {}
    except Exception:
        maps = {}
    m = maps.get(col) or {}
    try:
        key = float(code)
    except (TypeError, ValueError):
        key = code
    if key in m:
        return str(m[key])
    # float 键兼容 int 编码
    if isinstance(key, float) and key == int(key) and float(int(key)) in m:
        return str(m[float(int(key))])
    s = str(code).strip()
    if s.endswith(".0") and s[:-2].lstrip("-").isdigit():
        s = s[:-2]
    return s


def _build_table1(clin: pd.DataFrame, idx0: np.ndarray, idx1: np.ndarray,
                   lbl0: str, lbl1: str) -> pd.DataFrame:
    """
    Table1 列名：Variable | {阳性名}={n1} | {阴性名}={n0} | P value | Significance

    分类变量：头行（变量名 + 总体 P）+ 各水平子行 n (xx.x%)；
    连续变量：mean±SD 或 median [IQR]。
    """
    n0, n1 = len(idx0), len(idx1)
    col_pos = f"{lbl1}={n1}"   # 阳性在前
    col_neg = f"{lbl0}={n0}"

    # 因子编码后的分类列登记在 data_io.CLIN_LEVEL_MAPS（如 Histology 6 水平）
    try:
        from modules.stats import data_io as _dio
        _level_maps = getattr(_dio, "CLIN_LEVEL_MAPS", {}) or {}
    except Exception:
        _level_maps = {}

    def _is_categorical_col(col: str, series: pd.Series) -> bool:
        """分类判定：优先 data_io.CLIN_VAR_TYPES；否则与 data_io.classify_clin_series 一致。

        约定：英文字符串→计数；原始整列数字→计量；罗马数字等有序 token→计数；
        CLIN_FORCE_CATEGORICAL 可把数字列改回计数。
        """
        try:
            from modules.stats import data_io as _dio
            var_types = getattr(_dio, "CLIN_VAR_TYPES", {}) or {}
            if col in var_types:
                return var_types[col] == "categorical"
            if hasattr(_dio, "classify_clin_series"):
                return _dio.classify_clin_series(col, series, level_maps=_level_maps) == "categorical"
        except Exception as e:
            print(f"  ⚠ classify_clin fallback for {col}: {e}", flush=True)
        # 兜底：文本 / 已登记 level map → 分类；其余数值 → 计量
        if col in _level_maps:
            return True
        return not pd.api.types.is_numeric_dtype(series)

    rows = []
    for v in clin.columns:
        x = clin[v]
        is_categorical = _is_categorical_col(str(v), x)

        if is_categorical:
            x0 = x.iloc[idx0]
            x1 = x.iloc[idx1]
            N0 = int(x0.notna().sum())
            N1 = int(x1.notna().sum())
            if N0 == 0 or N1 == 0:
                continue
            # 稳定水平顺序：数值按 code 排序，文本按标签
            levels_raw = list(pd.unique(x.dropna()))
            try:
                levels_raw = sorted(levels_raw, key=lambda z: float(z))
            except (TypeError, ValueError):
                levels_raw = sorted(levels_raw, key=lambda z: str(z))
            if len(levels_raw) < 2:
                continue

            # 列联表：各水平 × 两组 → 总体 P
            table = []
            level_counts = []
            for lev in levels_raw:
                c0 = int((x0 == lev).sum())
                c1 = int((x1 == lev).sum())
                table.append([c0, c1])
                level_counts.append((lev, c0, c1))
            table = np.asarray(table, dtype=int)
            # 去掉全零水平
            keep = table.sum(axis=1) > 0
            table_use = table[keep]
            if table_use.shape[0] < 2 or table_use.shape[1] < 2:
                continue
            try:
                p = _categorical_p(table_use)
            except Exception:
                p = float("nan")

            p_str = (
                "<0.001" if (isinstance(p, float) and np.isfinite(p) and p < 0.001)
                else (f"{p:.3f}" if np.isfinite(p) else "NA")
            )
            # 头行：变量名 + P
            rows.append({
                "Variable": v,
                col_pos: "",
                col_neg: "",
                "P value": p_str,
                "_p_num": p if np.isfinite(p) else np.nan,
                "_is_level": False,
            })
            # 子行：各水平 n (%)
            for lev, c0, c1 in level_counts:
                lab = _level_label(v, lev)
                d0 = f"{c0} ({100 * c0 / N0:.1f}%)" if N0 > 0 else "0 (0.0%)"
                d1 = f"{c1} ({100 * c1 / N1:.1f}%)" if N1 > 0 else "0 (0.0%)"
                rows.append({
                    "Variable": f"  {lab}",
                    col_pos: d1,
                    col_neg: d0,
                    "P value": "",
                    "_p_num": np.nan,
                    "_is_level": True,
                })
        else:
            x0 = x.iloc[idx0].to_numpy(dtype=float)
            x1 = x.iloc[idx1].to_numpy(dtype=float)
            x0 = x0[np.isfinite(x0)]
            x1 = x1[np.isfinite(x1)]
            if len(x0) < MIN_GROUP_N_FOR_CONTINUOUS or len(x1) < MIN_GROUP_N_FOR_CONTINUOUS:
                continue
            p_norm0 = stats.shapiro(x0)[1] if 3 <= len(x0) <= 5000 else 0.0
            p_norm1 = stats.shapiro(x1)[1] if 3 <= len(x1) <= 5000 else 0.0
            if p_norm0 > 0.05 and p_norm1 > 0.05:
                # Welch t-test: do not assume equal variances
                p = stats.ttest_ind(x0, x1, equal_var=False).pvalue
                d0 = f"{x0.mean():.2f}±{x0.std(ddof=1):.2f}"
                d1 = f"{x1.mean():.2f}±{x1.std(ddof=1):.2f}"
            else:
                p = stats.mannwhitneyu(x0, x1, alternative="two-sided").pvalue
                q0 = np.percentile(x0, [25, 75])
                q1 = np.percentile(x1, [25, 75])
                d0 = f"{np.median(x0):.2f} [{q0[0]:.2f}-{q0[1]:.2f}]"
                d1 = f"{np.median(x1):.2f} [{q1[0]:.2f}-{q1[1]:.2f}]"

            rows.append({
                "Variable": v,
                col_pos: d1,
                col_neg: d0,
                "P value": f"{p:.3f}" if p >= 0.001 else "<0.001",
                "_p_num": p,
                "_is_level": False,
            })

    tbl = pd.DataFrame(rows)
    if tbl.empty:
        return tbl
    def _sig_row(r):
        if r.get("_is_level"):
            return ""
        return _sig_stars(r.get("_p_num"))
    tbl["Significance"] = tbl.apply(_sig_row, axis=1)
    return tbl


def baseline_include_vs() -> bool:
    """旧的模式门。列是否写出不再看它。

    ≥3 个原始水平时，总表和 Table 1 一律写 ANOVA 与两两列，
    选两组、全部组、全部两两都一样。两组仍只有一个 P，不另加 VS 列。
    """
    if bool(getattr(cfg, "PAIRWISE_ALL", False)):
        return False
    keep = [
        str(x).strip()
        for x in (getattr(cfg, "GROUP_KEEP", None) or [])
        if str(x).strip()
    ]
    return len(keep) == 0


def _baseline_binary_code(raw: str):
    """二分类后的 0 / 1 / 0.0 / 1.0。不是数字则 None。"""
    s = str(raw).strip()
    if not s:
        return None
    try:
        f = float(s)
    except ValueError:
        return None
    if f == 0.0:
        return 0
    if f == 1.0:
        return 1
    return None


def _baseline_display_names(raw_levels: Sequence[str]) -> list[str]:
    """总表列展示名。

    规则与 GROUP_DISPLAY_* / GROUP_LEVEL_DISPLAY 一致，但本表不把 HC 并进 CN：
    先算展示名；若冲突（如 HC 与 CN-ESRD 都映成 CN）则整表用原始水平名。
    结局已编成 0/1 时，0 用阴性展示名，1 用阳性展示名，不把 0.0 / 1.0 写进表头。
    """
    from modules.stats import data_io as _dio

    raws = [str(x).strip() for x in raw_levels if str(x).strip()]
    pos = str(getattr(cfg, "GROUP_DISPLAY_POS", "") or "").strip()
    neg = str(getattr(cfg, "GROUP_DISPLAY_NEG", "") or "").strip()
    pos_lab = str(getattr(cfg, "POSITIVE_GROUP_LABEL", "") or "").strip()
    keep = [
        str(x).strip()
        for x in (getattr(cfg, "GROUP_KEEP", None) or [])
        if str(x).strip()
    ]
    mapped: list[str] = []
    for raw in raws:
        d = _dio._mapped_level_name(raw)
        code = _baseline_binary_code(raw)
        if not d and code == 1 and pos:
            d = pos
        elif not d and code == 0 and neg:
            d = neg
        elif not d:
            if pos_lab and raw == pos_lab and pos:
                d = pos
            elif len(keep) == 2 and raw == keep[1] and neg:
                d = neg
            else:
                d = raw
        mapped.append(d)
    # 冲突（含 HC 并进 CN）→ 原始水平名，不造合并列
    if len(set(mapped)) != len(mapped):
        return list(raws)
    # 无冲突但 HC 被映走：仍显示 HC，避免总表跟建模展示名合并
    out = []
    for raw, d in zip(raws, mapped):
        if raw == "HC" and d != "HC":
            out.append("HC")
        else:
            out.append(d)
    if len(set(out)) != len(out):
        return list(raws)
    return out


def _anova_lsd(chunks: Sequence[np.ndarray]) -> tuple[float, dict[tuple[int, int], float]]:
    """单因素 ANOVA 的 P，以及 Fisher LSD 两两 P。

    LSD 用 ANOVA 的残差均方：t = (均数差) / sqrt(MSE × (1/n_i + 1/n_j))，
    df = N − k。每一对都给出，不要求 ANOVA 先显著。
    下标是传入列表的位置。样本不足的组不进入。
    """
    usable = [
        (i, np.asarray(a, dtype=float))
        for i, a in enumerate(chunks)
        if len(a) >= MIN_GROUP_N_FOR_CONTINUOUS
    ]
    pairs: dict[tuple[int, int], float] = {}
    if len(usable) < 2:
        return float("nan"), pairs
    try:
        p_anova = float(stats.f_oneway(*(a for _, a in usable)).pvalue)
    except Exception:
        p_anova = float("nan")
    k = len(usable)
    n_tot = int(sum(len(a) for _, a in usable))
    df = n_tot - k
    if df <= 0:
        for a_i in range(k):
            for b_i in range(a_i + 1, k):
                pairs[(usable[a_i][0], usable[b_i][0])] = float("nan")
        return p_anova, pairs
    sse = 0.0
    for _, a in usable:
        sse += float(np.sum((a - float(np.mean(a))) ** 2))
    mse = sse / df
    for a_i in range(k):
        ia, aa = usable[a_i]
        for b_i in range(a_i + 1, k):
            ib, bb = usable[b_i]
            diff = abs(float(np.mean(aa)) - float(np.mean(bb)))
            se = float(np.sqrt(mse * (1.0 / len(aa) + 1.0 / len(bb)))) if mse >= 0 else float("nan")
            if not np.isfinite(se):
                pv = float("nan")
            elif se == 0.0:
                pv = 0.0 if diff > 0 else 1.0
            else:
                pv = float(2.0 * stats.t.sf(diff / se, df))
            pairs[(ia, ib)] = pv
    return p_anova, pairs


MULTI_GROUP_P_NOTE = (
    "Continuous variables are mean±SD. The multi-group P is one-way ANOVA. "
    "Pairwise P is Fisher's LSD for every pair, using the ANOVA residual mean square "
    "(df = N − k). Categorical variables are n (%). Their multi-group and pairwise P "
    "use the chi-square test, Fisher exact test, or a Monte Carlo chi-square test when "
    "an expected count is below 5. Every original group level in the table is compared, "
    "including levels left out of the model."
)


def _build_table1_by_groups(
    clin: pd.DataFrame,
    group: pd.Series,
    include_vs: bool = False,
) -> pd.DataFrame:
    """多组总表：每实际水平一列；缺水平不造列。

    ≥3 个水平时一律追加「A vs B」列，不看 include_vs，也不看分组模式。
    连续变量：均数±标准差，多组 P 为 ANOVA，两两 P 为 Fisher LSD。
    分类变量：多组与两两仍是 χ² / Fisher / Monte Carlo。
    恰好 2 个水平时不另加 VS 列。include_vs 只留给旧调用，不再开门。
    """
    g = pd.Series(group).astype(str).str.strip().reset_index(drop=True)
    frame = clin.reset_index(drop=True)
    if len(g) != len(frame):
        raise ValueError(
            f"总表分组行数 {len(g)} 与临床表 {len(frame)} 不一致"
        )
    raw_levels = [str(x) for x in pd.unique(g) if str(x).strip() and str(x).strip().lower() != "nan"]
    if len(raw_levels) < 2:
        raise ValueError(f"总表至少需要 2 个组水平；当前: {raw_levels}")
    # 稳定顺序：阳性优先，其余按出现顺序。0/1 编码时 1 视为阳性。
    pos_lab = str(getattr(cfg, "POSITIVE_GROUP_LABEL", "") or "").strip()
    ordered = []
    if pos_lab and pos_lab in raw_levels:
        ordered.append(pos_lab)
    else:
        for lv in raw_levels:
            if _baseline_binary_code(lv) == 1:
                ordered.append(lv)
                break
    for lv in raw_levels:
        if lv not in ordered:
            ordered.append(lv)
    disp = _baseline_display_names(ordered)
    headers = [f"{d}={int((g == lv).sum())}" for d, lv in zip(disp, ordered)]
    level_idx = {lv: np.where(g.to_numpy() == lv)[0] for lv in ordered}

    try:
        from modules.stats import data_io as _dio
        _level_maps = getattr(_dio, "CLIN_LEVEL_MAPS", {}) or {}
    except Exception:
        _level_maps = {}

    def _is_cat(col: str, series: pd.Series) -> bool:
        try:
            from modules.stats import data_io as _dio
            var_types = getattr(_dio, "CLIN_VAR_TYPES", {}) or {}
            if col in var_types:
                return var_types[col] == "categorical"
            if hasattr(_dio, "classify_clin_series"):
                return _dio.classify_clin_series(col, series, level_maps=_level_maps) == "categorical"
        except Exception as e:
            raise ValueError(f"总表分类判定失败 {col!r}: {e}") from e
        if col in _level_maps:
            return True
        return not pd.api.types.is_numeric_dtype(series)

    def _fmt_p(p: float) -> str:
        if not (isinstance(p, float) and np.isfinite(p)):
            return "NA"
        return "<0.001" if p < 0.001 else f"{p:.3f}"

    rows = []
    # ≥3 组才写两两列。两组的 P 就是那一次比较，不再重复一列。
    vs_pairs = []
    if len(ordered) >= 3:
        for i in range(len(ordered)):
            for j in range(i + 1, len(ordered)):
                vs_pairs.append((ordered[i], ordered[j], f"{disp[i]} vs {disp[j]}"))
    del include_vs

    for v in frame.columns:
        x = frame[v]
        is_cat = _is_cat(str(v), x)
        row = {"Variable": v, "_is_level": False}
        for h in headers:
            row[h] = ""
        for _a, _b, vs_name in vs_pairs:
            row[vs_name] = ""

        if is_cat:
            levels_raw = list(pd.unique(x.dropna()))
            try:
                levels_raw = sorted(levels_raw, key=lambda z: float(z))
            except (TypeError, ValueError):
                levels_raw = sorted(levels_raw, key=lambda z: str(z))
            if len(levels_raw) < 2:
                continue
            table = []
            for lev in levels_raw:
                table.append([int((x.iloc[level_idx[lv]] == lev).sum()) for lv in ordered])
            table = np.asarray(table, dtype=int)
            keep = table.sum(axis=1) > 0
            table_use = table[keep]
            if table_use.shape[0] < 2 or table_use.shape[1] < 2:
                continue
            try:
                p = _categorical_p(table_use)
            except Exception:
                p = float("nan")
            row["P value"] = _fmt_p(p)
            row["_p_num"] = p if np.isfinite(p) else np.nan
            for a, b, vs_name in vs_pairs:
                sub = np.asarray([
                    [int((x.iloc[level_idx[a]] == lev).sum()),
                     int((x.iloc[level_idx[b]] == lev).sum())]
                    for lev in levels_raw
                ], dtype=int)
                sub = sub[sub.sum(axis=1) > 0]
                if sub.shape[0] < 2 or sub.shape[1] < 2:
                    row[vs_name] = "NA"
                else:
                    try:
                        row[vs_name] = _fmt_p(_categorical_p(sub))
                    except Exception:
                        row[vs_name] = "NA"
            rows.append(row)
            for lev in levels_raw:
                lab = _level_label(str(v), lev)
                child = {
                    "Variable": f"  {lab}",
                    "P value": "",
                    "_p_num": np.nan,
                    "_is_level": True,
                }
                for h, lv in zip(headers, ordered):
                    idx = level_idx[lv]
                    n_ok = int(x.iloc[idx].notna().sum())
                    c = int((x.iloc[idx] == lev).sum())
                    child[h] = f"{c} ({100 * c / n_ok:.1f}%)" if n_ok > 0 else "0 (0.0%)"
                for _a, _b, vs_name in vs_pairs:
                    child[vs_name] = ""
                rows.append(child)
        else:
            chunks = []
            prefer_median = False
            multi = len(ordered) >= 3
            for lv in ordered:
                idx = level_idx[lv]
                arr = pd.to_numeric(x.iloc[idx], errors="coerce").to_numpy(dtype=float)
                arr = arr[np.isfinite(arr)]
                chunks.append(arr)
                if not multi and len(arr) >= 3:
                    p_norm = stats.shapiro(arr)[1] if len(arr) <= 5000 else 0.0
                    if p_norm <= 0.05:
                        prefer_median = True
            if any(len(c) < MIN_GROUP_N_FOR_CONTINUOUS for c in chunks):
                # 某组太小则跳过该变量（不造假描述）
                if sum(len(c) >= MIN_GROUP_N_FOR_CONTINUOUS for c in chunks) < 2:
                    continue
            for h, arr in zip(headers, chunks):
                if len(arr) < MIN_GROUP_N_FOR_CONTINUOUS:
                    row[h] = "NA"
                elif prefer_median:
                    q = np.percentile(arr, [25, 75])
                    row[h] = f"{np.median(arr):.2f} [{q[0]:.2f}-{q[1]:.2f}]"
                else:
                    sd = float(np.std(arr, ddof=1)) if len(arr) > 1 else float("nan")
                    row[h] = f"{float(np.mean(arr)):.2f}±{sd:.2f}"
            usable = [c for c in chunks if len(c) >= MIN_GROUP_N_FOR_CONTINUOUS]
            if len(usable) < 2:
                continue
            lsd_map: dict[tuple[int, int], float] = {}
            if multi:
                p, lsd_map = _anova_lsd(chunks)
            else:
                try:
                    if prefer_median:
                        p = float(stats.kruskal(*usable).pvalue)
                    else:
                        p = float(stats.f_oneway(*usable).pvalue)
                except Exception:
                    p = float("nan")
            row["P value"] = _fmt_p(p)
            row["_p_num"] = p if np.isfinite(p) else np.nan
            for a, b, vs_name in vs_pairs:
                ia, ib = ordered.index(a), ordered.index(b)
                xa, xb = chunks[ia], chunks[ib]
                if len(xa) < MIN_GROUP_N_FOR_CONTINUOUS or len(xb) < MIN_GROUP_N_FOR_CONTINUOUS:
                    row[vs_name] = "NA"
                    continue
                key = (ia, ib) if ia < ib else (ib, ia)
                pv = lsd_map.get(key, float("nan"))
                row[vs_name] = _fmt_p(pv)
            rows.append(row)

    tbl = pd.DataFrame(rows)
    if tbl.empty:
        return tbl
    # 列顺序：Variable | 各组 | P value | VS… | Significance
    front = ["Variable"] + headers + ["P value"]
    vs_names = [name for _a, _b, name in vs_pairs]
    col_order = front + vs_names + ["Significance", "_p_num", "_is_level"]
    for c in col_order:
        if c not in tbl.columns:
            tbl[c] = ""
    tbl["Significance"] = tbl.apply(
        lambda r: "" if r.get("_is_level") else _sig_stars(r.get("_p_num")),
        axis=1,
    )
    return tbl[[c for c in col_order if c in tbl.columns]]


def _header_stem(col: str) -> str:
    m = re.match(r"^(.+)=(\d+)$", str(col).strip())
    return m.group(1).strip() if m else str(col).strip()


def _sample_stat_cell(row, meta: set) -> str:
    """同行里已有的描述，用来决定 HC 格是 mean±SD 还是 median [IQR]。"""
    for key in row.index:
        if key in meta or str(key).startswith("_"):
            continue
        val = row[key]
        if val is None or (isinstance(val, float) and np.isnan(val)):
            continue
        text = str(val).strip()
        if text:
            return text
    return ""


def _hc_continuous_cell(frame: pd.DataFrame, var: str, sample: str) -> str:
    if var not in frame.columns:
        raise ValueError(
            f"找不到列 {var!r}。HC 表实际列名: {list(frame.columns)}"
        )
    x = pd.to_numeric(frame[var], errors="coerce").to_numpy(dtype=float)
    x = x[np.isfinite(x)]
    if len(x) == 0:
        return "NA"
    # 与建模两组同一行的写法对齐。空单元格时用 mean±SD。
    if "[" in sample and "±" not in sample:
        q = np.percentile(x, [25, 75])
        return f"{np.median(x):.2f} [{q[0]:.2f}-{q[1]:.2f}]"
    sd = float(np.std(x, ddof=1)) if len(x) > 1 else float("nan")
    return f"{float(np.mean(x)):.2f}±{sd:.2f}"


def _hc_level_cell(frame: pd.DataFrame, parent: str, lab: str) -> str:
    if parent not in frame.columns:
        raise ValueError(
            f"找不到列 {parent!r}。HC 表实际列名: {list(frame.columns)}"
        )
    series = frame[parent]
    n_ok = int(series.notna().sum())
    count = 0
    for val in series:
        if pd.isna(val):
            continue
        if _level_label(parent, val) == lab:
            count += 1
    if n_ok <= 0:
        return "0 (0.0%)"
    return f"{count} ({100 * count / n_ok:.1f}%)"


def apply_hc_to_table1(table: Optional[pd.DataFrame], hc_frame, relabel_from: str = "") -> Optional[pd.DataFrame]:
    """Train vs Test 表上的 HC 列。

    建模组的原始水平已是 HC、展示名却被映走时，只把该列改回 HC，不再加一列。
    「选两组」把 HC 留在队列外时，用 hc_frame 补一列描述。P 仍是建模两组的检验。
    没有 HC 时 hc_frame 为 None，不造列。
    """
    if table is None:
        return table
    out = table.copy()
    stem = str(relabel_from or "").strip()
    if stem and stem != "HC":
        renamed = False
        for col in list(out.columns):
            if _header_stem(col) != stem:
                continue
            m = re.match(r"^.+=(\d+)$", str(col).strip())
            new = f"HC={m.group(1)}" if m else "HC"
            out = out.rename(columns={col: new})
            renamed = True
        if not renamed:
            raise ValueError(
                f"Train vs Test 要把展示名 {stem!r} 改回 HC，表里没有这一列。"
                f"当前列: {list(out.columns)}"
            )
        return out
    if hc_frame is None:
        return out
    if any(_header_stem(col) == "HC" for col in out.columns):
        return out
    meta = {"Variable", "P value", "Significance", "_is_level", "_p_num"}
    has_level = "_is_level" in out.columns
    is_level = []
    for i in range(len(out)):
        row = out.iloc[i]
        if has_level:
            is_level.append(bool(row["_is_level"]))
        else:
            is_level.append(str(row["Variable"]).startswith("  "))
    parent_has_levels = [False] * len(out)
    for i, flag in enumerate(is_level):
        if flag:
            continue
        j = i + 1
        while j < len(out) and is_level[j]:
            parent_has_levels[i] = True
            j += 1
    cells = []
    for i in range(len(out)):
        row = out.iloc[i]
        if is_level[i]:
            parent = str(row["Variable"]).strip()
            for k in range(i - 1, -1, -1):
                if not is_level[k]:
                    parent = str(out.iloc[k]["Variable"]).strip()
                    break
            cells.append(_hc_level_cell(hc_frame, parent, str(row["Variable"]).strip()))
        elif parent_has_levels[i]:
            cells.append("")
        else:
            var = str(row["Variable"]).strip()
            cells.append(_hc_continuous_cell(hc_frame, var, _sample_stat_cell(row, meta)))
    col_hc = f"HC={len(hc_frame)}"
    cols = list(out.columns)
    at = cols.index("P value") if "P value" in cols else len(cols)
    out.insert(at, col_hc, cells)
    return out


# ═════════════════════════════════════════════════════════════════
# Step 2: 单变量 Logistic 回归
#   对每个临床特征单独拟合 y ~ x，计算 OR (优势比) 及 95% Wald CI
# ═════════════════════════════════════════════════════════════════
def _univariate_logistic(clin: pd.DataFrame, y: np.ndarray) -> pd.DataFrame:
    rows = []
    for col in clin.columns:
        x = pd.to_numeric(clin[col], errors="coerce")
        sub = pd.DataFrame({"y": y, "x": x}).dropna()
        if len(sub) < MIN_UNIVARIATE_N:
            continue
        try:
            X = sm.add_constant(sub["x"])
            fit = sm.GLM(sub["y"], X, family=sm.families.Binomial()).fit()
        except Exception as e:
            print(f"  ⚠ univariate logistic skip {col}: {e}", flush=True)
            continue
        if len(fit.params) < 2:
            continue
        beta, se = fit.params.iloc[1], fit.bse.iloc[1]
        p = fit.pvalues.iloc[1]
        or_ = np.exp(beta)
        ci_low, ci_high = np.exp(beta - 1.96 * se), np.exp(beta + 1.96 * se)
        rows.append({"Feature": col, "OR": or_, "CI_low": ci_low, "CI_high": ci_high, "p": p})

    uni_df = pd.DataFrame(rows)
    if uni_df.empty:
        return uni_df
    uni_df = uni_df.sort_values("p").reset_index(drop=True)
    uni_df["Sig"] = uni_df["p"].apply(_sig_stars)
    return uni_df


def firth_logit(y: np.ndarray, X: np.ndarray, max_iter: int = 50,
                tol: float = 1e-8) -> Optional[dict]:
    """Jeffreys 先验惩罚 logistic。X 含截距。返回 beta / se，Wald 用惩罚信息阵。"""
    y = np.asarray(y, dtype=float).reshape(-1)
    X = np.asarray(X, dtype=float)
    n, p = X.shape
    if n < p + 2 or len(np.unique(y)) < 2:
        return None
    beta = np.zeros(p)
    converged = False
    for _ in range(int(max_iter)):
        eta = np.clip(X @ beta, -20.0, 20.0)
        mu = 1.0 / (1.0 + np.exp(-eta))
        w = np.clip(mu * (1.0 - mu), 1e-12, None)
        fisher = X.T @ (X * w[:, None])
        try:
            finv = np.linalg.inv(fisher)
        except np.linalg.LinAlgError:
            finv = np.linalg.pinv(fisher)
        h = np.clip(np.sum((X @ finv) * X, axis=1) * w, 0.0, 1.0)
        score = X.T @ (y - mu + h * (0.5 - mu))
        delta = finv @ score
        beta = beta + delta
        if float(np.max(np.abs(delta))) < tol:
            converged = True
            break
    eta = np.clip(X @ beta, -20.0, 20.0)
    mu = 1.0 / (1.0 + np.exp(-eta))
    w = np.clip(mu * (1.0 - mu), 1e-12, None)
    fisher = X.T @ (X * w[:, None])
    try:
        finv = np.linalg.inv(fisher)
    except np.linalg.LinAlgError:
        finv = np.linalg.pinv(fisher)
    se = np.sqrt(np.clip(np.diag(finv), 0.0, None))
    if not np.all(np.isfinite(beta)) or not np.all(np.isfinite(se)):
        return None
    return {"beta": beta, "se": se, "converged": converged, "n": int(n)}


def _firth_rows(names: Sequence[str], fit: dict) -> list[dict]:
    rows = []
    beta = fit["beta"]
    se = fit["se"]
    for i, name in enumerate(names):
        if i == 0:
            continue
        b, s = float(beta[i]), float(se[i])
        if not np.isfinite(b) or not np.isfinite(s) or s <= 0:
            continue
        z = b / s
        p = float(2 * stats.norm.sf(abs(z)))
        rows.append({
            "Feature": str(name),
            "OR": float(np.exp(b)),
            "CI_low": float(np.exp(b - 1.96 * s)),
            "CI_high": float(np.exp(b + 1.96 * s)),
            "p": p,
        })
    return rows


def _univariate_firth(clin: pd.DataFrame, y: np.ndarray) -> pd.DataFrame:
    rows = []
    y = np.asarray(y).astype(float)
    for col in clin.columns:
        x = pd.to_numeric(clin[col], errors="coerce")
        sub = pd.DataFrame({"y": y, "x": x}).dropna()
        if len(sub) < MIN_UNIVARIATE_N or sub["y"].nunique() < 2:
            continue
        X = sm.add_constant(sub[["x"]]).to_numpy(dtype=float)
        fit = firth_logit(sub["y"].to_numpy(dtype=float), X)
        if fit is None:
            continue
        rows.extend(_firth_rows(["Intercept", col], fit))
    uni = pd.DataFrame(rows)
    if uni.empty:
        return uni
    uni = uni.sort_values("p").reset_index(drop=True)
    uni["Sig"] = uni["p"].apply(_sig_stars)
    return uni


def _multivar_firth(clin: pd.DataFrame, y: np.ndarray, features: Sequence[str]) -> Optional[pd.DataFrame]:
    feats = [str(f) for f in features if str(f) in clin.columns]
    if not feats:
        return None
    df_m = clin[feats].apply(pd.to_numeric, errors="coerce")
    df_m["y"] = np.asarray(y).astype(float)
    df_m = df_m.dropna()
    if len(df_m) < len(feats) + 2 or df_m["y"].nunique() < 2:
        return None
    X = sm.add_constant(df_m[feats]).to_numpy(dtype=float)
    fit = firth_logit(df_m["y"].to_numpy(dtype=float), X)
    if fit is None:
        return None
    rows = _firth_rows(["Intercept", *feats], fit)
    if not rows:
        return None
    out = pd.DataFrame(rows)
    out["p"] = ["<0.001" if float(v) < 0.001 else f"{float(v):.3f}" for v in out["p"]]
    out["OR"] = [f"{float(v):.3f}" for v in out["OR"]]
    out["CI_low"] = [f"{float(v):.3f}" for v in out["CI_low"]]
    out["CI_high"] = [f"{float(v):.3f}" for v in out["CI_high"]]
    out["Sig"] = [
        _sig_stars(parse_p_value(v)) for v in out["p"]
    ]
    return out


# ═════════════════════════════════════════════════════════════════
# Step 3: backward-stepwise 多变量 logistic（AIC 驱动）
# ═════════════════════════════════════════════════════════════════
def _backward_stepwise(clin: pd.DataFrame, y: np.ndarray, candidates: Sequence[str]) -> List[str]:
    """
    以 AIC 为准则的向后逐步选择：从全变量模型开始，每步尝试移除一个
    变量，若移除后 AIC 下降则接受，直至无法进一步改善。
    """
    current = list(candidates)

    def _fit_aic(feats: List[str]):
        if len(feats) == 0:
            return None, np.inf
        df_m = clin[feats].apply(pd.to_numeric, errors="coerce")
        df_m["y"] = y
        df_m = df_m.dropna()
        if len(df_m) < len(feats) + 2:
            return None, np.inf
        X = sm.add_constant(df_m[feats])
        try:
            fit = sm.GLM(df_m["y"], X, family=sm.families.Binomial()).fit()
        except Exception:
            return None, np.inf
        return fit, fit.aic

    _, best_aic = _fit_aic(current)
    improved = True
    while improved and len(current) > 1:
        improved = False
        best_candidate_aic = best_aic
        best_candidate_feats = None
        for feat in current:
            trial = [f for f in current if f != feat]
            _, trial_aic = _fit_aic(trial)
            if trial_aic < best_candidate_aic:
                best_candidate_aic = trial_aic
                best_candidate_feats = trial
        if best_candidate_feats is not None:
            current = best_candidate_feats
            best_aic = best_candidate_aic
            improved = True

    return current


def _parse_vif_cell(v) -> float:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return float("nan")
    if isinstance(v, (int, float, np.floating, np.integer)):
        return float(v)
    s = str(v).strip().lower()
    if not s or s in ("na", "nan", "none", "—", "-"):
        return float("nan")
    try:
        return float(s)
    except ValueError:
        return float("nan")


def _is_separated_uni_row(row: pd.Series) -> bool:
    """完全分离：OR/CI 非有限，或 CI 文案含 inf。"""
    def _bad(x) -> bool:
        if x is None or (isinstance(x, float) and not np.isfinite(x)):
            return True
        s = str(x).strip().lower()
        if not s or s in ("na", "nan", "none", "—", "-"):
            return False
        if "inf" in s:
            return True
        try:
            return not np.isfinite(float(s))
        except (TypeError, ValueError):
            return False
    for col in ("OR", "CI_low", "CI_high"):
        if col in row.index and _bad(row[col]):
            return True
    if "CI" in row.index and "inf" in str(row.get("CI") or "").lower():
        return True
    return False


def _candidates_from_univariate(
    uni_df: pd.DataFrame,
    clin: pd.DataFrame,
    p_threshold: float,
    exclude: set,
    vif_high: Optional[float] = None,
) -> List[str]:
    """单因素 Logistic p < 阈值、无完全分离，才进多因素候选池。

    VIF 只作警示（VIF_THRESHOLD_WARN），不再按高阈值剔除候选。
    vif_high 保留参数以兼容旧调用，忽略不用。
    不用 Table1 组间 p（否则 Weight 可因 Table1 p<0.1 入选，Platinum 可因卡方显著但
    logistic 完全分离 p≈1 仍入选）。
    """
    if uni_df is None or uni_df.empty or "Feature" not in uni_df.columns:
        return []
    _ = vif_high  # retired: no high-cutoff exclude
    out: List[str] = []
    dropped: List[str] = []
    seen = set()
    for _, row in uni_df.iterrows():
        feat = str(row.get("Feature", "")).strip()
        if (not feat) or feat in seen or feat in exclude or feat not in clin.columns:
            continue
        p = parse_p_value(row.get("p"))
        if not np.isfinite(p) or p >= float(p_threshold):
            continue
        if _is_separated_uni_row(row):
            dropped.append(f"{feat} (complete separation)")
            continue
        out.append(feat)
        seen.add(feat)
    if dropped:
        print("  多因素候选池剔除: " + "; ".join(dropped))
    return out


def _attach_vif(df: pd.DataFrame, clin: pd.DataFrame, feats: Sequence[str]) -> pd.DataFrame:
    """把候选变量之间的 VIF 并入多因素表。"""
    if df is None or df.empty:
        return df
    key = "Feature" if "Feature" in df.columns else ("Variable" if "Variable" in df.columns else None)
    if key is None:
        return df
    try:
        from modules.stats.vif_analysis import compute_vif
        feat_list = [str(f) for f in feats if str(f) in clin.columns]
        vif_df = compute_vif(clin, feat_list) if len(feat_list) >= 2 else None
        vmap = {}
        if vif_df is not None and len(vif_df):
            vmap = {str(r["Variable"]): r["VIF"] for _, r in vif_df.iterrows()}
        out = df.copy()
        out["VIF"] = [
            f"{vmap[f]:.3f}" if f in vmap and np.isfinite(vmap[f]) else "NA"
            for f in out[key].astype(str)
        ]
        return out
    except Exception as e:
        print(f"  ⚠ VIF for multivariate pool failed: {e}")
        return df


def _pool_note(candidates: Sequence[str], stepwise: Sequence[str], p_threshold: float,
               mv_p_cut: float = 0.05) -> str:
    n = len(candidates)
    retained = list(stepwise or [])
    removed = [c for c in candidates if c not in set(retained)]
    warn = getattr(cfg, "VIF_THRESHOLD_WARN", 5)
    parts = [
        "Full multivariable model including candidate covariates with univariate logistic "
        f"p < {p_threshold:g} (n = {n}). "
        f"VIF is reported for collinearity warning (warn ≥ {warn:g}) and does not exclude candidates. "
        "Table 1 group-comparison p and complete-separation terms are not used for entry."
    ]
    if removed:
        parts.append(
            "Variables removed by AIC-based backward stepwise selection: "
            + ", ".join(removed)
            + " (stepwise retained: "
            + ", ".join(retained)
            + ")."
        )
    elif retained:
        parts.append(
            "AIC-based backward stepwise selection retained all candidates: "
            + ", ".join(retained)
            + "."
        )
    parts.append(
        "This table is the full candidate-pool model. Clinical / Combined / nomogram use "
        "FORCE_MODEL_FEATURES when set; if empty, default to variables in this table with "
        f"p < {mv_p_cut:g}."
    )
    return " ".join(parts)


# ═════════════════════════════════════════════════════════════════
# 主函数
# ═════════════════════════════════════════════════════════════════
def parse_p_value(p) -> float:
    """将 mv_df / 报告中的 p 单元格解析为 float（支持 '<0.001'）。"""
    if p is None or (isinstance(p, float) and np.isnan(p)):
        return float("nan")
    if isinstance(p, (int, float, np.floating, np.integer)):
        return float(p)
    s = str(p).strip().lower()
    if not s or s in ("na", "nan", "none", "—", "-"):
        return float("nan")
    if s.startswith("<"):
        try:
            return float(s[1:].strip())
        except ValueError:
            return 0.0  # e.g. <0.001
    try:
        return float(s)
    except ValueError:
        return float("nan")


def features_for_roc(clin_res: ClinSelectResult,
                     p_cut: Optional[float] = 0.05) -> List[str]:
    """ROC 单临床曲线 = Selected（手动 FORCE_MODEL，否则多因素 p 默认）。"""
    _ = p_cut
    return list(clin_res.features or [])


def _default_features_from_mv(
    mv_df: Optional[pd.DataFrame],
    p_cut: float,
    exclude: set,
) -> List[str]:
    """多因素候选池表中 p < p_cut 的特征，作列线图空名单时的默认。"""
    if mv_df is None or len(mv_df) == 0 or "Feature" not in mv_df.columns:
        return []
    out: List[str] = []
    seen = set()
    for _, row in mv_df.iterrows():
        feat = str(row.get("Feature", "")).strip()
        if (not feat) or feat in seen or feat in exclude:
            continue
        p = parse_p_value(row.get("p"))
        if np.isfinite(p) and p < float(p_cut):
            out.append(feat)
            seen.add(feat)
    return out


def _fit_multivar_glm(clin: pd.DataFrame, y_group: np.ndarray,
                      features: List[str]):
    """
    对给定特征拟合多变量 logistic，返回 (model, clinscore, mv_df)。
    样本不足或失败时返回 (None, None, None)。
    """
    if not features:
        return None, None, None
    df_clin = clin[features].apply(pd.to_numeric, errors="coerce")
    df_clin["y"] = y_group
    df_clin = df_clin.dropna()
    if len(df_clin) < len(features) + 2:
        print(f"  ⚠ GLM skipped: only {len(df_clin)} complete cases "
              f"for features {features} (need numeric encoding)")
        return None, None, None
    X = sm.add_constant(df_clin[features])
    try:
        model = sm.GLM(df_clin["y"], X, family=sm.families.Binomial()).fit()
    except Exception as e:
        print(f"  ⚠ GLM failed for {features}: {e}")
        return None, None, None
    clinscore = model.predict(X, which="linear").to_numpy()

    mv_df = None
    if len(model.params) > 1:
        betas = model.params.iloc[1:]
        ses = model.bse.iloc[1:]
        ps = model.pvalues.iloc[1:]
        or_ = np.exp(betas)
        cil = np.exp(betas - 1.96 * ses)
        cih = np.exp(betas + 1.96 * ses)
        mv_df = pd.DataFrame({
            "Feature": list(betas.index),
            "OR": [f"{v:.3f}" for v in or_],
            "CI_low": [f"{v:.3f}" for v in cil],
            "CI_high": [f"{v:.3f}" for v in cih],
            "p": ["<0.001" if v < 0.001 else f"{v:.3f}" for v in ps],
            "Sig": [_sig_stars(float(v)) for v in ps],
        })
    return model, clinscore, mv_df


def select_clinical(clin: pd.DataFrame, y_group: np.ndarray, group: pd.Series,
                     force_inter_features: Optional[Sequence[str]] = None,
                     force_remove_features: Optional[Sequence[str]] = None,
                     force_model_features: Optional[Sequence[str]] = None,
                     p_threshold: Optional[float] = None,
                     dataset_label: str = "all",
                     fixed_features: Optional[Sequence[str]] = None,
                     table1_only: bool = False,
                     mv_p_cut: Optional[float] = None) -> ClinSelectResult:
    """
    临床描述 + 手动入模。
    Table1 → Univariate（附 VIF）→ 候选池多因素表（单因素 p + VIF）
    → Selected = FORCE_MODEL_FEATURES（手动优先）；空则用候选池中多因素 p < mv_p_cut。
    当 fixed_features 不为 None 时（TEST 层），锁定给定特征，仍输出 Table1（不跑 uni/mv）。
    当 table1_only=True 时（All 层），仅输出 Table1。
    """
    from modules.utils import dry_run as _dry
    print(f"\n═══ CLIN SELECT [{dataset_label}] ═══")
    if _dry.exit_after_plan("clinical", [
        f"label={dataset_label}",
        f"n={len(clin)} p_features={clin.shape[1]}",
        f"table1_only={table1_only}",
        "Table1 / uni / mv (skipped)",
    ]):
        return ClinSelectResult(
            features=[],
            uni_res=pd.DataFrame(),
            table1=pd.DataFrame(),
            clinscore=None,
            model=None,
        )

    if p_threshold is None:
        p_threshold = float(getattr(cfg, "TABLE1_P_THRESHOLD", DEFAULT_P_THRESHOLD) or DEFAULT_P_THRESHOLD)
    if mv_p_cut is None:
        mv_p_cut = getattr(cfg, "ROC_MV_P_THRESHOLD", 0.05)

    force_inter_features = list(force_inter_features) if force_inter_features else []
    force_remove_features = list(force_remove_features) if force_remove_features else []
    if force_model_features is None:
        force_model_features = list(getattr(cfg, "FORCE_MODEL_FEATURES", None) or [])
    else:
        force_model_features = list(force_model_features)
    skip_set = {str(x).strip() for x in (getattr(cfg, "SKIP_CLIN_FEATURES", None) or []) if str(x).strip()}
    force_remove_features = [f for f in force_remove_features if str(f) not in skip_set]

    clin_vars = list(clin.columns)
    norm_map = {_normalize_name(c): c for c in clin_vars}
    force_inter_features = [norm_map[_normalize_name(f)] for f in force_inter_features
                             if _normalize_name(f) in norm_map]
    force_remove_features = [norm_map[_normalize_name(f)] for f in force_remove_features
                              if _normalize_name(f) in norm_map]
    force_model_features = [norm_map[_normalize_name(f)] for f in force_model_features
                             if _normalize_name(f) in norm_map]
    force_model_features = [
        f for f in force_model_features
        if f not in skip_set and f not in set(force_remove_features)
    ]

    y_group = np.asarray(y_group).astype(int)
    idx0 = np.where(y_group == 0)[0]
    idx1 = np.where(y_group == 1)[0]
    group = pd.Series(group).reset_index(drop=True)
    lbl0 = str(group.iloc[idx0[0]]) if len(idx0) else "0"
    lbl1 = str(group.iloc[idx1[0]]) if len(idx1) else "1"
    print(f"  {lbl0} N={len(idx0)}, {lbl1} N={len(idx1)}")

    # ── Step 1: Table1 ──
    tbl = _build_table1(clin, idx0, idx1, lbl0, lbl1)
    print(f"  Table1: {len(tbl)} rows")

    if table1_only:
        print("  Table1 only — skip Univariate / Multivariate / stepwise")
        return ClinSelectResult(
            features=[],
            uni_res=pd.DataFrame(),
            table1=tbl,
            clinscore=None,
            model=None,
            mv_df=None,
            mv_note=None,
            candidates=[],
            stepwise_features=[],
        )

    # ── Step 2 & 3: 单变量 + 候选池 + stepwise（或强制使用 fixed_features）──
    # Test/外部验证：fixed_features 锁定 train 特征 → 不筛选、不单变量/多变量表
    if fixed_features is not None:
        final_feat = [c for c in fixed_features if c in clin.columns]
        print(f"  验证模式 — 锁定 TRAIN 特征({len(final_feat)}): {', '.join(final_feat)}")
        print("  跳过 Univariate / Multivariate / stepwise")
        return ClinSelectResult(
            features=final_feat,
            uni_res=pd.DataFrame(),
            table1=tbl,
            clinscore=None,
            model=None,
            mv_df=None,
            mv_note=None,
            candidates=list(final_feat),
            stepwise_features=list(final_feat),
        )

    uni_df = _univariate_logistic(clin, y_group)
    print(f"  Univariate: {len(uni_df)} features")

    # VIF 并入 Univariate 表（在单因素列出的变量之间计算）
    if not uni_df.empty and "Feature" in uni_df.columns:
        try:
            from modules.stats.vif_analysis import compute_vif
            uni_feats = [
                str(f) for f in uni_df["Feature"].tolist()
                if str(f) in clin.columns
            ]
            vif_df = compute_vif(clin, uni_feats) if len(uni_feats) >= 2 else None
            vmap = {}
            if vif_df is not None and len(vif_df):
                vmap = {
                    str(r["Variable"]): r["VIF"]
                    for _, r in vif_df.iterrows()
                }
            uni_df = uni_df.copy()
            uni_df["VIF"] = [
                f"{vmap[f]:.3f}" if f in vmap and np.isfinite(vmap[f]) else "NA"
                for f in uni_df["Feature"].astype(str)
            ]
            print(f"  VIF attached to Univariate ({len(vmap)} vars)")
        except Exception as e:
            print(f"  ⚠ VIF for Univariate failed: {e}")
            uni_df = uni_df.copy()
            if "VIF" not in uni_df.columns:
                uni_df["VIF"] = "NA"

    rm_set = set(force_remove_features)
    if rm_set:
        print(f"  强制剔除（不进模型）: {', '.join(sorted(rm_set))}")

    # 候选池多因素表（单因素 p + VIF）；Selected 另取手动名单或其中 p < mv_p_cut
    skip_and_rm = set(skip_set) | rm_set
    candidates = _candidates_from_univariate(
        uni_df, clin, p_threshold, skip_and_rm,
    )
    print(
        f"  Candidate pool (univariate p < {p_threshold:g}, informational): "
        f"{', '.join(candidates) or '（无）'}"
    )
    mv_df = None
    stepwise_features: List[str] = []
    mv_note = None
    if candidates:
        try:
            _pool_model, _pool_lp, mv_df = _fit_multivar_glm(clin, y_group, candidates)
        except Exception as e:
            print(f"  ⚠ Candidate-pool GLM failed (informational): {e}")
            mv_df = None
        if mv_df is not None and len(mv_df):
            mv_df = _attach_vif(mv_df, clin, candidates)
        try:
            stepwise_features = _backward_stepwise(clin, y_group, candidates)
        except Exception as e:
            print(f"  ⚠ AIC stepwise failed (informational): {e}")
            stepwise_features = list(candidates)
        mv_note = _pool_note(
            candidates, stepwise_features, p_threshold, mv_p_cut=float(mv_p_cut),
        )
        print(
            "  AIC stepwise retained (informational): "
            f"{', '.join(stepwise_features) or '（无）'}"
        )

    # Selected：手动 FORCE_MODEL 优先；空则用多因素 p < mv_p_cut
    _rs = {"radscore", "rad_score"}
    manual_feat = [
        f for f in force_model_features
        if f not in rm_set and str(f).strip().lower() not in _rs
    ]
    if force_inter_features:
        print(
            "  ℹ FORCE_INTER_FEATURES 已忽略入选（改为手动 FORCE_MODEL_FEATURES）："
            + ", ".join(force_inter_features)
        )
    default_feat = _default_features_from_mv(mv_df, float(mv_p_cut), skip_and_rm)
    if manual_feat:
        final_feat = list(manual_feat)
        select_source = "manual"
        print(f"  Selected clinical (manual FORCE_MODEL): {', '.join(final_feat)}")
    else:
        final_feat = list(default_feat)
        select_source = "mv_p" if final_feat else None
        if final_feat:
            print(
                f"  Selected clinical (default multivariate p < {float(mv_p_cut):g}): "
                f"{', '.join(final_feat)}"
            )
        else:
            print(
                "  ⚠ 列线图变量为空，且多因素无 p < "
                f"{float(mv_p_cut):g} 的特征 — 无 Clinical 入选。"
            )

    firth_uni = None
    firth_mv = None
    if bool(getattr(cfg, "DO_FIRTH", False)):
        try:
            firth_uni = _univariate_firth(clin, y_group)
            print(f"  Firth univariate: {0 if firth_uni is None else len(firth_uni)} features")
        except Exception as e:
            print(f"  ⚠ Firth univariate failed: {e}")
            firth_uni = None
        if candidates:
            try:
                firth_mv = _multivar_firth(clin, y_group, candidates)
                print(f"  Firth candidate pool: {0 if firth_mv is None else len(firth_mv)} terms")
            except Exception as e:
                print(f"  ⚠ Firth multivariable failed: {e}")
                firth_mv = None

    if not final_feat:
        return ClinSelectResult(
            features=[],
            uni_res=uni_df,
            table1=tbl,
            clinscore=None,
            model=None,
            mv_df=mv_df,
            mv_note=mv_note,
            candidates=list(candidates),
            stepwise_features=list(stepwise_features),
            mv_selected=None,
            select_source=select_source,
            firth_uni=firth_uni,
            firth_mv=firth_mv,
        )

    model, clinscore, mv_selected = _fit_multivar_glm(clin, y_group, final_feat)
    return ClinSelectResult(
        features=final_feat,
        uni_res=uni_df,
        table1=tbl,
        clinscore=clinscore,
        model=model,
        mv_df=mv_df,
        mv_note=mv_note,
        candidates=list(candidates),
        stepwise_features=list(stepwise_features),
        mv_selected=mv_selected,
        select_source=select_source,
        firth_uni=firth_uni,
        firth_mv=firth_mv,
    )
