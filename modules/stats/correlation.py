"""
correlation.py — Spearman 相关性热图
═══════════════════════════════════════════════════════════════════
功能：将影像组学特征与临床特征合并，计算 Spearman 秩相关系数矩阵，
      并绘制带显著性标注的热图；同时提供针对单个目标变量（如 EGFR
      或 Group）的相关性汇总表（含 Bootstrap CI）。

统计方法：
    - Spearman 秩相关（非参数，对离群值稳健）
    - 显著性检验：t = ρ × √((n-2)/(1-ρ²))，双侧检验
    - 显著性标注：* p<0.05, ** p<0.01, *** p<0.001
═══════════════════════════════════════════════════════════════════
"""

from __future__ import annotations
from typing import Optional, Sequence

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy import stats

from modules import config as cfg

# ═════════════════════════════════════════════════════════════════
# §1  顶层可调参数
# ═════════════════════════════════════════════════════════════════
DEFAULT_NBOOT = 1000         # check_correlation() 的 Bootstrap 重复次数；优先 cfg.BOOTSTRAP_N
DEFAULT_CONF_LEVEL = 0.95    # Bootstrap CI 置信水平
MAX_HEATMAP_FEATURES = 40    # 热图最多特征数（过多时截断，避免 TIFF/内存爆炸）


def _col_token(name: str) -> str:
    return str(name).strip().lower().replace(" ", "").replace("_", "")


def group_row_mask(group, level: str, n: int | None = None):
    """分析组别。空字符串表示不筛行。没有匹配行时抛出 ValueError。"""
    want = str(level or "").strip()
    if not want:
        return None
    g = pd.Series(group).astype(str).str.strip().reset_index(drop=True)
    if n is not None and len(g) != int(n):
        raise ValueError(f"分析组别 {want!r}：Group 行数 {len(g)} 与数据 {int(n)} 不一致")
    mask = (g == want).to_numpy()
    if int(mask.sum()) == 0:
        raise ValueError(f"分析组别 {want!r}：没有这一组的行")
    return mask


def take_group_rows(df, mask):
    if df is None or mask is None:
        return df
    idx = np.flatnonzero(mask)
    return df.reset_index(drop=True).iloc[idx].reset_index(drop=True)


def take_group_values(values, mask):
    if values is None or mask is None:
        return values
    arr = np.asarray(values)
    if arr.shape[0] != len(mask):
        return None
    return arr[mask]


def heatmap_wanted(raw) -> list[str]:
    """热图列名单。空字符串、空列表表示没有指定列。"""
    if raw is None:
        return []
    if isinstance(raw, str):
        s = raw.strip()
        return [s] if s else []
    if isinstance(raw, (list, tuple)):
        out: list[str] = []
        seen: set[str] = set()
        for x in raw:
            s = str(x).strip()
            if not s or s in seen:
                continue
            seen.add(s)
            out.append(s)
        return out
    s = str(raw).strip()
    return [s] if s else []


def _unique_names(raw) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for x in raw or []:
        s = str(x).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def expand_heatmap_columns(
    wanted,
    clin_selected: Sequence[str] | None = None,
    rad_selected: Sequence[str] | None = None,
    score_names: Sequence[str] | None = None,
) -> tuple[list[str], list[str]]:
    """HEATMAP_COLS：Clinical / RadScore = 评分本身 + 入选成分特征。

    与 expand_corr_predictors 相同：保留复合评分名，再追加筛选后的成分列。
    分类 Logistic 时 RadScore 令牌展开为 score_names。Combined 等其它名字原样保留。
    第二项恒为空列表（兼容旧调用方）。
    """
    return expand_corr_predictors(
        wanted, rad_selected=rad_selected, clin_selected=clin_selected,
        score_names=score_names,
    ), []


def expand_corr_predictors(
    wanted,
    rad_selected: Sequence[str] | None = None,
    clin_selected: Sequence[str] | None = None,
    score_names: Sequence[str] | None = None,
) -> list[str]:
    """Expand Clinical / RadScore tokens for corr / partial / ANCOVA / MLR / heatmap.

    Keep the composite score name and append screened component features so
    RadScore is used together with its LASSO / logistic components (Clinical
    likewise with selected clinical columns). Combined stays a score token.
    Other names pass through unchanged.
    """
    clin_sel = _unique_names(clin_selected)
    rad_sel = _unique_names(rad_selected)
    scores = _unique_names(score_names)
    out: list[str] = []
    seen: set[str] = set()

    def _add(name: str) -> None:
        if not name or name in seen:
            return
        seen.add(name)
        out.append(name)

    for name in heatmap_wanted(wanted):
        tok = _col_token(name)
        if tok == "clinical":
            _add("Clinical")
            for c in clin_sel:
                _add(c)
            continue
        if tok == "radscore":
            if scores:
                for s in scores:
                    _add(s)
            else:
                _add("RadScore")
            for c in rad_sel:
                _add(c)
            continue
        _add(name)
    return out


