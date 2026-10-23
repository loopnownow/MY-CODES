"""
全队列线性关联：协方差分析、Group 多元线性回归、偏相关。

两组时 ANCOVA 的 Group Wald F 与 OLS 分组系数是同一个检验。
本模块的 FDR / FWER 只校正「每个因变量一个 Group 检验」。规格相同的两张表共用这一家，不重复校正。
组学单因素 Logistic 的 FDR / FWER 在 radiomics 模块另算一家（见 _attach_rad_uni_adjust），不与此处混算。
"""
from __future__ import annotations

import json
from typing import Any, Optional, Sequence

import numpy as np
import pandas as pd
import patsy
from scipy import stats
import statsmodels.formula.api as smf

from modules import config as cfg


def bh_fdr(p: Sequence[float]) -> np.ndarray:
    """Benjamini–Hochberg。非有限 p 保持 NaN。"""
    src = np.asarray(list(p), dtype=float)
    out = np.full(src.shape, np.nan)
    idx = np.flatnonzero(np.isfinite(src))
    m = int(idx.size)
    if m == 0:
        return out
    order = idx[np.argsort(src[idx], kind="mergesort")]
    ranked = src[order]
    q = ranked * m / np.arange(1, m + 1)
    q = np.minimum.accumulate(q[::-1])[::-1]
    out[order] = np.clip(q, 0.0, 1.0)
    return out


def holm_fwer(p: Sequence[float]) -> np.ndarray:
    """Holm step-down。非有限 p 保持 NaN。"""
    src = np.asarray(list(p), dtype=float)
    out = np.full(src.shape, np.nan)
    idx = np.flatnonzero(np.isfinite(src))
    m = int(idx.size)
    if m == 0:
        return out
    order = idx[np.argsort(src[idx], kind="mergesort")]
    ranked = src[order]
    adj = ranked * (m - np.arange(m))
    adj = np.maximum.accumulate(np.clip(adj, 0.0, None))
    out[order] = np.clip(adj, 0.0, 1.0)
    return out


def _as_name_list(raw: Any) -> list[str]:
    """单列字符串或列表。字符串不能交给 _clean，否则会按字符拆开。"""
    if raw is None:
        return []
    if isinstance(raw, str):
        s = raw.strip()
        return [s] if s else []
    return _clean(raw)


def _partial_pairs(xs: Sequence[str], ys: Sequence[str]) -> list[tuple[str, str]]:
    """连续变量 1 × 连续变量 2。同一列不跟自己配对，A–B 与 B–A 只留先出现的一对。"""
    seen: set[tuple[str, str]] = set()
    pairs: list[tuple[str, str]] = []
    for x in xs:
        for y in ys:
            if not x or not y or x == y:
                continue
            sig = (x, y)
            if sig in seen or (y, x) in seen:
                continue
            seen.add(sig)
            pairs.append(sig)
    return pairs


