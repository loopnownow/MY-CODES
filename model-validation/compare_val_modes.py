"""三种 VAL_MODE 验证对比（默认 zyp_OV）。

归属 ``0doc/GitHub/stats-analysis/model-validation``（统计/验证 CLI，非 tidy 清洗主链）。
路径与开关置顶软编码。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_STAT = Path(__file__).resolve().parent
_IMPORT = Path(os.environ.get("STATS_ROOT", r"D:\0Grok\0doc\GitHub\stats-analysis"))
_TIDY = Path(os.environ.get(
    "CLEAN_DATA_ROOT",
    str(_IMPORT.parent.parent / "02-data-processing" / "base" / "clean-data"),
))
for p in (_IMPORT, _TIDY):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from tidy_paths import project_root  # noqa: E402

# ==================== CONFIG ====================
PROJ = Path(os.environ.get("ZYP_OV_ROOT", str(project_root("zyp_OV"))))
DRY_RUN = False
# ================================================
os.chdir(PROJ)
os.environ["RAD_PROJECT"] = str(PROJ)

import numpy as np
import pandas as pd
import statsmodels.api as sm

from modules import config as cfg
from modules.config import settings as S
from modules.stats import data_io
from modules.pipeline import run_analysis, build_models
from modules.stats.models import build_nomogram
from modules.stats.curves_roc import plot_roc, ROC_METRICS, ROC_LOCK
from modules.stats.val_mode import (
    resolve_val_mode,
    val_mode_label,
    refit_clinical_prob,
    refit_radscore_ols,
    refit_combined_prob,
)

CMP_DIR = os.path.join(PROJ, "Group", "val_mode_compare")
PNG_DIR = os.path.join(CMP_DIR, "PNG")
os.makedirs(PNG_DIR, exist_ok=True)
for obj in (cfg, S):
    obj.OUT_DIR = CMP_DIR
    obj.PNG_DIR = PNG_DIR
    obj.DO_QC_REPORT = False
    obj.DO_FIGURE_PANELS = False
    obj.DO_FALSE_CLASS = False
    obj.DO_SHAP = False
    obj.DO_MEDIATION = False
    obj.DO_DAG = False
    obj.DO_VIF = False
    obj.DO_NRI = False
    obj.DO_PLOT = False

MODES = ("apply_formula", "lock_threshold", "refit")
FOCUS = ("Clinical", "RadScore", "Combined")


def _apply_train_radscore(layer, rad_train):
    if len(rad_train.features) == 0 or rad_train.betas is None:
        return None
    X = layer.rad[rad_train.features].apply(pd.to_numeric, errors="coerce").to_numpy()
    return rad_train.intercept + X @ rad_train.betas.to_numpy()


def _score_to_prob(score_tr, y_tr, score_te=None):
    if score_tr is None:
        return None, None
    score_tr = np.asarray(score_tr, dtype=float)
    y_tr = np.asarray(y_tr)
    ok = np.isfinite(score_tr)
    if ok.sum() < 5:
        return None, None
    X = sm.add_constant(score_tr[ok])
    fit = sm.GLM(y_tr[ok], X, family=sm.families.Binomial()).fit()
    p_tr = np.full(len(score_tr), np.nan)
    p_tr[ok] = fit.predict(X)
    p_te = None
    if score_te is not None:
        score_te = np.asarray(score_te, dtype=float)
        ok2 = np.isfinite(score_te)
        if ok2.sum() >= 1:
            X2 = sm.add_constant(score_te[ok2])
            p_te = np.full(len(score_te), np.nan)
            p_te[ok2] = fit.predict(X2)
    return p_tr, p_te


def _apply_clin_lp(clin_df, model, feats):
    if model is None or not feats:
        return None
    X = sm.add_constant(clin_df[feats].apply(pd.to_numeric, errors="coerce"))
    cols = [c for c in model.params.index if c in X.columns]
    return model.predict(X[cols], which="linear").to_numpy()


def _apply_combined(layer, rs, nomo):
    if nomo is None or nomo.model is None or rs is None:
        return None
    df_v = layer.clin.copy()
    df_v["RadScore"] = rs
    use = [f for f in nomo.features if f in df_v.columns]
    Xm = sm.add_constant(df_v[use].apply(pd.to_numeric, errors="coerce"))
    cols = [c for c in nomo.model.params.index if c in Xm.columns]
    return nomo.model.predict(Xm[cols]).to_numpy()


def _expand_to_full(prob_short, df_ref, n_full):
    if prob_short is None or df_ref is None:
        return None
    out = np.full(n_full, np.nan)
    out[df_ref.index.to_numpy()] = np.asarray(prob_short, dtype=float)
    return out


def main() -> None:
    print("=" * 70)
    print("zyp_OV VAL_MODE comparison  (train once, three validation modes)")
    print("=" * 70)
    data = data_io.load_data(file=cfg.INPUT, ratio=cfg.TRAIN_RATIO, seed=cfg.SEED)
    res_train = run_analysis(data.train, "train", skip_roc=True)
    train_clin = list(res_train["clin"].features)
    print(f"  Selected clinical: {train_clin}")
    print(f"  LASSO features: {len(res_train['rad'].features)}")

    rs_te = _apply_train_radscore(data.test, res_train["rad"])

    reader_col = getattr(cfg, "READER_COMPARE_COL", None)
    nomo_exclude = set(getattr(cfg, "READER_COLS", None) or [])
    if reader_col:
        nomo_exclude.add(reader_col)
    clin_feat_combined = []
    seen = set()
    for f in train_clin + list(getattr(cfg, "FORCE_CLIN_FOR_ROC", [])):
        if f in nomo_exclude:
            continue
        if f in data.train.clin.columns and f not in seen:
            clin_feat_combined.append(f)
            seen.add(f)
    print(f"  Combined features: {clin_feat_combined}")

    nomo = build_nomogram(
        train_label=data.train.label,
        radscore_train=res_train["rad"].radscore,
        train_clin=data.train.clin,
        clin_feat=clin_feat_combined,
        radscore_test=rs_te,
        test_clin=data.test.clin,
        test_label=data.test.label,
    )
    p_comb_tr = _expand_to_full(nomo.train_prob, nomo.df_train, len(data.train.label))
    clin_lp_tr = _apply_clin_lp(data.train.clin, res_train["clin"].model, train_clin)
    p_clin_tr, _ = _score_to_prob(clin_lp_tr, data.train.label, None)

    ml_tr = build_models(data.train.clin, res_train["rad"].radscore, clin_features=train_clin)
    if p_clin_tr is not None:
        ml_tr["Clinical"] = p_clin_tr
    if p_comb_tr is not None:
        ml_tr["Combined"] = p_comb_tr
    plot_roc(ml_tr, data.train.label, dataset_label="train")

    frames = []
    tr = ROC_METRICS.get("roc_train")
    if tr is not None:
        t = tr.copy()
        t.insert(0, "Split", "train")
        t.insert(1, "Mode", "— (development)")
        frames.append(t)

    for mode in MODES:
        print("\n" + "━" * 70)
        print(f"VALIDATION  {mode}  ({val_mode_label(mode)})")
        print("━" * 70)
        if mode == "refit":
            rs = refit_radscore_ols(data.test.rad, data.test.label, res_train["rad"].features)
            if rs is None:
                print("  ⚠ RadScore refit failed — train formula")
                rs = rs_te
            p_c = refit_clinical_prob(data.test.clin, data.test.label, train_clin)
            if p_c is None:
                print("  ⚠ Clinical refit failed — train formula")
                lp = _apply_clin_lp(data.test.clin, res_train["clin"].model, train_clin)
                _, p_c = _score_to_prob(clin_lp_tr, data.train.label, lp)
            p_comb = refit_combined_prob(
                data.test.clin, data.test.label, clin_feat_combined, rs,
            )
            if p_comb is None:
                print("  ⚠ Combined refit failed — train formula")
                p_comb = _apply_combined(data.test, rs, nomo)
        else:
            rs = rs_te
            lp = _apply_clin_lp(data.test.clin, res_train["clin"].model, train_clin)
            _, p_c = _score_to_prob(clin_lp_tr, data.train.label, lp)
            p_comb = _apply_combined(data.test, rs, nomo)

        ml = build_models(data.test.clin, rs, clin_features=train_clin)
        if p_c is not None:
            ml["Clinical"] = p_c
        if p_comb is not None:
            ml["Combined"] = p_comb
        plot_roc(ml, data.test.label, dataset_label=f"test_{mode}", val_mode=mode)
        mdf = ROC_METRICS.get(f"roc_test_{mode}")
        if mdf is not None:
            t = mdf.copy()
            t.insert(0, "Split", "test")
            t.insert(1, "Mode", mode)
            frames.append(t)

    all_df = pd.concat(frames, ignore_index=True)
    xlsx = os.path.join(CMP_DIR, "val_mode_compare.xlsx")
    html = os.path.join(CMP_DIR, "val_mode_compare.html")
    with pd.ExcelWriter(xlsx, engine="openpyxl") as w:
        all_df.to_excel(w, sheet_name="all_models", index=False)
        focus = all_df[all_df["Model"].isin(FOCUS)].copy()
        focus.to_excel(w, sheet_name="cln_rad_combined", index=False)

    focus = all_df[all_df["Model"].isin(FOCUS)].copy()
    show = focus.copy()
    for c in ("AUC", "AUC_CI_low", "AUC_CI_high", "Threshold", "ACC", "SEN", "SPE", "NPV", "PPV"):
        if c in show.columns:
            show[c] = show[c].map(lambda v: f"{float(v):.3f}" if pd.notna(v) else "NA")
    show["AUC (95% CI)"] = [
        f"{a} ({lo}–{hi})" for a, lo, hi in zip(
            show["AUC"], show["AUC_CI_low"], show["AUC_CI_high"]
        )
    ]
    cols = ["Split", "Mode", "Model", "AUC (95% CI)", "Threshold", "Thr source",
            "ACC", "SEN", "SPE", "NPV", "PPV"]
    if "Threshold_source" in show.columns:
        show["Thr source"] = show["Threshold_source"].map(
            {"train": "train", "this_set": "this set"}
        ).fillna(show.get("Threshold_source", ""))
    else:
        show["Thr source"] = ""
    show = show[[c for c in cols if c in show.columns]]

    n_tr, n_te = len(data.train.label), len(data.test.label)
    n1_tr = int((data.train.label == 1).sum())
    n1_te = int((data.test.label == 1).sum())
    rows_html = []
    for _, r in show.iterrows():
        rows_html.append(
            "<tr>" + "".join(f"<td>{r[c]}</td>" for c in show.columns) + "</tr>"
        )
    page = f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8"/>
<title>zyp_OV VAL_MODE comparison</title>
<style>
body{{font:15px/1.45 "Segoe UI",Calibri,sans-serif;margin:32px;color:#222}}
h1{{font-size:22px}} h2{{font-size:17px;margin-top:28px}}
table{{border-collapse:collapse}}
th,td{{border-bottom:1px solid #ccc;padding:6px 10px;text-align:left}}
th{{border-bottom:2px solid #222}}
.note{{color:#444;max-width:860px}}
code{{background:#f4f4f4;padding:1px 4px}}
</style></head><body>
<h1>zyp_OV — 三种验证方式对比</h1>
<p class="note">
Train N={n_tr} (event={n1_tr}) · Test N={n_te} (event={n1_te}) · seed={cfg.SEED} · ratio={cfg.TRAIN_RATIO}<br/>
Selected clinical: {", ".join(train_clin) or "—"} · Combined covariates: {", ".join(clin_feat_combined)} ·
LASSO features locked: {len(res_train["rad"].features)}<br/>
特征筛选只做一次。三种方式都不重新筛选。
</p>
<h2>Clinical / RadScore / Combined</h2>
<table><thead><tr>{"".join(f"<th>{c}</th>" for c in show.columns)}</tr></thead>
<tbody>{"".join(rows_html)}</tbody></table>
<h2>三种做法</h2>
<ul>
<li><code>apply_formula</code> 套公式：锁 Train 系数，验证集重算 Youden（当前默认）</li>
<li><code>lock_threshold</code> 锁定阈值：锁 Train 系数 + Train Youden</li>
<li><code>refit</code> 重新拟合：同一特征在验证集重估系数，Youden 本层重算</li>
</ul>
<p class="note">完整表：<code>val_mode_compare.xlsx</code>。ROC 图在 PNG/ 下。</p>
</body></html>
"""
    with open(html, "w", encoding="utf-8") as f:
        f.write(page)
    print("\n" + "=" * 70)
    print(show.to_string(index=False))
    print(f"\n  → {xlsx}")
    print(f"  → {html}")
    print("=" * 70)


if __name__ == "__main__":
    main()
