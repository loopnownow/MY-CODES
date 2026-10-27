"""Multiple imputation and Rubin pooling.

`u_impute.z_impute` does *single* imputation: one completed table, one analysis, one
standard error. That standard error is too small, because it treats imputed values as
observed. Multiple imputation fixes that: draw `m` completed datasets, analyse each,
then pool the estimates with Rubin's rules.

Two pieces live here:

`z_impute_mi`  — draw `m` completed datasets. Each draw uses
                 `IterativeImputer(sample_posterior=True)`, so the filled values are
                 draws from the posterior predictive rather than conditional means.
                 This is what makes the between-imputation variance meaningful; with
                 `sample_posterior=False` (the default in `z_impute`) every draw is
                 identical and Rubin pooling collapses to single imputation.

`rubin_pool`   — Rubin's rules, with Barnard–Rubin degrees of freedom.

Scope note (read before quoting a pooled AUC): Rubin's rules assume the estimator's
within-imputation variance is a complete-data variance. That holds for regression
coefficients, means, differences and OR/HR. It does **not** hold for a performance
metric produced by a pipeline that does its own selection (LASSO then AUC), because
the between-imputation variance must then also carry the selection step. For
prediction, run the whole pipeline inside each imputed dataset and report the pooled
estimate together with the per-imputation range — do not present an averaged AUC as
if it were a Rubin-pooled parameter.

References
----------
Rubin DB (1987) Multiple Imputation for Nonresponse in Surveys.
Barnard J, Rubin DB (1999) Small-sample degrees of freedom with multiple imputation.
    Biometrika 86:948-955.
Marshall A, Altman DG, Holder RL, Royston P (2009) Combining estimates of interest in
    prognostic modelling studies after multiple imputation. BMC Med Res Methodol 9:57.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Union

import numpy as np
import pandas as pd
from scipy import stats as sps
from sklearn.experimental import enable_iterative_imputer  # noqa: F401
from sklearn.impute import IterativeImputer
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import BayesianRidge

from modules.utils.u_impute import (
    IMPUTE_GROUP_COL,
    IMPUTE_MAX_ITER,
    IMPUTE_MF_MAX_DEPTH,
    IMPUTE_MF_MAX_FEATURES,
    IMPUTE_MF_N_TREES,
    IMPUTE_MIN_NONMISSING,
    IMPUTE_MIN_UNIQUE,
    IMPUTE_N_NEAREST,
    IMPUTE_SEED,
    _detect_categorical_cols,
    _decode_categoricals,
    _encode_categoricals,
)

# ═════════════════════════════════════════════════════════════════
# §1  Defaults
# ═════════════════════════════════════════════════════════════════
MI_DRAWS = 20          # m; 20 is the usual floor when FMI is expected to be moderate
MI_MIN_DRAWS = 2       # below this there is no between-imputation variance to estimate


def _posterior_estimator(kind: str, seed: int, trees: int, depth, max_features):
    if kind == "missForest":
        return RandomForestRegressor(
            n_estimators=trees,
            max_depth=depth,
            max_features=max_features,
            random_state=seed,
            n_jobs=-1,
        )
    return BayesianRidge()


def z_impute_mi(
    data: Union[str, pd.DataFrame],
    m: int = MI_DRAWS,
    sheet: Optional[str] = None,
    group_col: Optional[str] = IMPUTE_GROUP_COL,
    method: str = "iterative",
    seed: int = IMPUTE_SEED,
    max_iter: int = IMPUTE_MAX_ITER,
    mf_n_trees: int = IMPUTE_MF_N_TREES,
    mf_max_depth=IMPUTE_MF_MAX_DEPTH,
    mf_max_features=IMPUTE_MF_MAX_FEATURES,
    n_nearest_features: Optional[int] = IMPUTE_N_NEAREST,
    include_categorical: bool = False,
    categorical_cols: Optional[Sequence[str]] = None,
    allow_outcome_stratify: bool = True,
    verbose: bool = True,
) -> List[pd.DataFrame]:
    """Return `m` completed datasets drawn with a Bayesian posterior.

    Observed cells are identical across draws; only imputed cells differ. That
    invariant is asserted, because a draw that moved an observed value would
    corrupt the analysis silently.
    """
    if m < MI_MIN_DRAWS:
        raise ValueError(f"m must be >= {MI_MIN_DRAWS} for multiple imputation, got {m}")

    if isinstance(data, str):
        # Default to the first sheet; the CLI already resolved the file path.
        df = pd.read_excel(data, sheet_name=sheet if sheet is not None else 0)
    elif isinstance(data, pd.DataFrame):
        df = data.copy()
    else:
        raise TypeError("'data' must be a DataFrame or a file path.")

    stratify_col = group_col
    if stratify_col and str(stratify_col).strip().lower() in {
        "group", "label", "y", "outcome", "event",
    } and not allow_outcome_stratify:
        stratify_col = None
    if stratify_col is None:
        df["_impute_pool"] = 0
        stratify_col = "_impute_pool"
    elif stratify_col not in df.columns:
        raise ValueError(f"group_col '{stratify_col}' not found in columns: {list(df.columns)}")

    num_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c]) and c != stratify_col]
    cat_cols: List[str] = []
    code_to_label: Dict[str, Dict[int, object]] = {}
    work = df.copy()
    if include_categorical or categorical_cols is not None:
        cat_cols = _detect_categorical_cols(work, stratify_col, categorical_cols)
        if cat_cols:
            work, code_to_label = _encode_categoricals(work, cat_cols)

    impute_cols = num_cols + cat_cols
    if not impute_cols:
        raise ValueError("no imputable columns")

    if verbose:
        n_cells = int(work[impute_cols].isna().sum().sum())
        print(f"=== multiple imputation: m={m}, method={method} ===")
        print(f"Data: {work.shape[0]} rows x {work.shape[1]} cols | imputable cols: {len(impute_cols)}")
        print(f"Missing cells: {n_cells}")

    groups = sorted(work[stratify_col].dropna().unique(), key=str)
    draws: List[pd.DataFrame] = []

    for draw in range(m):
        out = work.copy()
        for gi, gv in enumerate(groups):
            mask = out[stratify_col] == gv
            g_data = out.loc[mask, impute_cols]
            if int(g_data.isna().sum().sum()) == 0:
                continue
            valid = [
                c
                for c in impute_cols
                if g_data[c].notna().sum() >= IMPUTE_MIN_NONMISSING
                and g_data[c].dropna().nunique() >= IMPUTE_MIN_UNIQUE
            ]
            if not valid:
                continue

            nn = n_nearest_features
            if nn is not None and len(valid) > 1:
                nn = max(1, min(int(nn), len(valid) - 1)) or None

            imputer = IterativeImputer(
                estimator=_posterior_estimator(method, seed + draw, mf_n_trees, mf_max_depth, mf_max_features),
                max_iter=max_iter,
                random_state=seed + draw * 1000 + gi,
                n_nearest_features=nn,
                sample_posterior=True,  # the draw, not the conditional mean
                tol=1e-3,
            )
            out.loc[mask, valid] = imputer.fit_transform(g_data[valid])

        if code_to_label:
            out = _decode_categoricals(out, code_to_label)
        if "_impute_pool" in out.columns and (group_col is None or group_col == "_impute_pool"):
            out = out.drop(columns=["_impute_pool"])
        elif "_impute_pool" in out.columns and str(group_col).lower() in {"group", "label", "y", "outcome", "event"}:
            out = out.drop(columns=["_impute_pool"], errors="ignore")

        # Observed cells must not move between draws.
        observed = df[impute_cols].notna()
        if not np.array_equal(
            out[impute_cols].where(observed).to_numpy(),
            df[impute_cols].where(observed).to_numpy(),
            equal_nan=True,
        ):
            raise AssertionError("an observed cell changed during imputation")

        draws.append(out)
        if verbose:
            print(f"  draw {draw + 1}/{m}: missing after = {int(out[impute_cols].isna().sum().sum())}")

    return draws


# ═════════════════════════════════════════════════════════════════
# §2  Rubin's rules
# ═════════════════════════════════════════════════════════════════
@dataclass
class PooledEstimate:
    """One pooled parameter."""

    estimate: float
    se: float
    ci_low: float
    ci_high: float
    df: float
    fmi: float          # fraction of missing information
    riv: float          # relative increase in variance
    within_var: float
    between_var: float


def _barnard_rubin_df(m: int, between: float, ubar: float, n_complete: Optional[int]) -> float:
    """Barnard–Rubin (1999) degrees of freedom.

    nu_old  = (m-1)(1 + 1/r)^2                 with r = (1+1/m) B / Ubar
    nu_obs  = (nu_c+1)/(nu_c+3) * nu_c * (1+1/r)
    nu      = nu_old * nu_obs / (nu_old + nu_obs)

    Without a complete-data sample size the conservative `m - 1` is used.
    """
    if ubar <= 0 or between <= 0:
        return float(m - 1)

    r = (1.0 + 1.0 / m) * between / ubar
    nu_old = (m - 1) * (1.0 + 1.0 / r) ** 2
    if n_complete is None or n_complete <= 1:
        return float(nu_old)

    nu_complete = float(n_complete - 1)
    nu_obs = (nu_complete + 1.0) / (nu_complete + 3.0) * nu_complete * (1.0 + 1.0 / r)
    return float(nu_old * nu_obs / (nu_old + nu_obs))


def rubin_pool(
    estimates: Sequence[float],
    variances: Sequence[float],
    n_complete: Optional[int] = None,
    level: float = 0.95,
) -> PooledEstimate:
    """Pool one scalar estimate across `m` imputations (Rubin 1987, Barnard–Rubin df).

    estimates : the point estimate from each imputed dataset
    variances : its estimated sampling variance from each imputed dataset
    n_complete: complete-data sample size, for the Barnard–Rubin adjustment. When
                omitted the conservative `m - 1` degrees of freedom are used.
    """
    q = np.asarray(list(estimates), dtype=float)
    u = np.asarray(list(variances), dtype=float)
    m = q.size
    if m != u.size:
        raise ValueError(f"estimates ({m}) and variances ({u.size}) must be the same length")
    if m < MI_MIN_DRAWS:
        raise ValueError(f"need at least {MI_MIN_DRAWS} imputations to pool, got {m}")
    if not np.all(np.isfinite(q)) or not np.all(np.isfinite(u)):
        raise ValueError("estimates and variances must be finite")
    if np.any(u < 0):
        raise ValueError("variances must be non-negative")

    qbar = float(q.mean())
    ubar = float(u.mean())
    between = float(q.var(ddof=1))
    total = ubar + (1.0 + 1.0 / m) * between

    lambda_ = (1.0 + 1.0 / m) * between / total if total > 0 else 0.0
    riv = (1.0 + 1.0 / m) * between / ubar if ubar > 0 else np.inf
    df = _barnard_rubin_df(m, between, ubar, n_complete)

    se = float(np.sqrt(total))
    crit = float(sps.t.ppf(0.5 + level / 2.0, df))
    return PooledEstimate(
        estimate=qbar,
        se=se,
        ci_low=qbar - crit * se,
        ci_high=qbar + crit * se,
        df=float(df),
        fmi=float(min(1.0, lambda_)),
        riv=float(riv),
        within_var=ubar,
        between_var=between,
    )


def rubin_pool_frame(
    table: pd.DataFrame,
    estimate_col: str = "estimate",
    variance_col: str = "variance",
    term_col: str = "term",
    n_complete: Optional[int] = None,
    level: float = 0.95,
) -> pd.DataFrame:
    """Pool a long table with one row per (imputation, term).

    Columns: term, estimate, variance. Returns one row per term.
    """
    for column in (term_col, estimate_col, variance_col):
        if column not in table.columns:
            raise ValueError(f"missing column {column!r}; have {list(table.columns)}")

    rows = []
    for term, block in table.groupby(term_col, sort=False):
        pooled = rubin_pool(
            block[estimate_col].to_numpy(),
            block[variance_col].to_numpy(),
            n_complete=n_complete,
            level=level,
        )
        rows.append(
            {
                "term": term,
                "estimate": pooled.estimate,
                "se": pooled.se,
                "ci_low": pooled.ci_low,
                "ci_high": pooled.ci_high,
                "df": pooled.df,
                "fmi": pooled.fmi,
                "riv": pooled.riv,
                "within_var": pooled.within_var,
                "between_var": pooled.between_var,
                "m": int(len(block)),
            }
        )
    return pd.DataFrame(rows)