def resolve_heatmap_columns(
    wanted,
    clin_names: Sequence[str],
    rad_names: Sequence[str],
) -> tuple[list[str], list[str], list[str]]:
    """把用户点的热图列拆成临床列、组学列、缺失名。

    同名时用临床列。Group（不区分大小写、空格、下划线）若在临床表里，
    用那一列；两边都没有时返回字面量 Group，由调用方挂上结局标签。
    """
    clin_list: list[str] = []
    for c in clin_names or []:
        s = str(c).strip()
        if s and s not in clin_list:
            clin_list.append(s)
    rad_list: list[str] = []
    for c in rad_names or []:
        s = str(c).strip()
        if s and s not in rad_list:
            rad_list.append(s)
    clin_exact = set(clin_list)
    rad_exact = set(rad_list)
    group_clin = next((c for c in clin_list if _col_token(c) == "group"), None)

    clin_cols: list[str] = []
    rad_cols: list[str] = []
    missing: list[str] = []
    used: set[str] = set()

    def _take(bucket: list[str], col: str) -> None:
        if col in used:
            return
        used.add(col)
        bucket.append(col)

    for name in heatmap_wanted(wanted):
        if name in clin_exact:
            _take(clin_cols, name)
            continue
        if _col_token(name) == "group" and group_clin:
            _take(clin_cols, group_clin)
            continue
        if name in rad_exact:
            _take(rad_cols, name)
            continue
        if _col_token(name) == "group":
            _take(clin_cols, "Group")
            continue
        missing.append(name)
    return clin_cols, rad_cols, missing


def select_heatmap_frames(wanted, clin_df, rad_df, group_labels=None):
    """按名单取热图用的两张表。Group 不在临床表里时用 group_labels。"""
    clin_names = [] if clin_df is None else [str(c) for c in clin_df.columns]
    rad_names = [] if rad_df is None else [str(c) for c in rad_df.columns]
    clin_cols, rad_cols, missing = resolve_heatmap_columns(wanted, clin_names, rad_names)
    clin_have = [c for c in clin_cols if c in set(clin_names)]
    if clin_df is None:
        clin_sel = pd.DataFrame()
    elif clin_have:
        clin_sel = clin_df.loc[:, clin_have].copy()
    else:
        clin_sel = pd.DataFrame(index=clin_df.index)
    if "Group" in clin_cols and "Group" not in list(clin_sel.columns) and group_labels is not None:
        clin_sel = clin_sel.copy()
        clin_sel["Group"] = group_labels
    rad_have = [c for c in rad_cols if c in set(rad_names)]
    if rad_df is None:
        rad_sel = pd.DataFrame()
    elif rad_have:
        rad_sel = rad_df.loc[:, rad_have].copy()
    else:
        rad_sel = pd.DataFrame(index=rad_df.index)
    return rad_sel, clin_sel, missing


def _sig_stars(p: float) -> str:
    if p is None or np.isnan(p):
        return ""
    if p < 0.001:
        return "***"
    if p < 0.01:
        return "**"
    if p < 0.05:
        return "*"
    return ""


def clear_selected_heatmap() -> None:
    """本次不画热图时删掉上次的 correlation_heatmap_selected，避免结果页继续嵌旧图。"""
    import os
    from modules.utils import dry_run as _dry

    name = "correlation_heatmap_selected"
    out = str(getattr(cfg, "OUT_DIR", "") or "")
    paths = [
        os.path.join(out, "PNG", f"{name}.png"),
        os.path.join(out, f"{name}.tiff"),
        os.path.join(out, f"{name}.pdf"),
    ]
    for path in paths:
        if _dry.skip_write(path, what="clear heatmap"):
            continue
        if os.path.isfile(path):
            os.remove(path)
            print(f"  removed stale {os.path.basename(path)}")


