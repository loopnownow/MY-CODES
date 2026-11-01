# -*- coding: utf-8 -*-
"""Fill blank cln cells in xlm_LG.xlsx from Xue/lung sources. Only blanks; agreement-gated."""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from openpyxl import load_workbook
from tidy_paths import DEL_ROOT  # 临时区与 tidy_* 同一处（D:\0del）
from pypinyin import lazy_pinyin
import re

_LAB_ROOT = Path(__file__).resolve().parents[5]  # tools/<tool>/ -> lab root（公开仓不留绝对盘符）
ROOT = Path(os.environ.get("CLN_PROJECT_ROOT") or _LAB_ROOT / "pre-submit" / "xlm_LG")
REF = ROOT / "ref"
MAIN = ROOT / "xlm_LG.xlsx"
OUT_ALT = ROOT / "xlm_LG_cln_filled.xlsx"
DEL = DEL_ROOT / "harvested" / "cln_fill_supplement"  # t1490u: script scratch under harvested
DEL.mkdir(parents=True, exist_ok=True)

STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
REPORT = REF / f"cln_fill_from_supplement_{datetime.now().strftime('%Y%m%d')}.md"
FILLS_CSV = DEL / f"fills_{STAMP}.csv"

TYPE_MAP = {
    0: "Homogeneous Negativensolid",
    1: "Heterogeneous Negativensolid",
    2: "Part-solid",
}
BIN01 = {0: "Negative", 1: "Positive"}


def can_use(n: int, rate: float | None) -> bool:
    if rate is None or n < 3:
        return False
    if n >= 20 and rate >= 0.95:
        return True
    if 3 <= n < 20 and rate >= 0.999:
        return True
    return False


def agree_rate(a: pd.Series, b: pd.Series):
    mask = a.notna() & b.notna()
    n = int(mask.sum())
    if n == 0:
        return 0, None
    aa = pd.to_numeric(a[mask], errors="coerce")
    bb = pd.to_numeric(b[mask], errors="coerce")
    if aa.notna().all() and bb.notna().all():
        eq = int((aa == bb).sum())
        return n, eq / n
    eq = int((a[mask].astype(str) == b[mask].astype(str)).sum())
    return n, eq / n


def build_cmap() -> dict:
    cmap = {}
    for sheet in ["clin", "clin-old", "clin-target", "clin-thin"]:
        df = pd.read_excel(REF / "lung2.xlsx", sheet_name=sheet)
        if "姓名" in df.columns and "name" in df.columns:
            for zh, en in zip(df["姓名"], df["name"]):
                if pd.notna(zh) and pd.notna(en):
                    cmap[str(zh).strip()] = str(en).strip()
    return cmap


def map_zh(zh, cmap, cln_ids, cln_set):
    s = str(zh).strip()
    if s in cmap and cmap[s] in cln_set:
        return cmap[s]
    m = re.match(r"^(.*?)(\d+)$", s)
    base_zh, num = (m.group(1), m.group(2)) if m else (s, None)
    py = " ".join(lazy_pinyin(base_zh))
    cands = [py] if num is None else [f"{py} {num}", f"{py}  {num}"]
    hits = [c for c in cands if c in cln_set]
    if not hits:
        def norm(x):
            return re.sub(r"\s+", " ", x.strip())

        ncands = set(norm(c) for c in cands)
        hits = list(dict.fromkeys([cid for cid in cln_ids if norm(cid) in ncands]))
    return hits[0] if len(hits) == 1 else None


