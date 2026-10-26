"""
shap_analysis.py — SHAP 解释 Combined 逻辑回归；失败时回退 permutation importance
"""
from __future__ import annotations
from typing import Dict, Optional, Sequence

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from sklearn.linear_model import LogisticRegression
from sklearn.inspection import permutation_importance

from modules import config as cfg


def _fit_sklearn_logit(X: np.ndarray, y: np.ndarray) -> LogisticRegression:
    clf = LogisticRegression(
        penalty=None if hasattr(LogisticRegression(), "penalty") else "none",
        solver="lbfgs", max_iter=2000, random_state=cfg.SEED,
    )
    # sklearn 新旧 API：penalty=None (1.2+) vs penalty='none'
    try:
        clf = LogisticRegression(penalty=None, solver="lbfgs", max_iter=2000,
                                 random_state=cfg.SEED)
    except Exception:
        clf = LogisticRegression(penalty="l2", C=1e6, solver="lbfgs", max_iter=2000,
                                 random_state=cfg.SEED)
    clf.fit(X, y)
    return clf


def run_shap(
    X_df: pd.DataFrame,
    y: np.ndarray,
    feature_names: Optional[Sequence[str]] = None,
    max_samples: Optional[int] = None,
    seed: Optional[int] = None,
    title_suffix: str = "",
) -> Dict:
    """
    对影像组学特征矩阵做 SHAP（LinearExplainer）或 permutation importance。
    默认仅用于 LASSO 入选组学特征解释（不含临床变量）。
    X_df: 数值特征 DataFrame；y: 0/1
    """
    from modules.utils import dry_run as _dry
    print("═══ SHAP / importance (radiomics) ═══")
    if _dry.exit_after_plan("shap_analysis", [
        f"shape={getattr(X_df, 'shape', None)}",
        "SHAP / permutation importance (skipped)",
    ]):
        return {"method": None, "importance": None, "dry_run": True}
    suf = f" {title_suffix}" if title_suffix else ""
    seed = int(seed if seed is not None else cfg.SEED)
    max_samples = int(max_samples if max_samples is not None
                      else getattr(cfg, "SHAP_MAX_SAMPLES", 200))
    feats = list(feature_names) if feature_names is not None else list(X_df.columns)
    X = X_df[feats].apply(pd.to_numeric, errors="coerce")
    y = np.asarray(y).astype(int)
    ok = X.notna().all(axis=1).to_numpy() & np.isfinite(y.astype(float))
    X = X.loc[ok].reset_index(drop=True)
    y = y[ok]
    if len(X) < 15 or X.shape[1] < 1:
        print("  ⚠ insufficient data for SHAP — skip")
        return {"method": None, "importance": None}

    # 标准化便于线性解释
    mu = X.mean()
    sd = X.std().replace(0, 1.0)
    Xs = (X - mu) / sd
    Xmat = Xs.to_numpy(dtype=float)
    clf = _fit_sklearn_logit(Xmat, y)

    importance = None
    method = None
    shap_values = None

    try:
        import shap
        # LinearExplainer on logistic (link=logit of decision function ≈ linear)
        explainer = shap.LinearExplainer(clf, Xmat, feature_perturbation="interventional")
        n_exp = min(len(Xmat), max_samples)
        rng = np.random.RandomState(seed)
        idx = rng.choice(len(Xmat), size=n_exp, replace=False) if len(Xmat) > n_exp else np.arange(len(Xmat))
        sv = explainer.shap_values(Xmat[idx])
        if isinstance(sv, list):
            sv = sv[1] if len(sv) > 1 else sv[0]
        shap_values = np.asarray(sv, dtype=float)
        mean_abs = np.abs(shap_values).mean(axis=0)
        try:
            from modules.stats.radiomics import display_rad_feature as _disp_rad
        except Exception:
            def _disp_rad(x):  # type: ignore
                return str(x)
        importance = pd.DataFrame({
            "Feature": [_disp_rad(f) for f in feats],
            "Feature_full": list(feats),
            "mean_|SHAP|": mean_abs,
        }).sort_values("mean_|SHAP|", ascending=False).reset_index(drop=True)
        method = "SHAP LinearExplainer"

        # bar（备用，HTML 默认不展示）
        h = max(2.8, 0.42 * len(feats) + 1.2)
        fig, ax = cfg.style_new_axes(figsize=(7.2, h))
        imp = importance.iloc[::-1]
        short_lab = [str(f) for f in imp["Feature"]]
        colors = plt.cm.Blues(np.linspace(0.45, 0.85, len(imp)))
        ax.barh(range(len(imp)), imp["mean_|SHAP|"].to_numpy(),
                color=colors, edgecolor="none", height=0.72)
        fs = cfg.FIG_FONT_SIZE
        ax.set_yticks(range(len(imp)))
        ax.set_yticklabels(short_lab, fontsize=fs)
        ax.set_xlabel("mean |SHAP value|", fontsize=fs)
        ax.set_title(f"SHAP importance — radiomics (train){suf}", fontsize=fs)
        cfg.apply_clean_theme(ax, grid=True, legend=False)
        ax.grid(axis="y", visible=False)
        fig.tight_layout()
        if cfg.save_plot("shap_bar", fig, width=7.2, height=h):
            print("  shap_bar.png saved")
        plt.close(fig)

        # beeswarm summary
        try:
            try:
                from modules.stats.radiomics import display_rad_feature
                short_names = [display_rad_feature(f) for f in feats]
            except Exception:
                short_names = []
                for f in feats:
                    s = str(f)
                    short_names.append(("…" + s[-36:]) if len(s) > 38 else s)
            h2 = max(3.2, 0.48 * len(feats) + 1.4)
            plt.figure(figsize=(8.2, h2), facecolor="white")
            shap.summary_plot(
                shap_values, Xmat[idx], feature_names=short_names,
                show=False, max_display=min(20, len(feats)),
                plot_size=None, color_bar_label="Feature value",
            )
            fig2 = plt.gcf()
            fig2.patch.set_facecolor("white")
            for a in fig2.axes:
                a.set_facecolor("white")
                for side in ("top", "right"):
                    a.spines[side].set_visible(False)
                a.tick_params(labelsize=fs, colors="#222222")
                a.xaxis.label.set_fontsize(fs)
                a.yaxis.label.set_fontsize(fs)
                a.title.set_fontsize(fs)
                a.title.set_fontweight("normal")
            fig2.tight_layout()
            if cfg.save_plot("shap_summary", fig2, width=8.2, height=h2):
                print("  shap_summary.png saved")
            plt.close("all")
        except Exception as e:
            print(f"  ⚠ shap summary_plot failed: {e}")

        print(f"  method={method}  n_features={len(feats)}  "
              f"top: {list(importance['Feature'].head(3))}")

    except Exception as e:
        print(f"  ⚠ SHAP unavailable ({e}); using permutation importance")
        method = "permutation_importance"
        r = permutation_importance(
            clf, Xmat, y, n_repeats=30, random_state=seed, scoring="roc_auc",
        )
        try:
            from modules.stats.radiomics import display_rad_feature as _disp_rad2
        except Exception:
            def _disp_rad2(x):  # type: ignore
                return str(x)
        importance = pd.DataFrame({
            "Feature": [_disp_rad2(f) for f in feats],
            "Feature_full": list(feats),
            "mean_importance": r.importances_mean,
            "std": r.importances_std,
        }).sort_values("mean_importance", ascending=False).reset_index(drop=True)
        # alias column for HTML
        importance["mean_|SHAP|"] = importance["mean_importance"]

        h = max(2.8, 0.42 * len(feats) + 1.2)
        fig, ax = cfg.style_new_axes(figsize=(7.2, h))
        imp = importance.iloc[::-1]
        ax.barh(range(len(imp)), imp["mean_importance"].to_numpy(),
                color="#7E57C2", xerr=imp["std"].to_numpy(),
                edgecolor="none", height=0.72, ecolor="#555555", capsize=2)
        fs = cfg.FIG_FONT_SIZE
        ax.set_yticks(range(len(imp)))
        ax.set_yticklabels([str(x) for x in imp["Feature"]], fontsize=fs)
        ax.set_xlabel("Permutation importance (Δ AUC)", fontsize=fs)
        ax.set_title(f"Permutation importance — radiomics (train){suf}", fontsize=fs)
        cfg.apply_clean_theme(ax, grid=True, legend=False)
        ax.grid(axis="y", visible=False)
        fig.tight_layout()
        if cfg.save_plot("shap_bar", fig, width=7.2, height=h):
            print("  shap_bar.png saved (permutation)")
        plt.close(fig)

    return {
        "method": method,
        "importance": importance,
        "features": feats,
        "note": (
            f"Method: {method}. SHAP explains LASSO-selected radiomics features only "
            f"(development set; standardised logistic model for Group). "
            f"Clinical covariates are not included."
        ),
    }