def run_correlation(rad_df: Optional[pd.DataFrame] = None, clin_df: Optional[pd.DataFrame] = None, dataset_label: str = "all") -> None:
    """
    合并 rad_df + clin_df，计算 Spearman 相关矩阵并绘制带显著性标注的
    热图。仅保存图片，不返回相关性长表（若需逐对统计量，请使用
    check_correlation()）。
    """
    from modules.utils import dry_run as _dry
    print(f"\n═══ CORRELATION [{dataset_label}] ═══")
    if _dry.exit_after_plan("correlation", [
        f"label={dataset_label}",
        "Spearman heatmap (skipped)",
    ]):
        return

    frames = []
    pos_lab = str(getattr(cfg, "GROUP_DISPLAY_POS", "") or "").strip()
    for d in (rad_df, clin_df):
        if d is None or d.shape[1] == 0:
            continue
        dd = d.copy()
        # Heatmap axis/legend: show positive outcome display name, not generic Group
        if pos_lab:
            ren = {}
            for c in dd.columns:
                if str(c).strip().lower().replace(" ", "").replace("_", "") == "group":
                    ren[c] = pos_lab
            if ren:
                dd = dd.rename(columns=ren)
        frames.append(dd)
    if len(frames) == 0:
        print("  No features — skip.")
        return
    df_full = pd.concat(frames, axis=1)

    # ── 自动编码：分类变量（字符/对象）→ 整数编码 ──
    for col in df_full.columns:
        if not pd.api.types.is_numeric_dtype(df_full[col]):
            df_full[col] = pd.factorize(df_full[col])[0].astype(float)
            df_full.loc[df_full[col] < 0, col] = np.nan
        else:
            df_full[col] = df_full[col].astype(float)

    nf = df_full.shape[1]
    print(f"  Features: {nf}")
    if nf > MAX_HEATMAP_FEATURES:
        # 优先保留 clin 侧列，再按方差取 rad 侧高变特征
        clin_n = 0 if clin_df is None else clin_df.shape[1]
        keep = list(df_full.columns[:clin_n]) if clin_n else []
        rest = [c for c in df_full.columns if c not in keep]
        if rest:
            vars_ = df_full[rest].var(numeric_only=True).sort_values(ascending=False)
            n_take = max(0, MAX_HEATMAP_FEATURES - len(keep))
            keep.extend(list(vars_.head(n_take).index))
        df_full = df_full[keep]
        nf = df_full.shape[1]
        print(f"  Heatmap truncated to top {nf} features (cap={MAX_HEATMAP_FEATURES})")

    # ── Spearman 相关矩阵（pairwise complete） ──
    rho_mat = df_full.corr(method="spearman")
    # ── 每对变量的有效观测数 ──
    notna = df_full.notna().astype(float)
    n_obs = notna.T @ notna
    n_obs = np.minimum(n_obs.to_numpy(), n_obs.to_numpy().T)
    n_obs = pd.DataFrame(n_obs, index=rho_mat.index, columns=rho_mat.columns)

    # ── t 统计量与双侧 p 值 ──
    with np.errstate(divide="ignore", invalid="ignore"):
        t_mat = rho_mat.to_numpy() * np.sqrt((n_obs.to_numpy() - 2) / (1 - rho_mat.to_numpy() ** 2))
    df_ = n_obs.to_numpy() - 2
    pval_mat = 2 * stats.t.sf(np.abs(t_mat), df=df_)
    np.fill_diagonal(pval_mat, np.nan)
    pval_mat[n_obs.to_numpy() < 3] = np.nan

    orig_names = list(df_full.columns)
    try:
        from modules.stats.radiomics import display_rad_feature as _disp_rad
    except Exception:
        def _disp_rad(x):  # type: ignore
            return str(x)
    # 轴标签缩短组学全名（standard_fbn_32_ 等）；矩阵索引仍用 orig_names
    disp_names = [_disp_rad(n) for n in orig_names]

    # ── 绘制热图（显式索引，确保 X/Y 轴标签与矩阵条目一一对应） ──
    fig, ax = plt.subplots(figsize=(max(9, nf * 0.7 + 3), max(7, nf * 0.6 + 2.5)))
    rho_plot = rho_mat.loc[orig_names, orig_names].to_numpy()
    pval_plot = pd.DataFrame(pval_mat, index=orig_names, columns=orig_names).to_numpy()
    im = ax.imshow(rho_plot, cmap="RdBu_r", vmin=-1, vmax=1)
    fs = cfg.FIG_FONT_SIZE
    ax.set_xticks(range(nf))
    ax.set_xticklabels(disp_names, rotation=45, ha="right", fontsize=fs)
    ax.set_yticks(range(nf))
    ax.set_yticklabels(disp_names, fontsize=fs)
    for i in range(nf):
        for j in range(nf):
            s = _sig_stars(pval_plot[i, j])
            if s:
                ax.text(j, i, s, ha="center", va="center",
                        fontsize=fs, fontweight="normal")
    cbar = fig.colorbar(im, ax=ax, shrink=0.8)
    cbar.set_label("Spearman ρ", fontsize=fs)
    cbar.ax.tick_params(labelsize=fs)
    ax.set_title("Spearman Correlation — Selected Features",
                 fontsize=fs, fontweight="normal")
    cfg.apply_clean_theme(ax, grid=False, legend=False)
    fig.tight_layout()

    corr_fn = f"correlation_heatmap_{dataset_label}"
    if cfg.save_plot(corr_fn, fig, width=max(9, nf * 0.7 + 3), height=max(7, nf * 0.6 + 2.5), dpi=300):
        print(f"  {corr_fn}.png saved")