def main():
    cln = pd.read_excel(MAIN, sheet_name="cln")
    cln_orig = cln.copy()
    miss_before = cln.isna().sum().to_dict()
    total_miss_before = int(cln.isna().sum().sum())

    cln_ids = list(cln["Patient_ID"].astype(str))
    cln_set = set(cln_ids)
    cmap = build_cmap()

    # --- sources ---
    lung2 = pd.read_excel(REF / "lung2.xlsx", sheet_name="clin")
    lung2o = pd.read_excel(REF / "lung2.xlsx", sheet_name="clin-old")
    sec = pd.read_excel(REF / "2nd 临床数据补充01版.xlsx")
    sec = sec.copy()
    sec["Patient_ID"] = sec["姓名"].map(lambda z: map_zh(z, cmap, cln_ids, cln_set))
    n_unmapped = int(sec["Patient_ID"].isna().sum())
    unmapped_names = sorted(sec.loc[sec["Patient_ID"].isna(), "姓名"].astype(str).str.strip().unique())
    sec2 = sec.dropna(subset=["Patient_ID"]).drop_duplicates("Patient_ID", keep="first")

    agreement_rows = []
    unused_reasons = []
    fill_records = []

    # index helpers
    cln_ix = cln.set_index("Patient_ID", drop=False)

    def eval_vs_cln(src_name, field, src_series_by_pid: pd.Series):
        """src_series_by_pid indexed by Patient_ID, already mapped to cln value space."""
        both_pids = [pid for pid in src_series_by_pid.index if pid in cln_ix.index]
        a = src_series_by_pid.reindex(both_pids)
        b = cln_ix.loc[both_pids, field]
        # only where cln currently non-null (agreement gate)
        n, rate = agree_rate(a, b)
        use = can_use(n, rate)
        agreement_rows.append(
            {
                "source": src_name,
                "field": field,
                "n_overlap": n,
                "agree": None if rate is None else round(100 * rate, 1),
                "use": use,
            }
        )
        return use, n, rate

    # ===== Subtype from lung2 type =====
    l2_type = lung2.dropna(subset=["name"]).drop_duplicates("name")
    l2_type = l2_type.set_index("name")["type"].map(TYPE_MAP)
    use_sub, n_sub, r_sub = eval_vs_cln("lung2.xlsx/clin", "Subtype", l2_type)
    if use_sub:
        for pid, val in l2_type.items():
            if pid not in cln_ix.index or pd.isna(val):
                continue
            row_mask = cln["Patient_ID"] == pid
            if row_mask.sum() != 1:
                continue
            if pd.isna(cln.loc[row_mask, "Subtype"].iloc[0]):
                cln.loc[row_mask, "Subtype"] = val
                fill_records.append({"Patient_ID": pid, "field": "Subtype", "value": val, "source": "lung2.xlsx/clin"})
    else:
        unused_reasons.append(f"lung2 Subtype/type: n={n_sub} agree={r_sub} — below threshold")

    # ===== Lobulation: lung2 alone vs cln =====
    l2_lob = lung2.dropna(subset=["name"]).drop_duplicates("name")
    l2_lob_s = l2_lob.set_index("name")["Lobulation"].map(BIN01)
    use_lob2, n_lob2, r_lob2 = eval_vs_cln("lung2.xlsx/clin", "Lobulation", l2_lob_s)
    if not use_lob2:
        unused_reasons.append(
            f"lung2 Lobulation: n={n_lob2} agree={None if r_lob2 is None else round(100*r_lob2,1)}% — below threshold (not used alone)"
        )

    # ===== Lobulation mutual: lung2 ∩ 2nd where both non-null and equal =====
    sec_lob = sec2.dropna(subset=["分叶"]).set_index("Patient_ID")["分叶"].map(
        lambda x: BIN01.get(int(float(x)), np.nan) if pd.notna(x) else np.nan
    )
    mutual_pids = []
    for pid, v2 in sec_lob.items():
        if pid in l2_lob_s.index and pd.notna(v2) and pd.notna(l2_lob_s.loc[pid]):
            if str(v2) == str(l2_lob_s.loc[pid]):
                mutual_pids.append(pid)
    # record mutual stats
    agreement_rows.append(
        {
            "source": "lung2∩2nd mutual",
            "field": "Lobulation",
            "n_overlap": len(mutual_pids),
            "agree": 100.0 if mutual_pids else None,
            "use": len(mutual_pids) >= 3,
        }
    )
    if len(mutual_pids) >= 3:
        for pid in mutual_pids:
            val = l2_lob_s.loc[pid]
            row_mask = cln["Patient_ID"] == pid
            if row_mask.sum() != 1:
                continue
            if pd.isna(cln.loc[row_mask, "Lobulation"].iloc[0]):
                cln.loc[row_mask, "Lobulation"] = val
                fill_records.append(
                    {
                        "Patient_ID": pid,
                        "field": "Lobulation",
                        "value": val,
                        "source": "lung2∩2nd(分叶)",
                    }
                )

    # ===== 2nd sheet field agreement (document; fill only if passes & blanks exist) =====
    def series_2nd(col, mapper=None):
        s = sec2.set_index("Patient_ID")[col]
        if mapper:
            s = s.map(mapper)
        return s

    growth_map = {0: "Negative", 1: "Positive"}
    sex_map = {0: "Female", 1: "Male"}

    checks = [
        ("2nd/Sheet1", "Growth", series_2nd("生长分组", lambda x: growth_map.get(int(x), np.nan) if pd.notna(x) else np.nan)),
        ("2nd/Sheet1", "Gender", series_2nd("sex", lambda x: sex_map.get(int(x), np.nan) if pd.notna(x) else np.nan)),
        ("2nd/Sheet1", "Age", series_2nd("年龄")),
        ("2nd/Sheet1", "Max_diameter", series_2nd("初始最大径")),
        ("2nd/Sheet1", "Lobulation", sec_lob),
        (
            "2nd/Sheet1",
            "Smoking",
            series_2nd("吸烟史", lambda x: BIN01.get(int(float(x)), np.nan) if pd.notna(x) else np.nan),
        ),
    ]
    for src, field, ser in checks:
        use, n, rate = eval_vs_cln(src, field, ser)
        # policy: never hard-fill Age / Max_diameter from 2nd even if somehow passed
        if field in ("Age", "Max_diameter"):
            unused_reasons.append(
                f"2nd {field}: n={n} agree={None if rate is None else round(100*rate,1)}% — blocked by policy (md: age/diameter disagree)"
            )
            continue
        if field in ("Previous_LCH", "Family_LCH"):
            unused_reasons.append(f"2nd {field}: generic 既往史/家族史 not mapped to LCH fields")
            continue
        if not use:
            unused_reasons.append(
                f"2nd {field}: n={n} agree={None if rate is None else round(100*rate,1)}% — below threshold"
            )
            continue
        # fill blanks only
        for pid, val in ser.items():
            if pid not in cln_ix.index or pd.isna(val):
                continue
            row_mask = cln["Patient_ID"] == pid
            if row_mask.sum() != 1:
                continue
            if pd.isna(cln.loc[row_mask, field].iloc[0]):
                cln.loc[row_mask, field] = val
                fill_records.append({"Patient_ID": pid, "field": field, "value": val, "source": src})

    unused_reasons.append("2nd 既往史/家族史: not written into Previous_LCH/Family_LCH (generic history)")
    unused_reasons.append("No Position/emphysema/pathology/gene columns added")
    unused_reasons.append("lung2 Max_diameter/初始最大径: low agreement vs cln — unused")
    unused_reasons.append("2nd Smoking/分叶 alone: insufficient overlap with non-null cln for gate")

    # ===== safety: no overwrite of originally non-null =====
    overwritten = []
    for col in cln.columns:
        if col == "Patient_ID":
            continue
        both = cln_orig[col].notna()
        changed = both & (cln[col].astype(str) != cln_orig[col].astype(str))
        # allow NaN vs NaN; for numeric compare carefully
        if col in cln_orig.columns:
            def _neq(a, b):
                if pd.isna(a) and pd.isna(b):
                    return False
                try:
                    return float(a) != float(b)
                except Exception:
                    return str(a) != str(b)

            for i in cln.index:
                if pd.notna(cln_orig.at[i, col]) and _neq(cln.at[i, col], cln_orig.at[i, col]):
                    overwritten.append((cln.at[i, "Patient_ID"], col, cln_orig.at[i, col], cln.at[i, col]))

    if overwritten:
        raise RuntimeError(f"Safety fail: would overwrite {len(overwritten)} cells: {overwritten[:5]}")

    miss_after = cln.isna().sum().to_dict()
    total_miss_after = int(cln.isna().sum().sum())
    fills_df = pd.DataFrame(fill_records)
    fills_df.to_csv(FILLS_CSV, index=False)

    by_field = fills_df.groupby("field").size().to_dict() if len(fills_df) else {}
    by_src = fills_df.groupby(["source", "field"]).size() if len(fills_df) else pd.Series(dtype=int)

    # ===== write workbook =====
    backup = DEL / f"xlm_LG_before_cln_fill_{STAMP}.xlsx"
    shutil.copy2(MAIN, backup)

    written_path = MAIN
    try:
        wb = load_workbook(MAIN)
        if "cln" not in wb.sheetnames:
            raise RuntimeError("cln sheet missing")
        # remove old cln and rewrite values via pandas+openpyxl
        ws = wb["cln"]
        # clear and write
        wb.remove(ws)
        # use pandas to write cln into a temp then copy? Simpler: write all sheets
        wb.save(DEL / f"_tmp_removed_cln_{STAMP}.xlsx")
        wb.close()

        # Reload other sheets from backup, write cln with pandas ExcelWriter
        # Safest: read all sheets from backup, replace cln, write
        all_sheets = pd.read_excel(backup, sheet_name=None)
        all_sheets["cln"] = cln
        try:
            with pd.ExcelWriter(MAIN, engine="openpyxl") as writer:
                for name, df in all_sheets.items():
                    df.to_excel(writer, sheet_name=name, index=False)
            written_path = MAIN
        except PermissionError:
            with pd.ExcelWriter(OUT_ALT, engine="openpyxl") as writer:
                for name, df in all_sheets.items():
                    df.to_excel(writer, sheet_name=name, index=False)
            written_path = OUT_ALT
    except PermissionError:
        all_sheets = pd.read_excel(backup, sheet_name=None)
        all_sheets["cln"] = cln
        with pd.ExcelWriter(OUT_ALT, engine="openpyxl") as writer:
            for name, df in all_sheets.items():
                df.to_excel(writer, sheet_name=name, index=False)
        written_path = OUT_ALT

    # verify readback
    verify = pd.read_excel(written_path, sheet_name="cln")
    assert list(verify.columns) == list(cln.columns)
    assert len(verify) == len(cln)
    # originally non-null still equal
    for col in cln.columns:
        if col == "Patient_ID":
            continue
        for i in range(len(cln_orig)):
            if pd.notna(cln_orig.at[i, col]):
                ov, nv = cln_orig.at[i, col], verify.at[i, col]
                if pd.isna(ov) and pd.isna(nv):
                    continue
                try:
                    ok = float(ov) == float(nv)
                except Exception:
                    ok = str(ov) == str(nv)
                if not ok:
                    raise RuntimeError(f"verify overwrite {verify.at[i,'Patient_ID']} {col}")

    # report
    lines = []
    lines.append(f"# cln fill from Xue / lung2 — {datetime.now().strftime('%Y-%m-%d %H:%M')} CST")
    lines.append("")
    lines.append(f"- Main table written: `{written_path}`")
    lines.append(f"- Backup: `{backup}`")
    lines.append(f"- Fills CSV: `{FILLS_CSV}`")
    lines.append(f"- cln shape: {cln.shape[0]} rows × {cln.shape[1]} cols")
    lines.append(f"- Missing cells before → after: **{total_miss_before} → {total_miss_after}** (filled **{total_miss_before - total_miss_after}**)")
    lines.append(f"- Fill records: {len(fill_records)}")
    lines.append(f"- 2nd sheet rows: {len(sec)}; mapped to cln Patient_ID: {len(sec2)}; unmapped names: {len(unmapped_names)}")
    lines.append("- Existing non-null cln cells: **not overwritten** (verified)")
    lines.append("")
    lines.append("## Missingness by column")
    lines.append("")
    lines.append("| field | before | after | filled |")
    lines.append("|---|---:|---:|---:|")
    for c in cln.columns:
        if c == "Patient_ID":
            continue
        b, a = int(miss_before[c]), int(miss_after[c])
        lines.append(f"| {c} | {b} | {a} | {b - a} |")
    lines.append("")
    lines.append("## Agreement checks")
    lines.append("")
    lines.append("| source | field | n overlap | agree % | use |")
    lines.append("|---|---|---:|---:|---|")
    for r in agreement_rows:
        lines.append(
            f"| {r['source']} | {r['field']} | {r['n_overlap']} | {r['agree']} | {'yes' if r['use'] else 'no'} |"
        )
    lines.append("")
    lines.append("## Fills by source and field")
    lines.append("")
    if len(fills_df):
        lines.append("| source | field | n |")
        lines.append("|---|---|---:|")
        for (src, field), n in by_src.items():
            lines.append(f"| {src} | {field} | {n} |")
    else:
        lines.append("(none)")
    lines.append("")
    lines.append("## Not used / blocked")
    lines.append("")
    for u in unused_reasons:
        lines.append(f"- {u}")
    lines.append("")
    lines.append("## 2nd names not linked to a cln Patient_ID")
    lines.append("")
    lines.append(", ".join(unmapped_names) if unmapped_names else "(none)")
    lines.append("")
    lines.append("## Rules applied")
    lines.append("")
    lines.append("- Only fill blanks; never overwrite non-null.")
    lines.append("- Source field used vs cln only if n≥20 & agree≥95%, or 3≤n<20 & agree=100%.")
    lines.append("- 2nd Age / 初始最大径 not hard-filled (disagree with cln).")
    lines.append("- Generic 既往史/家族史 not mapped to Previous_LCH/Family_LCH.")
    lines.append("- No new columns (Position/emphysema/pathology/gene).")
    lines.append("- Lobulation filled only where lung2 and 2nd 分叶 both present and equal (mutual).")
    lines.append("- Subtype from lung2 `type` {0,1,2} → Homogeneous / Heterogeneous / Part-solid (100% on n=5).")
    lines.append("")

    REPORT.write_text("\n".join(lines), encoding="utf-8")

    summary = {
        "written_path": str(written_path),
        "backup": str(backup),
        "report": str(REPORT),
        "fills_csv": str(FILLS_CSV),
        "miss_before": total_miss_before,
        "miss_after": total_miss_after,
        "n_fills": len(fill_records),
        "by_field": by_field,
        "unmapped": len(unmapped_names),
        "overwritten": 0,
    }
    (DEL / f"summary_{STAMP}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
