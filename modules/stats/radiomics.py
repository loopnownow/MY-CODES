"""
radiomics.py — 影像组学全管线：预处理 → LASSO 或 Logistic 特征选择 → RadScore
═══════════════════════════════════════════════════════════════════
功能：对影像组学特征矩阵执行完整的特征工程和建模流程：
    Step 1 — 预处理（zero-variance 剔除 + 中位数插补 + 标准化 + near-zero-variance 剔除）
    Step 2 — LASSO / 弹性网交叉验证特征选择（默认 10-fold CV，以 AUC 优化）
    Step 3 — 在原始尺度上拟合 OLS，构建 RadScore
    另：DO_RAD_LOGISTIC 时走 run_radiomics_logistic（与临床相同的 Logistic 筛选，界值用 RAD_LOGIT_*）。
    验证层「重新拟合」锁定入选特征与训练集标准化，只重估系数。

预处理与建模均使用 numpy / pandas / sklearn / statsmodels 实现。
高相关过滤由 config.ENABLE_CORR_FILTER 与 config.RAD_COR_CUTOFF 控制。
正式入口：python -m modules.pipeline
═══════════════════════════════════════════════════════════════════
"""

from __future__ import annotations
from dataclasses import dataclass
import hashlib
import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import warnings
import time
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold
from sklearn.metrics import roc_auc_score
from sklearn.exceptions import ConvergenceWarning
import joblib
from joblib import Parallel, delayed
import statsmodels.api as sm

from modules import config as cfg

# 缓存：同一 INPUT 只读一次 ICC 表
_ICC_CACHE: Dict[str, Optional[pd.DataFrame]] = {}

# saga 在较小数据集或严格容差下可能未完全收敛，忽略该警告以保持日志可读。
warnings.filterwarnings("ignore", category=ConvergenceWarning)

# ═════════════════════════════════════════════════════════════════
# §1  顶层可调参数
# ═════════════════════════════════════════════════════════════════
# near-zero-variance 判据：频率比阈值、唯一值占比(%) 阈值
NZV_FREQ_CUT = 95 / 5
NZV_UNIQUE_CUT = 10


@dataclass
class RadiomicsResult:
    features: List[str]                    # LASSO 选中的特征名
    coefs: np.ndarray                      # LASSO 系数（标准化尺度）
    radscore: Optional[np.ndarray]         # RadScore 向量（原始尺度 OLS 线性组合）
    intercept: Optional[float]             # OLS 截距
    betas: Optional[pd.Series]             # OLS 各特征系数（原始尺度）
    ols: Optional[sm.OLS]                  # statsmodels OLS 拟合结果
    lambda_used: float                     # 最优正则化强度（1/C）
    formula_df: Optional[pd.DataFrame]     # 公式系数表
    formula_text: Optional[str]            # 公式文本（HTML 格式，供报告使用）
    method: str = "lasso"                  # lasso | logistic
    preprocess: Optional[dict] = None      # 训练集预处理参数；logistic 套公式时使用
    uni_res: Optional[pd.DataFrame] = None
    mv_df: Optional[pd.DataFrame] = None
    mv_note: Optional[str] = None
    mv_selected: Optional[pd.DataFrame] = None
    candidates: Optional[List[str]] = None
    stepwise_features: Optional[List[str]] = None
    select_source: Optional[str] = None
    firth_uni: Optional[pd.DataFrame] = None
    firth_mv: Optional[pd.DataFrame] = None
    table2: Optional[pd.DataFrame] = None  # Baseline-style group table for rad pool
    groups: Optional[list] = None          # per-category logistic results; no pooled score
    group_name: Optional[str] = None
    score_name: Optional[str] = None


_ID_LIKE = ("Name", "patient_id", "Patient_ID", "Group")


def _id_like_names() -> set[str]:
    names = set(_ID_LIKE)
    rid = str(getattr(cfg, "RAD_ID_COL", "") or "").strip()
    if rid:
        names.add(rid)
    for c in list(getattr(cfg, "RAD_ID_COLS", None) or []):
        s = str(c).strip()
        if s:
            names.add(s)
    return names


def _rad_feature_frame(full_rad: pd.DataFrame) -> pd.DataFrame:
    """Drop ID-like columns; remaining columns are treated as radiomics features."""
    id_like = _id_like_names()
    drop = [c for c in full_rad.columns if c in id_like]
    return full_rad.drop(columns=drop, errors="ignore")


def _fit_preprocess(
    full_rad: pd.DataFrame,
    cutoff: Optional[float] = None,
    enable_corr: Optional[bool] = None,
    verbose: bool = False,
) -> Tuple[pd.DataFrame, dict]:
    """
    Fit radiomics preprocess on this matrix only (fold-train or final train).

    Steps: drop IDs → zv → median impute → center/scale → nzv → optional Spearman filter.
    Returns (transformed X, params) for `_apply_preprocess`.
    """
    if cutoff is None:
        cutoff = float(getattr(cfg, "RAD_COR_CUTOFF", 0.95))
    if enable_corr is None:
        enable_corr = bool(getattr(cfg, "ENABLE_CORR_FILTER", True))
    X = _rad_feature_frame(full_rad).apply(pd.to_numeric, errors="coerce").copy()

    variances = X.var(skipna=True)
    X = X.loc[:, variances[variances > 0].index]

    medians = X.median()
    X = X.fillna(medians)

    means = X.mean()
    stds = X.std(ddof=1).replace(0, 1.0)
    X = (X - means) / stds

    keep_cols = []
    for col in X.columns:
        vc = X[col].value_counts()
        if len(vc) < 2:
            continue
        freq_ratio = vc.iloc[0] / vc.iloc[1]
        unique_pct = 100 * len(vc) / len(X[col])
        if freq_ratio > NZV_FREQ_CUT and unique_pct < NZV_UNIQUE_CUT:
            continue
        keep_cols.append(col)
    X = X[keep_cols]

    if enable_corr and cutoff is not None and cutoff < 1.0 and X.shape[1] > 1:
        corr = X.corr(method="spearman").abs()
        upper = corr.where(np.triu(np.ones(corr.shape), k=1).astype(bool))
        to_drop = set()
        for col in upper.columns:
            high = upper.index[upper[col] > cutoff].tolist()
            for other in high:
                if other in to_drop or col in to_drop:
                    continue
                if X[col].var() >= X[other].var():
                    to_drop.add(other)
                else:
                    to_drop.add(col)
        if to_drop:
            X = X.drop(columns=list(to_drop))
            if verbose:
                print(f"  高相关过滤(|r|>{cutoff}): 删除 {len(to_drop)} 特征")

    params = {
        "medians": medians,
        "means": means,
        "stds": stds,
        "keep_cols": list(X.columns),
        "cutoff": cutoff,
        "enable_corr": enable_corr,
    }
    return X, params