def _col_stem(name: str) -> str:
    """括号前的短名。Age 与 Age (years) 是同一个词干。"""
    s = str(name).strip()
    cut = s.find(" (")
    if cut > 0:
        s = s[:cut].strip()
    return "".join(s.split()).replace("_", "").casefold()


def resolve_named_column(requested: str, columns, extras=None, *, allow_group_token: bool = False) -> str:
    """配置里的短名对到表列。

    先精确，再唯一的大小写命中，再唯一的括号前词干（只在 columns 里，不含派生列）。
    对不上或不止一个候选时抛错，写出请求名和临床表实际列名。
    allow_group_token：相关表的 Group 参考没有同名列时，交给分组标签，不在这里报错。
    """
    name = str(requested or "").strip()
    cols = [str(c) for c in (columns or [])]
    extra_names = [str(k) for k in (extras or {})]
    pool = cols + [e for e in extra_names if e not in cols]
    listed = ", ".join(cols)
    if not name:
        raise ValueError(f"列名为空。临床表实际列名: {listed}")
    if name in pool:
        return name
    seen: list[str] = []
    for col in pool:
        if col.casefold() == name.casefold() and col not in seen:
            seen.append(col)
    if len(seen) == 1:
        return seen[0]
    if len(seen) > 1:
        raise ValueError(
            f"列名 {name!r} 对上不止一列: {', '.join(seen)}。临床表实际列名: {listed}"
        )
    stem = _col_stem(name)
    hits = [col for col in cols if _col_stem(col) == stem]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        raise ValueError(
            f"列名 {name!r} 对上不止一列: {', '.join(hits)}。临床表实际列名: {listed}"
        )
    if allow_group_token and name.casefold() == "group":
        return "Group"
    raise ValueError(f"找不到列 {name!r}。临床表实际列名: {listed}")


