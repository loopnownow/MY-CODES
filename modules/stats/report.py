"""
modules.stats.report — 生成 Results{子项目}.html 综合报告
═══════════════════════════════════════════════════════════════════
功能：将 Pipeline 分析结果组装为自包含的单文件 HTML 报告，包含
      配置信息、各数据层 Table1/单变量/多变量/LASSO/RadScore公式/
      ROC 拼板、组合相关性、联合模型（Nomogram/Calibration/DCA/NRI）等。

由 modules.pipeline 在每个结局结束时调用 generate_results_html()。
HTML 仅嵌入 panel_* 与少数汇总图；写盘后自动删除未引用 PNG。
═══════════════════════════════════════════════════════════════════
"""

from __future__ import annotations
import html
import os
import re
from pathlib import Path
from typing import Optional, Dict, Any, List

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score, roc_curve

from modules import config as cfg
from modules.config.outcome import results_html_name, safe_dir_token, subproject_root
from modules.stats.curves_roc import ROC_METRICS, LANDMARK_ROC_METRICS, _boot_auc_ci
from modules.stats.curves_ipcw import IPCW_ROC_METRICS
from modules.stats.curves_calibration import HL_METRICS


# ═════════════════════════════════════════════════════════════════
# 辅助：三线表 + 统一表头 + 缩写表注
# ═════════════════════════════════════════════════════════════════
# 表头显示名统一（Title Case / 固定缩写大写）
_HEADER_MAP = {
    "feature": "Variable",
    "variable": "Variable",
    "model": "Model",
    "or": "OR",
    "ci": "95% CI",
    "ci_low": "95% CI lower",
    "ci_high": "95% CI upper",
    "p": "P value",
    "sig": "Significance",
    "threshold": "Threshold",
    "threshold_source": "Thr source",
    "val_mode": "Val mode",
    "auc": "AUC",
    "chi2": "χ²",
    "n_groups": "Groups",
    "acc": "ACC",
    "sen": "SEN",
    "spe": "SPE",
    "npv": "NPV",
    "ppv": "PPV",
    "predictor": "Predictor",
    "ref": "Reference",
    "egfr_col": "Reference",
    "n": "N",
    "spearman_rho": "Spearman ρ",
    "p_value": "P value",
    "ci_lower": "95% CI lower",
    "ci_upper": "95% CI upper",
    "coef": "Coefficient",
    "metric": "Metric",
    "value": "Value",
    "hr": "HR",
    "contrast": "Contrast",
    "ph_p": "Schoenfeld P",
    "logrank_p": "Log-rank P",
    "logrank_chi2": "Log-rank χ²",
    "median_months": "Median",
    "events": "Events",
    "stratum": "Stratum",
    "cox_hr": "Cox HR",
    "cox_p": "Cox P",
    "estimate": "Estimate",
    "se": "SE",
    "t": "t",
    "f": "F",
    "df1": "df1",
    "df2": "df2",
    "outcome": "Outcome",
    "term": "Term",
    "β": "β",
    "fdr p": "FDR P",
    "fwer p": "FWER P",
    "pass fdr": "Pass FDR",
    "pass fwer": "Pass FWER",
}

# 缩写 → 全称（仅收录报告中实际可能出现的；表注按首字母排序）
_ABBREV_GLOSSARY = {
    "ACC": "accuracy",
    "AUC": "area under the receiver operating characteristic curve",
    "CA125": "cancer antigen 125",
    "CI": "confidence interval",
    "DCA": "decision curve analysis",
    "DSI": "deep stromal invasion",
    "FIGO": "International Federation of Gynecology and Obstetrics",
    "HR": "hazard ratio",
    "IDI": "integrated discrimination improvement",
    "IP": "integrated specificity (non-events)",
    "IS": "integrated sensitivity (events)",
    "LNM": "lymph node metastasis",
    "LNSMR": "lymph node metastasis on magnetic resonance imaging",
    "LVSI": "lymphovascular space invasion",
    "NRI": "net reclassification improvement",
    "NPV": "negative predictive value",
    "OR": "odds ratio",
    "PCSR": "parametrial circumferential stromal ring integrity",
    "PMI": "parametrial invasion",
    "PMSMR": "parametrial invasion on magnetic resonance imaging",
    "PPV": "positive predictive value",
    "ROC": "receiver operating characteristic",
    "SCC": "squamous cell carcinoma antigen",
    "SEN": "sensitivity",
    "SPE": "specificity",
    "VIF": "variance inflation factor",
    "FDR": "false discovery rate (Benjamini–Hochberg)",
    "FWER": "family-wise error rate (Holm)",
    "SE": "standard error",
}


def _format_header(col: str) -> str:
    key = str(col).strip()
    if key.startswith("P vs ") or key.startswith("vs ") or key.startswith("DeLong vs "):
        return key
    low = key.lower()
    if low in _HEADER_MAP:
        return _HEADER_MAP[low]
    # 已知全大写缩写保持
    if key.upper() in _ABBREV_GLOSSARY and key.isupper():
        return key.upper()
    if key in _ABBREV_GLOSSARY:
        return key
    # 结局展示标签：LNM=n / non_LNM=n —— 保留 non_ 下划线
    import re
    if re.match(r"^(non_)?[A-Za-z0-9]+(=\d+)?$", key):
        return key
    # 默认：下划线转空格
    return key.replace("_", " ")


def _collect_abbrevs_from_df(df: pd.DataFrame) -> set:
    """从表头与单元格文本中收集出现过的缩写。"""
    found = set()
    text_bits = [str(c) for c in df.columns]
    for col in df.columns:
        for v in df[col].astype(str).tolist()[:200]:
            text_bits.append(v)
    blob = " | ".join(text_bits)
    # 按全称长度降序匹配，避免短串误伤；仅匹配词边界式缩写
    import re
    for abbr in _ABBREV_GLOSSARY:
        if re.search(rf"(?i)(?<![A-Za-z0-9]){re.escape(abbr)}(?![A-Za-z0-9])", blob):
            found.add(abbr)
    # 表头映射后的显示名
    for c in df.columns:
        h = _format_header(c)
        for abbr in _ABBREV_GLOSSARY:
            if re.search(rf"(?i)(?<![A-Za-z0-9]){re.escape(abbr)}(?![A-Za-z0-9])", h):
                found.add(abbr)
    return found


def _abbrev_note_html(abbrevs: set) -> str:
    if not abbrevs:
        return ""
    items = sorted(abbrevs, key=lambda a: a.upper())
    parts = [f"{a}, {_ABBREV_GLOSSARY[a]}" for a in items if a in _ABBREV_GLOSSARY]
    if not parts:
        return ""
    return (
        '<p class="table-note"><span class="note-label">Abbreviations:</span> '
        + "; ".join(parts)
        + ".</p>"
    )


def df_to_html(df: Optional[pd.DataFrame], elem_id: Optional[str] = None,
               extra_abbrevs: Optional[set] = None,
               caption: Optional[str] = None,
               footnote: Optional[str] = None) -> str:
    """发表级三线表 HTML + 缩写表注（按首字母排序）；caption 写入表头；footnote 为额外表注。"""
    if df is None or len(df) == 0:
        return '<p class="muted">No data.</p>'
    drop = []
    for c in df.columns:
        h = _format_header(c)
        if h in ("Thr source", "Val mode") or str(c) in (
            "Threshold_source", "threshold_source", "Val_mode", "val_mode",
        ):
            drop.append(c)
    if drop:
        df = df.drop(columns=drop, errors="ignore")
    delong_cols = [
        c for c in df.columns
        if str(c).startswith("DeLong vs ") or str(c).startswith("P vs ")
    ]
    if delong_cols:
        df = df.copy()
        for c in delong_cols:
            df[c] = [_fmt_delong_p(v) for v in df[c]]
            if str(c).startswith("P vs "):
                df = df.rename(columns={c: "DeLong vs " + str(c)[5:]})
    id_attr = f' id="{elem_id}"' if elem_id else ""
    headers = [_format_header(c) for c in df.columns]
    thead = "<thead><tr>" + "".join(f"<th>{h}</th>" for h in headers) + "</tr></thead>"
    tbody_rows = []
    for _, row in df.iterrows():
        cells = []
        for i, v in enumerate(row):
            # 首列左对齐；Table1 分类水平子行（前导空格）缩进
            txt = "" if pd.isna(v) else str(v)
            is_indent = i == 0 and (txt.startswith("  ") or txt.startswith("\u00a0"))
            if i == 0:
                cls = ' class="col-first indent"' if is_indent else ' class="col-first"'
                if is_indent:
                    txt = txt.lstrip()
            else:
                cls = ""
            cells.append(f"<td{cls}>{txt}</td>")
        tbody_rows.append("<tr>" + "".join(cells) + "</tr>")
    tbody = "<tbody>\n" + "\n".join(tbody_rows) + "\n</tbody>"
    cap = f"<caption>{caption}</caption>\n" if caption else ""
    abbrevs = _collect_abbrevs_from_df(df)
    if extra_abbrevs:
        abbrevs |= set(extra_abbrevs)
    notes = _abbrev_note_html(abbrevs)
    if footnote and str(footnote).strip():
        notes += (
            '<p class="table-note"><span class="note-label">Note:</span> '
            f'{footnote}</p>'
        )
    return (
        f'<div class="table-wrap">'
        f'<table class="threeline"{id_attr}>\n{cap}{thead}\n{tbody}\n</table>'
        f'{notes}</div>'
    )


def _f_exists(png_name: str, out_dir: Optional[str] = None) -> bool:
    if out_dir is None:
        out_dir = str(getattr(cfg, "OUT_DIR", "") or "")
    return os.path.exists(os.path.join(out_dir, "PNG", png_name))


def _img_tag(png_name: str, alt: str = "", out_dir: Optional[str] = None) -> str:
    # 图总闸关：结果 HTML 不嵌任何图，也不留占位。
    if not cfg.figures_on():
        return ""
    if out_dir is None:
        out_dir = str(getattr(cfg, "OUT_DIR", "") or "")
    if _f_exists(png_name, out_dir):
        return f'<img src="PNG/{png_name}" alt="{alt}" class="fig">'
    return f'<p class="muted">[Not available: {png_name}]</p>'