def _apply_preprocess(full_rad: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Apply fold-train / final-train preprocess params; never re-estimate."""
    X = _rad_feature_frame(full_rad).apply(pd.to_numeric, errors="coerce").copy()
    cols = list(params["medians"].index)
    X = X.reindex(columns=cols)
    X = X.fillna(params["medians"])
    X = (X - params["means"]) / params["stds"]
    return X.reindex(columns=list(params["keep_cols"]))



def _normalize_feature_source(source: str) -> str:
    """Map config aliases → canonical token: fbn_32 | fbs_16 | both."""
    s = str(source or "both").strip().lower()
    if s in ("fbn_32", "fbn32", "mri", "mr"):
        return "fbn_32"
    if s in ("fbs_16", "fbs16", "ct"):
        return "fbs_16"
    if s in ("both", "all", "combined", ""):
        return "both"
    raise ValueError(
        f"RAD_FEATURE_SOURCE={source!r} 无效；请用 fbn_32/MRI, fbs_16/CT, 或 both"
    )


def _filter_rad_by_source(full_rad: pd.DataFrame,
                           source: Optional[str] = None) -> pd.DataFrame:
    """
    按列名中的 fbn_32 / fbs_16 过滤组学特征。
    非特征列（Name/patient_id/Group）始终保留。
    """
    source = _normalize_feature_source(source if source is not None else cfg.RAD_FEATURE_SOURCE)
    id_like = _id_like_names()
    id_cols = [c for c in full_rad.columns if c in id_like]
    feat_cols = [c for c in full_rad.columns if c not in id_like]
    n0 = len(feat_cols)

    if source == "both":
        print(f"  特征源过滤 [both]: {n0}/{n0} 保留")
        return full_rad

    token = source  # fbn_32 or fbs_16
    kept = [c for c in feat_cols if token in str(c)]
    if len(kept) == 0:
        raise ValueError(
            f"特征源过滤 [{token}] 后无特征剩余（原始特征列 {n0}）"
        )
    label = "MRI/fbn_32" if token == "fbn_32" else "CT/fbs_16"
    print(f"  特征源过滤 [{label}]: {len(kept)}/{n0} 保留")
    return full_rad[id_cols + kept].copy()


def _load_icc_table() -> Optional[pd.DataFrame]:
    """从 INPUT 的 icc sheet 读取 ICC 表（缓存）。"""
    path = str(getattr(cfg, "INPUT", "data_merged.xlsx") or "data_merged.xlsx")
    sheet = str(getattr(cfg, "ICC_SHEET", "") or "").strip()
    if not sheet:
        print("  ICC table skipped (ICC_SHEET empty)")
        return None
    key = f"{path}::{sheet}"
    if key in _ICC_CACHE:
        return _ICC_CACHE[key]
    try:
        df = pd.read_excel(path, sheet_name=sheet)
        _ICC_CACHE[key] = df
        print(f"  ICC table loaded: {path} [{sheet}]  n={len(df)}")
        return df
    except Exception as e:
        print(f"  ⚠ ICC table not loaded ({path}/{sheet}): {e}")
        _ICC_CACHE[key] = None
        return None


def _build_icc_lookup(icc: pd.DataFrame) -> Dict[Tuple[Optional[str], str], Tuple[float, float]]:
    """
    构建 (mod|None|"CODE", base) → (intra, inter)。

    支持：elongation_Q3CK、C_original_mean_intensity_Q4LE、
    original_mean_intensity_Q4LE（habitat / 无模态前缀）。
    """
    feat_col = str(getattr(cfg, "ICC_FEATURE_COL", "Pictologics_feature"))
    intra_col = str(getattr(cfg, "ICC_INTRA_COL", "intra_ICC"))
    inter_col = str(getattr(cfg, "ICC_INTER_COL", "inter_ICC"))
    lookup: Dict[Tuple[Optional[str], str], Tuple[float, float]] = {}
    if feat_col not in icc.columns:
        print(f"  ⚠ ICC missing column {feat_col!r}")
        return lookup
    n_original = 0
    for _, row in icc.iterrows():
        p = row.get(feat_col)
        if pd.isna(p):
            continue
        p = str(p).strip()
        if not p or p.lower() == "nan":
            continue
        m = re.match(r"^(C|D|T|ADC|DWI\d*)_original_(.+)$", p, flags=re.I)
        if m:
            key: Tuple[Optional[str], str] = (m.group(1).upper(), m.group(2))
        else:
            key = (None, p)
        try:
            intra = float(row[intra_col]) if intra_col in icc.columns else np.nan
            inter = float(row[inter_col]) if inter_col in icc.columns else np.nan
        except (TypeError, ValueError):
            continue
        lookup[key] = (intra, inter)
        mo = re.match(r"^original_(.+)$", p, flags=re.I)
        if mo:
            base = mo.group(1)
            n_original += 1
            lookup[(None, base)] = (intra, inter)
            cm = re.search(r"_([A-Za-z0-9]{4})$", base)
            if cm:
                ck: Tuple[Optional[str], str] = ("CODE", cm.group(1).upper())
                if ck not in lookup:
                    lookup[ck] = (intra, inter)
    if n_original:
        print(f"  ICC lookup: original_ rows={n_original}  keys={len(lookup)}")
    return lookup


def _match_rad_icc(
    feat: str,
    lookup: Dict[Tuple[Optional[str], str], Tuple[float, float]],
) -> Optional[Tuple[float, float]]:
    """将 rad 列名映射到 ICC (intra, inter)。"""
    s = str(feat)
    m = re.match(
        r"^h\d_([A-Za-z])_standard_(?:fbn_32|fbs_16)_(.+)$",
        s,
    )
    if m:
        mod, base = m.group(1).upper(), m.group(2)
        if (mod, base) in lookup:
            return lookup[(mod, base)]
        if (None, base) in lookup:
            return lookup[(None, base)]
    m = re.match(
        r"^h\d+_delta_(?:abs|pct)_h\d+_standard_(?:fbn_32|fbs_16)_(.+)$",
        s,
    )
    if m:
        base = m.group(1)
        if (None, base) in lookup:
            return lookup[(None, base)]
        if (None, f"original_{base}") in lookup:
            return lookup[(None, f"original_{base}")]
    for (mod, base), val in lookup.items():
        if mod is not None or not base:
            continue
        if s.endswith("_" + base) or s.endswith(base):
            return val
    cm = re.search(r"_([A-Za-z0-9]{4})$", s)
    if cm:
        k: Tuple[Optional[str], str] = ("CODE", cm.group(1).upper())
        if k in lookup:
            return lookup[k]
    return None


def _filter_rad_by_icc(full_rad: pd.DataFrame) -> pd.DataFrame:
    """
    LASSO 前 ICC 筛选：匹配到 ICC 的特征须 intra 与 inter 均 ≥ 阈值；
    无 ICC 行默认保留（ICC_REQUIRE_ROW=True 则剔除）。
    """
    if not bool(getattr(cfg, "DO_ICC_FILTER", True)):
        return full_rad
    thr = float(getattr(cfg, "ICC_THRESHOLD", 0.75))
    require_row = bool(getattr(cfg, "ICC_REQUIRE_ROW", False))
    icc = _load_icc_table()
    if icc is None or len(icc) == 0:
        print("  ICC filter skipped (no table)")
        return full_rad

    lookup = _build_icc_lookup(icc)
    if not lookup:
        print("  ICC filter skipped (empty lookup)")
        return full_rad

    id_like = _id_like_names()
    id_cols = [c for c in full_rad.columns if c in id_like]
    feat_cols = [c for c in full_rad.columns if c not in id_like]

    keep, n_pass, n_fail, n_miss = [], 0, 0, 0
    for f in feat_cols:
        val = _match_rad_icc(f, lookup)
        if val is None:
            n_miss += 1
            if not require_row:
                keep.append(f)
            continue
        intra, inter = val
        if (
            np.isfinite(intra) and np.isfinite(inter)
            and intra >= thr and inter >= thr
        ):
            n_pass += 1
            keep.append(f)
        else:
            n_fail += 1

    print(
        f"  ICC filter (thr={thr:g}): keep {len(keep)}/{len(feat_cols)}  "
        f"(pass={n_pass}, fail_icc={n_fail}, no_icc_row={n_miss}"
        f"{' excluded' if require_row else ' kept'})"
    )
    return full_rad[id_cols + keep].copy()


def display_rad_feature(name: str) -> str:
    """缩短组学特征显示名。只剥列名开头或结尾的标记，不改内部列名。"""
    from modules.stats.data_io import _marker_anchor, _strip_anchored_token

    s = str(name)
    raw = getattr(cfg, "RAD_FEATURE_DISPLAY_STRIP", None)
    strips = [] if raw is None else [str(t) for t in list(raw) if t]
    for tok in sorted(strips, key=lambda t: len(_marker_anchor(t)[1]), reverse=True):
        s = _strip_anchored_token(s, tok)
    while "__" in s:
        s = s.replace("__", "_")
    return s.strip("_")


def _prefilter_by_auc(
    X_df: pd.DataFrame, y: np.ndarray, topk: int, verbose: bool = True,
) -> pd.DataFrame:
    """LASSO 前特征数上限。必须只在当前训练折 / 最终训练集上调用。

    - topk<=0：关闭预筛
    - n_features <= topk：不预筛
    - n_features > topk：强制按单特征 |AUC-0.5| 保留前 topk 个
    """
    n0 = int(X_df.shape[1])
    k = int(topk) if topk is not None else 0
    if k <= 0:
        if verbose:
            print(f"  LASSO prefilter: off (n={n0})", flush=True)
        return X_df
    if n0 <= k:
        if verbose:
            print(f"  LASSO prefilter: skip (n={n0} ≤ topk={k})", flush=True)
        return X_df

    y = np.asarray(y).astype(int)
    if len(np.unique(y)) < 2:
        if verbose:
            print(f"  LASSO prefilter: skip (single class, n={n0})", flush=True)
        return X_df

    scores = []
    for col in X_df.columns:
        x = pd.to_numeric(X_df[col], errors="coerce").to_numpy()
        m = np.isfinite(x)
        if m.sum() < 10 or len(np.unique(y[m])) < 2:
            scores.append((col, 0.0))
            continue
        try:
            auc = float(roc_auc_score(y[m], x[m]))
            scores.append((col, abs(auc - 0.5)))
        except Exception:
            scores.append((col, 0.0))
    scores.sort(key=lambda t: t[1], reverse=True)
    keep = [c for c, _ in scores[:k]]
    if verbose:
        print(
            f"  LASSO prefilter: n={n0} > topk={k} → force top-{len(keep)} by |AUC-0.5|",
            flush=True,
        )
    return X_df[keep]


def _fold_lasso_path_auc(
    Xtr: np.ndarray,
    ytr: np.ndarray,
    Xva: np.ndarray,
    yva: np.ndarray,
    Cs: np.ndarray,
    max_iter: int,
    tol: float,
    seed: int,
    alpha: float,
) -> np.ndarray:
    """单折：沿 C 从小→大 warm-start 拟合 L1/elasticnet，返回各 C 的 val AUC。"""
    scores = np.full(len(Cs), np.nan, dtype=float)
    if len(np.unique(yva)) < 2 or len(np.unique(ytr)) < 2:
        return scores

    use_l1 = float(alpha) >= 1.0 - 1e-12
    if use_l1:
        clf = LogisticRegression(
            penalty="l1", solver="liblinear",
            max_iter=max_iter, tol=tol, random_state=seed,
            warm_start=True, fit_intercept=True,
        )
    else:
        clf = LogisticRegression(
            penalty="elasticnet", solver="saga", l1_ratio=float(alpha),
            max_iter=max_iter, tol=tol, random_state=seed,
            warm_start=True, fit_intercept=True,
        )

    for i, C in enumerate(Cs):
        clf.set_params(C=float(C))
        clf.fit(Xtr, ytr)
        proba = clf.predict_proba(Xva)[:, 1]
        scores[i] = float(roc_auc_score(yva, proba))
    return scores


def _fold_preprocess_and_path(
    X_df: pd.DataFrame,
    y: np.ndarray,
    tr_idx: np.ndarray,
    va_idx: np.ndarray,
    Cs: np.ndarray,
    max_iter: int,
    tol: float,
    seed: int,
    alpha: float,
    topk: int,
    cutoff: float,
    enable_corr: bool,
) -> np.ndarray:
    """One CV fold: fit impute/scale/NZV/corr/AUC-prefilter on fold-train only."""
    Xtr_raw = X_df.iloc[list(tr_idx)]
    Xva_raw = X_df.iloc[list(va_idx)]
    ytr = np.asarray(y)[tr_idx]
    yva = np.asarray(y)[va_idx]
    Xtr_s, params = _fit_preprocess(
        Xtr_raw, cutoff=cutoff, enable_corr=enable_corr, verbose=False,
    )
    Xtr_s = _prefilter_by_auc(Xtr_s, ytr, topk, verbose=False)
    Xva_s = _apply_preprocess(Xva_raw, params).reindex(columns=list(Xtr_s.columns))
    Xva_s = Xva_s.fillna(0.0)
    if Xtr_s.shape[1] == 0:
        return np.full(len(Cs), np.nan, dtype=float)
    return _fold_lasso_path_auc(
        np.ascontiguousarray(Xtr_s.to_numpy(), dtype=np.float64), ytr,
        np.ascontiguousarray(Xva_s.to_numpy(), dtype=np.float64), yva,
        Cs, max_iter, tol, seed, alpha,
    )


_LASSO_CACHE_VERSION = 1


def _lasso_cache_dir() -> Path:
    """项目 ref/cache/lasso。测试可替换这个函数。"""
    from modules.config.ini_io import project_ref_dir
    d = project_ref_dir() / "cache" / "lasso"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _lasso_cache_digest(X_df: pd.DataFrame, y: np.ndarray, params: dict) -> str:
    """训练矩阵、标签和会改变入选的参数。并行核数不在键里。"""
    h = hashlib.sha256()
    h.update(json.dumps(params, sort_keys=True, separators=(",", ":")).encode())
    h.update(b"\0")
    h.update("\n".join(str(c) for c in X_df.columns).encode())
    num = X_df.apply(pd.to_numeric, errors="coerce")
    arr = np.ascontiguousarray(num.to_numpy(dtype=np.float64))
    h.update(str(arr.shape).encode())
    h.update(arr.tobytes())
    yy = np.ascontiguousarray(np.asarray(y).astype(np.int64))
    h.update(yy.tobytes())
    return h.hexdigest()


def _lasso_cache_params(
    alpha: float, nfolds: int, n_cs: int, seed: int, max_iter: int, topk: int,
    c_min: float, c_max: float, tol: float, cutoff: float, enable_corr: bool,
) -> dict:
    import sklearn
    return {
        "cache_version": _LASSO_CACHE_VERSION,
        "sklearn": sklearn.__version__,
        "alpha": float(alpha),
        "nfolds": int(nfolds),
        "n_cs": int(n_cs),
        "seed": int(seed),
        "max_iter": int(max_iter),
        "topk": int(topk or 0),
        "c_min": float(c_min),
        "c_max": float(c_max),
        "tol": float(tol),
        "cutoff": float(cutoff),
        "enable_corr": bool(enable_corr),
    }


def _lasso_cv_view(Cs, best_C: float, score_mat, coef_vec) -> object:
    class _LassoCVView:
        pass

    view = _LassoCVView()
    view.Cs_ = np.asarray(Cs, dtype=float)
    view.C_ = np.array([float(best_C)])
    view.scores_ = {1: np.asarray(score_mat, dtype=float)}
    coef = np.asarray(coef_vec, dtype=float).ravel()
    view.coef_ = coef.reshape(1, -1)
    view.coefs_paths_ = None
    return view


def _lasso_cache_load(digest: str) -> Optional[dict]:
    path = _lasso_cache_dir() / f"{digest}.joblib"
    if not path.is_file():
        return None
    try:
        payload = joblib.load(path)
    except Exception as exc:
        print(f"  LASSO cache unreadable {digest[:8]} ({exc}) — refit", flush=True)
        return None
    if not isinstance(payload, dict) or int(payload.get("cache_version", 0)) != _LASSO_CACHE_VERSION:
        print(f"  LASSO cache {digest[:8]} version mismatch — refit", flush=True)
        return None
    need = ("best_C", "coef_vec", "mean_scores", "Cs", "score_mat", "columns")
    if any(k not in payload for k in need):
        print(f"  LASSO cache {digest[:8]} incomplete — refit", flush=True)
        return None
    return payload


def _lasso_cache_store(
    digest: str, best_C: float, coef_vec, mean_scores, Cs, score_mat, columns, params: dict,
) -> None:
    try:
        directory = _lasso_cache_dir()
    except Exception as exc:
        print(f"  LASSO cache dir failed ({exc})", flush=True)
        return
    coef = np.asarray(coef_vec, dtype=float).ravel()
    payload = {
        "cache_version": _LASSO_CACHE_VERSION,
        "best_C": float(best_C),
        "coef_vec": coef,
        "mean_scores": np.asarray(mean_scores, dtype=float),
        "Cs": np.asarray(Cs, dtype=float),
        "score_mat": np.asarray(score_mat, dtype=float),
        "columns": [str(c) for c in columns],
        "params": params,
    }
    path = directory / f"{digest}.joblib"
    tmp = directory / f"{digest}.joblib.tmp"
    try:
        joblib.dump(payload, tmp)
        tmp.replace(path)
        meta = {
            "cache_version": _LASSO_CACHE_VERSION,
            "best_C": float(best_C),
            "n_features_fit": len(payload["columns"]),
            "n_selected": int(np.count_nonzero(coef)),
            "params": params,
        }
        (directory / f"{digest}.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8",
        )
    except Exception as exc:
        print(f"  LASSO cache write failed ({exc})", flush=True)
        return
    print(f"  LASSO cache write {digest[:8]}", flush=True)


def _lasso_cache_hit(
    digest: str, X_df: pd.DataFrame, y: np.ndarray, topk: int,
    cutoff: float, enable_corr: bool,
):
    payload = _lasso_cache_load(digest)
    if payload is None:
        return None
    X_scaled, _params = _fit_preprocess(
        X_df, cutoff=cutoff, enable_corr=enable_corr, verbose=True,
    )
    X_scaled = _prefilter_by_auc(X_scaled, y, int(topk or 0), verbose=True)
    cols = [str(c) for c in X_scaled.columns]
    cached_cols = [str(c) for c in payload["columns"]]
    coef = np.asarray(payload["coef_vec"], dtype=float).ravel()
    if cols != cached_cols or len(coef) != len(cols):
        print("  LASSO cache columns differ — refit", flush=True)
        return None
    best_C = float(payload["best_C"])
    mean_scores = np.asarray(payload["mean_scores"], dtype=float)
    Cs = np.asarray(payload["Cs"], dtype=float)
    score_mat = np.asarray(payload["score_mat"], dtype=float)
    view = _lasso_cv_view(Cs, best_C, score_mat, coef)
    if mean_scores.size and np.any(np.isfinite(mean_scores)):
        best_i = int(np.nanargmax(mean_scores))
        auc = float(mean_scores[best_i])
    else:
        auc = float("nan")
    print(
        f"  LASSO cache hit {digest[:8]}  best C={best_C:.4g}  mean AUC={auc:.3f}",
        flush=True,
    )
    return best_C, coef, mean_scores, Cs, view, X_scaled


def _fit_lasso_cv_path(
    X_df: pd.DataFrame,
    y: np.ndarray,
    nfolds: int,
    n_cs: int,
    seed: int,
    max_iter: int,
    alpha: float = 1.0,
    topk: int = 0,
    cutoff: Optional[float] = None,
    enable_corr: Optional[bool] = None,
) -> tuple:
    """
    LASSO C-path CV on TRAIN only (not nested CV).

    Imputation, scaling, NZV, correlation filter and AUC prefilter are fit
    inside each fold on fold-train and applied to fold-val. Final model is
    refit on the full training matrix after the same pipeline.
    """
    y = np.asarray(y).astype(int)
    if cutoff is None:
        cutoff = float(getattr(cfg, "RAD_COR_CUTOFF", 0.95))
    if enable_corr is None:
        enable_corr = bool(getattr(cfg, "ENABLE_CORR_FILTER", True))
    c_min = float(getattr(cfg, "LASSO_C_MIN", 1e-3))
    c_max = float(getattr(cfg, "LASSO_C_MAX", 50.0))
    tol = float(getattr(cfg, "LASSO_TOL", 1e-3))
    n_jobs = int(getattr(cfg, "LASSO_N_JOBS", -1))
    use_cache = bool(getattr(cfg, "LASSO_CACHE", True))
    cache_params = _lasso_cache_params(
        alpha, nfolds, n_cs, seed, max_iter, int(topk or 0),
        c_min, c_max, tol, float(cutoff), bool(enable_corr),
    )
    digest = _lasso_cache_digest(X_df, y, cache_params) if use_cache else ""
    if use_cache:
        hit = _lasso_cache_hit(
            digest, X_df, y, int(topk or 0), float(cutoff), bool(enable_corr),
        )
        if hit is not None:
            return hit
    Cs = np.logspace(np.log10(c_min), np.log10(c_max), int(n_cs))

    skf = StratifiedKFold(n_splits=nfolds, shuffle=True, random_state=seed)
    splits = list(skf.split(np.zeros(len(y)), y))

    t0 = time.perf_counter()
    fold_scores = Parallel(n_jobs=n_jobs, prefer="threads")(
        delayed(_fold_preprocess_and_path)(
            X_df, y, tr, va, Cs, max_iter, tol, seed, alpha,
            int(topk or 0), float(cutoff), bool(enable_corr),
        )
        for tr, va in splits
    )
    score_mat = np.vstack(fold_scores)  # (nfolds, n_cs)
    mean_scores = np.nanmean(score_mat, axis=0)
    if np.all(~np.isfinite(mean_scores)):
        best_i = len(Cs) - 1
    else:
        best_i = int(np.nanargmax(mean_scores))
    best_C = float(Cs[best_i])
    print(
        f"  LASSO path-CV (fold-local preprocess) done in {time.perf_counter() - t0:.1f}s  "
        f"(folds={nfolds}, Cs={n_cs}, n_jobs={n_jobs})  "
        f"best C={best_C:.4g}  mean AUC={mean_scores[best_i]:.3f}",
        flush=True,
    )

    # Full-training preprocess + prefilter, then refit at best C
    X_scaled, _params = _fit_preprocess(
        X_df, cutoff=cutoff, enable_corr=enable_corr, verbose=True,
    )
    X_scaled = _prefilter_by_auc(X_scaled, y, int(topk or 0), verbose=True)
    X = np.ascontiguousarray(X_scaled.to_numpy(), dtype=np.float64)
    use_l1 = float(alpha) >= 1.0 - 1e-12
    if use_l1:
        final = LogisticRegression(
            penalty="l1", solver="liblinear", C=best_C,
            max_iter=max_iter, tol=tol, random_state=seed,
        )
    else:
        final = LogisticRegression(
            penalty="elasticnet", solver="saga", l1_ratio=float(alpha),
            C=best_C, max_iter=max_iter, tol=tol, random_state=seed,
        )
    final.fit(X, y)
    coef_vec = final.coef_.ravel()
    view = _lasso_cv_view(Cs, best_C, score_mat, coef_vec)
    if use_cache:
        _lasso_cache_store(
            digest, best_C, coef_vec, mean_scores, Cs, score_mat,
            list(X_scaled.columns), cache_params,
        )
    return best_C, coef_vec, mean_scores, Cs, view, X_scaled


def run_radiomics(full_rad: pd.DataFrame, y_group: np.ndarray,
                   dataset_label: str = "all", nfolds: Optional[int] = None,
                   alpha: Optional[float] = None, seed: Optional[int] = None,
                   group: Optional[pd.Series] = None) -> RadiomicsResult:
    """影像组学主函数：ICC/源过滤 → 折内预处理的 LASSO CV → 全训练集重拟合 RadScore。

    交叉验证不是 nested CV：仍是 TRAIN 上 StratifiedKFold 的 C 路径。
    需要从数据估计的步骤（插补/标准化/NZV/相关过滤/AUC 预筛）只在折内训练侧拟合。
    LASSO 入选后构建 Table 2（原始尺度，同 Baseline Table 1 格式）。
    """
    from modules.utils import dry_run as _dry
    print(f"\n═══ RADIOMICS [{dataset_label}] ═══", flush=True)
    y_group = np.asarray(y_group).astype(int)
    if nfolds is None:
        nfolds = int(getattr(cfg, "LASSO_NFOLDS", 10))
    if alpha is None:
        alpha = float(getattr(cfg, "ALPHA", 1.0))
    if seed is None:
        seed = int(getattr(cfg, "SEED", 66666))
    if _dry.exit_after_plan("radiomics", [
        f"label={dataset_label}",
        f"rows={len(full_rad)} cols={full_rad.shape[1]}",
        f"nfolds={nfolds} seed={seed}",
        "ICC filter → fold-local LASSO CV → RadScore (skipped)",
    ]):
        return RadiomicsResult(
            features=[], coefs=np.array([]), radscore=None,
            intercept=None, betas=None, ols=None, lambda_used=float("nan"),
            formula_df=None, formula_text=None, method="lasso",
        )

    # ── Step 0: 特征源过滤（MRI fbn_32 / CT fbs_16 / both）──
    full_rad = _filter_rad_by_source(full_rad)

    # ── Step 0.5: ICC 预筛选（intra & inter ≥ 0.75；不使用分析结局 y）──
    full_rad = _filter_rad_by_icc(full_rad)

    X_raw = _rad_feature_frame(full_rad)
    print(f"  候选特征: {X_raw.shape[1]}", flush=True)

    # ── Step 1–2: 折内预处理 + LASSO C 路径 CV；全训练集重拟合 ──
    topk = int(getattr(cfg, "LASSO_PREFILTER_TOPK", 25) or 0)
    n_cs = int(getattr(cfg, "LASSO_N_LAMBDAS", 15))
    max_iter = int(getattr(cfg, "LASSO_MAX_ITER", 500))
    print(
        f"  LASSO CV: fold-local preprocess + path warm-start, Cs={n_cs}, folds={nfolds}, "
        f"solver={'liblinear' if float(alpha) >= 1 - 1e-12 else 'saga'}, "
        f"max_iter={max_iter}",
        flush=True,
    )
    best_C, coef_vec, _mean_scores, _Cs, lrcv, X_scaled = _fit_lasso_cv_path(
        X_raw, y_group, nfolds=nfolds, n_cs=n_cs, seed=seed,
        max_iter=max_iter, alpha=float(alpha), topk=topk,
    )
    print(f"  全训练集预处理保留: {X_scaled.shape[1]} 特征", flush=True)

    sel_idx = np.where(coef_vec != 0)[0]
    lasso_features = list(X_scaled.columns[sel_idx])
    lasso_coefs = coef_vec[sel_idx]
    # C = 1/lambda；报告 lambda 尺度便于解读正则化强度
    lambda_used = float(1.0 / best_C)
    print(f"  λ={lambda_used:.4f}  入选: {len(lasso_features)} 特征", flush=True)
    for f, c in zip(lasso_features, lasso_coefs):
        print(f"    {display_rad_feature(f):<50s}  {c:+.4f}")

    # ── 绘制并保存 LASSO 图（默认不重拟合路径，显著加速） ──
    _plot_lasso(X_scaled, y_group, lrcv, lambda_used, dataset_label, seed, nfolds, alpha)

    # ── Step 3: 构建 RadScore（原始尺度 OLS） ──
    if len(lasso_features) == 0:
        print("  ⚠ LASSO selected 0 features — skipping RadScore computation")
        return RadiomicsResult(features=[], coefs=np.array([]), radscore=None,
                                intercept=None, betas=None, ols=None,
                                lambda_used=lambda_used, formula_df=None, formula_text=None)

    df_ols = full_rad[lasso_features].apply(pd.to_numeric, errors="coerce")
    df_ols["y"] = y_group
    df_ols = df_ols.dropna()
    Xd = sm.add_constant(df_ols[lasso_features])
    ols = sm.OLS(df_ols["y"], Xd).fit()  # 原始尺度 OLS（identity link），系数用于构建 RadScore
    intercept = float(ols.params["const"])
    betas = ols.params.drop("const")

    # 公式/结果表用缩短显示名；内部 features/betas 索引仍为完整列名
    disp_names = [display_rad_feature(n) for n in betas.index]
    formula_parts = " ".join(
        f"{'+' if b >= 0 else '−'} {abs(b):.6f} · <i>{dn}</i>"
        for dn, b in zip(disp_names, betas.values)
    )
    formula_text = f"<b>RadScore</b> = {intercept:.6f} {formula_parts}"
    print(f"  RadScore = {intercept:.6f}", end="")
    for dn, b in zip(disp_names, betas.values):
        print(f" {'+' if b >= 0 else '−'}{abs(b):.6f}×{dn}", end="")
    print()

    formula_df = pd.DataFrame({
        "Term": ["const"] + disp_names,
        "Feature": ["const"] + list(betas.index),
        "Coefficient": [intercept] + list(betas.values),
    })

    radscore = intercept + (full_rad[lasso_features].apply(pd.to_numeric, errors="coerce")
                             .to_numpy() @ betas.to_numpy())
    print(f"  RadScore: [{np.nanmin(radscore):.3f}, {np.nanmax(radscore):.3f}]  "
          f"mean={np.nanmean(radscore):.3f}")

    # Table 2 on LASSO-selected features (original scale; same format as clinical Table 1)
    table2 = None
    try:
        table2 = build_rad_table2(
            full_rad, y_group, group=group, keep_cols=lasso_features,
        )
        print(f"  Table2 (LASSO-selected): {0 if table2 is None else len(table2)} rows")
    except Exception as e:
        print(f"  ⚠ Table2 (LASSO-selected) failed: {e}")

    return RadiomicsResult(features=lasso_features, coefs=lasso_coefs, radscore=radscore,
                            intercept=intercept, betas=betas, ols=ols,
                            lambda_used=lambda_used, formula_df=formula_df,
                            formula_text=formula_text, table2=table2)


def apply_locked_radscore(result: RadiomicsResult, full_rad: pd.DataFrame) -> np.ndarray:
    """把已锁定的 RadScore 套到另一层。

    Logistic 先套训练集的插补和标准化，再乘 log-odds 系数。
    LASSO 的系数已在原始尺度，直接线性相加。
    """
    feats = [str(f) for f in (result.features or [])]
    if result.intercept is None or result.betas is None or not feats:
        raise ValueError("RadScore formula is empty")
    params = getattr(result, "preprocess", None)
    if params:
        X = _apply_preprocess(full_rad, params).reindex(columns=feats).to_numpy(dtype=float)
    else:
        X = (
            full_rad.reindex(columns=feats)
            .apply(pd.to_numeric, errors="coerce")
            .to_numpy(dtype=float)
        )
    beta = result.betas.reindex(feats).to_numpy(dtype=float)
    return float(result.intercept) + X @ beta


def refit_radscore_logistic(
    result: RadiomicsResult,
    full_rad: pd.DataFrame,
    y: np.ndarray,
) -> Optional[np.ndarray]:
    """锁定入选特征，套训练集插补和标准化，在本层重估 Logistic 系数。

    返回与 full_rad 等长的线性预测值。失败时返回 None，调用方保留训练公式。
    """
    feats = [str(f) for f in (getattr(result, "features", None) or [])]
    params = getattr(result, "preprocess", None)
    if not feats or not isinstance(params, dict) or not params:
        return None
    y = np.asarray(y).astype(int)
    if len(y) != len(full_rad) or len(np.unique(y)) < 2:
        return None
    from modules.stats.clinical import _fit_multivar_glm

    try:
        X = _apply_preprocess(full_rad, params)
        use = [f for f in feats if f in X.columns]
        if not use:
            return None
        model, _score, _mv = _fit_multivar_glm(X, y, use)
        if model is None or "const" not in model.params.index:
            return None
        betas = model.params.drop("const")
        intercept = float(model.params["const"])
        cols = [str(c) for c in betas.index]
        Xsel = X.reindex(columns=cols).to_numpy(dtype=float)
        return intercept + Xsel @ betas.to_numpy(dtype=float)
    except Exception:
        return None


def _attach_univariate_vif(uni_df: pd.DataFrame, frame: pd.DataFrame) -> pd.DataFrame:
    """单因素表上的 VIF。计算失败时记 NA，不因此丢掉特征。"""
    if uni_df is None or uni_df.empty or "Feature" not in uni_df.columns:
        return uni_df
    out = uni_df.copy()
    try:
        from modules.stats.vif_analysis import compute_vif
        feats = [str(f) for f in out["Feature"].tolist() if str(f) in frame.columns]
        vif_df = compute_vif(frame, feats) if len(feats) >= 2 else None
        vmap = {}
        if vif_df is not None and len(vif_df):
            vmap = {str(r["Variable"]): r["VIF"] for _, r in vif_df.iterrows()}
        out["VIF"] = [
            f"{vmap[f]:.3f}" if f in vmap and np.isfinite(vmap[f]) else "NA"
            for f in out["Feature"].astype(str)
        ]
    except Exception as e:
        print(f"  ⚠ VIF for radiomics univariate failed: {e}")
        out["VIF"] = "NA"
    return out


def _attach_rad_uni_adjust(uni_df: Optional[pd.DataFrame]) -> pd.DataFrame:
    """组学单因素 p 的 FDR(BH) / FWER(Holm) 与通过标记。

    家族 = 本表全部有限单因素 p；与 ANCOVA/MLR 的 Group 家族分开。
    α = RAD_LOGIT_UNI_P（默认 0.1）。Pass = 校正 p < α。
    DO_FDR / DO_FWER 都关时原样返回。
    """
    if uni_df is None or uni_df.empty or "p" not in uni_df.columns:
        return uni_df
    do_fdr = bool(getattr(cfg, "DO_FDR", False))
    do_fwer = bool(getattr(cfg, "DO_FWER", False))
    if not do_fdr and not do_fwer:
        return uni_df
    from modules.stats.assoc import bh_fdr, holm_fwer
    from modules.stats.clinical import parse_p_value

    out = uni_df.copy()
    raw = [parse_p_value(v) for v in out["p"].tolist()]
    alpha = float(getattr(cfg, "RAD_LOGIT_UNI_P", 0.1) or 0.1)
    if do_fdr:
        q = bh_fdr(raw)
        out["FDR P"] = q
        out["Pass FDR"] = [
            "Y" if np.isfinite(v) and float(v) < alpha else "N" for v in q
        ]
    if do_fwer:
        h = holm_fwer(raw)
        out["FWER P"] = h
        out["Pass FWER"] = [
            "Y" if np.isfinite(v) and float(v) < alpha else "N" for v in h
        ]
    return out


def _rad_logit_candidates(
    uni_df: Optional[pd.DataFrame],
    frame: pd.DataFrame,
    p_threshold: float,
    exclude: set,
) -> List[str]:
    """组学 Logistic 候选池。

    DO_FDR / DO_FWER 关闭：与临床相同，单因素 p < α、无完全分离。
    任一开启：仅用开启的校正；两者都开时 AND（FDR 与 FWER 均 < α）。
    α = RAD_LOGIT_UNI_P。校正 p ≥ 原始 p，故不必再卡原始 p。
    """
    from modules.stats.clinical import (
        _candidates_from_univariate,
        _is_separated_uni_row,
        parse_p_value,
    )

    do_fdr = bool(getattr(cfg, "DO_FDR", False))
    do_fwer = bool(getattr(cfg, "DO_FWER", False))
    if not do_fdr and not do_fwer:
        return _candidates_from_univariate(uni_df, frame, p_threshold, exclude)

    if uni_df is None or uni_df.empty or "Feature" not in uni_df.columns:
        return []
    out: List[str] = []
    dropped: List[str] = []
    seen = set()
    alpha = float(p_threshold)
    for _, row in uni_df.iterrows():
        feat = str(row.get("Feature", "")).strip()
        if (not feat) or feat in seen or feat in exclude or feat not in frame.columns:
            continue
        if _is_separated_uni_row(row):
            dropped.append(f"{feat} (complete separation)")
            continue
        ok = True
        reasons: List[str] = []
        if do_fdr:
            q = parse_p_value(row.get("FDR P"))
            if (not np.isfinite(q)) or q >= alpha:
                ok = False
                reasons.append(f"FDR P={q if np.isfinite(q) else 'NA'}")
        if do_fwer:
            h = parse_p_value(row.get("FWER P"))
            if (not np.isfinite(h)) or h >= alpha:
                ok = False
                reasons.append(f"FWER P={h if np.isfinite(h) else 'NA'}")
        if not ok:
            continue
        out.append(feat)
        seen.add(feat)
    if dropped:
        print("  多因素候选池剔除: " + "; ".join(dropped))
    which = []
    if do_fdr:
        which.append("FDR")
    if do_fwer:
        which.append("FWER")
    rule = " AND ".join(which) if which else "raw p"
    print(
        f"  Candidate gate: {rule} < {alpha:g} "
        f"(enabled corrections must all pass; n pass = {len(out)})"
    )
    return out


def _logistic_pool_note(
    candidates: List[str],
    stepwise: List[str],
    p_threshold: float,
    mv_p_cut: float,
    vif_high: float = None,
) -> str:
    retained = list(stepwise or [])
    removed = [c for c in candidates if c not in set(retained)]
    warn = getattr(cfg, "VIF_THRESHOLD_WARN", 5)
    _ = vif_high
    do_fdr = bool(getattr(cfg, "DO_FDR", False))
    do_fwer = bool(getattr(cfg, "DO_FWER", False))
    if do_fdr or do_fwer:
        bits = []
        if do_fdr:
            bits.append(f"Benjamini–Hochberg FDR P < {p_threshold:g}")
        if do_fwer:
            bits.append(f"Holm FWER P < {p_threshold:g}")
        gate = " and ".join(bits)
        entry = (
            "Radiomics candidate pool: univariate logistic "
            f"{gate} (AND of enabled corrections; n = {len(candidates)}). "
            "FDR/FWER family = all finite radiomics univariate p values in this table "
            "(separate from the ANCOVA/MLR Group-test family). "
        )
    else:
        entry = (
            "Radiomics candidate pool: univariate logistic p "
            f"< {p_threshold:g} (n = {len(candidates)}). "
        )
    parts = [
        entry
        + f"VIF is reported for collinearity warning (warn ≥ {warn:g}) and does not exclude candidates. "
        "Complete-separation terms do not enter. "
        "Features were median-imputed and z-scored on the training set before regression; "
        "odds ratios are per 1 training SD."
    ]
    if removed:
        parts.append(
            "AIC backward stepwise is informational and removed: " + ", ".join(removed) + "."
        )
    elif retained:
        parts.append(
            "AIC backward stepwise is informational and retained all candidates: "
            + ", ".join(retained) + "."
        )
    parts.append(
        "Selected radiomics features are those in the pool table with multivariate p "
        f"< {mv_p_cut:g}. FORCE_MODEL_FEATURES does not select radiomics features."
    )
    return " ".join(parts)



def build_rad_table2(
    full_rad: pd.DataFrame,
    y_group: np.ndarray,
    group: Optional[pd.Series] = None,
    keep_cols: Optional[List[str]] = None,
    preprocess: Optional[dict] = None,
) -> pd.DataFrame:
    """Table 2: same format as clinical Table 1, stats on radiomics feature pool.

    Uses clinical._build_table1. Descriptive stats are on the original (median-
    imputed) scale of the preprocess keep_cols pool (fallback: non-zero-variance
    feature columns after source/ICC filters). Variable names use display_rad_feature.
    """
    from modules.stats.clinical import _build_table1

    y_group = np.asarray(y_group).astype(int)
    idx0 = np.where(y_group == 0)[0]
    idx1 = np.where(y_group == 1)[0]
    if len(idx0) == 0 or len(idx1) == 0:
        return pd.DataFrame()

    if group is not None:
        g = pd.Series(group).reset_index(drop=True)
        lbl0 = str(g.iloc[idx0[0]]) if len(idx0) else "0"
        lbl1 = str(g.iloc[idx1[0]]) if len(idx1) else "1"
    else:
        lbl0 = str(getattr(cfg, "GROUP_DISPLAY_NEG", None) or "0")
        lbl1 = str(getattr(cfg, "GROUP_DISPLAY_POS", None) or "1")

    rad = _filter_rad_by_source(full_rad)
    rad = _filter_rad_by_icc(rad)
    raw = _rad_feature_frame(rad).apply(pd.to_numeric, errors="coerce")
    if raw.shape[1] == 0:
        return pd.DataFrame()

    cols: List[str]
    if keep_cols is not None:
        cols = [c for c in list(keep_cols) if c in raw.columns]
    elif preprocess is not None and preprocess.get("keep_cols"):
        cols = [c for c in list(preprocess["keep_cols"]) if c in raw.columns]
    else:
        variances = raw.var(skipna=True)
        cols = list(variances[variances > 0].index)
    if not cols:
        return pd.DataFrame()

    frame = raw.reindex(columns=cols)
    frame = frame.fillna(frame.median(numeric_only=True))

    seen: Dict[str, int] = {}
    new_names: List[str] = []
    for c in frame.columns:
        dn = display_rad_feature(str(c)) or str(c)
        if dn in seen:
            seen[dn] += 1
            dn = f"{dn}__{seen[dn]}"
        else:
            seen[dn] = 0
        new_names.append(dn)
    frame = frame.copy()
    frame.columns = new_names
    return _build_table1(frame, idx0, idx1, lbl0, lbl1)


def _pack_logistic(
    *,
    features: List[str],
    coefs: np.ndarray,
    radscore: Optional[np.ndarray],
    intercept: Optional[float],
    betas: Optional[pd.Series],
    formula_df: Optional[pd.DataFrame],
    formula_text: Optional[str],
    preprocess: Optional[dict],
    uni_res: Optional[pd.DataFrame],
    mv_df: Optional[pd.DataFrame],
    mv_note: Optional[str],
    mv_selected: Optional[pd.DataFrame],
    candidates: Optional[List[str]],
    stepwise_features: Optional[List[str]],
    select_source: Optional[str],
    firth_uni: Optional[pd.DataFrame],
    firth_mv: Optional[pd.DataFrame],
    table2: Optional[pd.DataFrame] = None,
) -> RadiomicsResult:
    return RadiomicsResult(
        features=list(features),
        coefs=coefs,
        radscore=radscore,
        intercept=intercept,
        betas=betas,
        ols=None,
        lambda_used=float("nan"),
        formula_df=formula_df,
        formula_text=formula_text,
        method="logistic",
        preprocess=preprocess,
        uni_res=uni_res,
        mv_df=mv_df,
        mv_note=mv_note,
        mv_selected=mv_selected,
        candidates=candidates,
        stepwise_features=stepwise_features,
        select_source=select_source,
        firth_uni=firth_uni,
        firth_mv=firth_mv,
        table2=table2,
    )


def _score_column_name(group_name: str, used: Optional[set] = None) -> str:
    """RadScore_<分类名>。避开 Clinical / Combined / RadScore 这三个令牌。"""
    raw = str(group_name or "").strip()
    safe = "".join(ch if (ch.isalnum() or ch in "_-") else "_" for ch in raw).strip("_")
    if not safe:
        safe = "group"
    if safe.lower() in {"radscore", "clinical", "combined", "group"}:
        safe = safe + "_mod"
    name = "RadScore_" + safe
    if used is not None:
        base = name
        n = 2
        while name in used:
            name = f"{base}_{n}"
            n += 1
        used.add(name)
    return name


def normalize_prefix_group(raw=None) -> dict:
    """{'names': [...], 'assign': {prefix: name}}。缺键或空 = 不分组。"""
    if raw is None:
        raw = getattr(cfg, "RAD_FEATURE_PREFIX_GROUP", None)
    names: List[str] = []
    seen: set = set()
    assign: Dict[str, str] = {}
    if isinstance(raw, dict):
        for x in raw.get("names") or []:
            s = str(x).strip()
            if not s or s in seen:
                continue
            seen.add(s)
            names.append(s)
        src = raw.get("assign") or {}
        if isinstance(src, dict):
            for k, v in src.items():
                p = str(k).strip()
                g = str(v).strip()
                if p and g and g in seen:
                    assign[p] = g
    return {"names": names, "assign": assign}


def prefix_group_plan(columns) -> List[tuple]:
    """按分类把列切开。同一列命中多个标记时用更长的标记。没有有效归属则返回空。"""
    from modules.stats.data_io import _column_matches_prefix, _marker_anchor, _prefix_id_names

    spec = normalize_prefix_group()
    names = spec["names"]
    assign = spec["assign"]
    if not names or not assign:
        return []
    pairs = sorted(assign.items(), key=lambda kv: len(_marker_anchor(kv[0])[1]), reverse=True)
    ids = _prefix_id_names()
    buckets: Dict[str, List[str]] = {n: [] for n in names}
    claimed: set = set()
    for col in list(columns or []):
        s = str(col)
        if s in ids:
            continue
        for pref, grp in pairs:
            if _column_matches_prefix(s, pref):
                if s not in claimed and grp in buckets:
                    buckets[grp].append(s)
                    claimed.add(s)
                break
    return [(n, buckets[n]) for n in names if buckets[n]]


def score_map(result: Optional[RadiomicsResult]) -> Dict[str, np.ndarray]:
    """分类结果 → {RadScore_名: 向量}。没有分类时，有分数才返回 {'RadScore': 向量}。"""
    if result is None:
        return {}
    groups = list(getattr(result, "groups", None) or [])
    out: Dict[str, np.ndarray] = {}
    if groups:
        for g in groups:
            rs = getattr(g, "radscore", None)
            if rs is None:
                continue
            name = str(getattr(g, "score_name", "") or "RadScore")
            out[name] = np.asarray(rs, dtype=float)
        return out
    rs = getattr(result, "radscore", None)
    if rs is None:
        return {}
    name = str(getattr(result, "score_name", "") or "RadScore")
    return {name: np.asarray(rs, dtype=float)}


def selected_feature_names(result: Optional[RadiomicsResult]) -> List[str]:
    if result is None:
        return []
    groups = list(getattr(result, "groups", None) or [])
    if groups:
        out: List[str] = []
        seen: set = set()
        for g in groups:
            for f in (getattr(g, "features", None) or []):
                s = str(f)
                if s and s not in seen:
                    seen.add(s)
                    out.append(s)
        return out
    return [str(f) for f in (getattr(result, "features", None) or [])]


def grouped_table2(result: RadiomicsResult, full_rad: pd.DataFrame,
                   y_group: np.ndarray, group: Optional[pd.Series] = None) -> Optional[pd.DataFrame]:
    """各类用自己的训练集预处理，在给定层上各做一张 Table 2，再纵向拼上 Category。"""
    frames = []
    for g in list(getattr(result, "groups", None) or []):
        pre = getattr(g, "preprocess", None)
        if not pre:
            continue
        t2 = build_rad_table2(full_rad, y_group, group=group, preprocess=pre)
        if t2 is None or len(t2) == 0:
            continue
        t = t2.copy()
        t.insert(0, "Category", str(getattr(g, "group_name", "") or ""))
        frames.append(t)
    if not frames:
        return None
    return pd.concat(frames, ignore_index=True)


def apply_locked_result(result: RadiomicsResult, full_rad: pd.DataFrame) -> RadiomicsResult:
    """把训练公式套到另一层。有分类时各类分开套，不合成一个 RadScore。"""
    from dataclasses import replace

    groups = list(getattr(result, "groups", None) or [])
    if not groups:
        if not result.features or result.betas is None or result.intercept is None:
            return result
        return replace(result, radscore=apply_locked_radscore(result, full_rad))
    applied = []
    for g in groups:
        if not g.features or g.betas is None or g.intercept is None:
            applied.append(g)
            continue
        try:
            applied.append(replace(g, radscore=apply_locked_radscore(g, full_rad)))
        except Exception as e:
            label = getattr(g, "score_name", None) or getattr(g, "group_name", "") or "group"
            print(f"  ⚠ {label} apply failed — keep empty score: {e}")
            applied.append(g)
    return replace(result, groups=applied, radscore=None)


def refit_locked_result(result: RadiomicsResult, full_rad: pd.DataFrame,
                        y: np.ndarray) -> RadiomicsResult:
    """锁定各类入选特征，套训练集标准化，在本层重估系数。失败则退回训练公式。"""
    from dataclasses import replace

    groups = list(getattr(result, "groups", None) or [])
    if not groups:
        if str(getattr(result, "method", "") or "") != "logistic":
            return result
        rs = refit_radscore_logistic(result, full_rad, y)
        if rs is None:
            return result
        return replace(result, radscore=rs)
    out = []
    for g in groups:
        label = str(getattr(g, "score_name", "") or getattr(g, "group_name", "") or "group")
        if str(getattr(g, "method", "") or "") != "logistic" or not g.features:
            out.append(g)
            continue
        rs = refit_radscore_logistic(g, full_rad, y)
        if rs is None:
            print(f"  ⚠ {label} logistic refit failed — keep train formula")
            try:
                rs = apply_locked_radscore(g, full_rad)
            except Exception:
                rs = g.radscore
        else:
            print(f"  Refit {label} ({len(g.features)} locked features, train scaling)")
        out.append(replace(g, radscore=rs))
    return replace(result, groups=out, radscore=None)


def _run_grouped_logistic(full_rad: pd.DataFrame, y_group: np.ndarray,
                          dataset_label: str,
                          group: Optional[pd.Series],
                          plan: List[tuple]) -> RadiomicsResult:
    """每一类单独走原来的 Logistic。未归类的列不进入任何一类。"""
    from modules.stats.data_io import _prefix_id_names

    ids = _prefix_id_names()
    id_cols = [c for c in full_rad.columns if str(c) in ids]
    claimed = {c for _n, cols in plan for c in cols}
    left = [str(c) for c in full_rad.columns if str(c) not in ids and str(c) not in claimed]
    print(f"\n═══ RADIOMICS LOGISTIC BY CATEGORY [{dataset_label}] ═══", flush=True)
    print("  Categories: " + ", ".join(f"{n} ({len(cols)})" for n, cols in plan), flush=True)
    if left:
        shown = ", ".join(left[:8])
        more = "" if len(left) <= 8 else f" … (+{len(left) - 8})"
        print(f"  Unassigned columns stay out of every logistic ({len(left)}): {shown}{more}")
    used_names: set = set()
    children: List[RadiomicsResult] = []
    from dataclasses import replace
    for name, cols in plan:
        score = _score_column_name(name, used_names)
        sub_cols = id_cols + [c for c in cols if c in full_rad.columns]
        sub = full_rad.loc[:, sub_cols]
        print(f"\n  ── {name} → {score}  ({len(cols)} columns) ──", flush=True)
        child = run_radiomics_logistic(
            sub, y_group, dataset_label=f"{dataset_label}/{name}", group=group,
            split_groups=False, score_name=score, group_name=name,
        )
        children.append(replace(child, group_name=name, score_name=score))
    frames = []
    for g in children:
        t2 = getattr(g, "table2", None)
        if t2 is None or len(t2) == 0:
            continue
        t = t2.copy()
        t.insert(0, "Category", g.group_name or "")
        frames.append(t)
    table2 = pd.concat(frames, ignore_index=True) if frames else None
    return RadiomicsResult(
        features=[], coefs=np.array([]), radscore=None, intercept=None, betas=None,
        ols=None, lambda_used=float("nan"), formula_df=None, formula_text=None,
        method="logistic", table2=table2, groups=children, select_source="grouped",
    )


def run_radiomics_logistic(full_rad: pd.DataFrame, y_group: np.ndarray,
                           dataset_label: str = "all",
                           group: Optional[pd.Series] = None,
                           *,
                           split_groups: bool = True,
                           score_name: str = "RadScore",
                           group_name: str = "") -> RadiomicsResult:
    """组学 Logistic 筛选。预处理与 LASSO 相同，入选规则与临床筛选相同，界值独立。

    默认：单因素 p < RAD_LOGIT_UNI_P、无完全分离 → 候选池（VIF 仅警告，不剔除）。
    DO_FDR / DO_FWER 开启时：用校正 p 门控（开启项 AND；α = RAD_LOGIT_UNI_P），
    并在单因素表写出 FDR P / FWER P 与 Pass 标记。
    入选 = 候选池多因素 p < RAD_LOGIT_MV_P。
    RadScore = 入选特征的 Logistic 线性预测值（训练集标准化后的 log-odds）。
    """
    from modules.utils import dry_run as _dry
    if _dry.exit_after_plan("radiomics_logistic", [
        f"label={dataset_label}",
        f"rows={len(full_rad)} cols={full_rad.shape[1]}",
        "ICC → preprocess → uni/mv logistic → RadScore (skipped)",
    ]):
        return RadiomicsResult(
            features=[], coefs=np.array([]), radscore=None,
            intercept=None, betas=None, ols=None, lambda_used=float("nan"),
            formula_df=None, formula_text=None, method="logistic",
        )
    if split_groups:
        plan = prefix_group_plan(list(full_rad.columns))
        if plan:
            return _run_grouped_logistic(
                full_rad, y_group, dataset_label, group, plan,
            )
    from modules.stats.clinical import (
        _attach_vif,
        _backward_stepwise,
        _default_features_from_mv,
        _fit_multivar_glm,
        _multivar_firth,
        _univariate_firth,
        _univariate_logistic,
    )

    print(f"\n═══ RADIOMICS LOGISTIC [{dataset_label}] ═══", flush=True)
    y_group = np.asarray(y_group).astype(int)
    empty_coef = np.array([])
    if len(y_group) != len(full_rad):
        print("  ⚠ logistic: 标签行数与组学行数不一致 — 跳过")
        return _pack_logistic(
            features=[], coefs=empty_coef, radscore=None, intercept=None, betas=None,
            formula_df=None, formula_text=None, preprocess=None, uni_res=None,
            mv_df=None, mv_note=None, mv_selected=None, candidates=[], stepwise_features=[],
            select_source=None, firth_uni=None, firth_mv=None,
        )

    full_rad = _filter_rad_by_source(full_rad)
    full_rad = _filter_rad_by_icc(full_rad)
    X_scaled, params = _fit_preprocess(full_rad, verbose=True)
    print(f"  预处理后候选: {X_scaled.shape[1]}", flush=True)
    table2 = build_rad_table2(
        full_rad, y_group, group=group, preprocess=params,
    )
    print(f"  Table2: {0 if table2 is None else len(table2)} rows")
    p_threshold = float(getattr(cfg, "RAD_LOGIT_UNI_P", 0.1) or 0.1)
    mv_p_cut = float(getattr(cfg, "RAD_LOGIT_MV_P", 0.05) or 0.05)

    def _empty(uni_res, mv_df, mv_note, mv_selected, candidates, stepwise,
               select_source, firth_uni, firth_mv):
        return _pack_logistic(
            features=[], coefs=empty_coef, radscore=None, intercept=None, betas=None,
            formula_df=None, formula_text=None, preprocess=params,
            uni_res=uni_res, mv_df=mv_df, mv_note=mv_note, mv_selected=mv_selected,
            candidates=list(candidates or []), stepwise_features=list(stepwise or []),
            select_source=select_source, firth_uni=firth_uni, firth_mv=firth_mv,
            table2=table2,
        )

    if X_scaled.shape[1] == 0 or len(np.unique(y_group)) < 2:
        print("  ⚠ logistic: 无可用特征或标签只有一类 — 跳过")
        return _empty(None, None, None, None, [], [], None, None, None)

    uni_df = _attach_univariate_vif(_univariate_logistic(X_scaled, y_group), X_scaled)
    uni_df = _attach_rad_uni_adjust(uni_df)
    print(f"  Univariate: {0 if uni_df is None else len(uni_df)} features")
    candidates = _rad_logit_candidates(
        uni_df, X_scaled, p_threshold, set(),
    )
    do_fdr = bool(getattr(cfg, "DO_FDR", False))
    do_fwer = bool(getattr(cfg, "DO_FWER", False))
    if do_fdr or do_fwer:
        gate_bits = []
        if do_fdr:
            gate_bits.append("FDR")
        if do_fwer:
            gate_bits.append("FWER")
        gate_lbl = " AND ".join(gate_bits) + f" < {p_threshold:g}"
    else:
        gate_lbl = f"univariate p < {p_threshold:g}"
    print(
        f"  Candidate pool ({gate_lbl}): "
        f"{', '.join(candidates) or '（无）'}"
    )

    mv_df = None
    stepwise: List[str] = []
    mv_note = None
    if candidates:
        try:
            _pool_model, _pool_lp, mv_df = _fit_multivar_glm(X_scaled, y_group, candidates)
        except Exception as e:
            print(f"  ⚠ Candidate-pool GLM failed (informational): {e}")
            mv_df = None
        if mv_df is not None and len(mv_df):
            mv_df = _attach_vif(mv_df, X_scaled, candidates)
        try:
            stepwise = _backward_stepwise(X_scaled, y_group, candidates)
        except Exception as e:
            print(f"  ⚠ AIC stepwise failed (informational): {e}")
            stepwise = list(candidates)
        mv_note = _logistic_pool_note(
            candidates, stepwise, p_threshold, mv_p_cut,
        )
        print(
            "  AIC stepwise retained (informational): "
            f"{', '.join(stepwise) or '（无）'}"
        )

    final_feat = _default_features_from_mv(mv_df, mv_p_cut, set())
    firth_uni = None
    firth_mv = None
    if bool(getattr(cfg, "DO_RAD_FIRTH", False)):
        try:
            firth_uni = _univariate_firth(X_scaled, y_group)
            print(f"  Firth univariate: {0 if firth_uni is None else len(firth_uni)} features")
        except Exception as e:
            print(f"  ⚠ Firth univariate failed: {e}")
        if candidates:
            try:
                firth_mv = _multivar_firth(X_scaled, y_group, candidates)
                print(f"  Firth candidate pool: {0 if firth_mv is None else len(firth_mv)} terms")
            except Exception as e:
                print(f"  ⚠ Firth multivariable failed: {e}")

    if not final_feat:
        print(f"  ⚠ 多因素无 p < {mv_p_cut:g} 的组学特征 — 无 RadScore。")
        return _empty(
            uni_df, mv_df, mv_note, None, candidates, stepwise, None, firth_uni, firth_mv,
        )

    model, _score, mv_selected = _fit_multivar_glm(X_scaled, y_group, final_feat)
    if model is None or "const" not in model.params.index:
        print("  ⚠ Selected logistic failed — 无 RadScore。")
        return _empty(
            uni_df, mv_df, mv_note, mv_selected, candidates, stepwise, "mv_p",
            firth_uni, firth_mv,
        )

    betas = model.params.drop("const")
    features = [str(c) for c in betas.index]
    intercept = float(model.params["const"])
    coefs = betas.to_numpy(dtype=float)
    Xsel = X_scaled.reindex(columns=features)
    radscore = intercept + Xsel.to_numpy(dtype=float) @ coefs
    disp_names = [display_rad_feature(n) for n in features]
    formula_parts = " ".join(
        f"{'+' if b >= 0 else '−'} {abs(b):.6f} · <i>{dn}</i>"
        for dn, b in zip(disp_names, coefs)
    )
    import html as _html
    formula_text = f"<b>{_html.escape(score_name)}</b> = {intercept:.6f} {formula_parts}"
    formula_df = pd.DataFrame({
        "Term": ["const"] + disp_names,
        "Feature": ["const"] + features,
        "Coefficient": [intercept] + list(coefs),
    })
    print(
        f"  Selected radiomics (multivariate p < {mv_p_cut:g}): {', '.join(disp_names)}"
    )
    print(f"  {score_name} = {intercept:.6f}", end="")
    for dn, b in zip(disp_names, coefs):
        print(f" {'+' if b >= 0 else '−'}{abs(b):.6f}×{dn}", end="")
    print()
    print(
        f"  {score_name} (log-odds / 1 SD): [{np.nanmin(radscore):.3f}, {np.nanmax(radscore):.3f}]  "
        f"mean={np.nanmean(radscore):.3f}"
    )
    packed = _pack_logistic(
        features=features, coefs=coefs, radscore=radscore, intercept=intercept, betas=betas,
        formula_df=formula_df, formula_text=formula_text, preprocess=params,
        uni_res=uni_df, mv_df=mv_df, mv_note=mv_note, mv_selected=mv_selected,
        candidates=list(candidates), stepwise_features=list(stepwise),
        select_source="mv_p", firth_uni=firth_uni, firth_mv=firth_mv,
        table2=table2,
    )
    if score_name != "RadScore" or group_name:
        from dataclasses import replace
        packed = replace(
            packed,
            score_name=score_name,
            group_name=group_name or None,
        )
    return packed


def _plot_lasso(X_scaled: pd.DataFrame, y: np.ndarray, lrcv,
                 lambda_used: float, dataset_label: str, seed: int, nfolds: int,
                 alpha: float) -> None:
    """
    绘制 LASSO 图。
    默认 LASSO_PLOT_PATH=False：仅画 CV-AUC（快）。
    若 True：优先用 LogisticRegressionCV.coefs_paths_ 画路径，避免二次重拟合；
    仅当无 coefs_paths_ 时才回退到逐 C 重拟合（慢）。
    """
    Cs = np.asarray(lrcv.Cs_, dtype=float)
    log_lambda = np.log(1.0 / Cs)
    mean_scores = (
        lrcv.scores_[1].mean(axis=0) if 1 in lrcv.scores_
        else list(lrcv.scores_.values())[0].mean(axis=0)
    )
    line_c = getattr(cfg, "ROC_COLOR_MAP", {}).get("RadScore", "#B2182B")
    mark_c = "#E64B35"
    do_path = bool(getattr(cfg, "LASSO_PLOT_PATH", False))

    if do_path:
        fig, axes = plt.subplots(1, 2, figsize=(12, 5))
        ax0, ax1 = axes[0], axes[1]
        path = None
        # sklearn: coefs_paths_[class] shape (n_folds, n_cs, n_features) for multi-class list
        try:
            cp = lrcv.coefs_paths_
            # binary: often dict {1: ndarray} or list
            if isinstance(cp, dict):
                arr = cp.get(1, list(cp.values())[0])
            else:
                arr = cp[1] if isinstance(cp, (list, tuple)) else cp
            arr = np.asarray(arr)
            # (n_folds, n_cs, n_features) → mean over folds
            if arr.ndim == 3:
                path = arr.mean(axis=0)
            elif arr.ndim == 2:
                path = arr
        except Exception:
            path = None

        if path is not None and path.shape[0] == len(Cs):
            for j in range(min(path.shape[1], X_scaled.shape[1])):
                ax0.plot(log_lambda, path[:, j], linewidth=0.6, color="steelblue", alpha=0.5)
            ax0.set_title("LASSO Path (from CV)")
        else:
            # 慢路径回退
            from sklearn.linear_model import LogisticRegression
            path = np.zeros((len(Cs), X_scaled.shape[1]))
            pen = "l1" if float(alpha) >= 1.0 - 1e-12 else "elasticnet"
            for i, C in enumerate(Cs):
                kw = dict(C=C, penalty=pen, solver="liblinear" if pen == "l1" else "saga",
                          max_iter=int(getattr(cfg, "LASSO_MAX_ITER", 2000)), random_state=seed)
                if pen == "elasticnet":
                    kw["l1_ratio"] = alpha
                    kw["solver"] = "saga"
                m = LogisticRegression(**kw)
                m.fit(X_scaled.to_numpy(), y)
                path[i, :] = m.coef_.ravel()
            for j in range(path.shape[1]):
                ax0.plot(log_lambda, path[:, j], linewidth=0.6, color="steelblue", alpha=0.5)
            ax0.set_title("LASSO Path (refit)")
        ax0.axvline(np.log(lambda_used), color=mark_c, linestyle="--", linewidth=2)
        ax0.set_xlabel("log(λ)")
        ax0.set_ylabel("Coefficients")
        cfg.apply_clean_theme(ax0)
    else:
        fig, ax1 = plt.subplots(1, 1, figsize=(6.5, 5))

    ax1.plot(log_lambda, mean_scores, marker="o", markersize=3, color=line_c)
    ax1.axvline(np.log(lambda_used), color=mark_c, linestyle="--", linewidth=2)
    ax1.set_xlabel("log(λ)")
    ax1.set_ylabel("CV AUC")
    ax1.set_title("CV AUC")
    cfg.apply_clean_theme(ax1)

    fig.tight_layout()
    lasso_fn = "lasso" if dataset_label == "all" else f"lasso_{dataset_label}"
    w = 12 if do_path else 6.5
    if cfg.save_plot(lasso_fn, fig, width=w, height=5, dpi=150):
        print(f"  ✓ {lasso_fn}.png  (path_plot={do_path})")
