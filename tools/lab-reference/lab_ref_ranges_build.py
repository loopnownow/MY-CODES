#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build lab-reference-ranges.xlsx from curated rows + optional inventory quotes.

Peer-review numeric plausibility only — NOT clinical decision support.
Wrap-up 2026-10-19: reclassify auto study_cutoff over-tags → cohort_*.
"""
from __future__ import annotations

import csv
import json
import os
import re
import tempfile
from collections import Counter, defaultdict

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo

from lab_ref_ranges_rows import (
    BANNER,
    DICT_HEADER,
    DICT_ROWS,
    GAPS,
    GAPS_HEADER,
    QC_HEADER,
    QC_RULES,
    QUERY_COLS,
    ROWS,
    SOURCE_HEADER,
    SOURCES,
)

OUT_DIR = Path(os.environ.get("LAB_REF_RANGES") or Path(__file__).resolve().parents[5] / "0doc" / "_library" / "lab-ref-ranges")
OUT_XLSX = os.path.join(OUT_DIR, "lab-reference-ranges.xlsx")
INV_JSONL = os.path.join(OUT_DIR, "inventory", "indicators-from-01-02.jsonl")
SCRATCH_ROOT = os.environ.get("SCRATCH_ROOT") or os.path.join(tempfile.gettempdir(), "lab-scratch")
SCRATCH = os.path.join(SCRATCH_ROOT, "lab-ref-ranges")

HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
HEADER_FONT = Font(color="FFFFFF", bold=True, size=10)
BANNER_FILL = PatternFill("solid", fgColor="FFF2CC")
THIN = Border(
    left=Side(style="thin", color="B0B0B0"),
    right=Side(style="thin", color="B0B0B0"),
    top=Side(style="thin", color="B0B0B0"),
    bottom=Side(style="thin", color="B0B0B0"),
)

# --- reclassify heuristics (wrap-up) ---
RE_CONTROL = re.compile(
    r"对照|control|healthy|健康|正常组|NC\d|Control\d|Comtrol",
    re.I,
)
RE_DISEASE = re.compile(
    r"患者|disease|病例|病例组|观察组|肿瘤|癌|子痫|肝性脑病|\bMHE\b|PE\b|GH\b|"
    r"内膜异位|囊肿|肝硬化|HDP|破裂",
    re.I,
)
RE_THRESH_LANG = re.compile(
    r"阈值|截断|cutoff|threshold|dichotom|分组标准|诊断标准|定义为|"
    r"diagnostic\s+criteria|criteria|高于.{0,12}诊断|低于.{0,12}诊断|"
    r"Youden|截断值|临界值|阳性标准|阴性标准",
    re.I,
)
RE_PVAL = re.compile(r"[Pp]\s*[=＜<]|P值|p值|统计学", re.I)
RE_CORR = re.compile(r"\b[Rr]2?\s*=|\br\s*=|相关|correlation|ICC\s*>=?", re.I)


def _has(x) -> bool:
    return x is not None and str(x).strip() != ""


def _cutoff_float(cut):
    if not _has(cut):
        return None
    s = str(cut).strip().rstrip(".")
    try:
        return float(s)
    except ValueError:
        return None


def looks_pvalue_or_corr(cut, notes: str) -> bool:
    """True when captured 'cutoff' is likely a p-value, r, R2, ICC — not a clinical threshold."""
    v = _cutoff_float(cut)
    notes = notes or ""
    if v is None:
        return False
    if RE_CORR.search(notes) and v < 1.0:
        return True
    if v < 0.05:
        return True
    if v < 1.0 and RE_PVAL.search(notes):
        return True
    # bare 0 / 0.000 from P=0.000
    if v == 0.0 and RE_PVAL.search(notes):
        return True
    return False


def looks_n_equals(cut, notes: str) -> bool:
    if not _has(cut):
        return False
    return bool(
        re.search(
            r"\(\s*n\s*=\s*" + re.escape(str(cut).rstrip(".")) + r"\s*\)",
            notes or "",
            re.I,
        )
    )


def cohort_arm(notes: str) -> str:
    notes = notes or ""
    if RE_CONTROL.search(notes) and not RE_DISEASE.search(notes):
        return "cohort_control"
    if RE_CONTROL.search(notes) and RE_DISEASE.search(notes):
        # Table comparing both — default disease arm for auto quote (curator may split)
        return "cohort_disease"
    if RE_DISEASE.search(notes):
        return "cohort_disease"
    return "cohort_disease"


def reclassify_quote_row(row: dict) -> dict:
    """Move obvious Table-1 descriptive stats out of study_cutoff; keep true thresholds.

    Rules (documented in EXECUTION.md):
      R1 Descriptive present (mean/median/sd/IQR) → cohort_*; clear spurious cutoffs
          that look like p / (n=…) / correlation fragments.
      R2 Cutoff-only + p/r/ICC fragment → clear cutoff; keep as cohort_* display quote
          only if some other numeric remains; else drop caller-side.
      R3 True dichotomization: cutoff present, no descriptive mean/median, AND
          (threshold language OR absolute clinical threshold with ≥/>/≤/< and not R2)
          → keep study_cutoff.
      R4 Pathology stays path_category unless a true IHC % cutoff (R3).
    """
    notes = row.get("notes") or ""
    cut = row.get("cutoff")
    direction = row.get("cutoff_direction") or ""
    has_desc = (
        _has(row.get("mean"))
        or _has(row.get("median"))
        or _has(row.get("sd"))
        or _has(row.get("q1"))
        or _has(row.get("q3"))
    )
    has_ri = _has(row.get("ref_low")) or _has(row.get("ref_high"))
    pv = looks_pvalue_or_corr(cut, notes)
    ne = looks_n_equals(cut, notes)
    thresh = bool(RE_THRESH_LANG.search(notes))
    cat = row.get("category") or ""

    tag = []
    if has_ri:
        row["context"] = "ri_normal"
        tag.append("R-ri")
    elif cat == "pathology" and not (
        _has(cut) and not has_desc and not pv and not ne and (thresh or (direction in (">", "≥", "<", "≤") and (_cutoff_float(cut) or 0) >= 1))
    ):
        row["context"] = "path_category"
        if pv or ne:
            row["cutoff"] = ""
            row["cutoff_direction"] = ""
            tag.append("R4-path-clear-spurious")
        else:
            tag.append("R4-path")
    elif has_desc:
        # R1 Table-1 stats
        row["context"] = cohort_arm(notes)
        if _has(cut) and (pv or ne or not thresh):
            # Spurious cutoff beside descriptive stats (p-value, n=, or bare = from table)
            row["cutoff"] = ""
            row["cutoff_direction"] = ""
            tag.append("R1-clear-spurious-cut")
        elif _has(cut) and thresh:
            # Rare: table has both descriptive and explicit threshold language — keep cutoff
            # but context stays cohort (stats describe the sample)
            tag.append("R1-keep-cut-in-cohort")
        else:
            tag.append("R1-desc")
    elif _has(cut) and (pv or ne) and not thresh:
        # R2 cutoff-only spurious
        row["cutoff"] = ""
        row["cutoff_direction"] = ""
        row["context"] = cohort_arm(notes)
        tag.append("R2-spurious-cut-only")
    elif _has(cut) and not has_desc and not pv and not ne:
        # R3 true dichotomization / diagnostic criteria
        v = _cutoff_float(cut)
        has_unit = _has(row.get("unit_as_reported"))
        # Keep when: explicit threshold language, OR inequality with unit / clinically large absolute
        keep = False
        if thresh:
            keep = True
        elif direction in (">", "≥", "<", "≤") and (has_unit or (v is not None and v >= 10)):
            keep = True
        elif direction == "=" and thresh:
            keep = True
        if keep:
            row["context"] = "study_cutoff"
            tag.append("R3-keep-cutoff")
        else:
            row["cutoff"] = ""
            row["cutoff_direction"] = ""
            row["context"] = cohort_arm(notes)
            tag.append("R2-weak-cut-cleared")
    else:
        # fallback — never promote bare leftovers to study_cutoff
        if has_desc:
            row["context"] = cohort_arm(notes)
            tag.append("R-fallback-desc")
        elif _has(cut):
            row["cutoff"] = ""
            row["cutoff_direction"] = ""
            row["context"] = cohort_arm(notes)
            tag.append("R-fallback-clear-cut")
        else:
            row["context"] = cohort_arm(notes)
            tag.append("R-fallback-cohort")

    if tag:
        prefix = "[reclass:" + ",".join(tag) + "] "
        if not notes.startswith("[reclass:"):
            row["notes"] = (prefix + notes)[:400]
    row["check_role"] = (
        "display_only" if row["context"] == "path_category" else "claim_crosscheck"
    )
    return row


def parse_rows():
    keys = [k for k, _h, _w in QUERY_COLS]
    out = []
    for line in ROWS.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = re.split(r"\|(?=[a-z][a-z0-9_]*=)", line)
        row = {k: "" for k in keys}
        row["record_id"] = parts[0].strip()
        for chunk in parts[1:]:
            if not chunk.strip():
                continue
            key, _sep, value = chunk.partition("=")
            key = key.strip()
            if key not in row:
                raise KeyError(f"unknown key '{key}' in {row['record_id']}")
            row[key] = value.strip()
        out.append(row)
    return out


def load_inventory_quotes(limit_per_canon=2):
    """Pull up to N quoted numeric mentions per canonical into cohort/cutoff rows."""
    if not os.path.isfile(INV_JSONL):
        return []
    by = defaultdict(list)
    with open(INV_JSONL, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            m = json.loads(line)
            if not m.get("numeric_raw"):
                continue
            by[m["canonical_id"]].append(m)

    keys = [k for k, _h, _w in QUERY_COLS]
    extra = []
    for cid, items in sorted(by.items()):
        # Prefer true descriptive stats and RI; deprioritize bare cutoff (often p-values)
        items = sorted(
            items,
            key=lambda x: (
                0 if x.get("mean") or x.get("median") else 1,
                0 if x.get("ref_low") else 1,
                0 if x.get("cutoff") else 1,
            ),
        )
        for m in items[:limit_per_canon]:
            prefix = {"lab": "LAB", "clinical": "CLN", "pathology": "PTH", "score": "CLN"}.get(
                m.get("category", "lab"), "LAB"
            )
            rid = f"{prefix}90-{abs(hash(m['source_path'] + m.get('snippet',''))) % 10000:04d}"

            ctx = m.get("context_guess") or "cohort_disease"
            if m.get("ref_low"):
                ctx = "ri_normal"
            elif m.get("cutoff"):
                ctx = "study_cutoff"
            elif m.get("category") == "pathology":
                ctx = "path_category"

            row = {k: "" for k in keys}
            row.update(
                {
                    "record_id": rid,
                    "canonical_id": cid,
                    "name_en": m.get("name_en") or "",
                    "name_zh": m.get("name_zh") or "",
                    "aliases": m.get("alias_seen") or "",
                    "category": m.get("category") or "",
                    "unit_as_reported": m.get("unit_as_reported") or "",
                    "context": ctx,
                    "condition": "from local bank quote — see snippet in notes",
                    "population": "unspecified",
                    "mean": m.get("mean") or "",
                    "sd": m.get("sd") or "",
                    "median": m.get("median") or "",
                    "q1": m.get("q1") or "",
                    "q3": m.get("q3") or "",
                    "ref_low": m.get("ref_low") or "",
                    "ref_high": m.get("ref_high") or "",
                    "cutoff": m.get("cutoff") or "",
                    "cutoff_direction": m.get("cutoff_direction") or "",
                    "source_tier": m.get("source_tier") or "",
                    "source_path_or_cite": m.get("source_path") or "",
                    "source_locus": m.get("source_locus") or "",
                    "extracted_at_shanghai": m.get("extracted_at_shanghai") or "",
                    "curator": "auto-inventory",
                    "confidence": "quoted",
                    "check_role": "claim_crosscheck" if ctx != "path_category" else "display_only",
                    "notes": (m.get("snippet") or "")[:300],
                }
            )
            row = reclassify_quote_row(row)
            # Drop rows that lost all numerics after clearing spurious cutoffs
            if not any(
                _has(row.get(k))
                for k in (
                    "mean",
                    "sd",
                    "median",
                    "q1",
                    "q3",
                    "cutoff",
                    "ref_low",
                    "ref_high",
                    "n",
                )
            ):
                continue
            extra.append(row)
    return extra


def style_header(ws, row_idx=1):
    for cell in ws[row_idx]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(vertical="center", horizontal="center", wrap_text=True)


def add_sheet(wb, title, header, rows, widths=None, freeze="A2", table_name=None, banner=False):
    ws = wb.create_sheet(title)
    start = 1
    if banner:
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(header))
        c = ws.cell(1, 1, BANNER)
        c.fill = BANNER_FILL
        c.font = Font(bold=True, size=10, color="833C0C")
        c.alignment = Alignment(wrap_text=True, vertical="center")
        ws.row_dimensions[1].height = 36
        start = 2
    for col, h in enumerate(header, 1):
        ws.cell(start, col, h)
    style_header(ws, start)
    for r in rows:
        if isinstance(r, dict):
            ws.append([r.get(h, "") for h in header])
        else:
            ws.append(list(r))
    for idx in range(len(header)):
        ws.column_dimensions[get_column_letter(idx + 1)].width = (widths or {}).get(idx, 14)
    ws.freeze_panes = f"A{start + 1}"
    n_data = len(rows)
    if n_data and table_name:
        ref = f"A{start}:{get_column_letter(len(header))}{start + n_data}"
        tbl = Table(displayName=table_name, ref=ref)
        tbl.tableStyleInfo = TableStyleInfo(name="TableStyleLight9", showRowStripes=True)
        ws.add_table(tbl)
    elif n_data:
        ws.auto_filter.ref = f"A{start}:{get_column_letter(len(header))}{start + n_data}"
    ws.row_dimensions[start].height = 32
    return ws


def load_alias_map():
    path = os.path.join(OUT_DIR, "alias-map.tsv")
    rows = []
    if os.path.isfile(path):
        with open(path, encoding="utf-8-sig") as fh:
            r = csv.reader(fh, delimiter="\t")
            header = next(r)
            for line in r:
                rows.append(line)
        return header, rows
    return ["alias", "canonical_id", "category", "name_en", "name_zh", "docs_hit", "notes"], []


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(SCRATCH, exist_ok=True)

    curated = parse_rows()
    quotes = load_inventory_quotes(limit_per_canon=2)
    seen = set()
    all_rows = []
    for r in curated + quotes:
        if r["record_id"] in seen:
            r["record_id"] = r["record_id"] + "x"
        seen.add(r["record_id"])
        all_rows.append(r)

    wb = Workbook()
    default = wb.active
    wb.remove(default)

    q_header = [k for k, _h, _w in QUERY_COLS]
    q_widths = {i: w for i, (_k, _h, w) in enumerate(QUERY_COLS)}
    add_sheet(
        wb,
        "01_query",
        q_header,
        all_rows,
        widths=q_widths,
        freeze="A3",
        table_name="T01_query",
        banner=True,
    )

    a_header, a_rows = load_alias_map()
    add_sheet(wb, "02_alias_map", a_header, a_rows, table_name="T02_alias", banner=True)
    add_sheet(wb, "03_sources", SOURCE_HEADER, SOURCES, table_name="T03_sources", banner=True)
    add_sheet(wb, "04_gaps", GAPS_HEADER, GAPS, table_name="T04_gaps", banner=True)
    add_sheet(wb, "05_dictionary", DICT_HEADER, DICT_ROWS, table_name="T05_dict", banner=True)
    add_sheet(wb, "06_qc_rules", QC_HEADER, QC_RULES, table_name="T06_qc", banner=True)

    wb.save(OUT_XLSX)
    print(f"Wrote {OUT_XLSX}")
    print(f"  curated={len(curated)} inventory_quotes={len(quotes)} total={len(all_rows)}")

    ctx = Counter(r["context"] for r in all_rows)
    conf = Counter(r["confidence"] for r in all_rows)
    print("  by context:", dict(ctx))
    print("  by confidence:", dict(conf))

    with open(os.path.join(SCRATCH, "build-summary.json"), "w", encoding="utf-8") as fh:
        json.dump(
            {
                "curated": len(curated),
                "inventory_quotes": len(quotes),
                "total_query_rows": len(all_rows),
                "by_context": dict(ctx),
                "by_confidence": dict(conf),
                "gaps": len(GAPS),
                "wrap": "2026-10-19-reclass",
            },
            fh,
            ensure_ascii=False,
            indent=2,
        )


if __name__ == "__main__":
    main()