def check_correlation(data_all, radscore: Optional[np.ndarray] = None,
                       egfr_col: str = "Estimated glomerular filtration rate",
                       targets: Optional[Sequence[str]] = None,
                       extra_predictors: Optional[dict] = None,
                       nboot: int = DEFAULT_NBOOT, conf_level: float = DEFAULT_CONF_LEVEL,
                       seed: Optional[int] = None) -> pd.DataFrame:
    """
    针对单个参考变量（egfr_col / Group / clin 列）与多个 predictor 计算
    Spearman 相关及 Bootstrap CI。

    parameters
    ----------
    targets : 预测变量名列表。支持 clin 列名，以及 RadScore / Clinical / Combined
              （后两者需通过 extra_predictors 提供向量）。
    extra_predictors : dict[str, array]，如 {"Clinical": ..., "Combined": ..., "RadScore": ...}
    """
    if data_all is None or data_all.clin is None:
        raise ValueError("data_all and data_all.clin must be provided to check_correlation()")
    if seed is None:
        seed = int(getattr(cfg, "SEED", 66666) or 66666)
    nboot = int(getattr(cfg, "BOOTSTRAP_N", None) or nboot or DEFAULT_NBOOT)

    if targets is None:
        targets = list(getattr(cfg, "CORR_PREDICTORS", ["RadScore", "Clinical", "Combined"]))
    extra_predictors = extra_predictors or {}

    # 名称归一：方便匹配 RadScore / Clinical / Combined
    def _key(name: str) -> str:
        return str(name).strip().lower().replace(" ", "")

    extra_norm = {_key(k): (k, np.asarray(v, dtype=float)) for k, v in extra_predictors.items()}
    if radscore is not None and "radscore" not in extra_norm:
        extra_norm["radscore"] = ("RadScore", np.asarray(radscore, dtype=float))
        extra_predictors = dict(extra_predictors)
        extra_predictors.setdefault("RadScore", radscore)
    clin_cols = list(data_all.clin.columns)
    # 参考名对不上列时直接报错。不把 Spearman ρ / P / CI 写成 NA。
    resolved_ref = resolve_named_column(
        egfr_col, clin_cols, extras=extra_predictors, allow_group_token=True,
    )
    if resolved_ref in data_all.clin.columns:
        egfr_vec = pd.to_numeric(data_all.clin[resolved_ref], errors="coerce").to_numpy()
    elif _key(resolved_ref) in extra_norm:
        _disp, egfr_vec = extra_norm[_key(resolved_ref)]
        egfr_vec = np.asarray(egfr_vec, dtype=float)
    elif str(resolved_ref).lower() == "group":
        if getattr(data_all, "label", None) is not None:
            egfr_vec = np.asarray(data_all.label, dtype=float)
        elif data_all.group is not None:
            egfr_vec = pd.factorize(data_all.group)[0].astype(float)
        else:
            raise ValueError(
                f"找不到列 {egfr_col!r}。临床表没有 Group，这一层也没有分组标签。"
                f"临床表实际列名: {', '.join(map(str, clin_cols))}"
            )
    else:
        raise ValueError(
            f"找不到列 {egfr_col!r}。临床表实际列名: {', '.join(map(str, clin_cols))}"
        )

    rows = []
    for i, pred_name in enumerate(targets):
        n_val, rho, pval, ci_lo, ci_hi = np.nan, np.nan, np.nan, np.nan, np.nan
        pk = _key(pred_name)
        x = None
        display_name = pred_name

        rad_df = getattr(data_all, "rad", None)
        rad_cols = []
        if rad_df is not None and getattr(rad_df, "columns", None) is not None:
            rad_cols = [str(c) for c in rad_df.columns]
        # 预测因子同样对列名。对不上就停，不留 NA 行。
        resolved_pred = resolve_named_column(
            pred_name, clin_cols + rad_cols, extras=extra_predictors,
        )
        pk = _key(resolved_pred)
        display_name = resolved_pred
        if pk in extra_norm:
            display_name, x = extra_norm[pk]
        elif resolved_pred in data_all.clin.columns:
            x = pd.to_numeric(data_all.clin[resolved_pred], errors="coerce").to_numpy()
        elif rad_df is not None and resolved_pred in rad_df.columns:
            x = pd.to_numeric(rad_df[resolved_pred], errors="coerce").to_numpy()
        else:
            raise ValueError(
                f"找不到列 {pred_name!r}。临床表实际列名: {', '.join(map(str, clin_cols))}"
            )

        if x is not None and egfr_vec is not None:
            x = np.asarray(x, dtype=float).ravel()
            if len(x) != len(egfr_vec):
                print(f"  ⚠ length mismatch for '{display_name}' "
                      f"({len(x)} vs {len(egfr_vec)}) — skip")
            else:
                keep = np.isfinite(x) & np.isfinite(egfr_vec)
                npair = int(keep.sum())
                n_val = npair
                if npair >= 4:
                    rho, pval = stats.spearmanr(x[keep], egfr_vec[keep])
                    rng = np.random.RandomState(seed + i)
                    boots = []
                    for _ in range(nboot):
                        ii = rng.randint(0, npair, npair)
                        xb, eb = x[keep][ii], egfr_vec[keep][ii]
                        if np.std(xb) == 0 or np.std(eb) == 0:
                            continue
                        boots.append(stats.spearmanr(xb, eb)[0])
                    if boots:
                        a = (1 - conf_level) / 2
                        ci_lo, ci_hi = np.quantile(boots, [a, 1 - a])

        rows.append({
            "predictor": display_name,
            "ref": egfr_col,
            "n": n_val,
            "spearman_rho": rho,
            "p_value": pval,
            "ci_lower": ci_lo,
            "ci_upper": ci_hi,
        })

    return pd.DataFrame(rows)