def _clean(names: Sequence[Any] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for x in names or []:
        s = str(x).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _qname(name: str) -> str:
    return "Q(" + json.dumps(str(name)) + ")"


def _is_categorical(series: pd.Series, name: str) -> bool:
    force_cont = {str(x).strip() for x in (getattr(cfg, "CLIN_FORCE_CONTINUOUS", None) or [])}
    force_cat = {str(x).strip() for x in (getattr(cfg, "CLIN_FORCE_CATEGORICAL", None) or [])}
    if name in force_cont:
        return False
    if name in force_cat:
        return True
    if series.dtype == object or str(series.dtype).startswith("string"):
        return True
    num = pd.to_numeric(series, errors="coerce")
    if int(num.notna().sum()) < max(3, int(0.5 * len(series))):
        return True
    return int(num.nunique(dropna=True)) <= 3


def _present_levels(series: pd.Series) -> list[str]:
    s = series.astype(str).str.strip()
    s = s[~s.str.lower().isin(["", "nan", "none", "nat"])]
    return [str(x) for x in pd.unique(s)]


def _reference_level(levels: Sequence[str], positive: str, negative: str) -> str:
    lev = [str(x) for x in levels if str(x).strip()]
    pos = str(positive or "").strip()
    neg = str(negative or "").strip()
    if neg and neg in lev and neg != pos:
        return neg
    if pos and pos in lev:
        others = [x for x in lev if x != pos]
        if others:
            return others[0]
    return sorted(lev)[0] if lev else ""


def subset_frame_by_group(frame: pd.DataFrame, level: str) -> pd.DataFrame:
    """偏相关的分析组别。空字符串返回原表。"""
    want = str(level or "").strip()
    if not want:
        return frame
    if "Group" not in frame.columns:
        raise ValueError(f"分析组别 {want!r}：表里没有 Group 列")
    g = frame["Group"].astype(str).str.strip()
    sub = frame.loc[g == want].copy()
    if len(sub) == 0:
        raise ValueError(f"分析组别 {want!r}：没有这一组的行")
    return sub


def analysis_frame(layer, extras: Optional[dict[str, Any]] = None) -> pd.DataFrame:
    """临床列 + Group 原文 + 组学表里的测量列，按行对齐。

    extras：派生列（如 LASSO/Logistic 之后的 RadScore、Clinical、Combined），
    合并进分析框供 ANCOVA / MLR / 偏相关使用。
    """
    clin = layer.clin.reset_index(drop=True).copy()
    group = pd.Series(layer.group).reset_index(drop=True)
    if len(group) != len(clin):
        raise ValueError(f"Group 行数 {len(group)} 与临床 {len(clin)} 不一致")
    clin["Group"] = group.astype(str).to_numpy()
    rad = getattr(layer, "rad", None)
    if rad is not None and len(rad) > 0:
        rad = rad.reset_index(drop=True)
        if len(rad) != len(clin):
            raise ValueError(f"组学行数 {len(rad)} 与临床 {len(clin)} 不一致")
        for col in rad.columns:
            s = str(col)
            if s in clin.columns or s in ("Name", "Patient_ID", "PatientID"):
                continue
            clin[s] = rad[col].to_numpy()
    if extras:
        n = len(clin)
        for key, values in extras.items():
            name = str(key).strip()
            if not name or values is None:
                continue
            arr = np.asarray(values)
            if arr.ndim > 1:
                arr = np.asarray(arr, dtype=float).ravel()
            else:
                arr = np.asarray(arr)
            if len(arr) != n:
                raise ValueError(f"{name} 行数 {len(arr)} 与临床 {n} 不一致")
            clin[name] = arr
    return clin


def _design(df: pd.DataFrame, outcome: str, group: str, covariates: Sequence[str],
            reference: str) -> tuple[Any, pd.DataFrame, str]:
    cols = [outcome, group, *covariates]
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError("缺少列 " + ", ".join(missing))
    sub = df.loc[:, cols].copy()
    sub[outcome] = pd.to_numeric(sub[outcome], errors="coerce")
    sub[group] = sub[group].astype(str).str.strip()
    sub = sub.replace({"": np.nan}).dropna(subset=[outcome, group])
    terms = [
        "C(" + _qname(group) + ", Treatment(reference=" + json.dumps(reference) + "))"
    ]
    for cov in covariates:
        if _is_categorical(sub[cov], cov):
            sub[cov] = sub[cov].astype(str).str.strip()
            terms.append("C(" + _qname(cov) + ")")
        else:
            sub[cov] = pd.to_numeric(sub[cov], errors="coerce")
            terms.append(_qname(cov))
    sub = sub.dropna()
    levels = [str(x) for x in pd.unique(sub[group]) if str(x).strip()]
    if reference not in levels:
        raise ValueError(f"参照组 {reference} 不在 {group} 的取值 {levels} 中")
    if len(levels) < 2:
        raise ValueError(f"{group} 只有一个水平")
    if len(sub) < len(terms) + 5:
        raise ValueError(f"完整例数 {len(sub)} 过少")
    formula = _qname(outcome) + " ~ " + " + ".join(terms)
    model = smf.ols(formula, data=sub).fit()
    return model, sub, formula


def _group_index(model, group: str) -> list[int]:
    token = _qname(group)
    names = [str(n) for n in model.params.index]
    return [i for i, name in enumerate(names) if token in name and "[T." in name]


def _level_from_term(name: str) -> str:
    text = str(name)
    if "[T." not in text:
        return text
    return text.split("[T.", 1)[1].rstrip("]")


def _pretty_term(name: str, group: str, reference: str) -> str:
    text = str(name)
    if text == "Intercept":
        return "Intercept"
    if "[T." in text and _qname(group) in text:
        return f"{_level_from_term(text)} vs {reference}"
    if text.startswith("Q(") and text.endswith(")"):
        inner = text[2:-1].strip()
        if len(inner) >= 2 and inner[0] == inner[-1] and inner[0] in "\"'":
            inner = inner[1:-1]
        return inner
    if "[T." in text:
        return _level_from_term(text)
    return text


def _joint_group_test(model, group: str) -> tuple[float, float, float, float]:
    idx = _group_index(model, group)
    if not idx:
        return (np.nan, np.nan, np.nan, np.nan)
    names = list(model.params.index)
    R = np.zeros((len(idx), len(names)))
    for row, col in enumerate(idx):
        R[row, col] = 1.0
    tested = model.f_test(R)
    f_val = float(np.asarray(tested.fvalue).reshape(-1)[0])
    p_val = float(np.asarray(tested.pvalue).reshape(-1)[0])
    df1 = float(np.asarray(tested.df_num).reshape(-1)[0])
    df2 = float(np.asarray(tested.df_denom).reshape(-1)[0])
    return f_val, df1, df2, p_val


def _marginal_mean(model, data: pd.DataFrame, group: str, level: str) -> tuple[float, float, float]:
    newdata = data.copy()
    newdata[group] = level
    design = model.model.data.design_info
    mat = patsy.build_design_matrices([design], newdata, return_type="dataframe")[0]
    xbar = np.asarray(mat, dtype=float).mean(axis=0)
    beta = np.asarray(model.params, dtype=float)
    cov = np.asarray(model.cov_params(), dtype=float)
    mu = float(xbar @ beta)
    se = float(np.sqrt(max(0.0, float(xbar @ cov @ xbar))))
    df_resid = float(model.df_resid)
    crit = float(stats.t.ppf(0.975, df_resid)) if df_resid > 0 else 1.96
    return mu, mu - crit * se, mu + crit * se


def _positive_contrast(model, group: str, positive: str, reference: str) -> Optional[dict[str, Any]]:
    idx = _group_index(model, group)
    if not idx:
        return None
    names = [str(n) for n in model.params.index]
    chosen = idx[0]
    pos = str(positive or "").strip()
    if pos:
        for i in idx:
            if _level_from_term(names[i]) == pos:
                chosen = i
                break
    elif len(idx) == 1:
        chosen = idx[0]
    beta = float(model.params.iloc[chosen])
    se = float(model.bse.iloc[chosen])
    p_val = float(model.pvalues.iloc[chosen])
    lo, hi = [float(v) for v in model.conf_int().iloc[chosen].tolist()]
    level = _level_from_term(names[chosen])
    return {
        "contrast": f"{level} vs {reference}",
        "beta": beta,
        "se": se,
        "p": p_val,
        "lo": lo,
        "hi": hi,
    }


def fit_outcomes(df: pd.DataFrame, outcomes: Sequence[str], group: str,
                 covariates: Sequence[str], reference: str) -> tuple[dict[str, Any], list[str]]:
    fits: dict[str, Any] = {}
    notes: list[str] = []
    for outcome in _clean(outcomes):
        try:
            model, sub, formula = _design(df, outcome, group, covariates, reference)
        except Exception as exc:
            notes.append(f"{outcome}: {exc}")
            continue
        fits[outcome] = {"model": model, "data": sub, "formula": formula}
    return fits, notes


def _attach_adjust(rows: list[dict[str, Any]], do_fdr: bool, do_fwer: bool,
                   qmap: Optional[dict[str, tuple[float, float]]] = None) -> None:
    if not rows:
        return
    if qmap is not None:
        for row in rows:
            fdr, fwer = qmap.get(str(row["Outcome"]), (np.nan, np.nan))
            if do_fdr:
                row["FDR P"] = fdr
            if do_fwer:
                row["FWER P"] = fwer
        return
    if not do_fdr and not do_fwer:
        return
    raw = [row.get("_p_family", row.get("P")) for row in rows]
    fdr = bh_fdr(raw) if do_fdr else None
    fwer = holm_fwer(raw) if do_fwer else None
    for i, row in enumerate(rows):
        if fdr is not None:
            row["FDR P"] = float(fdr[i])
        if fwer is not None:
            row["FWER P"] = float(fwer[i])


def tables_from_fits(fits: dict[str, Any], outcomes: Sequence[str], group: str,
                     covariates: Sequence[str], reference: str, positive: str,
                     do_fdr: bool, do_fwer: bool,
                     qmap: Optional[dict[str, tuple[float, float]]] = None,
                     ) -> dict[str, Any]:
    order = [y for y in _clean(outcomes) if y in fits]
    params: list[dict[str, Any]] = []
    means: list[dict[str, Any]] = []
    betas: list[dict[str, Any]] = []
    levels_order: list[str] = []
    family: list[tuple[str, float]] = []
    for outcome in order:
        packed = fits[outcome]
        model = packed["model"]
        sub = packed["data"]
        for name in model.params.index:
            i = list(model.params.index).index(name)
            lo, hi = [float(v) for v in model.conf_int().iloc[i].tolist()]
            params.append({
                "Outcome": outcome,
                "Term": _pretty_term(str(name), group, reference),
                "Estimate": float(model.params.iloc[i]),
                "SE": float(model.bse.iloc[i]),
                "t": float(model.tvalues.iloc[i]),
                "P": float(model.pvalues.iloc[i]),
                "CI_low": lo,
                "CI_high": hi,
            })
        f_val, df1, df2, p_joint = _joint_group_test(model, group)
        family.append((outcome, p_joint))
        present = [str(x) for x in pd.unique(sub[group]) if str(x).strip()]
        ordered_levels = []
        pos = str(positive or "").strip()
        if pos in present:
            ordered_levels.append(pos)
        if reference in present and reference not in ordered_levels:
            ordered_levels.append(reference)
        for lev in present:
            if lev not in ordered_levels:
                ordered_levels.append(lev)
        for lev in ordered_levels:
            if lev not in levels_order:
                levels_order.append(lev)
        mean_row: dict[str, Any] = {
            "Outcome": outcome,
            "F": f_val,
            "df1": df1,
            "df2": df2,
            "P": p_joint,
            "_p_family": p_joint,
            "N": int(len(sub)),
        }
        for lev in ordered_levels:
            mu, lo, hi = _marginal_mean(model, sub, group, lev)
            mean_row[f"Adj. mean {lev}"] = mu
            mean_row[f"CI_low {lev}"] = lo
            mean_row[f"CI_high {lev}"] = hi
        means.append(mean_row)
        contrast = _positive_contrast(model, group, positive, reference)
        if contrast is not None:
            betas.append({
                "Outcome": outcome,
                "Contrast": contrast["contrast"],
                "β": contrast["beta"],
                "CI_low": contrast["lo"],
                "CI_high": contrast["hi"],
                "P": contrast["p"],
                "_p_family": p_joint,
                "N": int(len(sub)),
            })
    if qmap is None and (do_fdr or do_fwer):
        raw = [p for _y, p in family]
        fdr_v = bh_fdr(raw) if do_fdr else None
        fwer_v = holm_fwer(raw) if do_fwer else None
        qmap = {}
        for i, (outcome, _p) in enumerate(family):
            qmap[outcome] = (
                float(fdr_v[i]) if fdr_v is not None else np.nan,
                float(fwer_v[i]) if fwer_v is not None else np.nan,
            )
    _attach_adjust(means, do_fdr, do_fwer, qmap)
    _attach_adjust(betas, do_fdr, do_fwer, qmap)
    for row in means + betas:
        row.pop("_p_family", None)
    cov_txt = ", ".join(covariates) if covariates else "none"
    formula = f"outcome ~ {group} ({positive or 'non-reference'} vs {reference}) + {cov_txt}"
    return {
        "params": pd.DataFrame(params),
        "means": pd.DataFrame(means),
        "betas": pd.DataFrame(betas),
        "qmap": qmap or {},
        "formula": formula,
        "n_family": len(family),
    }


def partial_corr(df: pd.DataFrame, x: str, y: str, controls: Sequence[str],
                 method: str = "spearman") -> dict[str, Any]:
    """残差偏相关。Spearman：连续变量先秩变换，分类控制变量保持哑变量。"""
    method = str(method or "spearman").strip().lower()
    if method not in ("spearman", "pearson"):
        method = "spearman"
    controls = [c for c in _clean(controls) if c not in (x, y)]
    cols = [x, y, *controls]
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError("缺少列 " + ", ".join(missing))
    sub = df.loc[:, cols].copy()
    cat_cols = [c for c in controls if _is_categorical(sub[c], c)]
    num_cols = [c for c in controls if c not in cat_cols]
    for col in [x, y, *num_cols]:
        sub[col] = pd.to_numeric(sub[col], errors="coerce")
    for col in cat_cols:
        sub[col] = sub[col].astype(str).str.strip()
    sub = sub.dropna()
    n = int(len(sub))
    if n < len(controls) + 5:
        raise ValueError(f"完整例数 {n} 过少")
    work = sub.copy()
    if method == "spearman":
        for col in [x, y, *num_cols]:
            work[col] = work[col].rank(method="average")
    pieces = [np.ones(n)]
    for col in num_cols:
        pieces.append(pd.to_numeric(work[col], errors="coerce").to_numpy(dtype=float))
    if cat_cols:
        dummies = pd.get_dummies(work[cat_cols], drop_first=True, dtype=float)
        if dummies.shape[1]:
            pieces.append(dummies.to_numpy(dtype=float))
    Z = np.column_stack(pieces)
    k = int(Z.shape[1] - 1)

    def _resid(vec: np.ndarray) -> np.ndarray:
        coef, *_rest = np.linalg.lstsq(Z, vec, rcond=None)
        return vec - Z @ coef

    vx = work[x].to_numpy(dtype=float)
    vy = work[y].to_numpy(dtype=float)
    rx, ry = _resid(vx), _resid(vy)
    if np.allclose(rx, rx[0]) or np.allclose(ry, ry[0]):
        raise ValueError("残差没有变异")
    r = float(np.corrcoef(rx, ry)[0, 1])
    df = n - 2 - k
    if df <= 0 or not np.isfinite(r) or abs(r) >= 1:
        p_val = float("nan")
    else:
        t_val = r * np.sqrt(df / max(1e-15, 1.0 - r * r))
        p_val = float(2 * stats.t.sf(abs(t_val), df))
    se_z = 1.0 / np.sqrt(max(n - k - 3, 1))
    z = np.arctanh(np.clip(r, -0.999999, 0.999999))
    lo, hi = float(np.tanh(z - 1.96 * se_z)), float(np.tanh(z + 1.96 * se_z))
    return {
        "x": x,
        "y": y,
        "controls": controls,
        "method": method,
        "r": r,
        "lo": lo,
        "hi": hi,
        "p": p_val,
        "n": n,
        "df": int(df),
        "k": k,
    }


def _spec_key(outcomes: Sequence[str], group: str, covariates: Sequence[str]) -> tuple:
    return (
        frozenset(_clean(outcomes)),
        str(group or "").strip(),
        frozenset(_clean(covariates)),
    )


def run_assoc(
    layer,
    extras: Optional[dict[str, Any]] = None,
    rad_selected: Optional[Sequence[str]] = None,
    clin_selected: Optional[Sequence[str]] = None,
    score_names: Optional[Sequence[str]] = None,
) -> Optional[dict[str, Any]]:
    """读 cfg。因变量为空且偏相关未配对时返回 None。

    extras：合并进分析框的派生列（RadScore / Clinical / Combined）。
    rad_selected / clin_selected：把 ANCOVA/MLR 因变量与协变量、PARTIAL_X/Y/
    CONTROLS 里的 RadScore / Clinical 展开为「评分本身 + 入选成分特征」。
    """
    from modules.utils import dry_run as _dry
    from modules.stats.correlation import expand_corr_predictors, resolve_named_column
    if _dry.exit_after_plan("assoc", [
        "ANCOVA / MLR / partial (skipped)",
    ]):
        return None
    _kw = dict(
        rad_selected=rad_selected,
        clin_selected=clin_selected,
        score_names=score_names,
    )
    anc_y = expand_corr_predictors(
        _as_name_list(getattr(cfg, "ANCOVA_OUTCOMES", None)), **_kw,
    )
    anc_g = str(getattr(cfg, "ANCOVA_GROUP", "") or "").strip() or "Group"
    anc_c = expand_corr_predictors(
        _as_name_list(getattr(cfg, "ANCOVA_COVARIATES", None)), **_kw,
    )
    mlr_y = expand_corr_predictors(
        _as_name_list(getattr(cfg, "MLR_OUTCOMES", None)), **_kw,
    )
    mlr_g = str(getattr(cfg, "MLR_GROUP", "") or "").strip() or "Group"
    mlr_c = expand_corr_predictors(
        _as_name_list(getattr(cfg, "MLR_COVARIATES", None)), **_kw,
    )
    xs = expand_corr_predictors(
        _as_name_list(getattr(cfg, "PARTIAL_X", None)), **_kw,
    )
    ys = expand_corr_predictors(
        _as_name_list(getattr(cfg, "PARTIAL_Y", None)), **_kw,
    )
    pc = expand_corr_predictors(
        _as_name_list(getattr(cfg, "PARTIAL_CONTROLS", None)), **_kw,
    )
    method = str(getattr(cfg, "PARTIAL_METHOD", "") or "spearman").strip().lower()
    do_fdr = bool(getattr(cfg, "DO_FDR", False))
    do_fwer = bool(getattr(cfg, "DO_FWER", False))
    if not bool(getattr(cfg, "DO_ANCOVA", False)):
        anc_y = []
    if not bool(getattr(cfg, "DO_MLR", False)):
        mlr_y = []
    if not bool(getattr(cfg, "DO_PARTIAL", False)):
        xs, ys = [], []
    pairs = _partial_pairs(xs, ys)
    if not anc_y and not mlr_y and not pairs:
        return None
    frame = analysis_frame(layer, extras=extras)
    positive = str(getattr(cfg, "POSITIVE_GROUP_LABEL", "") or "").strip()
    negative = str(getattr(cfg, "GROUP_DISPLAY_NEG", "") or "").strip()
    out: dict[str, Any] = {
        "do_fdr": do_fdr,
        "do_fwer": do_fwer,
        "warnings": [],
        "shared_family": False,
    }
    same = bool(anc_y and mlr_y and _spec_key(anc_y, anc_g, anc_c) == _spec_key(mlr_y, mlr_g, mlr_c))
    if anc_y or (same and mlr_y):
        levels = _present_levels(frame[anc_g]) if anc_g in frame.columns else []
        reference = _reference_level(levels, positive, negative)
        fits, notes = fit_outcomes(frame, anc_y or mlr_y, anc_g, anc_c, reference)
        out["warnings"].extend(notes)
        packed = tables_from_fits(
            fits, anc_y or mlr_y, anc_g, anc_c, reference, positive,
            do_fdr, do_fwer,
        )
        if anc_y:
            out["ancova"] = packed
        if same:
            out["mlr"] = packed
            out["shared_family"] = True
    if mlr_y and not same:
        levels = _present_levels(frame[mlr_g]) if mlr_g in frame.columns else []
        reference = _reference_level(levels, positive, negative)
        fits, notes = fit_outcomes(frame, mlr_y, mlr_g, mlr_c, reference)
        out["warnings"].extend(notes)
        out["mlr"] = tables_from_fits(
            fits, mlr_y, mlr_g, mlr_c, reference, positive, do_fdr, do_fwer,
        )
    partial_rows = []
    partial_frame = frame
    partial_group = str(getattr(cfg, "PARTIAL_GROUP", "") or "").strip()
    if pairs and partial_group:
        try:
            partial_frame = subset_frame_by_group(frame, partial_group)
            print(f"  Partial group: {partial_group} (N={len(partial_frame)})")
        except ValueError as exc:
            out["warnings"].append(str(exc))
            pairs = []
    elif pairs:
        print("  Partial group: 全部")
    for px, py in pairs:
        try:
            # 短名（Age）对到展示列（Age (years)）。对不上就停，不写成警告。
            names = list(partial_frame.columns)
            rx = resolve_named_column(px, names)
            ry = resolve_named_column(py, names)
            rcontrols = [resolve_named_column(c, names) for c in pc]
            row = partial_corr(partial_frame, rx, ry, rcontrols, method)
            if partial_group:
                row["group"] = partial_group
            partial_rows.append(row)
        except ValueError as exc:
            text = str(exc)
            if text.startswith("找不到列") or text.startswith("缺少列") or "对上不止一列" in text:
                raise
            out["warnings"].append(f"偏相关 {px} ~ {py}: {exc}")
        except Exception as exc:
            out["warnings"].append(f"偏相关 {px} ~ {py}: {exc}")
    if partial_rows:
        out["partial"] = partial_rows
    if not out.get("ancova") and not out.get("mlr") and not out.get("partial") and not out["warnings"]:
        return None
    return out