def _cleanup_unreferenced_pngs(html_path: str, out_dir: str) -> int:
    """删除 PNG/ 中未被 HTML 引用的 .png（保留 panel 与报告内嵌图）。"""
    png_dir = os.path.join(out_dir, "PNG")
    if not os.path.isdir(png_dir) or not os.path.isfile(html_path):
        return 0
    try:
        with open(html_path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return 0
    refs = set()
    for m in re.finditer(r"""src=["'](?:PNG/)?([^"']+\.png)["']""", text, re.I):
        refs.add(os.path.basename(m.group(1)))
    for m in re.finditer(r"""PNG/([A-Za-z0-9_\-]+\.png)""", text, re.I):
        refs.add(m.group(1))
    n_del = 0
    for name in os.listdir(png_dir):
        if not name.lower().endswith(".png"):
            continue
        if name in refs:
            continue
        try:
            os.remove(os.path.join(png_dir, name))
            n_del += 1
        except OSError:
            pass
    if n_del:
        print(f"  cleaned {n_del} unreferenced PNG(s)")
    return n_del


def _merge_ci_cols(df: pd.DataFrame) -> pd.DataFrame:
    """将 CI_low + CI_high 合并为单列 CI（展示用）。"""
    out = df.copy()
    if "CI_low" in out.columns and "CI_high" in out.columns:
        def _fmt_pair(lo, hi):
            def _one(v):
                if isinstance(v, str):
                    return v
                try:
                    return f"{float(v):.3f}"
                except Exception:
                    return str(v)
            return f"{_one(lo)}-{_one(hi)}"
        out["CI"] = [_fmt_pair(lo, hi) for lo, hi in zip(out["CI_low"], out["CI_high"])]
        out = out.drop(columns=["CI_low", "CI_high"])
        # 列序：Feature/OR/CI/p/VIF 优先
        preferred = [c for c in ("Feature", "Term", "OR", "CI", "p", "FDR P", "FWER P", "Pass FDR", "Pass FWER", "VIF", "Sig")
                     if c in out.columns]
        rest = [c for c in out.columns if c not in preferred]
        out = out[preferred + rest]
    elif "VIF" in out.columns:
        preferred = [c for c in ("Feature", "Term", "OR", "CI", "p", "FDR P", "FWER P", "Pass FDR", "Pass FWER", "VIF", "Sig")
                     if c in out.columns]
        rest = [c for c in out.columns if c not in preferred]
        out = out[preferred + rest]
    return out


def _format_mv_df(mv: pd.DataFrame) -> pd.DataFrame:
    """统一多因素表列序，与单因素一致：Variable / OR / 95% CI / P / VIF / Significance。"""
    mv = _merge_ci_cols(mv)
    if "Sig" not in mv.columns and "p" in mv.columns:
        from modules.stats.clinical import _sig_stars, parse_p_value
        mv = mv.copy()
        mv["Sig"] = [_sig_stars(parse_p_value(v)) for v in mv["p"]]
    preferred = [c for c in ("Feature", "Term", "OR", "CI", "p", "FDR P", "FWER P", "Pass FDR", "Pass FWER", "VIF", "Sig")
                 if c in mv.columns]
    rest = [c for c in mv.columns if c not in preferred]
    return mv[preferred + rest]


def _mv_table_html(clin_res, panel_label: str) -> str:
    """Train 多变量 Logistic：候选池表 + Selected（手动优先，否则多因素 p 默认）。"""
    parts: List[str] = []
    if clin_res is None:
        return ""
    mv_pool = getattr(clin_res, "mv_df", None)
    if mv_pool is not None and len(mv_pool) > 0:
        mv = _format_mv_df(mv_pool)
        foot = getattr(clin_res, "mv_note", None) or (
            "Full candidate-pool multivariable table. Clinical / Combined / nomogram "
            "use FORCE_MODEL_FEATURES when set; if empty, multivariate p default."
        )
        parts.append(
            '<h3>Multivariate Logistic (Train — candidate pool)</h3>'
            + df_to_html(mv, f"multivar-pool-{panel_label}", footnote=foot)
        )
    mv_sel = getattr(clin_res, "mv_selected", None)
    if mv_sel is not None and len(mv_sel) > 0:
        mv2 = _format_mv_df(mv_sel)
        sel_feats = list(getattr(clin_res, "features", None) or [])
        src = getattr(clin_res, "select_source", None)
        p_cut = getattr(cfg, "ROC_MV_P_THRESHOLD", 0.05)
        if src == "mv_p":
            foot2 = (
                "Default clinical model from candidate-pool multivariate p < "
                f"{p_cut:g} (n features = {len(sel_feats)}): "
                + (", ".join(sel_feats) if sel_feats else "none")
                + ". Override by setting FORCE_MODEL_FEATURES."
            )
        else:
            foot2 = (
                "Manual clinical model (FORCE_MODEL_FEATURES) for Clinical score "
                f"(n features = {len(sel_feats)}): "
                + (", ".join(sel_feats) if sel_feats else "none")
                + ". Manual list overrides the multivariate p default."
            )
        parts.append(
            '<h3>Multivariate Logistic (Train — selected)</h3>'
            + df_to_html(mv2, f"multivar-sel-{panel_label}", footnote=foot2)
        )
    return "\n".join(parts)




def _fmt_delong_p(p) -> str:
    if p is None or (isinstance(p, str) and p.strip() in ("", "—", "-")):
        return "—"
    if isinstance(p, str):
        s = p.strip()
        if s.startswith("<") or s in ("NA", "—"):
            return s
    try:
        v = float(p)
    except (TypeError, ValueError):
        return "NA"
    if not np.isfinite(v):
        return "—"
    if v < 0.001:
        return "<0.001"
    return f"{v:.3f}"


def _hl_metrics_html(splits, elem_id: str = "hosmer-lemeshow") -> str:
    """Hosmer-Lemeshow 表；splits 为 dataset_label 元组，缺键则跳过。"""
    rows = []
    for key in splits:
        rec = HL_METRICS.get(str(key))
        if not rec:
            continue
        chi2, dof, p = rec.get("chi2"), rec.get("df"), rec.get("p")
        rows.append({
            "Split": key,
            "chi2": "NA" if chi2 is None or not np.isfinite(chi2) else f"{float(chi2):.2f}",
            "df": "NA" if dof is None or not np.isfinite(dof) else int(dof),
            "P": _fmt_delong_p(p),
            "n_groups": rec.get("n_groups"),
            "N": rec.get("n"),
        })
    if not rows:
        return ""
    return df_to_html(
        pd.DataFrame(rows),
        elem_id,
        caption="Hosmer-Lemeshow goodness-of-fit",
        footnote=(
            "Quantile bins match the calibration curve. "
            "df = Groups − 2. P &gt; 0.05 does not prove perfect calibration."
        ),
    )


def _roc_metrics_format_df(mdf: pd.DataFrame) -> pd.DataFrame:
    """Format a ROC_METRICS frame for HTML: no Thr source; DeLong p as extra columns."""
    num = lambda s, nd=3: s.map(lambda v: "NA" if pd.isna(v) else f"{float(v):.{nd}f}")
    out = {
        "Model": mdf["Model"],
        "Threshold": num(mdf["Threshold"]),
        "AUC": num(mdf["AUC"]),
        "CI": [f"({float(lo):.3f}-{float(hi):.3f})" for lo, hi in zip(mdf["AUC_CI_low"], mdf["AUC_CI_high"])],
        "ACC": num(mdf["ACC"]),
        "SEN": num(mdf["SEN"]),
        "SPE": num(mdf["SPE"]),
        "NPV": num(mdf["NPV"]),
        "PPV": num(mdf["PPV"]),
    }
    for c in mdf.columns:
        name = str(c)
        if name.startswith("DeLong vs "):
            out[name] = [_fmt_delong_p(v) for v in mdf[c]]
        elif name.startswith("P vs "):
            out["DeLong vs " + name[5:]] = [_fmt_delong_p(v) for v in mdf[c]]
    return pd.DataFrame(out)


def _roc_metrics_display_df(panel_label: str, rad_res, ds) -> Optional[pd.DataFrame]:
    """ROC 指标展示表（无 HTML）；优先读取内存中的 ROC_METRICS。"""
    roc_key = "roc" if panel_label == "all" else f"roc_{panel_label}"
    mdf = ROC_METRICS.get(roc_key)
    if mdf is not None and len(mdf) > 0:
        return _roc_metrics_format_df(mdf)
    smap: Dict[str, np.ndarray] = {}
    if rad_res is not None and ds is not None:
        try:
            from modules.stats.radiomics import score_map
            smap = score_map(rad_res)
        except Exception:
            smap = {}
        if not smap and getattr(rad_res, "radscore", None) is not None:
            smap = {"RadScore": np.asarray(rad_res.radscore, dtype=float)}
    if smap and ds is not None:
        y = np.asarray(ds.label)
        rows = []
        for model_name, score in smap.items():
            score = np.asarray(score, dtype=float)
            ok = np.isfinite(score) & ~pd.isna(y)
            if ok.sum() <= 10 or len(np.unique(y[ok])) < 2:
                continue
            auc = roc_auc_score(y[ok], score[ok])
            ci = _boot_auc_ci(y[ok], score[ok])
            fpr, tpr, thr = roc_curve(y[ok], score[ok])
            idx = int(np.argmax(tpr - fpr))
            thr_y, sens, spec = thr[idx], tpr[idx], 1 - fpr[idx]
            pred = (score[ok] >= thr_y).astype(int)
            TP = int(((pred == 1) & (y[ok] == 1)).sum())
            TN = int(((pred == 0) & (y[ok] == 0)).sum())
            FP = int(((pred == 1) & (y[ok] == 0)).sum())
            FN = int(((pred == 0) & (y[ok] == 1)).sum())
            acc = (TP + TN) / len(y[ok])
            npv = TN / (TN + FN) if (TN + FN) > 0 else np.nan
            ppv = TP / (TP + FP) if (TP + FP) > 0 else np.nan
            rows.append({
                "Model": model_name, "Threshold": f"{thr_y:.3f}", "AUC": f"{auc:.3f}",
                "CI": f"({ci[0]:.3f}-{ci[1]:.3f})", "ACC": f"{acc:.3f}", "SEN": f"{sens:.3f}",
                "SPE": f"{spec:.3f}", "NPV": "NA" if np.isnan(npv) else f"{npv:.3f}",
                "PPV": "NA" if np.isnan(ppv) else f"{ppv:.3f}",
            })
        if rows:
            return pd.DataFrame(rows)
    return None



def _roc_metrics_train_test_html(res_train, res_test, data) -> str:
    """Train + Test ROC summary 合并为一表；首列为 Split（train / test）。"""
    frames: List[pd.DataFrame] = []
    if res_train is not None and data is not None:
        tr = _roc_metrics_display_df("train", res_train.get("rad"), data.train)
        if tr is not None and len(tr) > 0:
            tr = tr.copy()
            tr.insert(0, "Split", "train")
            frames.append(tr)
    if res_test is not None and data is not None and getattr(data, "test", None) is not None:
        te = _roc_metrics_display_df("test", res_test.get("rad"), data.test)
        if te is not None and len(te) > 0:
            te = te.copy()
            te.insert(0, "Split", "test")
            frames.append(te)
    if not frames:
        return ""
    merged = pd.concat(frames, ignore_index=True)
    cap = "ROC summary — train & test"
    note = None
    if any(str(c).startswith("DeLong vs ") or str(c).startswith("P vs ") for c in merged.columns):
        note = (
            "DeLong vs Model: two-sided DeLong test of AUCs on the same patients "
            "in that split. Em dash = same model."
        )
    return (
        '<h4>ROC summary</h4>'
        + df_to_html(merged, "roc-metrics-train-test", caption=cap, footnote=note)
    )


def _is_validation_panel(panel_label: str) -> bool:
    """test / external_* 验证面板：无筛选、无 uni/mv、无 LASSO 拟合图。"""
    pl = str(panel_label or "")
    return pl == "test" or pl.startswith("test") or pl.startswith("ext")


def _combined_block_html(
    panel_label: str,
    combined_info: Optional[Dict[str, Any]],
    is_val: bool,
    rad_res=None,
    ds=None,
    out_dir: Optional[str] = None,
) -> List[str]:
    """
    Combined Model 块：系数/列线图 → ROC + ROC summary → Calibration → DCA。
    不含 NRI/IDI。
    """
    if out_dir is None:
        out_dir = str(getattr(cfg, "OUT_DIR", "") or "")
    parts: List[str] = []
    if not bool(getattr(cfg, "DO_COMBINED_MODEL", True)):
        return parts
    if combined_info is None or not (panel_label == "train" or is_val):
        return parts
    nomo = combined_info.get("nomo")
    if nomo is not None and panel_label == "train":
        try:
            params = nomo.model.params
            pvals = nomo.model.pvalues
            rows = []
            for name in params.index:
                or_ = np.exp(params[name]) if name != "const" else np.nan
                rows.append({
                    "Term": name,
                    "Coef": f"{params[name]:.4f}",
                    "OR": f"{or_:.3f}" if name != "const" else "—",
                    "p": "<0.001" if pvals[name] < 0.001 else f"{pvals[name]:.3f}",
                })
            parts.append(df_to_html(pd.DataFrame(rows), f"combined-coef-{panel_label}"))
        except Exception as e:
            print(f"  ⚠ combined-coef HTML skipped [{panel_label}]: {e}", flush=True)
    if not parts:
        return parts
    return ['<h3>Combined Model (Clinical + RadScore)</h3>', *parts]


def _shap_inline_html(advanced_info: Optional[Dict[str, Any]], out_dir: str) -> List[str]:
    """SHAP：图用 panel_shap；此处仅 importance 表（可选）。"""
    sh = (advanced_info or {}).get("shap") if advanced_info else None
    if not sh:
        return []
    parts: List[str] = []
    imp = sh.get("importance")
    if imp is not None and len(imp):
        disp = imp.copy()
        # 不展示内部全名列
        if "Feature_full" in disp.columns:
            disp = disp.drop(columns=["Feature_full"])
        for col in disp.columns:
            if col == "Feature":
                continue
            if pd.api.types.is_numeric_dtype(disp[col]):
                disp[col] = disp[col].map(lambda v: f"{float(v):.4f}" if pd.notna(v) else "NA")
        parts += ["<h3>SHAP importance table</h3>", df_to_html(disp, "shap-importance")]
    return parts


def _fmt_ci(lo, hi) -> str:
    try:
        lo_f, hi_f = float(lo), float(hi)
        if np.isfinite(lo_f) and np.isfinite(hi_f):
            return f"{lo_f:.4f}–{hi_f:.4f}"
    except (TypeError, ValueError):
        pass
    return "NA"


def _nri_old_label(block: Dict[str, Any]) -> str:
    named = str((block or {}).get("old_label") or "").strip()
    if named:
        return named
    reader = getattr(cfg, "READER_COMPARE_COL", None)
    old_spec = list(getattr(cfg, "NRI_MODEL_OLD", ["clinical"]) or ["clinical"])
    if reader:
        return str(reader)
    if old_spec and str(old_spec[0]).strip().lower() not in ("clinical", "clin"):
        return ", ".join(str(x) for x in old_spec)
    return "Clinical"


def _nri_idi_section_html(combined_info: Optional[Dict[str, Any]], out_dir: str) -> str:
    """NRI/IDI 合并表（均含 95%CI）；多个旧模型按顺序各出一块，下一块在上一块下面。"""
    if not bool(getattr(cfg, "DO_NRI", False)):
        return ""
    if not combined_info:
        return ""
    blocks = list(combined_info.get("nri_blocks") or [])
    if not blocks:
        nri_res = combined_info.get("nri")
        idi_res = combined_info.get("idi")
        if nri_res is None and idi_res is None:
            return ""
        blocks = [{
            "nri": nri_res,
            "idi": idi_res,
            "old_label": "",
            "chord_file": "",
            "idi_file": "",
        }]
    sections: List[str] = []
    for i, block in enumerate(blocks):
        nri_res = block.get("nri") if isinstance(block, dict) else None
        idi_res = block.get("idi") if isinstance(block, dict) else None
        if nri_res is None and idi_res is None:
            continue
        gmap = combined_info.get("group_labels") or {}
        if isinstance(nri_res, dict) and nri_res.get("group_labels"):
            gmap = nri_res["group_labels"]
        elif isinstance(idi_res, dict) and idi_res.get("group_labels"):
            gmap = idi_res["group_labels"]
        name0 = str(gmap.get(0, "Negative"))
        name1 = str(gmap.get(1, "Positive"))
        old_label = _nri_old_label(block if isinstance(block, dict) else {})
        layer = ""
        apparent = False
        cutoff_txt = ""
        if isinstance(nri_res, dict):
            layer = str(nri_res.get("layer") or "")
            apparent = bool(nri_res.get("apparent"))
            cutoff_txt = str(nri_res.get("cutoff") or "")
        elif isinstance(idi_res, dict):
            layer = str(idi_res.get("layer") or "")
            apparent = bool(idi_res.get("apparent"))
        note = (
            f"Locked-model probabilities on the <b>{layer or 'evaluation'}</b> layer. "
            "Bootstrap treats predicted probabilities as fixed (no refit). "
            "<b>PC</b> = previous classified; <b>SC</b> = subsequent classified."
        )
        if apparent:
            note = (
                "<b>Apparent</b> (development-set) reclassification — not independent "
                "incremental value. " + note
            )
        if cutoff_txt:
            note += f" Risk cutoffs: {cutoff_txt}."
        parts = [
            f'<section><h2>F. NRI / IDI ({old_label} → Combined)</h2>',
            f'<p class="note">{note}</p>',
        ]
        rows = []
        if isinstance(nri_res, dict):
            rows.extend([
                {
                    "Metric": f"NRI ({name1})",
                    "Estimate": f"{nri_res['NRI_events']:.4f}",
                    "95% CI": _fmt_ci(
                        nri_res.get("NRI_events_CI_low"), nri_res.get("NRI_events_CI_high")
                    ),
                },
                {
                    "Metric": f"NRI ({name0})",
                    "Estimate": f"{nri_res['NRI_nonevents']:.4f}",
                    "95% CI": _fmt_ci(
                        nri_res.get("NRI_nonevents_CI_low"),
                        nri_res.get("NRI_nonevents_CI_high"),
                    ),
                },
                {
                    "Metric": "NRI total",
                    "Estimate": f"{nri_res['NRI_total']:.4f}",
                    "95% CI": _fmt_ci(
                        nri_res.get("NRI_total_CI_low"), nri_res.get("NRI_total_CI_high")
                    ),
                },
            ])
        if isinstance(idi_res, dict):
            is_v = idi_res.get("IS_events", float("nan"))
            ip_v = idi_res.get("IP_nonevents", float("nan"))
            rows.extend([
                {
                    "Metric": "IDI",
                    "Estimate": f"{idi_res['IDI']:.4f}",
                    "95% CI": _fmt_ci(idi_res.get("IDI_CI_low"), idi_res.get("IDI_CI_high")),
                },
                {
                    "Metric": f"IS ({name1})",
                    "Estimate": f"{is_v:.4f}" if np.isfinite(float(is_v)) else "NA",
                    "95% CI": "—",
                },
                {
                    "Metric": f"IP ({name0})",
                    "Estimate": f"{ip_v:.4f}" if np.isfinite(float(ip_v)) else "NA",
                    "95% CI": "—",
                },
            ])
        table_id = "nri-idi-merged" if i == 0 else f"nri-idi-merged-{i + 1}"
        if rows:
            parts.append(
                df_to_html(
                    pd.DataFrame(rows), table_id,
                    extra_abbrevs={"NRI", "IDI", "IS", "IP", "CI", "PC", "SC"},
                    caption="NRI and IDI",
                )
            )
        if isinstance(nri_res, dict):
            named = str(block.get("chord_file") or "").strip()
            if named and _f_exists(named, out_dir):
                chord_name = named
            elif i == 0:
                layer_stem = str(nri_res.get("layer") or layer or "").strip()
                chord_cands = []
                if layer_stem:
                    chord_cands.append(f"chord_{layer_stem}.png")
                chord_cands.extend(["chord_test.png", "chord_train.png", "chord.png"])
                chord_name = next((n for n in chord_cands if _f_exists(n, out_dir)), "")
            else:
                chord_name = ""
            if chord_name:
                parts += [
                    f'<h3>NRI reclassification — {name0} / {name1}</h3>',
                    _img_tag(chord_name, f"NRI chord {old_label}", out_dir),
                ]
        idi_named = str(block.get("idi_file") or "").strip()
        if idi_named and _f_exists(idi_named, out_dir):
            idi_name = idi_named
        elif i == 0 and isinstance(idi_res, dict) and _f_exists("idi.png", out_dir):
            idi_name = "idi.png"
        else:
            idi_name = ""
        if idi_name:
            parts += [
                "<h3>IDI</h3>",
                _img_tag(idi_name, f"IDI {old_label}", out_dir),
            ]
        parts.append("</section>")
        sections.append("\n".join(parts))
    return "\n".join(sections)


def _mediation_section_html(advanced_info: Optional[Dict[str, Any]], out_dir: str) -> str:
    if not bool(getattr(cfg, "DO_MEDIATION", False)):
        return ""
    med = (advanced_info or {}).get("mediation") if advanced_info else None
    has_panel = _f_exists("panel_mediation.png", out_dir)
    if not med and not has_panel:
        return ""
    note_explore = (
        '<p class="note">Development-set exploratory analyses. '
        "Not causal identification.</p>"
    )
    parts = ['<section><h2>I. Mediation Analysis (exploratory)</h2>', note_explore]
    if has_panel:
        parts += [
            "<h3>Mediation paths</h3>",
            _img_tag("panel_mediation.png", "Mediation panel", out_dir),
        ]
    if med:
        if med.get("note"):
            parts.append(
                f'<p class="note">{med["note"]} Bootstrap B={med.get("n_boot", "NA")}.</p>'
            )
        summ = med.get("summary")
        if summ is not None and len(summ):
            disp = summ.copy()
            for col in disp.columns:
                if col in ("Path", "Label", "ACME 95% CI"):
                    continue
                if pd.api.types.is_numeric_dtype(disp[col]):
                    disp[col] = disp[col].map(
                        lambda v: f"{float(v):.4f}"
                        if pd.notna(v) and np.isfinite(float(v))
                        else "NA"
                    )
            parts += [
                "<h3>Path summary</h3>",
                df_to_html(
                    disp, "mediation-summary",
                    footnote="ACME = a×b (indirect); ADE = c′ (direct); total = c.",
                ),
            ]
    parts.append("</section>")
    return "\n".join(parts)


def _dag_section_html(out_dir: str) -> str:
    if not bool(getattr(cfg, "DO_DAG", False)):
        return ""
    png = "dag_mediation_paths.png"
    if not _f_exists(png, out_dir) and _f_exists("dag_assumed.png", out_dir):
        png = "dag_assumed.png"
    if not _f_exists(png, out_dir):
        return ""
    return "\n".join([
        '<section><h2>H. Assumed DAG</h2>',
        '<p class="note">Domain-knowledge DAG. Red edges mark mediation-related paths. '
        "Not data-driven structure learning.</p>",
        _img_tag(png, "Assumed DAG", out_dir),
        "</section>",
    ])



def _clean_table1_df(clin_res) -> Optional[pd.DataFrame]:
    """取出可展示的 Table1（无内部列）；Significance 并入 P value。"""
    if clin_res is None or getattr(clin_res, "table1", None) is None:
        return None
    if len(clin_res.table1) == 0:
        return None
    t1 = clin_res.table1.copy()
    # 保留 _is_level 供简化用
    has_level = "_is_level" in t1.columns
    drop_internal = [c for c in t1.columns if c.startswith("_") and c != "_is_level"]
    t1 = t1.drop(columns=drop_internal, errors="ignore")
    if "Sig" in t1.columns and "Significance" not in t1.columns:
        t1 = t1.rename(columns={"Sig": "Significance"})
    if "p" in t1.columns and "P value" not in t1.columns:
        t1 = t1.rename(columns={"p": "P value"})
    if "Variable" not in t1.columns:
        return None
    t1["Variable"] = t1["Variable"].astype(str)
    if "P value" in t1.columns:
        if "Significance" in t1.columns:
            t1["P value"] = [
                f"{'' if pd.isna(p) else str(p)}{'' if pd.isna(s) or str(s).strip()=='' else str(s)}"
                for p, s in zip(t1["P value"], t1["Significance"])
            ]
            t1 = t1.drop(columns=["Significance"])
    if not has_level and "_is_level" in t1.columns:
        t1 = t1.drop(columns=["_is_level"], errors="ignore")
    return t1.reset_index(drop=True)



def _clean_table2_df(rad_res) -> Optional[pd.DataFrame]:
    """取出可展示的 radiomics Table 2（与 Table1 清洗规则相同）。"""
    if rad_res is None or getattr(rad_res, "table2", None) is None:
        return None
    if len(rad_res.table2) == 0:
        return None
    # Reuse Table1 cleaner by duck-typing .table1
    class _T:
        pass
    tmp = _T()
    tmp.table1 = rad_res.table2
    return _clean_table1_df(tmp)


def _rad_table2_method(rad_res) -> str:
    """lasso | logistic — drives Table 2 titles."""
    if rad_res is None:
        return "lasso"
    return str(getattr(rad_res, "method", "") or "lasso")


def _rad_table2_caption(rad_res, *, is_all: bool = False, panel_label: str = "") -> str:
    method = _rad_table2_method(rad_res)
    if method == "logistic":
        if is_all:
            return "Table 2 — Radiomics Baseline (All)"
        return f"Table 2 — Radiomics ({panel_label})" if panel_label else "Table 2 — Radiomics Baseline"
    if is_all:
        return "Table 2 — LASSO-selected Radiomics (All)"
    return "Table 2 — LASSO-selected Radiomics"


def _table2_train_test_section(res_train, res_test) -> str:
    """Radiomics Table 2 — Train vs Test (same multiheader as Table 1)."""
    rad_tr = res_train["rad"] if res_train else None
    t_tr = _clean_table2_df(rad_tr)
    t_te = _clean_table2_df(res_test["rad"] if res_test else None)
    if t_tr is None and t_te is None:
        return ""
    if t_tr is not None:
        t_tr = t_tr.drop(columns=[c for c in t_tr.columns if c.startswith("_")], errors="ignore")
    if t_te is not None:
        t_te = t_te.drop(columns=[c for c in t_te.columns if c.startswith("_")], errors="ignore")
    body = _table1_multiheader_html(
        t_tr, t_te, elem_id="table2-train-test",
    )
    method = _rad_table2_method(rad_tr)
    if method == "logistic":
        cap = "Table 2 — Radiomics Train vs Test"
        h2 = "B2. Radiomics Table 2 — Train vs Test"
        note = (
            "Same layout as Baseline Table 1; group-wise descriptive "
            "statistics for the radiomics feature pool (original scale, median-imputed; "
            "columns = training preprocess keep_cols)."
        )
    else:
        cap = "Table 2 — LASSO-selected Radiomics"
        h2 = "B2. Table 2 — LASSO-selected Radiomics (Train vs Test)"
        note = (
            "Same layout as Baseline Table 1; group-wise descriptive "
            "statistics for LASSO-selected radiomics features "
            "(original scale, median-imputed)."
        )
    body = body.replace(
        "<caption>Table 1</caption>",
        f"<caption>{cap}</caption>",
        1,
    )
    return (
        f'<section><h2>{h2}</h2>\n'
        f'<p class="note">{note}</p>\n'
        + body
        + "</section>"
    )


def _parse_group_header(col: str):
    """解析 'Pathologic_Grade=128' → (name, n) 或 (col, None)。"""
    import re
    m = re.match(r"^(.+)=(\d+)$", str(col).strip())
    if m:
        return m.group(1).strip(), int(m.group(2))
    return str(col).strip(), None


def _positive_level_key(lab: str) -> bool:
    """判断二分类子行是否为“阳性”水平（仅用于恰好 2 水平的变量）。

    不把 Grade_3/Grade_4 等有序多分类水平当作阳性，避免多水平变量被压成单行。
    """
    s = str(lab).strip().lower().replace(" ", "_")
    # 整词匹配；不含 grade_1–4（那些是 ≥3 水平有序分类）
    pos_exact = {
        "positive", "pos", "yes", "true", "high", "high_grade",
        "dmi", "csi", "lnm", "lvsi",
    }
    neg_exact = {
        "negative", "neg", "no", "false", "low", "low_grade",
        "smi", "no_invasion",
    }
    if s in neg_exact or s.startswith("non_"):
        return False
    if s in pos_exact:
        return True
    return False


def _pct_only(cell) -> str:
    """从 'n (xx.x%)' 抽出 'xx.x%'。"""
    import re
    cell_s = "" if pd.isna(cell) else str(cell)
    m = re.search(r"\(([^)]+%)\)", cell_s)
    return m.group(1) if m else cell_s


def _vs_columns(df: pd.DataFrame) -> list:
    """两两 P 列。不是分组描述列。"""
    return [c for c in df.columns if " vs " in str(c)]


def _group_columns(df: pd.DataFrame) -> list:
    """'MCI=40' 这种描述列。两两 P 列不进这里。"""
    meta = {"Variable", "P value", "Significance", "_is_level", "_p_num"}
    vs = set(_vs_columns(df))
    return [c for c in df.columns if c not in meta and c not in vs]


def _simplify_table1_all(t1: pd.DataFrame) -> pd.DataFrame:
    """
    All 表简化：
      - 恰好 2 水平：一行，仅阳性水平百分比
      - 多水平（≥3）：始终保留各水平子行，单元格仅百分比
      - 连续 / 无子行：原样
      - 「A vs B」是 P，留在父行，不改成百分比
    """
    if t1 is None or len(t1) == 0:
        return t1
    df = t1.copy()
    meta = {"Variable", "P value", "Significance", "_is_level", "_p_num"}
    stat = _vs_columns(df)
    gcols = [c for c in df.columns if c not in meta and c not in set(stat)]
    if not gcols:
        return df.drop(columns=[c for c in df.columns if c.startswith("_")], errors="ignore")

    rows = []
    i = 0
    n = len(df)
    while i < n:
        r = df.iloc[i]
        is_level = (
            bool(r.get("_is_level", False))
            if "_is_level" in df.columns
            else str(r["Variable"]).startswith("  ")
        )
        if is_level:
            i += 1
            continue
        levels = []
        j = i + 1
        while j < n:
            rj = df.iloc[j]
            is_lv = (
                bool(rj.get("_is_level", False))
                if "_is_level" in df.columns
                else str(rj["Variable"]).startswith("  ")
            )
            if not is_lv:
                break
            levels.append(rj)
            j += 1

        if not levels:
            # 连续变量或无水平展开
            row = {"Variable": r["Variable"]}
            for c in gcols:
                row[c] = r[c] if c in r.index else ""
            row["P value"] = r.get("P value", "")
            for c in stat:
                row[c] = r[c] if c in r.index else ""
            rows.append(row)
        elif len(levels) == 2:
            # 仅二分类压成阳性 %；多水平绝不进入此分支
            pos_row = None
            for lv in levels:
                if _positive_level_key(lv["Variable"]):
                    pos_row = lv
                    break
            if pos_row is None:
                # 无明确标签时取第二水平为阳性（0/1 编码常见）
                pos_row = levels[1]
            row = {"Variable": r["Variable"]}
            for c in gcols:
                row[c] = _pct_only(pos_row[c] if c in pos_row.index else "")
            row["P value"] = r.get("P value", "")
            for c in stat:
                row[c] = r[c] if c in r.index else ""
            rows.append(row)
        else:
            # 多水平分类（Histology、FIGO、Grade_1–4 等）：保留各水平，仅 %
            rows.append({
                "Variable": r["Variable"],
                **{c: "" for c in gcols},
                "P value": r.get("P value", ""),
                **{c: (r[c] if c in r.index else "") for c in stat},
            })
            for lv in levels:
                rows.append({
                    "Variable": lv["Variable"],
                    **{c: _pct_only(lv[c] if c in lv.index else "") for c in gcols},
                    "P value": "",
                    **{c: "" for c in stat},
                })
        i = j if levels else i + 1
    out = pd.DataFrame(rows)
    return out.fillna("")


def _is_table1_level_row(row, has_level_col: bool) -> bool:
    """Level sub-rows: `_is_level` if present, else Variable prefixed with two spaces."""
    if has_level_col:
        try:
            return bool(row["_is_level"])
        except Exception as e:
            print(f"  ⚠ table1 level-row flag read failed: {e}", flush=True)
    return str(row["Variable"]).startswith("  ")


def _table1_variable_blocks(df: pd.DataFrame) -> list:
    """Group Table 1 rows into parent blocks.

    Binary markers (EGFR, PD_L1, …) all use the same child labels
    ``  Negative`` / ``  Positive``. Aligning train vs test by Variable
    string alone therefore collides; the parent name + level is unique.
    """
    if df is None or len(df) == 0:
        return []
    has_level = "_is_level" in df.columns
    blocks = []
    i = 0
    n = len(df)
    while i < n:
        r = df.iloc[i]
        if _is_table1_level_row(r, has_level):
            blocks.append({
                "variable": str(r["Variable"]),
                "header": r,
                "levels": [],
            })
            i += 1
            continue
        levels = []
        j = i + 1
        while j < n:
            rj = df.iloc[j]
            if not _is_table1_level_row(rj, has_level):
                break
            levels.append((str(rj["Variable"]), rj))
            j += 1
        blocks.append({
            "variable": str(r["Variable"]),
            "header": r,
            "levels": levels,
        })
        i = j
    return blocks


def _table1_multiheader_html(
    t_train: Optional[pd.DataFrame],
    t_test: Optional[pd.DataFrame],
    elem_id: str = "table1-train-test",
) -> str:
    """
    三行表头：
      行1 Split: Train | Test
      行2 Group: Pathologic_Grade | non_...
      行3 n=128 | n=611
    Align by parent variable, then by level label under that parent.
    Do not set_index('Variable'): level names repeat across markers.
    """
    if t_train is None and t_test is None:
        return ""

    def _prep(df):
        if df is None:
            return None
        keep = [c for c in df.columns if (not str(c).startswith("_")) or c == "_is_level"]
        return df.loc[:, keep].copy() if keep else df.copy()

    t_train, t_test = _prep(t_train), _prep(t_test)

    g_tr = _group_columns(t_train) if t_train is not None else []
    g_te = _group_columns(t_test) if t_test is not None else []
    vs_tr = _vs_columns(t_train) if t_train is not None else []
    vs_te = _vs_columns(t_test) if t_test is not None else []
    n_tr, n_te = len(g_tr), len(g_te)

    def _rowspan_headers(cols) -> str:
        return "".join(f'<th rowspan="3">{_format_header(c)}</th>' for c in cols)

    r1 = '<th rowspan="3" class="col-first">Variable</th>'
    if n_tr:
        r1 += f'<th colspan="{n_tr}">Train</th>'
    if n_te:
        r1 += f'<th colspan="{n_te}">Test</th>'
    if n_tr:
        r1 += '<th rowspan="3">P value (Train)</th>'
        r1 += _rowspan_headers(vs_tr)
    if n_te:
        r1 += '<th rowspan="3">P value (Test)</th>'
        r1 += _rowspan_headers(vs_te)
    r1 = f"<tr>{r1}</tr>"

    def _hdr_names(cols):
        cells = []
        for c in cols:
            name, _n = _parse_group_header(c)
            cells.append(f"<th>{_format_header(name)}</th>")
        return "".join(cells)

    def _hdr_ns(cols):
        cells = []
        for c in cols:
            _name, n = _parse_group_header(c)
            cells.append(f"<th>n={n}</th>" if n is not None else "<th></th>")
        return "".join(cells)

    r2 = "<tr>" + _hdr_names(g_tr) + _hdr_names(g_te) + "</tr>"
    r3 = "<tr>" + _hdr_ns(g_tr) + _hdr_ns(g_te) + "</tr>"
    thead = f"<thead>\n{r1}\n{r2}\n{r3}\n</thead>"

    def _td_vals(row, cols):
        if row is None:
            return ["<td></td>" for _ in cols]
        out = []
        for c in cols:
            val = row[c] if c in row.index else ""
            out.append(f"<td>{'' if pd.isna(val) else val}</td>")
        return out

    def _td_p(row, has):
        if not has:
            return []
        if row is None:
            return ["<td></td>"]
        pv = row["P value"] if "P value" in row.index else ""
        return [f"<td>{'' if pd.isna(pv) else pv}</td>"]

    def _td_vs(row, cols):
        if not cols:
            return []
        if row is None:
            return ["<td></td>" for _ in cols]
        out = []
        for c in cols:
            val = row[c] if c in row.index else ""
            out.append(f"<td>{'' if pd.isna(val) else val}</td>")
        return out

    def _name_cell(name: str) -> str:
        indent = str(name).startswith("  ")
        cls = "col-first indent" if indent else "col-first"
        return f'<td class="{cls}">{str(name).lstrip()}</td>'

    blocks_tr = _table1_variable_blocks(t_train) if t_train is not None else []
    blocks_te = _table1_variable_blocks(t_test) if t_test is not None else []
    tr_map = {b["variable"]: b for b in blocks_tr}
    te_map = {b["variable"]: b for b in blocks_te}
    order_parents = [b["variable"] for b in blocks_tr]
    seen = set(order_parents)
    for b in blocks_te:
        if b["variable"] not in seen:
            order_parents.append(b["variable"])
            seen.add(b["variable"])

    body_rows = []
    for parent in order_parents:
        btr = tr_map.get(parent)
        bte = te_map.get(parent)
        hdr_tr = None if btr is None else btr["header"]
        hdr_te = None if bte is None else bte["header"]
        cells = [_name_cell(parent)]
        cells += _td_vals(hdr_tr, g_tr)
        cells += _td_vals(hdr_te, g_te)
        cells += _td_p(hdr_tr, n_tr > 0)
        cells += _td_vs(hdr_tr, vs_tr)
        cells += _td_p(hdr_te, n_te > 0)
        cells += _td_vs(hdr_te, vs_te)
        body_rows.append("<tr>" + "".join(cells) + "</tr>")

        lev_order = [lab for lab, _ in (btr["levels"] if btr else [])]
        seen_lev = set(lev_order)
        for lab, _ in (bte["levels"] if bte else []):
            if lab not in seen_lev:
                lev_order.append(lab)
                seen_lev.add(lab)
        lev_tr = {lab: s for lab, s in (btr["levels"] if btr else [])}
        lev_te = {lab: s for lab, s in (bte["levels"] if bte else [])}
        for lab in lev_order:
            cells = [_name_cell(lab)]
            cells += _td_vals(lev_tr.get(lab), g_tr)
            cells += _td_vals(lev_te.get(lab), g_te)
            cells += _td_p(lev_tr.get(lab), n_tr > 0)
            cells += _td_vs(lev_tr.get(lab), vs_tr)
            cells += _td_p(lev_te.get(lab), n_te > 0)
            cells += _td_vs(lev_te.get(lab), vs_te)
            body_rows.append("<tr>" + "".join(cells) + "</tr>")

    tbody = "<tbody>\n" + "\n".join(body_rows) + "\n</tbody>"
    return (
        f'<div class="table-wrap"><table class="threeline multihead" id="{elem_id}">\n'
        f'<caption>Table 1</caption>\n'
        f"{thead}\n{tbody}\n</table>"
        f'<p class="table-note"><span class="note-label">Note:</span> '
        f"Shared Variable rows; blank cells when absent in one split. "
        f"Significance stars are appended to P value."
        f"{(' ' + _multi_group_p_note()) if (vs_tr or vs_te) else ''}"
        f"</p></div>"
    )


def _multi_group_p_note() -> str:
    from modules.stats.clinical import MULTI_GROUP_P_NOTE
    return MULTI_GROUP_P_NOTE


def _multigroup_split_tables(data):
    """≥3 个原始水平时，Table 1 的 Train / Test 改用全水平 ANOVA + LSD。

    没有这套帧，或水平不足 3 个，返回 None，调用方继续用建模两组的表。
    """
    if data is None:
        return None
    tr_c = getattr(data, "baseline_train_clin", None)
    tr_g = getattr(data, "baseline_train_group", None)
    te_c = getattr(data, "baseline_test_clin", None)
    te_g = getattr(data, "baseline_test_group", None)

    def _levels(grp) -> set[str]:
        if grp is None or len(grp) == 0:
            return set()
        return {
            str(x).strip() for x in pd.unique(grp)
            if str(x).strip() and str(x).strip().lower() != "nan"
        }

    if len(_levels(tr_g) | _levels(te_g)) < 3:
        return None
    from modules.stats.clinical import _build_table1_by_groups

    def _one(frame, grp):
        if frame is None or grp is None or len(_levels(grp)) < 2:
            return None
        built = _build_table1_by_groups(frame, grp)

        class _Bag:
            table1 = built

        return _clean_table1_df(_Bag())

    return _one(tr_c, tr_g), _one(te_c, te_g)


def _table1_train_test_section(res_train, res_test, data=None) -> str:
    """B. Table 1 合并 Train/Test（三行表头 + 按父变量对齐水平行）。"""
    # 关=不写这一节。队列和建模拆分已经完成，这里不再改它们。
    if not bool(getattr(cfg, "DO_BASELINE_TRAIN_TEST", True)):
        return ""
    multi = _multigroup_split_tables(data)
    if multi is not None:
        t_tr, t_te = multi
    else:
        t_tr = _clean_table1_df(res_train["clin"] if res_train else None)
        t_te = _clean_table1_df(res_test["clin"] if res_test else None)
    if t_tr is None and t_te is None:
        return ""
    # Keep `_is_level` so repeated Negative/Positive labels stay under their parent.
    if t_tr is not None:
        t_tr = t_tr.drop(
            columns=[c for c in t_tr.columns if str(c).startswith("_") and c != "_is_level"],
            errors="ignore",
        )
    if t_te is not None:
        t_te = t_te.drop(
            columns=[c for c in t_te.columns if str(c).startswith("_") and c != "_is_level"],
            errors="ignore",
        )
    # ≥3 组的表已经含全部原始水平。不再把 HC 另贴一列，否则组和 P 会对不上。
    if data is not None and multi is None:
        from modules.stats.clinical import apply_hc_to_table1
        hc_from = str(getattr(data, "hc_relabel_from", "") or "")
        t_tr = apply_hc_to_table1(t_tr, getattr(data, "hc_train", None), hc_from)
        t_te = apply_hc_to_table1(t_te, getattr(data, "hc_test", None), hc_from)
    body = _table1_multiheader_html(t_tr, t_te)
    return (
        '<section><h2>B. Table 1</h2>\n'
        f"{body}\n</section>"
    )


def _nomogram_calibration_html(out_dir: str) -> str:
    """列线图关则不写 Nomogram；校准关则不写校准。遗留 panel 不覆盖开关。"""
    do_nomo = bool(getattr(cfg, "DO_NOMOGRAM", False))
    do_cal = bool(getattr(cfg, "DO_CALIBRATION", False))
    if not do_nomo and not do_cal:
        return ""
    if not _f_exists("panel_nomogram_calibration.png", out_dir):
        return ""
    if do_nomo and do_cal:
        title = "Nomogram &amp; Calibration"
        note = (
            "Top: nomogram; bottom: calibration train | test. "
            "Hosmer-Lemeshow results are tabulated below (not drawn on the figure)."
        )
        alt = "Nomogram calibration panel"
    elif do_nomo:
        title = "Nomogram"
        note = ""
        alt = "Nomogram panel"
    else:
        title = "Calibration"
        note = "Hosmer-Lemeshow results are tabulated below (not drawn on the figure)."
        alt = "Calibration panel"
    bits = [f"<h3>{title}</h3>"]
    if note:
        bits.append(f'<p class="note">{note}</p>')
    bits.append(_img_tag("panel_nomogram_calibration.png", alt, out_dir))
    if do_cal:
        hl_tbl = _hl_metrics_html(("train", "test"), "hosmer-lemeshow-internal")
        if hl_tbl:
            bits.append(hl_tbl)
    return "\n".join(bits)


def _roc_enabled() -> bool:
    return bool(getattr(cfg, "DO_ROC", True))


def _auc_time_text(v) -> str:
    try:
        t = float(v)
    except (TypeError, ValueError):
        return str(v)
    return str(int(t)) if float(t).is_integer() else f"{t:g}"


def _auc_ci_text(mdf: pd.DataFrame) -> list[str]:
    return [
        "NA" if pd.isna(lo) or pd.isna(hi) else f"({float(lo):.3f}-{float(hi):.3f})"
        for lo, hi in zip(mdf["AUC_CI_low"], mdf["AUC_CI_high"])
    ]


def _auc_num_text(s: pd.Series) -> pd.Series:
    return s.map(lambda v: "NA" if pd.isna(v) else f"{float(v):.3f}")


def _landmark_display(mdf: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({
        "Time": mdf["Time"].map(_auc_time_text),
        "Time column": mdf["Time_col"].astype(str),
        "Status": mdf["Status"].astype(str),
        "Model": mdf["Model"].astype(str),
        "N": mdf["N"].map(lambda v: str(int(v))),
        "Events": mdf["Events"].map(lambda v: str(int(v))),
        "AUC": _auc_num_text(mdf["AUC"]),
        "CI": _auc_ci_text(mdf),
    })


def _ipcw_display(mdf: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({
        "Time": mdf["Time"].map(_auc_time_text),
        "Model": mdf["Model"].astype(str),
        "N": mdf["N"].map(lambda v: str(int(v))),
        "Cases": mdf["Cases"].map(lambda v: str(int(v))),
        "Controls": mdf["Controls"].map(lambda v: str(int(v))),
        "Censored before t": mdf["Censored_before"].map(lambda v: str(int(v))),
        "AUC": _auc_num_text(mdf["AUC"]),
        "CI": _auc_ci_text(mdf),
        "Risk set": mdf["Risk_set"].astype(str),
    })


def _split_ordered(keys: list[str], prefix: str) -> list[str]:
    preferred = [f"{prefix}train", f"{prefix}test"]
    return [k for k in preferred if k in keys] + sorted(k for k in keys if k not in preferred)


def _time_png_key(token: str):
    try:
        return (0, float(token))
    except (TypeError, ValueError):
        return (1, str(token).lower())


def _panel_time_pngs(out_dir: str, prefix: str) -> list[str]:
    """panel_landmark_roc_6.png 按时间排序。不含别的前缀。"""
    if not cfg.figures_on():
        return []
    png_dir = os.path.join(out_dir, "PNG")
    names = os.listdir(png_dir) if os.path.isdir(png_dir) else []
    found = []
    for name in names:
        if name.startswith(prefix) and name.endswith(".png"):
            found.append((_time_png_key(name[len(prefix):-4]), name))
    return [name for _key, name in sorted(found)]


def _auc_by_time_frames(store: dict, prefix: str, display) -> list[pd.DataFrame]:
    keys = [k for k, v in store.items() if v is not None and len(v)]
    frames = []
    for key in _split_ordered(keys, prefix):
        show = display(store[key])
        show.insert(0, "Split", key[len(prefix):] or "all")
        frames.append(show)
    return frames


def _landmark_roc_html(out_dir: str) -> str:
    if not cfg.landmark_roc_on():
        return ""
    frames = _auc_by_time_frames(LANDMARK_ROC_METRICS, "landmark_", _landmark_display)
    pngs = _panel_time_pngs(out_dir, "panel_landmark_roc_")
    if not frames and not pngs:
        return ""
    bits = ["<h3>Landmark ROC (train | test)</h3>"]
    bits.append(
        '<p class="note">Landmark ROC of the score locked on the modeling outcome. '
        "Each panel is one window: training cohort on the left and test cohort on the right. "
        "A blank time column means the window month is read from the status column name. "
        "The score is not re-fit.</p>"
    )
    for name in pngs:
        bits.append(_img_tag(name, "Landmark ROC train | test", out_dir))
    if frames:
        bits.append(df_to_html(
            pd.concat(frames, ignore_index=True),
            elem_id="landmark-roc-metrics",
            caption="Landmark AUC — locked score at each time",
            footnote=(
                "Events are status = 1 in the paired status column. "
                "The cut-point is not re-chosen at the other times. "
                "When the raw AUC is below 0.5, the score direction is reversed and the reported AUC is 1 minus the raw AUC."
            ),
        ))
    return "\n".join(bits)


def _ipcw_roc_html(out_dir: str) -> str:
    if not cfg.ipcw_roc_on():
        return ""
    frames = _auc_by_time_frames(IPCW_ROC_METRICS, "ipcw_", _ipcw_display)
    pngs = _panel_time_pngs(out_dir, "panel_ipcw_roc_")
    if not frames and not pngs:
        return ""
    bits = ["<h3>Time-dependent ROC (train | test)</h3>"]
    bits.append(
        '<p class="note">Cumulative/dynamic ROC with inverse probability of censoring weights. '
        "Each panel is one horizon: training cohort on the left and test cohort on the right. "
        "Cases had the event at or before t; controls were still event-free after t. "
        "Censoring weights come from the Kaplan-Meier estimate of the censoring distribution.</p>"
    )
    for name in pngs:
        bits.append(_img_tag(name, "Time-dependent ROC train | test", out_dir))
    if frames:
        bits.append(df_to_html(
            pd.concat(frames, ignore_index=True),
            elem_id="ipcw-tdauc-metrics",
            caption="IPCW cumulative/dynamic AUC at each horizon",
            footnote=(
                "Case weight 1/G(T-); control weight 1/G(t). "
                "CI: percentile bootstrap with the censoring KM re-fitted in each draw. "
                "Risk set is small when cases or controls are fewer than 10. "
                "When the raw AUC is below 0.5, the score direction is reversed and the reported AUC is 1 minus the raw AUC."
            ),
        ))
    return "\n".join(bits)


def _dca_enabled() -> bool:
    return bool(getattr(cfg, "DO_DCA", True))


def _shap_enabled() -> bool:
    return bool(getattr(cfg, "DO_SHAP", False))


def _internal_holdout() -> bool:
    """训练比例 < 1，或指定了拆分列，才有内部 test。比例为 1 时没有 test。"""
    if str(getattr(cfg, "SPLIT_COL", "") or "").strip():
        return True
    try:
        ratio = float(getattr(cfg, "TRAIN_RATIO", 0.7) or 0.7)
    except (TypeError, ValueError):
        return True
    return ratio < 1.0


def _figures_section_html(
    out_dir: str,
    res_train=None,
    res_test=None,
    data=None,
    advanced_info=None,
) -> str:
    """D. Figures：panel_* 合成图 + ROC summary 仅出现一次。"""
    parts = ['<section><h2>D. Figures</h2>']
    any_fig = False
    if _roc_enabled() and _f_exists("panel_roc.png", out_dir):
        parts += ["<h3>ROC (train | test)</h3>", _img_tag("panel_roc.png", "ROC panel", out_dir)]
        any_fig = True
        # train + test ROC summary 合并为一表（首列 Split）
        roc_tbl = _roc_metrics_train_test_html(res_train, res_test, data)
        if roc_tbl:
            parts.append(roc_tbl)
    for td_html in (_landmark_roc_html(out_dir), _ipcw_roc_html(out_dir)):
        if td_html:
            parts.append(td_html)
            any_fig = True
    if _dca_enabled() and _f_exists("panel_dca.png", out_dir):
        dca_title = "DCA (train | test)" if _internal_holdout() else "DCA"
        parts += [f"<h3>{dca_title}</h3>", _img_tag("panel_dca.png", "DCA panel", out_dir)]
        any_fig = True
    nomo_cal = _nomogram_calibration_html(out_dir)
    if nomo_cal:
        parts.append(nomo_cal)
        any_fig = True
    if _shap_enabled() and _f_exists("panel_shap.png", out_dir):
        parts += ["<h3>SHAP</h3>", _img_tag("panel_shap.png", "SHAP panel", out_dir)]
        any_fig = True
        parts.extend(_shap_inline_html(advanced_info, out_dir))
    if not any_fig:
        return ""
    parts.append("</section>")
    return "\n".join(parts)


def _survival_section_html(out_dir: str) -> str:
    """KM 图 + Cox 表。高低危切点与图一致（train 中位数套到全队列）。

    公共开关 DO_SURVIVAL=关 时不写这一节，也不再扫目录里遗留的 km_*.png。
    """
    if not bool(getattr(cfg, "DO_SURVIVAL", False)):
        return ""
    from modules.stats.curves_survival import COX_METRICS, SURVIVAL_METRICS

    png_dir = os.path.join(out_dir, "PNG")
    km_pngs = []
    if os.path.isdir(png_dir):
        for name in sorted(os.listdir(png_dir)):
            low = name.lower()
            if low.startswith("km_") and low.endswith(".png"):
                km_pngs.append(name)
    cox_frames = [df for df in COX_METRICS.values() if df is not None and len(df)]
    if not km_pngs and not cox_frames and not SURVIVAL_METRICS:
        return ""
    parts = ['<section><h2>Survival</h2>']
    parts.append(
        '<p class="note">Kaplan–Meier with log-rank; Cox PH on the same split. '
        "High vs Low uses the train-median cut shown on the KM plot. "
        "Continuous models are per 1 SD. Schoenfeld P tests proportional hazards. "
        "Panel: A overall, B Combined-risk split (labels lower left).</p>"
    )
    skip_km = set()
    if _f_exists("panel_survival.png", out_dir):
        parts.append(_img_tag("panel_survival.png", "Survival panel A overall | B risk", out_dir))
        skip_km = {"km_overall.png", "km_by_combined_risk.png"}
    for name in km_pngs:
        if name.lower() in skip_km:
            continue
        alt = name.replace(".png", "").replace("_", " ")
        parts.append(_img_tag(name, alt, out_dir))
    km_keys = sorted(SURVIVAL_METRICS.keys())
    for key in km_keys:
        df = SURVIVAL_METRICS.get(key)
        if df is None or len(df) == 0:
            continue
        disp = df.copy()
        for col in disp.columns:
            if col in ("Stratum",):
                continue
            if pd.api.types.is_numeric_dtype(disp[col]):
                if col in ("N", "Events"):
                    disp[col] = disp[col].map(lambda v: "NA" if pd.isna(v) else f"{int(v)}")
                elif col in ("Logrank_p", "Cox_p"):
                    disp[col] = disp[col].map(_fmt_delong_p)
                elif col in ("Cox_HR", "Cox_CI_low", "Cox_CI_high", "Median_months", "Logrank_chi2"):
                    disp[col] = disp[col].map(
                        lambda v: "NA" if pd.isna(v) or not np.isfinite(float(v)) else f"{float(v):.3f}"
                    )
        parts += [f"<h3>{key.replace('_', ' ')}</h3>", df_to_html(disp, f"km-{key}")]
    if cox_frames:
        cox = pd.concat(cox_frames, ignore_index=True)
        disp = cox.copy()
        def _hr(v):
            try:
                x = float(v)
            except (TypeError, ValueError):
                return "NA"
            return "NA" if not np.isfinite(x) else f"{x:.2f}"
        if "HR" in disp.columns:
            disp["HR"] = disp["HR"].map(_hr)
        if "CI_low" in disp.columns and "CI_high" in disp.columns:
            disp["95% CI"] = [
                f"({_hr(a)}–{_hr(b)})" if _hr(a) != "NA" else "NA"
                for a, b in zip(cox["CI_low"], cox["CI_high"])
            ]
            disp = disp.drop(columns=["CI_low", "CI_high"])
        for col in ("P", "PH_p"):
            if col in disp.columns:
                disp[col] = disp[col].map(_fmt_delong_p)
        for col in ("N", "Events"):
            if col in disp.columns:
                disp[col] = disp[col].map(lambda v: "NA" if pd.isna(v) else f"{int(v)}")
        # keep Predictor, Contrast, HR, 95% CI, P, N, Events, PH_p
        order = [c for c in ("Predictor", "Contrast", "HR", "95% CI", "P", "N", "Events", "PH_p") if c in disp.columns]
        disp = disp[order] if order else disp
        parts += [
            "<h3>Cox proportional hazards</h3>",
            df_to_html(
                disp, "cox-ph", extra_abbrevs={"HR"},
                footnote=(
                    "Univariable Cox PH (Breslow ties). High vs Low matches the KM cut. "
                    "Continuous = per 1 SD. Schoenfeld P from residual–time correlation; "
                    "P &lt; 0.05 suggests PH violation."
                ),
            ),
        ]
    parts.append("</section>")
    return "\n".join(parts)


def _external_section_html(external_results: Optional[list], out_dir: str) -> str:
    """主模型锁死后的外验 ROC / 校准 / DCA。多条横拼，一条不拼。"""
    rows = list(external_results or [])
    if not rows:
        return ""
    if rows and rows[0].get("data") is not None and rows[0].get("res") is not None:
        return ""
    mode = str((rows[0].get("val_mode") if rows else "") or "").strip() or "apply_formula"
    if mode == "lock_threshold":
        mode_note = "training Youden locked"
    elif mode == "refit":
        mode_note = "features locked; coefficients refit on external"
    else:
        mode_note = "coefficients locked; Youden re-cut on external"
    n_ext = len(rows)
    parts = [
        "<section><h2>External validation — locked nomogram</h2>",
        f'<p class="note">Main Combined / RadScore / Clinical model is not re-selected. '
        f"Val mode: <code>{mode}</code> ({mode_note}). "
        + (
            "Multiple external sets are stitched and labelled A, B, …."
            if n_ext >= 2
            else "A single external set is shown without stitching."
        )
        + "</p>",
    ]
    summary_rows = []
    for rec in rows:
        info = (rec.get("splits") or {}).get("ext") or {}
        summary_rows.append({
            "External": rec.get("display") or rec.get("level"),
            "N": info.get("n"),
            "Events": info.get("n1"),
            "Non-events": info.get("n0"),
            "AUC (Combined)": (
                None if info.get("auc") is None else f"{float(info['auc']):.3f}"
            ),
        })
    if summary_rows and _roc_enabled():
        parts.append(df_to_html(pd.DataFrame(summary_rows), "external-auc-summary"))
    if n_ext >= 2:
        for key, stem, label in (
            ("roc", "panel_roc_ext.png", "ROC external (stitched)"),
            ("cal", "panel_cal_ext.png", "Calibration external (stitched)"),
            ("dca", "panel_dca_ext.png", "DCA external (stitched)"),
        ):
            if key == "roc" and not _roc_enabled():
                continue
            if key == "dca" and not _dca_enabled():
                continue
            if _f_exists(stem, out_dir):
                parts.append(_img_tag(stem, label, out_dir))
                if key == "cal":
                    ext_keys = tuple(
                        f"ext_{rec.get('token') or 'ext'}" for rec in rows
                    )
                    hl_tbl = _hl_metrics_html(ext_keys, "hosmer-lemeshow-external")
                    if hl_tbl:
                        parts.append(hl_tbl)
    for rec in rows:
        title = rec.get("display") or rec.get("level")
        parts.append(f"<h3>{title}</h3>")
        info = (rec.get("splits") or {}).get("ext") or {}
        parts.append(
            f'<p class="note">n={info.get("n")} '
            f'(events={info.get("n1")}, non-events={info.get("n0")})</p>'
        )
        skip = str(info.get("skip_reason") or "").strip()
        if skip:
            parts.append(f'<p class="warn">{skip}</p>')
        ipcw_note = str(info.get("ipcw_note") or "").strip()
        if ipcw_note:
            parts.append(f'<p class="warn">{ipcw_note}</p>')
        mdf = info.get("metrics")
        if _roc_enabled() and isinstance(mdf, pd.DataFrame) and len(mdf):
            need = {"Model", "Threshold", "AUC", "AUC_CI_low", "AUC_CI_high"}
            disp = _roc_metrics_format_df(mdf) if need.issubset(set(mdf.columns)) else mdf
            note = None
            if any(str(c).startswith("DeLong vs ") or str(c).startswith("P vs ") for c in disp.columns):
                note = (
                    "DeLong vs Model: two-sided DeLong test of AUCs on the same patients. "
                    "Em dash = same model."
                )
            parts.append(df_to_html(disp, f"ext-roc-{rec.get('token')}", footnote=note))
        if n_ext < 2:
            for key, label in (
                ("panel", f"ROC {title}"),
                ("cal", f"Calibration {title}"),
                ("dca", f"DCA {title}"),
            ):
                if key == "panel" and not _roc_enabled():
                    continue
                if key == "dca" and not _dca_enabled():
                    continue
                png = info.get(key)
                if png and _f_exists(str(png), out_dir):
                    parts.append(_img_tag(str(png), label, out_dir))
                    if key == "cal":
                        tok = rec.get("token") or "ext"
                        hl_tbl = _hl_metrics_html(
                            (f"ext_{tok}",), f"hosmer-lemeshow-ext-{tok}"
                        )
                        if hl_tbl:
                            parts.append(hl_tbl)
    parts.append("</section>")
    return "\n".join(parts)


def _subgroup_section_html(subgroup_results: Optional[list], out_dir: str) -> str:
    """主模型锁死后的亚组 ROC / 校准 / DCA。"""
    rows = list(subgroup_results or [])
    if not rows:
        return ""
    col = str(rows[0].get("col") or "subgroup")
    sg_mode = str(rows[0].get("val_mode") or "").strip() or "apply_formula"
    if sg_mode == "lock_threshold":
        mode_note = "training Youden locked"
    elif sg_mode == "refit":
        mode_note = "features locked; coefficients refit in the subgroup"
    else:
        mode_note = "coefficients locked; Youden re-cut in the subgroup"
    parts = [
        "<section><h2>E. Subgroup performance — locked nomogram</h2>",
        f'<p class="note">Main Combined / RadScore / Clinical model is not refit. '
        f"Subgroup column: <code>{col}</code>. Val mode: <code>{sg_mode}</code> "
        f"({mode_note}). Metrics apply the training formula within each level "
        f"(training and test). Like-kind figures are stitched train | test "
        f"and labelled A, B.</p>",
    ]
    summary_rows = []
    for rec in rows:
        for split, info in (rec.get("splits") or {}).items():
            summary_rows.append({
                "Subgroup": rec.get("display") or rec.get("level"),
                "Split": split,
                "N": info.get("n"),
                "Events": info.get("n1"),
                "Non-events": info.get("n0"),
                "AUC (Combined)": (
                    None if info.get("auc") is None else f"{float(info['auc']):.3f}"
                ),
            })
    if summary_rows and _roc_enabled():
        parts.append(df_to_html(pd.DataFrame(summary_rows), "subgroup-auc-summary"))
    for rec in rows:
        title = rec.get("display") or rec.get("level")
        parts.append(f"<h3>{title}</h3>")
        for split in ("train", "test"):
            info = (rec.get("splits") or {}).get(split)
            if not info:
                continue
            parts.append(
                f'<p class="note">{split}: n={info.get("n")} '
                f'(events={info.get("n1")}, non-events={info.get("n0")})</p>'
            )
            mdf = info.get("metrics")
            if _roc_enabled() and isinstance(mdf, pd.DataFrame) and len(mdf):
                need = {"Model", "Threshold", "AUC", "AUC_CI_low", "AUC_CI_high"}
                disp = _roc_metrics_format_df(mdf) if need.issubset(set(mdf.columns)) else mdf
                parts.append(df_to_html(
                    disp, f"sg-roc-{rec.get('token')}-{split}",
                ))
        panels = rec.get("panels") or {}
        token = rec.get("token") or ""
        shown_panel = False
        for key, stem, label in (
            ("roc", f"panel_roc_sg_{token}.png", f"ROC {title} (A train | B test)"),
            ("cal", f"panel_cal_sg_{token}.png", f"Calibration {title} (A train | B test)"),
            ("dca", f"panel_dca_sg_{token}.png", f"DCA {title} (A train | B test)"),
        ):
            if key == "roc" and not _roc_enabled():
                continue
            if key == "dca" and not _dca_enabled():
                continue
            png = panels.get(key) or stem
            if png and _f_exists(str(png), out_dir):
                parts.append(_img_tag(str(png), label, out_dir))
                shown_panel = True
        if not shown_panel:
            for split in ("train", "test"):
                info = (rec.get("splits") or {}).get(split) or {}
                for key, label in (
                    ("panel", f"ROC {title} {split}"),
                    ("cal", f"Calibration {title} {split}"),
                    ("dca", f"DCA {title} {split}"),
                ):
                    if key == "panel" and not _roc_enabled():
                        continue
                    if key == "dca" and not _dca_enabled():
                        continue
                    png = info.get(key)
                    if png and _f_exists(str(png), out_dir):
                        parts.append(_img_tag(str(png), label, out_dir))
    parts.append("</section>")
    return "\n".join(parts)


def _build_panel(panel_label: str, ds, rad_res, clin_res, panel_title: str,
                  combined_info: Optional[Dict[str, Any]] = None,
                  advanced_info: Optional[Dict[str, Any]] = None,
                  out_dir: Optional[str] = None,
                  include_table1: bool = True,
                  include_figures_note: bool = True) -> str:
    """
    构建数据层 HTML 面板。
    Train/Test 默认不再内嵌 Table1 与单图（见 B / D 节）。
    """
    if out_dir is None:
        out_dir = str(getattr(cfg, "OUT_DIR", "") or "")
    parts = [f'<h2>{panel_title}</h2>']
    is_val = _is_validation_panel(panel_label)
    is_all = panel_label == "all"

    # ① Table1（All：简化显示）。DO_BASELINE_ALL 只关这一张，组学 Table 2 仍写。
    show_base_all = bool(getattr(cfg, "DO_BASELINE_ALL", True))
    if include_table1 and clin_res is not None and getattr(clin_res, "table1", None) is not None and len(clin_res.table1) > 0:
        t1 = _clean_table1_df(clin_res)
        if t1 is not None and is_all:
            if show_base_all:
                t1 = _simplify_table1_all(t1)
                t1 = t1.drop(columns=[c for c in t1.columns if c.startswith("_")], errors="ignore")
                foot = _multi_group_p_note() if _vs_columns(t1) else None
                parts.append(
                    df_to_html(t1, f"table1-{panel_label}", caption="总表", footnote=foot)
                )
        elif t1 is not None:
            t1 = t1.drop(columns=[c for c in t1.columns if c.startswith("_")], errors="ignore")
            tag = {"all": "All", "train": "Train", "test": "Test"}.get(
                panel_label, panel_label.replace("_", " ").title()
            )
            parts.append(
                df_to_html(t1, f"table1-{panel_label}", caption=f"Table 1 — {tag}")
            )

    # ①b Radiomics Table 2（All / optional layers）
    if include_table1 and rad_res is not None and getattr(rad_res, "table2", None) is not None and len(rad_res.table2) > 0:
        t2 = _clean_table2_df(rad_res)
        if t2 is not None:
            if is_all:
                t2 = _simplify_table1_all(t2)
            t2 = t2.drop(columns=[c for c in t2.columns if c.startswith("_")], errors="ignore")
            cap = _rad_table2_caption(rad_res, is_all=is_all, panel_label=panel_label)
            parts.append(df_to_html(t2, f"table2-{panel_label}", caption=cap))

    if is_all:
        if len(parts) <= 1:
            return ""
        return '<section>\n' + '\n'.join(parts) + '\n</section>'

    # ② Univariate（train only）。开关只决定写出，不改变已经算好的筛选。
    show_uni = bool(getattr(cfg, "DO_UNI_LOGISTIC_TABLE", True))
    if show_uni and (not is_val) and clin_res is not None and getattr(clin_res, "uni_res", None) is not None \
            and len(clin_res.uni_res) > 0:
        u = clin_res.uni_res.copy()
        for col in ("OR", "CI_low", "CI_high"):
            if col in u.columns and pd.api.types.is_numeric_dtype(u[col]):
                u[col] = u[col].map(lambda v: f"{v:.3f}")
        if "p" in u.columns:
            u["p"] = u["p"].map(
                lambda v: "<0.001" if (isinstance(v, (int, float)) and v < 0.001)
                else (f"{v:.3f}" if isinstance(v, (int, float)) else v)
            )
        u = _merge_ci_cols(u)
        uni_foot = None
        uni_abbr = None
        if "VIF" in u.columns:
            uni_abbr = {"VIF"}
            uni_foot = (
                "VIF among variables shown in this univariate table "
                f"(warn ≥ {getattr(cfg, 'VIF_THRESHOLD_WARN', 5):g}; warning only). "
                "Multivariate candidate-pool entry uses univariate logistic p "
                f"< {getattr(cfg, 'TABLE1_P_THRESHOLD', 0.1):g}; "
                "VIF does not exclude candidates. "
                "Table 1 p and complete-separation terms do not enter. "
                "Clinical / Combined / nomogram: FORCE_MODEL_FEATURES if set, else "
                "multivariate p "
                f"< {getattr(cfg, 'ROC_MV_P_THRESHOLD', 0.05):g} from the pool table."
            )
        parts += [
            '<h3>Univariate Logistic</h3>',
            df_to_html(
                u, f"uni-{panel_label}",
                footnote=uni_foot, extra_abbrevs=uni_abbr,
            ),
        ]

    # ③ Multivar（train）→ Selected clinical → Selected radiomics
    # 多因素表开关不关 Firth，也不关已选变量那一行。
    # 单因素表开关同样不关 Firth。Firth 仍只看 DO_FIRTH。
    show_mv = bool(getattr(cfg, "DO_MV_LOGISTIC_TABLE", True))
    if not is_val:
        parts.append(_firth_uni_html(clin_res, panel_label))
        if show_mv:
            parts.append(_mv_table_html(clin_res, panel_label))
        parts.append(_firth_mv_html(clin_res, panel_label))
        if clin_res is not None and getattr(clin_res, "features", None):
            feat_str = ", ".join(clin_res.features)
            src = getattr(clin_res, "select_source", None)
            tag = (
                "manual FORCE_MODEL" if src == "manual"
                else ("default multivariate p" if src == "mv_p" else "selected")
            )
            parts.append(f'<p class="sel-feat"><b>Selected clinical features '
                          f'({tag}, {len(clin_res.features)}):</b> {feat_str}</p>')
        if rad_res is not None and str(getattr(rad_res, "method", "") or "") == "logistic":
            rad_log_html = _radiomics_logistic_html(rad_res, panel_label)
            if rad_log_html:
                parts.append(rad_log_html)
        groups = list(getattr(rad_res, "groups", None) or []) if rad_res is not None else []
        feat_sources = groups if groups else ([rad_res] if rad_res is not None else [])
        for src in feat_sources:
            if src is None or not getattr(src, "features", None):
                continue
            rfeats = list(src.features)
            if len(rfeats) > 0:
                try:
                    from modules.stats.radiomics import display_rad_feature
                    rdisp = [display_rad_feature(f) for f in rfeats]
                except Exception:
                    rdisp = list(rfeats)
                shown = rdisp if len(rdisp) <= 40 else rdisp[:40]
                more = "" if len(rdisp) <= 40 else f" … (+{len(rdisp) - 40} more)"
                method = str(getattr(src, "method", "") or "lasso")
                if method == "logistic" and getattr(src, "select_source", None) == "mv_p":
                    tag = "logistic, multivariate p"
                elif method == "logistic":
                    tag = "logistic"
                else:
                    tag = "LASSO"
                gname = str(getattr(src, "group_name", "") or "").strip()
                title = "Selected radiomics features"
                if gname:
                    title = f"Selected radiomics features — {html.escape(gname)}"
                parts.append(
                    f'<p class="sel-feat"><b>{title} '
                    f'({tag}, {len(rfeats)}):</b> {", ".join(shown)}{more}</p>'
                )
                # LASSO Table 2 — after selection / C training narrative
                if not groups and method != "logistic":
                    t2 = _clean_table2_df(src)
                    if t2 is not None:
                        t2 = t2.drop(
                            columns=[c for c in t2.columns if c.startswith("_")],
                            errors="ignore",
                        )
                        parts.append(
                            df_to_html(
                                t2,
                                f"table2-lasso-{panel_label}",
                                caption="Table 2 — LASSO-selected Radiomics",
                            )
                        )
                        parts.append(
                            '<p class="note">Group-wise descriptive statistics for '
                            "LASSO-selected radiomics features on the original "
                            "(median-imputed) scale; same format as Baseline Table 1.</p>"
                        )

    # ④ Combined：系数/列线图 → ROC+summary → Calibration → DCA
    parts.extend(_combined_block_html(
        panel_label, combined_info, is_val,
        rad_res=rad_res, ds=ds, out_dir=out_dir,
    ))

    # 单图 ROC 等已迁至 D. Figures（panel_*）

    # ⑤ RadScore Formula（分类时每类一条，不合成一份）
    if not is_val:
        formula_html = _radiomics_formula_html(rad_res)
        if formula_html:
            parts.append(formula_html)

    # SHAP 表在 D. Figures 中与 panel_shap 一并展示
    return '<section>\n' + '\n'.join(parts) + '\n</section>'


# ═════════════════════════════════════════════════════════════════
# 主函数：生成完整的 Results{子项目}.html 报告
# ═════════════════════════════════════════════════════════════════
def _fmt_p(v) -> str:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "NA"
    if not np.isfinite(x):
        return "NA"
    if x < 0.001:
        return "<0.001"
    return f"{x:.3f}"


def _fmt_est(v) -> str:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "NA"
    if not np.isfinite(x):
        return "NA"
    return f"{x:.3f}"


def _fmt_span(lo, hi) -> str:
    try:
        a, b = float(lo), float(hi)
    except (TypeError, ValueError):
        return "NA"
    if not np.isfinite(a) or not np.isfinite(b):
        return "NA"
    return f"{a:.3f}–{b:.3f}"


def _logistic_display(df: pd.DataFrame) -> pd.DataFrame:
    u = df.copy()
    for col in ("OR", "CI_low", "CI_high"):
        if col in u.columns and pd.api.types.is_numeric_dtype(u[col]):
            u[col] = u[col].map(_fmt_est)
    for col in ("p", "FDR P", "FWER P"):
        if col in u.columns and pd.api.types.is_numeric_dtype(u[col]):
            u[col] = u[col].map(_fmt_p)
    return _merge_ci_cols(u)


_FIRTH_NOTE = (
    "Firth penalized logistic regression (Jeffreys prior). "
    "Wald 95% CI from the penalized information matrix. "
    "Shown in addition to the maximum-likelihood tables and not used for feature selection."
)


def _firth_uni_html(clin_res, panel_label: str) -> str:
    if not bool(getattr(cfg, "DO_FIRTH", False)):
        return ""
    raw = getattr(clin_res, "firth_uni", None) if clin_res is not None else None
    if raw is None or len(raw) == 0:
        return ""
    return (
        "<h3>Firth Penalized Logistic — Univariate</h3>"
        + df_to_html(
            _logistic_display(raw), f"firth-uni-{panel_label}",
            footnote=_FIRTH_NOTE,
        )
    )


def _radiomics_feature_names(df: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
    if df is None or len(df) == 0 or "Feature" not in df.columns:
        return df
    from modules.stats.radiomics import display_rad_feature
    out = df.copy()
    out["Feature"] = [display_rad_feature(v) for v in out["Feature"].astype(str)]
    return out


def _radiomics_formula_html(rad_res) -> str:
    """RadScore 公式。分类结果按类各写一条，不合并成一个分数。"""
    if rad_res is None:
        return ""
    groups = list(getattr(rad_res, "groups", None) or [])
    sources = groups if groups else [rad_res]
    bits: List[str] = []
    logistic = False
    for src in sources:
        text = getattr(src, "formula_text", None)
        if not text:
            continue
        bits.append(f'<p class="formula">{text}</p>')
        if str(getattr(src, "method", "") or "") == "logistic":
            logistic = True
    if not bits:
        return ""
    note = ""
    if logistic:
        note = (
            '<p class="note">Radiomics logistic linear predictor. '
            'Coefficients are log-odds per 1 training SD after median imputation '
            'and scaling fit on the training set. Features are not re-selected. '
            'Each evaluation layer follows its validation mode: refit re-estimates '
            'these coefficients after the training scaling; apply-formula and '
            'lock-threshold reuse the training coefficients.</p>'
        )
    return "<h3>RadScore Formula</h3>" + "".join(bits) + note


def _radiomics_logistic_html(rad_res, panel_label: str) -> str:
    """训练集组学 Logistic 表。验证层不调用。有分类时每类一块。"""
    if rad_res is None or str(getattr(rad_res, "method", "") or "") != "logistic":
        return ""
    groups = list(getattr(rad_res, "groups", None) or [])
    if groups:
        blocks: List[str] = []
        for i, g in enumerate(groups):
            inner = _radiomics_logistic_html(g, f"{panel_label}-g{i}")
            if not inner:
                continue
            raw_name = str(getattr(g, "group_name", "") or getattr(g, "score_name", "") or "group")
            blocks.append(
                f"<h2>Radiomics logistic — {html.escape(raw_name)}</h2>\n{inner}"
            )
        return "\n".join(blocks)
    parts: List[str] = []
    # Table 2 baseline is rendered in A / B2 (parallel to clinical Table 1), not here.
    uni = getattr(rad_res, "uni_res", None)
    if uni is not None and len(uni) > 0:
        u = _logistic_display(_radiomics_feature_names(uni))
        uni_p = float(getattr(cfg, "RAD_LOGIT_UNI_P", 0.1) or 0.1)
        mv_p = float(getattr(cfg, "RAD_LOGIT_MV_P", 0.05) or 0.05)
        do_fdr = bool(getattr(cfg, "DO_FDR", False))
        do_fwer = bool(getattr(cfg, "DO_FWER", False))
        if do_fdr or do_fwer:
            bits = []
            if do_fdr:
                bits.append(f"Benjamini–Hochberg FDR P < {uni_p:g}")
            if do_fwer:
                bits.append(f"Holm FWER P < {uni_p:g}")
            gate = " and ".join(bits) + " (AND of enabled corrections)"
            adj_note = (
                " Adjusted P values use this table's univariate tests only "
                f"(one test per feature; family size = {len(uni)}). "
                "The ANCOVA/MLR Group-test family is separate."
            )
        else:
            gate = f"univariate p < {uni_p:g}"
            adj_note = ""
        foot = (
            "Univariate logistic on training-set median-imputed, z-scored radiomics features. "
            "Odds ratios are per 1 training SD. "
            f"Candidate-pool entry uses {gate} and no complete separation.{adj_note} "
            f"VIF is reported for collinearity warning "
            f"(warn ≥ {getattr(cfg, 'VIF_THRESHOLD_WARN', 5):g}) and does not exclude candidates. "
            "Selected features are candidate-pool multivariate p "
            f"< {mv_p:g}."
        )
        abbr = set()
        if "VIF" in u.columns:
            abbr.add("VIF")
        if "FDR P" in u.columns or "Pass FDR" in u.columns:
            abbr.add("FDR")
        if "FWER P" in u.columns or "Pass FWER" in u.columns:
            abbr.add("FWER")
        abbr = abbr or None
        parts.append(
            "<h3>Radiomics Univariate Logistic</h3>"
            + df_to_html(u, f"rad-uni-{panel_label}", footnote=foot, extra_abbrevs=abbr)
        )
    firth_uni = getattr(rad_res, "firth_uni", None)
    if firth_uni is not None and len(firth_uni) > 0:
        parts.append(
            "<h3>Radiomics Firth Penalized Logistic — Univariate</h3>"
            + df_to_html(
                _logistic_display(_radiomics_feature_names(firth_uni)),
                f"rad-firth-uni-{panel_label}",
                footnote=_FIRTH_NOTE,
            )
        )
    mv_pool = getattr(rad_res, "mv_df", None)
    if mv_pool is not None and len(mv_pool) > 0:
        parts.append(
            "<h3>Radiomics Multivariate Logistic (Train — candidate pool)</h3>"
            + df_to_html(
                _format_mv_df(_radiomics_feature_names(mv_pool)),
                f"rad-multivar-pool-{panel_label}",
                footnote=getattr(rad_res, "mv_note", None),
            )
        )
    firth_mv = getattr(rad_res, "firth_mv", None)
    if firth_mv is not None and len(firth_mv) > 0:
        parts.append(
            "<h3>Radiomics Firth Penalized Logistic — Candidate Pool</h3>"
            + df_to_html(
                _format_mv_df(_radiomics_feature_names(firth_mv)),
                f"rad-firth-mv-{panel_label}",
                footnote=_FIRTH_NOTE + " Same candidate pool as the radiomics multivariable table.",
            )
        )
    mv_sel = getattr(rad_res, "mv_selected", None)
    if mv_sel is not None and len(mv_sel) > 0:
        sel = [str(f) for f in (getattr(rad_res, "features", None) or [])]
        try:
            from modules.stats.radiomics import display_rad_feature
            sel_txt = ", ".join(display_rad_feature(f) for f in sel) if sel else "none"
        except Exception:
            sel_txt = ", ".join(sel) if sel else "none"
        p_cut = float(getattr(cfg, "RAD_LOGIT_MV_P", 0.05) or 0.05)
        foot2 = (
            "Selected radiomics model from candidate-pool multivariate p < "
            f"{p_cut:g} (n features = {len(sel)}): {sel_txt}."
        )
        parts.append(
            "<h3>Radiomics Multivariate Logistic (Train — selected)</h3>"
            + df_to_html(
                _format_mv_df(_radiomics_feature_names(mv_sel)),
                f"rad-multivar-sel-{panel_label}",
                footnote=foot2,
            )
        )
    return "\n".join(parts)


def _firth_mv_html(clin_res, panel_label: str) -> str:
    if not bool(getattr(cfg, "DO_FIRTH", False)):
        return ""
    raw = getattr(clin_res, "firth_mv", None) if clin_res is not None else None
    if raw is None or len(raw) == 0:
        return ""
    return (
        "<h3>Firth Penalized Logistic — Candidate Pool</h3>"
        + df_to_html(
            _format_mv_df(raw), f"firth-mv-{panel_label}",
            footnote=_FIRTH_NOTE + " Same candidate pool as the maximum-likelihood multivariable table.",
        )
    )


def _assoc_display(df: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
    if df is None or len(df) == 0:
        return None
    out = df.copy()
    ci_pairs = [("CI_low", "CI_high", "95% CI")]
    level_los = [c for c in out.columns if str(c).startswith("CI_low ")]
    for lo_name in level_los:
        lev = str(lo_name)[len("CI_low "):]
        hi_name = f"CI_high {lev}"
        if hi_name in out.columns:
            ci_pairs.append((lo_name, hi_name, f"95% CI {lev}"))
    drop = []
    for lo_name, hi_name, label in ci_pairs:
        if lo_name in out.columns and hi_name in out.columns:
            out[label] = [_fmt_span(a, b) for a, b in zip(out[lo_name], out[hi_name])]
            drop.extend([lo_name, hi_name])
    if drop:
        out = out.drop(columns=drop)
    for col in list(out.columns):
        if col in ("Outcome", "Term", "Contrast", "N") or str(col).startswith("95%"):
            continue
        if col in ("df1", "df2"):
            out[col] = out[col].map(lambda v: _fmt_est(v).rstrip("0").rstrip(".") if _fmt_est(v) != "NA" else "NA")
            continue
        if pd.api.types.is_numeric_dtype(out[col]):
            if col in ("P", "FDR P", "FWER P"):
                out[col] = out[col].map(_fmt_p)
            else:
                out[col] = out[col].map(_fmt_est)
    prefer = [c for c in (
        "Outcome", "Term", "Contrast", "Estimate", "SE", "t", "β", "95% CI",
        "F", "df1", "df2", "P", "FDR P", "FWER P", "N",
    ) if c in out.columns]
    rest = [c for c in out.columns if c not in prefer]
    return out[prefer + rest]


def _adjust_note(n_family: int, do_fdr: bool, do_fwer: bool, shared: bool) -> str:
    bits = []
    if do_fdr or do_fwer:
        which = []
        if do_fdr:
            which.append("Benjamini–Hochberg FDR")
        if do_fwer:
            which.append("Holm FWER")
        fam = (
            f"Adjusted P values ({' and '.join(which)}) use this table's Group tests only "
            f"(one test per outcome; family size = {n_family}). "
            "Table 1, logistic screening, LASSO, and a single partial correlation are not in this family."
        )
        if shared:
            fam += " The matching linear-model table repeats this same adjustment and is not a second family."
        bits.append(fam)
    return " ".join(bits)


def _assoc_sections_html(assoc: Optional[dict]) -> list[str]:
    if not assoc:
        return []
    do_fdr = bool(assoc.get("do_fdr"))
    do_fwer = bool(assoc.get("do_fwer"))
    shared = bool(assoc.get("shared_family"))
    parts: list[str] = []
    anc = assoc.get("ancova") or {}
    params = _assoc_display(anc.get("params"))
    means = _assoc_display(anc.get("means"))
    if params is not None or means is not None:
        n_fam = int(anc.get("n_family") or 0)
        note = (
            "Ordinary least squares on the full analysis cohort. "
            f"Model: {anc.get('formula') or 'outcome ~ Group + covariates'}. "
            "F is the Wald test of the Group coefficients. "
            "Adjusted means are estimated marginal means, averaged over the observed covariate distribution. "
            "95% CIs are Wald intervals."
        )
        extra = _adjust_note(n_fam, do_fdr, do_fwer, shared)
        if extra:
            note = note + " " + extra
        block = ["<section><h2>ANCOVA</h2>", f'<p class="note">{note}</p>']
        if params is not None:
            block.append("<h3>Parameter estimates</h3>")
            block.append(df_to_html(params, "ancova-params", caption="ANCOVA parameter estimates"))
        if means is not None:
            block.append("<h3>Group test and adjusted means</h3>")
            block.append(df_to_html(
                means, "ancova-means",
                caption="ANCOVA Group test and adjusted means",
                footnote=note,
            ))
        block.append("</section>")
        parts.append("\n".join(block))
    mlr = assoc.get("mlr") or {}
    betas = _assoc_display(mlr.get("betas"))
    if betas is not None:
        n_fam = int(mlr.get("n_family") or 0)
        note = (
            "Group coefficient from outcome ~ Group + covariates on the full analysis cohort. "
            f"Model: {mlr.get('formula') or 'outcome ~ Group + covariates'}. "
            "β is the adjusted mean difference for the contrast shown. "
            "With one binary Group contrast, this P equals the ANCOVA Group F test."
        )
        extra = _adjust_note(n_fam, do_fdr, do_fwer, shared)
        if extra:
            note = note + " " + extra
        parts.append("\n".join([
            "<section><h2>Multiple Linear Regression</h2>",
            f'<p class="note">{note}</p>',
            df_to_html(
                betas, "mlr-group",
                caption="Group effect",
                footnote=note,
            ),
            "</section>",
        ]))
    warns = [str(w) for w in (assoc.get("warnings") or []) if str(w).strip()]
    if warns and parts:
        items = "".join(f"<li>{w}</li>" for w in warns)
        parts.append(f'<section class="warn"><h2>Linear model warnings</h2><ul>{items}</ul></section>')
    elif warns and not parts:
        items = "".join(f"<li>{w}</li>" for w in warns)
        parts.append(f'<section class="warn"><h2>Linear model warnings</h2><ul>{items}</ul></section>')
    return parts


def _partial_rows(assoc: Optional[dict]) -> list[dict]:
    if not assoc or not assoc.get("partial"):
        return []
    raw = assoc["partial"]
    if isinstance(raw, dict):
        return [raw]
    return [row for row in raw if isinstance(row, dict)]


def _partial_html(assoc: Optional[dict]) -> str:
    # DO_CORR 关了也不许把偏相关一起关掉；这里只看偏相关自己的开关。
    if not bool(getattr(cfg, "DO_PARTIAL", False)):
        return ""
    rows = _partial_rows(assoc)
    if not rows:
        return ""
    group = str(rows[0].get("group") or "").strip()
    disp = pd.DataFrame([{
        "Group": group or "All",
        "Predictor": row.get("x"),
        "Reference": row.get("y"),
        "Controls": ", ".join(row.get("controls") or []) or "none",
        "Method": str(row.get("method") or "").title(),
        "Partial r": _fmt_est(row.get("r")),
        "95% CI": _fmt_span(row.get("lo"), row.get("hi")),
        "P": _fmt_p(row.get("p")),
        "N": row.get("n"),
        "df": row.get("df"),
    } for row in rows])
    if not group:
        disp = disp.drop(columns=["Group"])
    method = str(rows[0].get("method") or "")
    scope = f" Sample limited to {group}." if group else ""
    if len(rows) == 1:
        controls = ", ".join(rows[0].get("controls") or []) or "none"
        lead = (
            f"Partial correlation of {rows[0].get('x')} and {rows[0].get('y')} "
            f"controlling for {controls}"
        )
    else:
        lead = f"Partial correlations for {len(rows)} pre-specified pairs"
    note = (
        f"{lead} ({method}; residual method"
        + ("; continuous variables rank-transformed" if method == "spearman" else "")
        + "). "
        "Pre-specified pairs are not entered into the Group-test FDR/FWER family. "
        "95% CI uses Fisher's z."
        f"{scope}"
    )
    return "\n".join([
        "<h3>Partial correlation</h3>",
        f'<p class="note">{note}</p>',
        df_to_html(disp, "partial-corr", caption="Partial correlation", footnote=note),
    ])


def generate_results_html(data, res_all, res_train=None, res_test=None,
                           corr_egfr_df: Optional[pd.DataFrame] = None,
                           corr_group_df: Optional[pd.DataFrame] = None,
                           corr_ref_tables: Optional[list] = None,
                           seed: Optional[int] = None, train_ratio: Optional[float] = None,
                           out_dir: Optional[str] = None,
                           combined_info: Optional[Dict[str, Any]] = None,
                           external_results: Optional[list] = None,
                           advanced_info: Optional[Dict[str, Any]] = None,
                           subgroup_results: Optional[list] = None,
                           run_status: Optional[Dict[str, Any]] = None,
                           assoc_info: Optional[Dict[str, Any]] = None) -> str:
    """
    组装完整 HTML 报告并写出到 {out_dir}/Results{子项目}.html。
    两两比较仍用 {阳性}_vs_{阴性}-results.html。

    external_results : 可选锁死评估记录（同亚组：splits/metrics/panels）
    """
    from modules.utils import dry_run as _dry
    if seed is None:
        seed = int(getattr(cfg, "SEED", 66666) or 66666)
    if train_ratio is None:
        train_ratio = float(getattr(cfg, "TRAIN_RATIO", 0.7) or 0.7)
    if out_dir is None:
        out_dir = str(getattr(cfg, "OUT_DIR", "") or "")
    out_dir = str(out_dir or "").strip()
    if _dry.exit_after_plan("report", [
        f"out_dir={out_dir or '(unset)'}",
        "assemble Results*.html (skipped write)",
    ]):
        return ""
    if not out_dir or out_dir in (".", "./"):
        raise ValueError(
            "generate_results_html: OUT_DIR 为空，禁止写到工程根；请先 set_outcome"
        )
    from modules.config.outcome import normalize_analysis_out_dir

    out_dir = normalize_analysis_out_dir(out_dir)
    src = str(getattr(cfg, "GROUP_SOURCE_COL", None) or "").strip()
    cols = [
        str(x).strip()
        for x in (getattr(cfg, "OUTCOME_COLS", None) or [])
        if str(x).strip()
    ]
    disp_pos = str(getattr(cfg, "GROUP_DISPLAY_POS", "") or "").strip()
    disp_neg = str(getattr(cfg, "GROUP_DISPLAY_NEG", "") or "").strip()
    keep = [
        str(x).strip()
        for x in (getattr(cfg, "GROUP_KEEP", None) or [])
        if str(x).strip()
    ]
    if len(keep) == 2 and disp_pos and disp_neg:
        page_title = f"{safe_dir_token(f'{disp_pos}_vs_{disp_neg}')}-results"
        html_name = f"{page_title}.html"
    else:
        mapping = getattr(cfg, "SUBPROJECT_BY_OUTCOME", None) or {}
        if not isinstance(mapping, dict):
            mapping = {}
        use_subdir = bool(getattr(cfg, "OUTCOME_OUT_SUBDIR", True))
        sub = subproject_root(src, out_dir, mapping, use_subdir)
        if not sub:
            picked = src if src and src in cols else (disp_pos or src or "Results")
            sub = picked
        html_name = results_html_name(sub)
        page_title = html_name[:-5]
    print(f"═══ {page_title.upper()} ═══")

    html_parts = [
        f'<header>\n<h1>{page_title}</h1>\n</header>',
    ]
    # C2: modules provenance note under page header (best-effort).
    try:
        from modules import __version__ as _mod_ver
        from modules.provenance import collect as _prov_collect, format_html_note

        _pkg = Path(__file__).resolve().parents[1]
        html_parts.append(format_html_note(_prov_collect(_pkg, version=_mod_ver)))
    except Exception:
        pass
    if run_status and str(run_status.get("status") or "OK") != "OK":
        fails = run_status.get("failures") or []
        items = "".join(
            f"<li><b>{x.get('name')}</b>: {x.get('error')}</li>" for x in fails
        )
        html_parts.append(
            f'<section class="warn"><h2>Run status: {run_status.get("status")}</h2>'
            f"<p>One or more requested analyses did not complete. "
            f"Do not treat missing modules as having been run.</p>"
            f"<ul>{items}</ul></section>"
        )

    # A. All Table1
    html_parts.append(_build_panel(
        "all", data.all, res_all["rad"], res_all["clin"],
        "A. All Data — Baseline", out_dir=out_dir,
        include_table1=True,
    ))
    html_parts.extend(_assoc_sections_html(assoc_info))

    # B. Train | Test Table1 并排
    t1_dual = _table1_train_test_section(res_train, res_test, data=data)
    if t1_dual:
        html_parts.append(t1_dual)

    # B2. Radiomics Table 2 Train | Test（logistic pool 或 LASSO-selected）
    t2_dual = _table2_train_test_section(res_train, res_test)
    if t2_dual:
        html_parts.append(t2_dual)

    # C. Training（无 Table1、无单图）
    if res_train is not None:
        html_parts.append(_build_panel(
            "train", data.train, res_train["rad"], res_train["clin"],
            "C. Training Set", combined_info=combined_info,
            advanced_info=advanced_info, out_dir=out_dir,
            include_table1=False,
        ))

    # D. Figures（panel 合成图）
    fig_sec = _figures_section_html(
        out_dir, res_train=res_train, res_test=res_test,
        data=data, advanced_info=advanced_info,
    )
    if fig_sec:
        html_parts.append(fig_sec)

    surv_sec = _survival_section_html(out_dir)
    if surv_sec:
        html_parts.append(surv_sec)

    sg_sec = _subgroup_section_html(subgroup_results, out_dir)
    if sg_sec:
        html_parts.append(sg_sec)

    ext_sec = _external_section_html(external_results, out_dir)
    if ext_sec:
        html_parts.append(ext_sec)

    # F. NRI / IDI
    nri_sec = _nri_idi_section_html(combined_info, out_dir)
    if nri_sec:
        html_parts.append(nri_sec)

    # G. Correlation
    corr_bits = ['<section><h2>G. Correlation</h2>']
    corr_png = "correlation_heatmap_selected.png"
    corr_group = str(getattr(cfg, "CORR_GROUP", "") or "").strip()
    if bool(getattr(cfg, "DO_HEATMAP", True)) and _f_exists(corr_png, out_dir):
        scope = f" Rows limited to {corr_group}." if corr_group else " All rows."
        corr_bits += [
            '<h3>Selected features heatmap</h3>',
            '<p class="note">Spearman correlation of the heatmap columns.'
            f'{scope} '
            '<b>*</b> p&lt;0.05, <b>**</b> p&lt;0.01, <b>***</b> p&lt;0.001.</p>',
            _img_tag(corr_png, "Combined Correlation", out_dir),
        ]

    def _prep_corr_disp(df: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
        if df is None or len(df) == 0:
            return None
        disp = df.copy()
        drop_cols = [c for c in ("n", "N") if c in disp.columns]
        if drop_cols:
            disp = disp.drop(columns=drop_cols)
        if "ci_lower" in disp.columns and "ci_upper" in disp.columns:
            def _fmt_ci(lo, hi):
                if pd.isna(lo) or pd.isna(hi):
                    return "NA"
                return f"{float(lo):.4f}–{float(hi):.4f}"
            disp["95% CI"] = [
                _fmt_ci(lo, hi) for lo, hi in zip(disp["ci_lower"], disp["ci_upper"])
            ]
            disp = disp.drop(columns=["ci_lower", "ci_upper"])
        for col in ("spearman_rho", "p_value"):
            if col in disp.columns:
                disp[col] = disp[col].map(lambda v: f"{v:.4f}" if pd.notna(v) else "NA")
        # Keep Predictor first; the caller inserts Reference as the second column.
        prefer = [c for c in ("predictor", "spearman_rho", "p_value", "95% CI")
                  if c in disp.columns]
        rest = [c for c in disp.columns if c not in prefer and c != "ref"]
        return disp[prefer + rest]

    def _ref_name(df, default_ref: str) -> str:
        if df is not None and len(df) > 0 and "ref" in df.columns:
            vals = [str(v) for v in df["ref"].dropna().unique()]
            if vals:
                return vals[0]
        return default_ref

    # 参考变量相关表（CORR_REFS），只出表不出图。关相关表时不写，即使上游还留着表。
    # 不能在关了之后再落到 corr_egfr_df，否则 DO_CORR 关不掉这张表。
    corr_frames: List[pd.DataFrame] = []
    if not bool(getattr(cfg, "DO_CORR", True)):
        tables = []
    else:
        tables = list(corr_ref_tables or [])
        if not tables:
            tables = [x for x in (corr_egfr_df, corr_group_df) if x is not None]
    fallback_refs = [str(x).strip() for x in (getattr(cfg, "CORR_REFS", None) or []) if str(x).strip()]
    for i, df_corr in enumerate(tables):
        disp = _prep_corr_disp(df_corr)
        if disp is None or len(disp) == 0:
            continue
        default_ref = fallback_refs[i] if i < len(fallback_refs) else f"ref{i+1}"
        ref = _ref_name(df_corr, default_ref)
        block = disp.copy()
        block.insert(1, "Reference", ref)
        corr_frames.append(block)
    if corr_frames:
        corr_merged = pd.concat(corr_frames, ignore_index=True)
        corr_bits += [
            "<h3>Correlation with reference variables</h3>",
            '<p class="note">Spearman tables only (no extra figures). '
            "One block per selected reference.</p>",
            df_to_html(
                corr_merged, "corr-ref-summary",
                caption="Correlation with selected references",
            ),
        ]
    partial_html = _partial_html(assoc_info)
    if partial_html:
        if len(corr_bits) == 1:
            corr_bits = ['<section><h2>G. Correlation</h2>']
        corr_bits.append(partial_html)
    if len(corr_bits) > 1:
        corr_bits.append("</section>")
        html_parts.append("\n".join(corr_bits))

    # H. DAG
    dag_sec = _dag_section_html(out_dir)
    if dag_sec:
        html_parts.append(dag_sec)

    # I. Mediation
    med_sec = _mediation_section_html(advanced_info, out_dir)
    if med_sec:
        html_parts.append(med_sec)

    css = _report_css()
    full_html = (
        '<!DOCTYPE html>\n<html lang="en" data-bg="night">\n<head>\n<meta charset="UTF-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        f'<title>{page_title}</title>\n{_report_theme_boot()}{css}\n</head>\n<body>\n'
        f'{_report_theme_bar()}\n\n'
        + '\n\n'.join(html_parts) + '\n\n</body>\n</html>'
    )

    os.makedirs(out_dir, exist_ok=True)
    html_path = os.path.join(out_dir, html_name)
    from modules.utils import dry_run as _dry_write
    if _dry_write.skip_write(html_path, what="results html"):
        return full_html
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(full_html)
    print(f"  {html_name} ({len(full_html.encode('utf-8'))} bytes)")
    _cleanup_unreferenced_pngs(html_path, out_dir)
    return full_html


def _report_theme_boot() -> str:
    return (
        '<script>(function(){var k="rad-results-bg",ok={night:1,paper:1,white:1},n="night";'
        'try{var s=localStorage.getItem(k);if(ok[s])n=s;}catch(e){}'
        'document.documentElement.setAttribute("data-bg",n);})();</script>\n'
    )


def _report_theme_bar() -> str:
    return '''<div class="bg-pick" id="bgPick">
<button type="button" data-bg-btn="night">深色护眼</button>
<button type="button" data-bg-btn="paper">暖纸</button>
<button type="button" data-bg-btn="white">白底</button>
</div>
<script>
(function(){
  var k="rad-results-bg", ok={night:1,paper:1,white:1};
  function apply(n){
    if(!ok[n]) n="night";
    document.documentElement.setAttribute("data-bg", n);
    try{localStorage.setItem(k,n);}catch(e){}
    document.querySelectorAll("[data-bg-btn]").forEach(function(b){
      b.classList.toggle("on", b.getAttribute("data-bg-btn")===n);
    });
  }
  document.querySelectorAll("[data-bg-btn]").forEach(function(b){
    b.addEventListener("click", function(){ apply(b.getAttribute("data-bg-btn")); });
  });
  apply(document.documentElement.getAttribute("data-bg")||"night");
})();
</script>'''


def _report_css() -> str:
    return '''<style>
:root,html[data-bg="night"]{
  --bg:#1c241f;--ink:#d7d2c6;--hd:#e6e1d4;--br:#667066;--mu:#a3aa9c;
  --line:#c9c3b6;--note:#cfc9bc;--h3:#ddd6c8;--soft:#2a342e;--formula:#28322c;
  --warn-bg:#3a2828;--warn-bd:#a86a6a;--warn-ink:#f0d0d0;
}
html[data-bg="paper"]{
  --bg:#f3ead6;--ink:#2c261c;--hd:#2c261c;--br:#c4b89a;--mu:#6e6454;
  --line:#2c261c;--note:#3d362a;--h3:#2c261c;--soft:#e7dcc4;--formula:#fbf6ea;
  --warn-bg:#f8e4e4;--warn-bd:#b91c1c;--warn-ink:#991b1b;
}
html[data-bg="white"]{
  --bg:#fff;--ink:#111;--hd:#1a1a1a;--br:#bdc3c7;--mu:#666;
  --line:#000;--note:#333;--h3:#222;--soft:#f7f7f7;--formula:#fafafa;
  --warn-bg:#fef2f2;--warn-bd:#b91c1c;--warn-ink:#991b1b;
}
*{box-sizing:border-box;margin:0;padding:0}
html{background:var(--bg);min-height:100%}
body{font-family:"Times New Roman",Times,serif;max-width:1100px;margin:0 auto;padding:1.2rem 1.5rem 2rem;color:var(--ink);background:var(--bg);line-height:1.55;font-size:11pt}
.bg-pick{position:sticky;top:0;z-index:5;display:flex;flex-wrap:wrap;gap:4px;justify-content:flex-end;margin:0 0 .8rem;padding:6px 0;background:var(--bg)}
.bg-pick button{border:1px solid var(--br);background:var(--soft);color:var(--ink);border-radius:5px;padding:3px 8px;cursor:pointer;font:12px/1.3 "Segoe UI",Arial,sans-serif}
.bg-pick button.on{font-weight:700;outline:2px solid var(--hd)}
.t1-grid{display:grid;grid-template-columns:1fr 1fr;gap:1.2rem;align-items:start}
.t1-col{min-width:0;overflow-x:auto}
@media (max-width:900px){.t1-grid{grid-template-columns:1fr}}
table.multihead thead th{text-align:center;vertical-align:middle;font-weight:600}
table.multihead thead tr:nth-child(3) th{font-weight:500;font-size:0.95em}
header{border-bottom:2px solid var(--hd);padding-bottom:1rem;margin-bottom:1.5rem}
h1{font-size:1.5rem;color:var(--hd);font-family:"Segoe UI",Arial,sans-serif}
h2{font-size:1.15rem;color:var(--hd);border-bottom:1px solid var(--br);padding-bottom:.3rem;margin:1.8rem 0 .8rem;font-family:"Segoe UI",Arial,sans-serif}
h3,h4{font-size:1.0rem;color:var(--h3);margin:1.1rem 0 .45rem;font-family:"Segoe UI",Arial,sans-serif;font-weight:600}
.timestamp{color:var(--mu);font-size:.85rem;font-family:"Segoe UI",Arial,sans-serif}
.muted{color:var(--mu);font-style:italic}
.warn{border:2px solid var(--warn-bd);background:var(--warn-bg);color:var(--warn-ink);padding:12px 16px;margin:12px 0}
.warn h2{color:var(--warn-ink);margin-top:0}
.sel-feat{background:var(--soft);padding:.55rem 1rem;border-left:3px solid var(--hd);margin:.7rem 0;font-size:.9rem}
.note{color:var(--note);font-size:.9rem;margin:.5rem 0}
/* three-line tables */
.table-wrap{overflow-x:auto;margin:.6rem 0 1.4rem}
table.threeline{
  width:100%;
  border-collapse:collapse;
  border-top:2px solid var(--line);
  border-bottom:2px solid var(--line);
  font-size:10pt;
  margin:0 0 .35rem 0;
}
table.threeline caption{
  caption-side:top;
  text-align:left;
  font-weight:700;
  font-size:10.5pt;
  padding:0 0 6px 0;
  color:var(--ink);
}
table.threeline thead th,
table.threeline th{
  border:none;
  border-bottom:1px solid var(--line);
  background:transparent;
  font-weight:700;
  text-align:center;
  padding:7px 8px;
  vertical-align:bottom;
  white-space:nowrap;
}
table.threeline td{
  border:none;
  padding:5px 8px;
  text-align:center;
  vertical-align:middle;
}
table.threeline td.col-first,
table.threeline td:first-child{
  text-align:left;
  font-weight:400;
}
table.threeline td.col-first.indent{
  padding-left:1.6em;
  color:var(--note);
  font-weight:400;
}
table.threeline tbody tr:nth-child(even){background:transparent}
.table-note{
  font-size:8.5pt;
  color:var(--note);
  margin:.15rem 0 0 0;
  line-height:1.45;
  text-align:left;
}
.table-note .note-label{font-style:italic;margin-right:.25em}
.formula{font-family:"Cambria Math","Times New Roman",serif;font-size:1.05rem;background:var(--formula);color:var(--ink);padding:.9rem 1.2rem;border-left:3px solid var(--hd);margin:1rem 0;line-height:1.75;overflow-x:auto;white-space:nowrap}
.fig{max-width:100%;height:auto;display:block;margin:1rem 0;border:none;background:#fff}
@media print{
  html,body,h1,h2,h3,h4,.note,.table-note,table.threeline caption,
  table.threeline td,table.threeline th,.timestamp,.muted,.formula,.sel-feat{
    background:#fff!important;color:#000!important;
  }
  .bg-pick{display:none!important}
  body{max-width:100%;padding:0}
  .fig{break-inside:avoid}h2{break-after:avoid}
  table.threeline{border-top:2px solid #000;border-bottom:2px solid #000}
  table.threeline th{border-bottom:1px solid #000}
}
</style>'''
