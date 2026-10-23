"""
u_qc.py — Habitat radiomics pipeline quality-control from lung_xlm_summary.csv
═══════════════════════════════════════════════════════════════════
Reads the per-patient pipeline summary table and exports reproducible QC
tables/figures for the manuscript Supplementary Material.

Independent run:
    python -m modules.utils.u_qc
    python -m modules.utils.u_qc --input lung_xlm_summary.csv --out QC

Local CSVs (qc_table_s1.csv, qc_patient_level.csv) are not written.
qc.html / qc-ext-*.html 写在工程 ref/；清单写在各子工程 QC/。

Columns used (when present):
    Status, Habitat_Mode, Habitat_K, Habitat_Fallback, Habitat_Verify_K,
    Habitat_Verify_NonEmpty, Habitat0/1/2_Pct, Habitat0/1/2_Voxels,
    Radiomics_Verified, Feature_Count, Feature_Rows, Radiomics_Seq_List,
    Radiomics_Skip_Habitats, TumorVoxels, Superpixel_Used

Missing columns are reported as N/A; sparse fields (e.g. TumorVoxels) are
summarized with available-n noted.
═══════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# ═════════════════════════════════════════════════════════════════
# §1  Defaults
# ═════════════════════════════════════════════════════════════════
DEFAULT_INPUT = "lung_xlm_summary.csv"
DEFAULT_OUT = "QC"
SUCCESS_TOKEN = "成功(全流程)"
YES_TOKENS = {"是", "yes", "Yes", "Y", "TRUE", "True", "true", 1, "1"}
QC_HTML_MAIN = "qc.html"
QC_HTML_EXT = "qc-ext.html"
QC_HTML_EXT_PREFIX = "qc-ext-"


def _project_root() -> Path:
    # 子工程根（yt_RDL 等），不是 0Grok 仓库根。
    from modules.config.ini_io import resolve_project_dir
    return resolve_project_dir()


def qc_output_dir(project_root: str | Path | None = None) -> Path:
    """QC HTML 一律写在工程 ref/，不进工程根、不进结局子目录。"""
    from modules.config.ini_io import project_ref_dir, resolve_project_dir
    root = Path(project_root) if project_root else resolve_project_dir()
    if root.name.lower() == "ref":
        root.mkdir(parents=True, exist_ok=True)
        return root
    return project_ref_dir(root)


def qc_html_rel(filename: str) -> str:
    return f"ref/{Path(str(filename or QC_HTML_MAIN)).name}"


def resolve_qc_html_path(
    out_html: str | Path = QC_HTML_MAIN,
    project_root: str | Path | None = None,
) -> Path:
    dest = qc_output_dir(project_root)
    p = Path(out_html)
    name = p.name if p.suffix else QC_HTML_MAIN
    return dest / name


def relocate_legacy_qc_html(project_root: str | Path | None = None) -> list[Path]:
    """把工程根遗留的 qc.html / qc-ext-*.html 挪进 ref/（目标已有则不覆盖）。"""
    root = Path(project_root) if project_root else _project_root()
    if root.name.lower() == "ref":
        root = root.parent
    dest = qc_output_dir(root)
    moved: list[Path] = []
    if not root.is_dir():
        return moved
    for p in root.iterdir():
        if not p.is_file() or not is_qc_html_name(p.name):
            continue
        target = dest / p.name
        if target.exists():
            continue
        p.replace(target)
        print(f"  ◆ QC HTML → {target}")
        moved.append(target)
    return moved


def external_qc_html_name(name: str) -> str:
    """外验 QC 文件名。永远不是 qc.html。无名 / ext → qc-ext.html，否则 qc-ext-{token}.html。"""
    from modules.config.outcome import safe_dir_token
    token = safe_dir_token(str(name or "").strip())
    low = token.lower()
    if low in {"", "unnamed", "ext", "external", "qc", "html"}:
        return QC_HTML_EXT
    return f"{QC_HTML_EXT_PREFIX}{token}.html"


def is_qc_html_name(filename: str) -> bool:
    n = str(filename or "").strip().lower()
    if n == QC_HTML_MAIN or n == QC_HTML_EXT:
        return True
    return n.startswith(QC_HTML_EXT_PREFIX) and n.endswith(".html")


def _external_specs_from_settings(settings: Dict[str, Any] | None) -> list[dict]:
    settings = settings or {}
    specs = list(settings.get("EXTERNAL_TESTS") or [])
    if not specs:
        cs = str(settings.get("EXTERNAL_CLIN_SHEET") or "").strip()
        rs = str(settings.get("EXTERNAL_RAD_SHEET") or "").strip()
        f = str(settings.get("EXTERNAL_FILE") or "").strip()
        if f and cs and rs:
            specs = [{"name": "ext", "file": f, "clin_sheet": cs, "rad_sheet": rs}]
    out: list[dict] = []
    for i, spec in enumerate(specs):
        if not isinstance(spec, dict):
            continue
        cs = str(spec.get("clin_sheet") or "").strip()
        rs = str(spec.get("rad_sheet") or "").strip()
        f = str(spec.get("file") or "").strip()
        if not f or not cs or not rs:
            continue
        name = str(spec.get("name") or "").strip() or ("ext" if not i else f"ext{i + 1}")
        item = {"name": name, "file": f, "clin_sheet": cs, "rad_sheet": rs}
        rid = str(spec.get("rad_id_col") or "").strip()
        if rid:
            item["rad_id_col"] = rid
        for k in ("qc_sheet", "icc_sheet"):
            v = str(spec.get(k) or "").strip()
            if v:
                item[k] = v
        out.append(item)
    return out


def load_summary(path: str | Path) -> pd.DataFrame:
    """Load pipeline summary CSV; strip column names."""
    p = Path(path)
    if not p.is_absolute():
        p = _project_root() / p
    df = pd.read_csv(p)
    df.columns = [str(c).strip() for c in df.columns]
    return df


def _is_yes(series: pd.Series) -> pd.Series:
    return series.map(lambda x: x in YES_TOKENS if pd.notna(x) else False)


def pipeline_pass_rate(df: pd.DataFrame) -> Dict[str, Any]:
    n = len(df)
    if "Status" not in df.columns:
        return {"n_total": n, "n_success": None, "pass_rate": None, "status_counts": {}}
    counts = df["Status"].value_counts(dropna=False).to_dict()
    n_success = int((df["Status"] == SUCCESS_TOKEN).sum())
    return {
        "n_total": n,
        "n_success": n_success,
        "pass_rate": float(n_success / n) if n else None,
        "status_counts": {str(k): int(v) for k, v in counts.items()},
    }


def habitat_qc(df: pd.DataFrame) -> Dict[str, Any]:
    out: Dict[str, Any] = {"n": len(df)}
    if "Habitat_Mode" in df.columns:
        mode_counts = df["Habitat_Mode"].value_counts(dropna=False).to_dict()
        out["habitat_mode_counts"] = {str(k): int(v) for k, v in mode_counts.items()}
        out["global_model_n"] = int((df["Habitat_Mode"] == "全局模型").sum())
        out["global_model_rate"] = float(out["global_model_n"] / len(df)) if len(df) else None
    if "Habitat_K" in df.columns:
        out["habitat_k_counts"] = {
            str(k): int(v) for k, v in df["Habitat_K"].value_counts(dropna=False).items()
        }
        out["habitat_k_unique"] = [int(x) for x in sorted(df["Habitat_K"].dropna().unique())]
    if "Habitat_Verify_K" in df.columns:
        out["verify_k_counts"] = {
            str(k): int(v) for k, v in df["Habitat_Verify_K"].value_counts(dropna=False).items()
        }
    if "Habitat_Verify_NonEmpty" in df.columns:
        out["verify_nonempty_counts"] = {
            str(k): int(v)
            for k, v in df["Habitat_Verify_NonEmpty"].value_counts(dropna=False).items()
        }
    if "Habitat_Fallback" in df.columns:
        n_fb = int(_is_yes(df["Habitat_Fallback"]).sum())
        out["fallback_n"] = n_fb
        out["fallback_rate"] = float(n_fb / len(df)) if len(df) else None
    else:
        out["fallback_n"] = None
        out["fallback_rate"] = None
    if "Superpixel_Used" in df.columns:
        out["superpixel_used_n"] = int(_is_yes(df["Superpixel_Used"]).sum())
        out["superpixel_nan_n"] = int(df["Superpixel_Used"].isna().sum())
    return out


def _series_stats(s: pd.Series) -> Dict[str, Any]:
    s = pd.to_numeric(s, errors="coerce").dropna()
    if s.empty:
        return {"n": 0}
    q = s.quantile([0.25, 0.5, 0.75])
    return {
        "n": int(s.shape[0]),
        "mean": float(s.mean()),
        "std": float(s.std(ddof=1)) if len(s) > 1 else 0.0,
        "min": float(s.min()),
        "q25": float(q.loc[0.25]),
        "median": float(q.loc[0.5]),
        "q75": float(q.loc[0.75]),
        "max": float(s.max()),
    }


def habitat_volume_stats(df: pd.DataFrame) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for col in [
        "Habitat0_Pct",
        "Habitat1_Pct",
        "Habitat2_Pct",
        "Habitat0_Voxels",
        "Habitat1_Voxels",
        "Habitat2_Voxels",
        "TumorVoxels",
    ]:
        if col in df.columns:
            out[col] = _series_stats(df[col])
    return out


def radiomics_qc(df: pd.DataFrame) -> Dict[str, Any]:
    out: Dict[str, Any] = {"n": len(df)}
    if "Radiomics_Verified" in df.columns:
        n_ok = int(_is_yes(df["Radiomics_Verified"]).sum())
        out["radiomics_verified_n"] = n_ok
        out["radiomics_verified_rate"] = float(n_ok / len(df)) if len(df) else None
    if "Feature_Count" in df.columns:
        fc = pd.to_numeric(df["Feature_Count"], errors="coerce")
        out["feature_count_unique"] = [int(x) for x in sorted(fc.dropna().unique())]
        out["feature_count_stats"] = _series_stats(fc)
    if "Feature_Rows" in df.columns:
        fr = pd.to_numeric(df["Feature_Rows"], errors="coerce")
        out["feature_rows_counts"] = {
            str(k): int(v) for k, v in fr.value_counts(dropna=False).items()
        }
    if "Radiomics_Seq_List" in df.columns:
        out["radiomics_seq_counts"] = {
            str(k): int(v) for k, v in df["Radiomics_Seq_List"].value_counts(dropna=False).items()
        }
    if "Radiomics_Skip_Habitats" in df.columns:
        out["skip_habitats_nonnull_n"] = int(df["Radiomics_Skip_Habitats"].notna().sum())
    if "Radiomics_NSeq" in df.columns:
        out["nseq_counts"] = {
            str(k): int(v)
            for k, v in pd.to_numeric(df["Radiomics_NSeq"], errors="coerce")
            .value_counts(dropna=False)
            .items()
        }
    return out


def build_qc_summary(df: pd.DataFrame) -> Dict[str, Any]:
    return {
        "pipeline": pipeline_pass_rate(df),
        "habitat": habitat_qc(df),
        "habitat_volume": habitat_volume_stats(df),
        "radiomics": radiomics_qc(df),
    }


def export_qc_tables(df: pd.DataFrame, out_dir: str | Path | None = None) -> Dict[str, Path]:
    """Write JSON summary to ref/. Do not emit local qc_table_s1 / patient-level CSVs."""
    _ = out_dir
    summary = build_qc_summary(df)
    from modules.config.ini_io import project_ref_dir
    json_path = project_ref_dir() / "qc_summary.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    return {"json": json_path}


def plot_habitat_fractions(df: pd.DataFrame, out_dir: str | Path) -> Dict[str, Path]:
    """Box/violin-style distributions of habitat volume fractions."""
    from modules import config as cfg
    if not cfg.figures_on():
        print("  DO_FIGURE_PANELS=false — skip habitat QC figures", flush=True)
        return {}
    out = Path(out_dir)
    if not out.is_absolute():
        out = _project_root() / out
    out.mkdir(parents=True, exist_ok=True)
    paths: Dict[str, Path] = {}

    cols = [c for c in ["Habitat0_Pct", "Habitat1_Pct", "Habitat2_Pct"] if c in df.columns]
    if cols:
        data = [pd.to_numeric(df[c], errors="coerce").dropna().values for c in cols]
        labels = ["Habitat 0", "Habitat 1", "Habitat 2"]
        fig, ax = plt.subplots(figsize=(6.5, 4.2))
        bp = ax.boxplot(data, tick_labels=labels, patch_artist=True, showfliers=True)
        colors = ["#4C78A8", "#F58518", "#54A24B"]
        for patch, color in zip(bp["boxes"], colors):
            patch.set_facecolor(color)
            patch.set_alpha(0.65)
        ax.set_ylabel("Volume fraction within nodule (%)")
        ax.set_title("Distribution of habitat volume fractions (n = {})".format(len(df)))
        ax.grid(axis="y", linestyle="--", alpha=0.4)
        fig.tight_layout()
        p1 = out / "suppl_fig_s1_habitat_fractions.png"
        fig.savefig(p1, dpi=200)
        plt.close(fig)
        paths["fig_s1"] = p1

    # Fallback split comparison
    if "Habitat_Fallback" in df.columns and cols:
        fb = _is_yes(df["Habitat_Fallback"])
        fig, axes = plt.subplots(1, 3, figsize=(9.5, 3.6), sharey=True)
        for ax, col, lab, color in zip(
            axes, cols, ["Habitat 0", "Habitat 1", "Habitat 2"], ["#4C78A8", "#F58518", "#54A24B"]
        ):
            vals_no = pd.to_numeric(df.loc[~fb, col], errors="coerce").dropna()
            vals_yes = pd.to_numeric(df.loc[fb, col], errors="coerce").dropna()
            ax.boxplot(
                [vals_no.values, vals_yes.values],
                tick_labels=["No fallback", "Fallback"],
                patch_artist=True,
            )
            for i, patch in enumerate(ax.artists if hasattr(ax, "artists") else []):
                pass
            ax.set_title(lab)
            ax.grid(axis="y", linestyle="--", alpha=0.35)
        axes[0].set_ylabel("Volume fraction (%)")
        fig.suptitle("Habitat fractions by nodule-level K-means fallback", y=1.02)
        fig.tight_layout()
        p2 = out / "suppl_fig_s2_habitat_by_fallback.png"
        fig.savefig(p2, dpi=200, bbox_inches="tight")
        plt.close(fig)
        paths["fig_s2"] = p2

    return paths


def write_qc_report_md(df: pd.DataFrame, out_path: str | Path) -> Path:
    summary = build_qc_summary(df)
    pipe = summary["pipeline"]
    hab = summary["habitat"]
    rad = summary["radiomics"]
    vol = summary["habitat_volume"]

    lines = [
        "# Pipeline quality control (from lung_xlm_summary.csv)",
        "",
        f"- Total cases: **{pipe.get('n_total')}**",
        f"- Full pipeline success: **{pipe.get('n_success')}** "
        f"({pipe.get('pass_rate', 0)*100:.1f}%)" if pipe.get("pass_rate") is not None else "",
        f"- Habitat mode (global model): **{hab.get('global_model_n')}** "
        f"({hab.get('global_model_rate', 0)*100:.1f}%)" if hab.get("global_model_rate") is not None else "",
        f"- Habitat K: **{hab.get('habitat_k_unique')}**",
        f"- Nodule-level fallback: **{hab.get('fallback_n')}** "
        f"({hab.get('fallback_rate', 0)*100:.1f}%)" if hab.get("fallback_rate") is not None else "",
        f"- Radiomics verified: **{rad.get('radiomics_verified_n')}** "
        f"({rad.get('radiomics_verified_rate', 0)*100:.1f}%)"
        if rad.get("radiomics_verified_rate") is not None
        else "",
        f"- Feature count per habitat: **{rad.get('feature_count_unique')}**",
        "",
        "## Habitat volume fractions (mean ± SD, %)",
    ]
    for key, label in [
        ("Habitat0_Pct", "Habitat 0"),
        ("Habitat1_Pct", "Habitat 1"),
        ("Habitat2_Pct", "Habitat 2"),
    ]:
        st = vol.get(key, {})
        if st.get("n"):
            lines.append(f"- {label}: {st['mean']:.1f} ± {st['std']:.1f} (n={st['n']})")

    lines.extend(
        [
            "",
            "Notes:",
            "- TumorVoxels may be sparse in the summary table; habitat percentages are complete for all cases.",
            "- Superpixel pre-segmentation was not used when Superpixel_Used is missing/NaN.",
            "",
        ]
    )

    out = Path(out_path)
    if not out.is_absolute():
        out = _project_root() / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join([ln for ln in lines if ln is not None]), encoding="utf-8")
    return out


def run_qc(
    input_path: str | Path = DEFAULT_INPUT,
    out_dir: str | Path = DEFAULT_OUT,
) -> Dict[str, Any]:
    df = load_summary(input_path)
    summary = build_qc_summary(df)
    paths = export_qc_tables(df, out_dir)
    fig_paths = plot_habitat_fractions(df, out_dir)
    out = Path(out_dir)
    if not out.is_absolute():
        out = _project_root() / out
    from modules.config.ini_io import project_ref_dir
    md_path = write_qc_report_md(df, project_ref_dir() / "qc_report.md")
    return {
        "summary": summary,
        "tables": {k: str(v) for k, v in paths.items()},
        "figures": {k: str(v) for k, v in fig_paths.items()},
        "report_md": str(md_path),
        "n": len(df),
    }


# ═════════════════════════════════════════════════════════════════
# Excel qc sheet → ref/qc.html
# ═════════════════════════════════════════════════════════════════

def load_qc_from_excel(
    path: str | Path,
    sheet: str = "qc",
) -> pd.DataFrame:
    """读取 data_merged.xlsx 的 qc 工作表。"""
    p = Path(path)
    if not p.is_absolute():
        p = _project_root() / p
    df = pd.read_excel(p, sheet_name=sheet)
    df.columns = [str(c).strip() for c in df.columns]
    return df


def _fmt_num(v: Any, nd: int = 3) -> str:
    try:
        x = float(v)
        if not np.isfinite(x):
            return "NA"
        return f"{x:.{nd}f}"
    except (TypeError, ValueError):
        return "NA"


def _series_desc(s: pd.Series) -> Dict[str, Any]:
    x = pd.to_numeric(s, errors="coerce").dropna()
    if len(x) == 0:
        return {"n": 0}
    return {
        "n": int(len(x)),
        "mean": float(x.mean()),
        "std": float(x.std(ddof=1)) if len(x) > 1 else 0.0,
        "median": float(x.median()),
        "min": float(x.min()),
        "max": float(x.max()),
    }


def _qc_html_shell(title: str, body_parts: list[str]) -> str:
    return "\n".join([
        "<!DOCTYPE html><html><head><meta charset='utf-8'/>",
        f"<title>{title}</title>",
        "<style>",
        "body{font-family:Segoe UI,Arial,sans-serif;max-width:980px;margin:24px auto;padding:0 16px;color:#222}",
        "h1{font-size:1.4rem}h2{font-size:1.15rem;margin-top:1.6rem;border-bottom:1px solid #ddd;padding-bottom:.3rem}",
        "table{border-collapse:collapse;width:100%;margin:.6rem 0 1rem;font-size:0.92rem}",
        "th,td{border:1px solid #ccc;padding:6px 8px;text-align:left}",
        "th{background:#f4f4f4} .muted{color:#666;font-size:0.9rem}",
        "caption{text-align:left;font-weight:600;margin-bottom:.35rem}",
        "td.warn{background:#f8e4e4}",
        "</style></head><body>",
        *body_parts,
        "</body></html>",
    ])


def _html_table(headers: list, rows: list, caption: str = "") -> str:
    cap = f"<caption>{caption}</caption>" if caption else ""
    th = "".join(f"<th>{h}</th>" for h in headers)
    body = ""
    for r in rows:
        cells = []
        for c in r:
            if isinstance(c, tuple) and len(c) == 2:
                cells.append(f"<td class='{c[1]}'>{c[0]}</td>")
            else:
                cells.append(f"<td>{c}</td>")
        body += "<tr>" + "".join(cells) + "</tr>"
    return f"<table>{cap}<thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>"


def build_qc_html_from_sheet(df: pd.DataFrame, title: str = "Imaging pipeline QC") -> str:
    """从 Excel qc 工作表构建自包含 HTML（无 QC_Status、无患者明细，以统计为主）。"""
    n = len(df)
    parts: list[str] = [
        f"<h1>{title}</h1>",
        f"<p class='muted'>N = {n} · statistical summary from qc worksheet</p>",
    ]
    parts.extend(_imaging_qc_sections(df))
    return _qc_html_shell(title, parts)


def _imaging_qc_sections(df: pd.DataFrame) -> list[str]:
    n = len(df)
    parts: list[str] = []

    def _table(headers: list, rows: list, caption: str = "") -> str:
        return _html_table(headers, rows, caption)

    # 1 MI
    parts.append("<h2>1. Registration / mutual information</h2>")
    mi_rows = []
    if "QC_D_MI" in df.columns:
        st = _series_desc(df["QC_D_MI"])
        if st.get("n"):
            mi_rows.append([
                "QC_D_MI", st["n"], _fmt_num(st["mean"]), _fmt_num(st["std"]),
                _fmt_num(st["median"]), _fmt_num(st["min"]), _fmt_num(st["max"]),
            ])
    if "QC_D_Threshold" in df.columns:
        thr = pd.to_numeric(df["QC_D_Threshold"], errors="coerce").dropna()
        if len(thr):
            mi_rows.append([
                "QC_D_Threshold", len(thr),
                _fmt_num(thr.mean()) if len(thr) else "NA",
                _fmt_num(thr.std(ddof=1)) if len(thr) > 1 else "NA",
                _fmt_num(thr.median()) if len(thr) else "NA",
                _fmt_num(thr.min()) if len(thr) else "NA",
                _fmt_num(thr.max()) if len(thr) else "NA",
            ])
    if "QC_MI_Warn" in df.columns:
        st = _series_desc(df["QC_MI_Warn"])
        if st.get("n"):
            mi_rows.append([
                "QC_MI_Warn", st["n"], _fmt_num(st["mean"]), _fmt_num(st["std"]),
                _fmt_num(st["median"]), _fmt_num(st["min"]), _fmt_num(st["max"]),
            ])
    if mi_rows:
        parts.append(_table(
            ["Metric", "n", "mean", "SD", "median", "min", "max"], mi_rows
        ))
    else:
        parts.append("<p class='muted'>No MI columns.</p>")

    # 2 Sequence preprocessing（压缩：一行序列）
    parts.append("<h2>2. Sequence preprocessing (C / D / T)</h2>")
    pre_rows = []
    for seq in ("C", "D", "T"):
        bits = []
        for col, lab in [
            (f"{seq}_Modality", "Modality"),
            (f"{seq}_NormMethod", "Norm"),
            (f"{seq}_TargetSpacing", "Spacing"),
        ]:
            if col not in df.columns:
                continue
            vc = df[col].astype(str).value_counts(dropna=False)
            # 单一取值则直接写值，否则写 top1 (n)
            if len(vc) == 1:
                bits.append(f"{lab}={vc.index[0]}")
            elif len(vc) > 0:
                k0, v0 = vc.index[0], int(vc.iloc[0])
                bits.append(f"{lab}={k0} ({v0}/{n})")
        if bits:
            pre_rows.append([seq, "; ".join(bits)])
    if pre_rows:
        parts.append(_table(
            ["Sequence", "Summary"], pre_rows,
            caption="Compressed: dominant value (count) per field",
        ))
    else:
        parts.append("<p class='muted'>No sequence preprocessing columns.</p>")

    # 3 Habitat
    parts.append("<h2>3. Habitat volume fractions (%)</h2>")
    hab_rows = []
    for col in ("Habitat0_Pct", "Habitat1_Pct", "Habitat2_Pct"):
        if col not in df.columns:
            continue
        st = _series_desc(df[col])
        if not st.get("n"):
            continue
        hab_rows.append([
            col, st["n"], _fmt_num(st["mean"], 1), _fmt_num(st["std"], 1),
            _fmt_num(st["median"], 1), _fmt_num(st["min"], 1), _fmt_num(st["max"], 1),
        ])
    if hab_rows:
        parts.append(_table(
            ["Habitat", "n", "mean", "SD", "median", "min", "max"], hab_rows
        ))

    # 4 Tumor / features
    parts.append("<h2>4. Tumor voxels & feature count</h2>")
    vol_rows = []
    for col in ("TumorVoxels", "Feature_Count", "Align_Verify_Voxels", "Fusion_Units"):
        if col not in df.columns:
            continue
        st = _series_desc(df[col])
        if not st.get("n"):
            continue
        vol_rows.append([
            col, st["n"], _fmt_num(st["mean"], 1), _fmt_num(st["std"], 1),
            _fmt_num(st["median"], 1), _fmt_num(st["min"], 0), _fmt_num(st["max"], 0),
        ])
    if vol_rows:
        parts.append(_table(
            ["Variable", "n", "mean", "SD", "median", "min", "max"], vol_rows
        ))

    # 5 汇总统计（替代患者明细）：数值列 n/mean/SD/median/min/max
    parts.append("<h2>5. Continuous variables — descriptive statistics</h2>")
    # 排除路径/长文本类
    skip_sub = ("path", "source", "time", "list", "status", "method", "modality",
                "spacing", "size", "dims", "patient", "id")
    cont_rows = []
    for col in df.columns:
        cl = str(col).lower()
        if any(s in cl for s in skip_sub):
            continue
        st = _series_desc(df[col])
        if not st.get("n") or st["n"] < max(3, int(0.5 * n)):
            # 跳过几乎全缺失或非数值
            if not st.get("n") or st["n"] < 3:
                continue
        # 已在上面章节出现的列仍可进汇总，便于一览
        cont_rows.append([
            col, st["n"], _fmt_num(st["mean"], 3), _fmt_num(st["std"], 3),
            _fmt_num(st["median"], 3), _fmt_num(st["min"], 3), _fmt_num(st["max"], 3),
        ])
    # 排序：n 降序
    cont_rows.sort(key=lambda r: -int(r[1]) if str(r[1]).isdigit() else 0)
    if cont_rows:
        parts.append(_table(
            ["Variable", "n", "mean", "SD", "median", "min", "max"], cont_rows,
            caption="All numeric QC fields with ≥3 non-missing values",
        ))
    else:
        parts.append("<p class='muted'>No continuous statistics available.</p>")

    return parts


def _feat_like(name: str) -> bool:
    s = str(name).lower()
    return s.startswith(("h0_", "h1_", "h2_", "h3_", "original_", "wavelet_", "log-", "log_"))


def write_project_data_qc(
    input_xlsx: str | Path,
    settings: Dict[str, Any] | None = None,
    out_dir: str | Path | None = None,
    out_html: str | Path = "qc.html",
    label: str | None = None,
) -> Dict[str, Any]:
    """整表 QC：临床 / 组学 / 对齐 / ICC / 影像质控。
    主队列写 ref/qc.html。外验走 write_external_qc_reports（ref/qc-ext-*.html）。
    """
    from modules.utils import dry_run as _dry
    settings = settings or {}
    xlsx = Path(input_xlsx)
    if not xlsx.is_absolute():
        xlsx = _project_root() / xlsx
    proj = Path(out_dir) if out_dir else _project_root()
    if proj.name.lower() == "ref":
        proj = proj.parent
    if not proj.is_absolute():
        proj = _project_root() / proj
    if _dry.exit_after_plan("u_qc", [
        f"xlsx={xlsx}",
        f"out_dir={proj}",
        f"out_html={out_html}",
        "write ref/qc.html (skipped)",
    ]):
        return {"ok": True, "dry_run": True, "xlsx": str(xlsx), "out_dir": str(proj)}
    relocate_legacy_qc_html(proj)
    out = Path(out_html)
    if out.is_absolute() and out.parent.name.lower() == "ref":
        pass
    else:
        out = resolve_qc_html_path(out_html, proj)

    xl = pd.ExcelFile(xlsx)
    sheets = [str(s) for s in xl.sheet_names]

    def _hit(name: str) -> str | None:
        if not name:
            return None
        if name in sheets:
            return name
        low = str(name).lower()
        return next((s for s in sheets if s.lower() == low), None)

    clin_sheet = _hit(str(settings.get("CLIN_SHEET") or "cln"))
    rad_sheet = _hit(str(settings.get("RAD_SHEET") or "rad"))
    qc_sheet = _hit(str(settings.get("QC_SHEET") or "qc"))
    icc_sheet = _hit(str(settings.get("ICC_SHEET") or "icc"))
    clin_id = str(settings.get("CLIN_ID_COL") or "Patient_ID")
    rad_id = str(settings.get("RAD_ID_COL") or "Patient_ID")
    outcomes = [str(c).strip() for c in (settings.get("OUTCOME_COLS") or []) if str(c).strip()]
    skip_clin = {
        str(x).strip()
        for x in (settings.get("SKIP_CLIN_FEATURES") or [])
        if str(x).strip()
    }
    skip_clin.update({"Patient_ID", "Group", "record_id", clin_id})
    lab = str(settings.get("LABEL_COL") or "").strip()
    if lab:
        skip_clin.add(lab)
    skip_clin.update(outcomes)

    cdf = xl.parse(clin_sheet) if clin_sheet else pd.DataFrame()
    rdf = xl.parse(rad_sheet) if rad_sheet else pd.DataFrame()
    qdf = xl.parse(qc_sheet) if qc_sheet else pd.DataFrame()
    idf = xl.parse(icc_sheet) if icc_sheet else pd.DataFrame()

    if clin_id not in cdf.columns and len(cdf.columns):
        for cand in ("Patient_ID", "PatientID", "patient_id", "Name"):
            if cand in cdf.columns:
                clin_id = cand
                break
        else:
            clin_id = str(cdf.columns[0])
    if rad_id not in rdf.columns and len(rdf.columns):
        for cand in ("Patient_ID", "PatientID", "patient_id", "Name"):
            if cand in rdf.columns:
                rad_id = cand
                break
        else:
            first = str(rdf.columns[0])
            rad_id = first if not _feat_like(first) else rad_id

    cset = (
        {str(x).strip() for x in cdf[clin_id].dropna().tolist() if str(x).strip()}
        if clin_id in cdf.columns else set()
    )
    rset = (
        {str(x).strip() for x in rdf[rad_id].dropna().tolist() if str(x).strip()}
        if rad_id in rdf.columns else set()
    )
    overlap = cset & rset if cset and rset else set()

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    tag = str(label or "").strip()
    title = f"Project data QC — {xlsx.name}" + (f" ({tag})" if tag else "")
    kind = f"外部验证 QC · {tag}" if tag else "整体数据 QC"
    parts: list[str] = [
        f"<h1>{title}</h1>",
        f"<p class='muted'>{kind} · {stamp} · {xlsx}</p>",
    ]

    sheet_rows = []
    for s in sheets:
        try:
            d0 = xl.parse(s, nrows=0)
            n = int(len(xl.parse(s, usecols=[0])))
            sheet_rows.append([s, n, int(len(d0.columns))])
        except Exception:
            sheet_rows.append([s, "?", "?"])
    parts.append("<h2>0. Workbook</h2>")
    parts.append(_html_table(["Sheet", "n rows", "n cols"], sheet_rows))

    parts.append("<h2>1. ID alignment</h2>")
    parts.append(_html_table(
        ["Side", "ID col", "n unique"],
        [
            ["clin", clin_id if not cdf.empty else "—", len(cset)],
            ["rad", rad_id if not rdf.empty else "—", len(rset)],
            ["overlap", "—", len(overlap)],
            ["clin only", "—", len(cset - rset)],
            ["rad only", "—", len(rset - cset)],
        ],
    ))

    if outcomes and not cdf.empty:
        parts.append("<h2>2. Outcome completeness</h2>")
        orows = []
        for col in outcomes:
            if col not in cdf.columns:
                orows.append([col, "MISSING COLUMN", "—", "—", "—"])
                continue
            s = cdf[col]
            n_miss = int(s.isna().sum())
            n_ok = int(s.notna().sum())
            vc = s.astype(str).where(s.notna(), "NA").value_counts()
            bits = ", ".join(f"{k}={int(v)}" for k, v in vc.head(12).items())
            orows.append([col, n_ok, n_miss, f"{100.0 * n_miss / max(len(s), 1):.1f}%", bits])
        parts.append(_html_table(
            ["Outcome", "n observed", "n missing", "missing %", "levels"],
            orows,
        ))

    if not cdf.empty:
        parts.append("<h2>3. Clinical missingness</h2>")
        crows = []
        n_c = len(cdf)
        for col in cdf.columns:
            if str(col) in skip_clin:
                continue
            n_miss = int(cdf[col].isna().sum())
            pct = 100.0 * n_miss / max(n_c, 1)
            flag = "warn" if pct > 50 else ""
            crows.append([
                (str(col), flag),
                n_c - n_miss,
                n_miss,
                (f"{pct:.1f}%", flag),
            ])
        crows.sort(key=lambda r: -int(r[2]) if isinstance(r[2], int) else 0)
        n_hi = sum(1 for r in crows if isinstance(r[0], tuple) and r[0][1] == "warn")
        parts.append(
            f"<p class='muted'>临床列 {len(crows)} · 缺失&gt;50% 的 {n_hi} 列（红底；建模前会丢掉）</p>"
        )
        parts.append(_html_table(
            ["Column", "n observed", "n missing", "missing %"],
            crows[:80],
            caption="Top 80 by missingness; skip/ID/outcome columns omitted",
        ))

    if not rdf.empty:
        parts.append("<h2>4. Radiomics matrix</h2>")
        feat_cols = [c for c in rdf.columns if _feat_like(c)]
        if not feat_cols:
            feat_cols = [c for c in rdf.columns if str(c) not in {rad_id, "Name", "Group", "Patient_ID"}]
        n_r = len(rdf)
        if feat_cols:
            miss_by_row = rdf[feat_cols].isna().mean(axis=1)
            miss_by_col = rdf[feat_cols].isna().mean(axis=0)
            n_empty_feat = int((miss_by_col >= 1.0).sum())
            n_hi_feat = int((miss_by_col > 0.5).sum())
            parts.append(_html_table(
                ["Item", "Value"],
                [
                    ["n patients", n_r],
                    ["n feature columns", len(feat_cols)],
                    ["features all-missing", n_empty_feat],
                    ["features missing &gt;50%", n_hi_feat],
                    ["row missing mean %", f"{100.0 * float(miss_by_row.mean()):.2f}"],
                    ["row missing max %", f"{100.0 * float(miss_by_row.max()):.2f}"],
                ],
            ))
        else:
            parts.append("<p class='muted'>No radiomics feature columns detected.</p>")

    if not idf.empty:
        parts.append("<h2>5. ICC</h2>")
        thr = float(settings.get("ICC_THRESHOLD") or 0.75)
        intra_c = str(settings.get("ICC_INTRA_COL") or "intra_ICC")
        inter_c = str(settings.get("ICC_INTER_COL") or "inter_ICC")
        row = [["n rows", len(idf)], ["threshold", thr]]
        if intra_c in idf.columns and inter_c in idf.columns:
            intra = pd.to_numeric(idf[intra_c], errors="coerce")
            inter = pd.to_numeric(idf[inter_c], errors="coerce")
            both = (intra >= thr) & (inter >= thr)
            row.extend([
                [f"both ≥ {thr}", int(both.sum())],
                ["missing ICC", int((intra.isna() | inter.isna()).sum())],
            ])
        parts.append(_html_table(["Item", "Value"], row))

    if not qdf.empty:
        parts.append("<h2>6. Imaging pipeline QC</h2>")
        parts.append(f"<p class='muted'>From sheet {qc_sheet!r} · N = {len(qdf)}</p>")
        parts.extend(_imaging_qc_sections(qdf))

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_qc_html_shell(title, parts), encoding="utf-8")
    print(f"  ✓ Project data QC → {out}  (clin={len(cdf)} rad={len(rdf)} overlap={len(overlap)})")
    return {
        "html": str(out),
        "n_clin": int(len(cdf)),
        "n_rad": int(len(rdf)),
        "n_overlap": int(len(overlap)),
        "n_qc": int(len(qdf)),
        "sheets": sheets,
        "label": tag,
    }


def _pick_named_sheet(sheets: list[str], *cands: str) -> str:
    have = [str(s) for s in sheets]
    low = {s.lower(): s for s in have}
    for raw in cands:
        n = str(raw or "").strip()
        if not n:
            continue
        if n in have:
            return n
        hit = low.get(n.lower())
        if hit:
            return hit
    return ""


def write_external_qc_reports(
    settings: Dict[str, Any] | None = None,
    *,
    dest: str | Path | None = None,
    skip_existing: bool = False,
    input_dir: str | Path | None = None,
) -> list[dict]:
    """为每条 EXTERNAL_TESTS 写 ref/qc-ext[-name].html。永不写 qc.html。"""
    settings = dict(settings or {})
    proj = Path(dest) if dest else _project_root()
    if not proj.is_absolute():
        proj = _project_root() / proj
    if proj.name.lower() == "ref":
        proj = proj.parent
    relocate_legacy_qc_html(proj)
    root = qc_output_dir(proj)
    base = Path(input_dir) if input_dir else proj
    written: list[dict] = []
    used_names: set[str] = set()
    for spec in _external_specs_from_settings(settings):
        fname = external_qc_html_name(spec["name"])
        if fname.lower() == QC_HTML_MAIN:
            fname = QC_HTML_EXT
        if fname.lower() in used_names:
            token = external_qc_html_name(spec["name"]).removesuffix(".html")
            n = 2
            while f"{token}-{n}.html".lower() in used_names:
                n += 1
            fname = f"{token}-{n}.html"
        used_names.add(fname.lower())
        out_path = root / fname
        if skip_existing and out_path.is_file() and out_path.stat().st_size > 200:
            print(f"  exists → skip  ({out_path})")
            written.append({
                "name": spec["name"],
                "html": str(out_path),
                "skipped": True,
            })
            continue
        src = Path(spec["file"])
        if not src.is_absolute():
            src = base / src
        if not src.is_file():
            print(f"  ⚠ external QC [{spec['name']}] missing file: {src}")
            written.append({
                "name": spec["name"],
                "file": str(src),
                "error": f"missing file: {src}",
            })
            continue
        try:
            xl = pd.ExcelFile(src)
            sheets = [str(s) for s in xl.sheet_names]
            xl.close()
        except Exception as e:
            print(f"  ⚠ external QC [{spec['name']}] open failed: {e}")
            written.append({"name": spec["name"], "file": str(src), "error": str(e)})
            continue
        ext_settings = dict(settings)
        ext_settings["CLIN_SHEET"] = spec["clin_sheet"]
        ext_settings["RAD_SHEET"] = spec["rad_sheet"]
        if spec.get("rad_id_col"):
            ext_settings["RAD_ID_COL"] = spec["rad_id_col"]
        ext_settings["QC_SHEET"] = _pick_named_sheet(
            sheets, spec.get("qc_sheet", ""), settings.get("QC_SHEET"), "qc",
        )
        ext_settings["ICC_SHEET"] = _pick_named_sheet(
            sheets, spec.get("icc_sheet", ""), settings.get("ICC_SHEET"), "icc",
        )
        info = write_project_data_qc(
            src,
            settings=ext_settings,
            out_dir=proj,
            out_html=fname,
            label=f"external:{spec['name']}",
        )
        info["name"] = spec["name"]
        info["qc_html"] = qc_html_rel(fname)
        written.append(info)
    return written


def write_qc_report(
    input_xlsx: str | Path,
    out_html: str | Path = "qc.html",
    sheet: str = "qc",
) -> Path:
    """
    从 Excel qc sheet 生成 ref/qc.html。
    供 pipeline 主循环前调用一次。
    """
    df = load_qc_from_excel(input_xlsx, sheet=sheet)
    html = build_qc_html_from_sheet(
        df, title=f"Imaging pipeline QC — {Path(input_xlsx).name} [{sheet}]"
    )
    out = resolve_qc_html_path(out_html)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"  ✓ QC report → {out}  (N={len(df)})")
    return out


def main(argv: Optional[list] = None) -> None:
    parser = argparse.ArgumentParser(description="Habitat pipeline QC from lung_xlm_summary.csv")
    parser.add_argument("--input", default=DEFAULT_INPUT, help="Path to lung_xlm_summary.csv")
    parser.add_argument("--out", default=DEFAULT_OUT, help="Output directory for QC artifacts")
    args = parser.parse_args(argv)

    result = run_qc(args.input, args.out)
    s = result["summary"]
    print("=== u_qc summary ===")
    print(f"N = {result['n']}")
    print("Pipeline:", s["pipeline"])
    print("Habitat:", {k: s["habitat"][k] for k in s["habitat"] if k != "habitat_mode_counts"})
    print("Radiomics verified:", s["radiomics"].get("radiomics_verified_n"))
    print("Outputs:")
    for k, v in {**result["tables"], **result["figures"], "report_md": result["report_md"]}.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
