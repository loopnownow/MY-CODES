# -*- coding: utf-8 -*-
"""Align xlm_LG.xlsx cln rows to rad patient_id and fill empty clinical cells.

Existing cln values are copied unchanged. New rad rows are filled only from
reference columns whose labels agree with the current cln on overlapping IDs.
Exact ID text is kept (double-space nodule ids are distinct). Fuzzy ID
normalization is not used: it would collapse those pairs.

    python D:\\0Grok\\0doc\\_code\\02-data-processing/clean-data\\align_cln_to_rad.py           # report only
    python D:\\0Grok\\0doc\\_code\\02-data-processing/clean-data\\align_cln_to_rad.py --apply   # backup + write cln
"""
from __future__ import annotations

import argparse
import re
import shutil
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook

from tidy_paths import DEL_ROOT  # 临时区与 tidy_* 同一处（D:\0del）

_LAB_ROOT = Path(__file__).resolve().parents[5]  # tools/<tool>/ -> lab root（公开仓不留绝对盘符）
ROOT = Path(os.environ.get("CLN_PROJECT_ROOT") or _LAB_ROOT / "pre-submit" / "xlm_LG")
XLSX = ROOT / "xlm_LG.xlsx"
REF = ROOT / "ref"
DEL = DEL_ROOT  # 备份/中间产物统一落 D:\0del（{DEL}；DEL_ROOT 环境变量可覆盖）

COLS = [
    "Patient_ID", "Growth", "Gender", "Age", "Diameter", "Max_diameter",
    "Subtype", "Pleural_retraction", "Spiculation", "Lobulation",
    "Smoking", "Previous_LCH", "Family_LCH",
]
INT_COLS = {"Age", "Diameter", "Max_diameter"}

GROWTH = {"0": "Negative", "1": "Positive"}
SEX = {"0": "Female", "1": "Male", "女": "Female", "男": "Male"}
POS = {"0": "Negative", "1": "Positive"}
SUBTYPE = {
    "1": "Homogeneous Negativensolid",
    "2": "Heterogeneous Negativensolid",
    "3": "Part-solid",
    "4": "Solid",
}

# Overlap with the locked cln: enable a source field when agreement is high.
MIN_N_STRICT = 20
MIN_RATE_STRICT = 0.95
MIN_N_SMALL = 3


def code(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip().replace("\u00a0", " ")
    if s.lower() in {"", "nan", "none", "null", "nat", "-", "/"}:
        return None
    if s.endswith(".0"):
        s = s[:-2]
    return s


def norm_ws(v) -> str:
    s = code(v) or ""
    return re.sub(r"\s+", " ", s).strip().lower()


def py_base(v) -> str:
    s = norm_ws(v)
    return re.sub(r"\s+\d+$", "", s)


def cn_parts(v):
    s = re.sub(r"\s+", "", code(v) or "")
    m = re.match(r"^(.*?)(\d+)?$", s)
    return (m.group(1) if m else s), (m.group(2) or "")


def mapped(table, v):
    c = code(v)
    if c is None:
        return None
    return table.get(c)


def as_int_label(label):
    """Store numbers as int when the mapped value is numeric text."""
    c = code(label)
    if c is None:
        return None
    try:
        f = float(c)
    except ValueError:
        return c
    if f == int(f):
        return str(int(f))
    return str(f)


def read_id_col(sheet: str) -> list[str]:
    df = pd.read_excel(XLSX, sheet_name=sheet, usecols=[0], dtype=str)
    return [
        str(x).replace("\u00a0", " ").strip() if pd.notna(x) else ""
        for x in df.iloc[:, 0]
    ]


def load_cln() -> pd.DataFrame:
    df = pd.read_excel(XLSX, sheet_name="cln", dtype=str)
    df["Patient_ID"] = [
        str(x).replace("\u00a0", " ").strip() if pd.notna(x) else ""
        for x in df["Patient_ID"]
    ]
    return df


def consensus(recs, field):
    vals = [r[field] for r in recs if r.get(field) not in (None, "")]
    if not vals:
        return None
    uniq = set(vals)
    if len(uniq) == 1:
        return vals[0]
    return "CONFLICT"


class Source:
    def __init__(self, name: str):
        self.name = name
        self.by_key = defaultdict(list)

    def add(self, key: str, fields: dict):
        if not key:
            return
        clean = {k: v for k, v in fields.items() if v not in (None, "")}
        if clean:
            self.by_key[key].append(clean)


def build_sources():
    sources = []

    clinical = pd.read_excel(REF / "lung.xlsx", sheet_name="clinical", dtype=str)
    src = Source("lung.xlsx/clinical")
    for _, r in clinical.iterrows():
        src.add(norm_ws(r["name"]), {
            "Growth": mapped(GROWTH, r["group"]),
            "Gender": mapped(SEX, r["gender"]),
            "Age": as_int_label(r["age"]),
            "Diameter": as_int_label(r["ave dia"]),
            "Max_diameter": as_int_label(r["max dia"]),
            "Subtype": mapped(SUBTYPE, r["nodule type"]),
            "Pleural_retraction": mapped(POS, r["pleural retraction"]),
            "Spiculation": mapped(POS, r["spiculation"]),
            "Lobulation": mapped(POS, r["lobulation"]),
            "Smoking": mapped(POS, r["smoking"]),
            "Previous_LCH": mapped(POS, r["previous LCH"]),
            "Family_LCH": mapped(POS, r["family LCH"]),
        })
    sources.append(src)

    for sheet in ("data", "val"):
        df = pd.read_excel(
            REF / "lung.xlsx", sheet_name=sheet,
            usecols=["name", "group", "gender", "age", "diameter", "subtype"],
            dtype=str,
        )
        src = Source(f"lung.xlsx/{sheet}")
        for _, r in df.iterrows():
            src.add(norm_ws(r["name"]), {
                "Growth": mapped(GROWTH, r["group"]),
                "Gender": mapped(SEX, r["gender"]),
                "Age": as_int_label(r["age"]),
                "Diameter": as_int_label(r["diameter"]),
                "Subtype": mapped(SUBTYPE, r["subtype"]),
            })
        sources.append(src)

    radi = pd.read_excel(
        REF / "lung.xlsx", sheet_name="radiomics",
        usecols=["name", "group", "gender", "age", "average diameter",
                 "nodule types by density"],
        dtype=str,
    )
    src = Source("lung.xlsx/radiomics")
    for _, r in radi.iterrows():
        src.add(norm_ws(r["name"]), {
            "Growth": mapped(GROWTH, r["group"]),
            "Gender": mapped(SEX, r["gender"]),
            "Age": as_int_label(r["age"]),
            "Diameter": as_int_label(r["average diameter"]),
            "Subtype": mapped(SUBTYPE, r["nodule types by density"]),
        })
    sources.append(src)

    l2 = pd.read_excel(REF / "lung2.xlsx", sheet_name="clin", dtype=str)
    old = pd.read_excel(REF / "lung2.xlsx", sheet_name="clin-old", dtype=str)
    old_dia = {norm_ws(n): as_int_label(d) for n, d in zip(old["name"], old["初始最大径"])}
    src = Source("lung2.xlsx/clin")
    for _, r in l2.iterrows():
        key = norm_ws(r["name"])
        src.add(key, {
            "Growth": mapped(GROWTH, r["group"]),
            "Gender": mapped(SEX, r["sex"]),
            "Age": as_int_label(r["age"]),
            "Max_diameter": old_dia.get(key),
            "Pleural_retraction": mapped(POS, r["pleural indentation"]),
            "Spiculation": mapped(POS, r["spicule sign"]),
            "Lobulation": mapped(POS, r["Lobulation"]),
        })
    sources.append(src)

    l3 = pd.read_excel(REF / "lung3.xlsx", sheet_name="clin", dtype=str)
    src = Source("lung3.xlsx/clin")
    for _, r in l3.iterrows():
        src.add(norm_ws(r["name"]), {
            "Growth": mapped(GROWTH, r["group"]),
            "Gender": mapped(SEX, r["Gender"]),
            "Age": as_int_label(r["Age"]),
            "Pleural_retraction": mapped(POS, r["pleural.indentation"]),
            "Spiculation": mapped(POS, r["spicule.sign"]),
        })
    sources.append(src)

    # Chinese name -> one pinyin base. Indexed Chinese names share the base.
    cmap = defaultdict(set)
    pairs = [
        (l3["names"], l3["name"]),
        (l2["姓名"], l2["name"]),
        (clinical["mi"], clinical["name"]),
    ]
    for cns, pys in pairs:
        for cn, py in zip(cns, pys):
            base_cn, _ = cn_parts(cn)
            base_py = py_base(py)
            if base_cn and base_py:
                cmap[base_cn].add(base_py)
    amb = {k for k, v in cmap.items() if len(v) != 1}

    sec = pd.read_excel(REF / "2nd 临床数据补充01版.xlsx", dtype=str)
    src = Source("2nd/Sheet1")
    unmapped = []
    for _, r in sec.iterrows():
        base_cn, idx = cn_parts(r["姓名"])
        if base_cn in amb or base_cn not in cmap:
            unmapped.append(code(r["姓名"]) or "")
            continue
        py = next(iter(cmap[base_cn]))
        key = f"{py} {idx}" if idx else py
        src.add(key, {
            "Growth": mapped(GROWTH, r["生长分组"]),
            "Gender": mapped(SEX, r["sex"]),
            "Age": as_int_label(r["年龄"]),
            "Max_diameter": as_int_label(r["初始最大径"]),
        })
    sources.append(src)
    return sources, sorted({u for u in unmapped if u}), len(amb)


def cln_lookup(cln: pd.DataFrame):
    by_exact = {}
    for _, r in cln.iterrows():
        by_exact[r["Patient_ID"]] = {c: code(r[c]) if c != "Patient_ID" else r["Patient_ID"]
                                     for c in COLS}
        if c_age := by_exact[r["Patient_ID"]].get("Age"):
            by_exact[r["Patient_ID"]]["Age"] = as_int_label(c_age)
        for col in ("Diameter", "Max_diameter"):
            by_exact[r["Patient_ID"]][col] = as_int_label(by_exact[r["Patient_ID"]][col])
    return by_exact


def unique_key_map(rad_ids):
    counts = Counter(norm_ws(x) for x in rad_ids)
    return {rid: (norm_ws(rid) if counts[norm_ws(rid)] == 1 else None) for rid in rad_ids}


def gate_fields(sources, cln_by_exact, key_of):
    """Return {(source, field): (enabled, n, rate)}."""
    cln_by_key = {}
    key_counts = Counter(k for k in key_of.values() if k)
    for rid, row in cln_by_exact.items():
        k = key_of.get(rid)
        if k and key_counts[k] == 1:
            cln_by_key[k] = row
    report = {}
    enabled = {}
    for src in sources:
        fields = set()
        for recs in src.by_key.values():
            for rec in recs:
                fields.update(rec)
        for field in sorted(fields):
            n = agree = 0
            for k, gold in cln_by_key.items():
                recs = src.by_key.get(k)
                if not recs:
                    continue
                val = consensus(recs, field)
                if val in (None, "CONFLICT"):
                    continue
                g = gold.get(field)
                if g in (None, ""):
                    continue
                n += 1
                if val == g:
                    agree += 1
            rate = (agree / n) if n else 0.0
            if n >= MIN_N_STRICT:
                ok = rate >= MIN_RATE_STRICT
            elif n >= MIN_N_SMALL:
                ok = rate == 1.0
            else:
                ok = False
            report[(src.name, field)] = (ok, n, agree, rate)
            enabled[(src.name, field)] = ok
    return enabled, report


# lung2 and lung3 use the same 0/1 codes. Where they agree on every shared
# patient, a blank cln cell can take that value even if a few curated cln rows
# differ. Existing cln cells are still never overwritten.
CROSS_PAIRS = (
    ("lung2.xlsx/clin", "lung3.xlsx/clin", "Age"),
    ("lung2.xlsx/clin", "lung3.xlsx/clin", "Pleural_retraction"),
    ("lung2.xlsx/clin", "lung3.xlsx/clin", "Spiculation"),
)


def cross_enable(sources, enabled):
    by_name = {s.name: s for s in sources}
    notes = []
    for left, right, field in CROSS_PAIRS:
        sa, sb = by_name[left], by_name[right]
        n = agree = 0
        for key in set(sa.by_key) & set(sb.by_key):
            va = consensus(sa.by_key[key], field)
            vb = consensus(sb.by_key[key], field)
            if va in (None, "CONFLICT") or vb in (None, "CONFLICT"):
                continue
            n += 1
            if va == vb:
                agree += 1
        ok = n >= 100 and agree == n
        notes.append((left, right, field, n, agree, ok))
        if not ok:
            continue
        for name in (left, right):
            enabled[(name, field)] = True
    return notes


def align(rad_ids, cln_by_exact, sources, enabled, key_of):
    rows = []
    fills = []
    conflicts = Counter()
    for rid in rad_ids:
        if rid in cln_by_exact:
            rows.append(dict(cln_by_exact[rid]))
            continue
        row = {c: None for c in COLS}
        row["Patient_ID"] = rid
        key = key_of.get(rid)
        if key:
            for src in sources:
                recs = src.by_key.get(key)
                if not recs:
                    continue
                for field in COLS[1:]:
                    if not enabled.get((src.name, field), False):
                        continue
                    if row[field] not in (None, ""):
                        continue
                    val = consensus(recs, field)
                    if val == "CONFLICT":
                        conflicts[(src.name, field)] += 1
                        continue
                    if val is None:
                        continue
                    row[field] = val
                    fills.append((rid, field, val, src.name))
        rows.append(row)
    return rows, fills, conflicts


def summarise(rad_ids, cln_by_exact, rows, fills, conflicts, report, unmapped, n_amb, cross_notes, enabled):
    lines = []
    lines.append("# xlm_LG cln aligned to rad")
    lines.append("")
    lines.append(f"- rad rows: {len(rad_ids)}")
    lines.append(f"- cln rows before: {len(cln_by_exact)}")
    lines.append(f"- cln rows after: {len(rows)}")
    new_n = len(rad_ids) - len(cln_by_exact)
    lines.append(f"- new rows (rad only): {new_n}")
    lines.append(f"- cells filled: {len(fills)}")
    lines.append(f"- 2nd-sheet Chinese names with no single pinyin: {len(unmapped)} "
                 f"(ambiguous bases in the name map: {n_amb})")
    lines.append("")
    lines.append("Existing cln cells were not changed. A source field is used only when")
    lines.append("its labels match the current cln on shared IDs "
                 f"(n>={MIN_N_STRICT} and agreement>={MIN_RATE_STRICT:.0%}, "
                 f"or {MIN_N_SMALL}<=n<{MIN_N_STRICT} and agreement=100%).")
    lines.append("Double-space ids (`cao xiao hong  1` vs `cao xiao hong 1`) stay distinct.")
    lines.append("Generic 既往史/家族史 were not written into Previous_LCH/Family_LCH.")
    lines.append("Position, emphysema, pathology, and gene columns were not added.")
    lines.append("Age, pleural retraction, and spiculation from lung2/lung3 are used on")
    lines.append("blank rows when those two files agree on every shared patient.")
    lines.append("2nd-sheet age and initial diameter stay unused: they do not match cln.")
    lines.append("")
    lines.append("## lung2 vs lung3")
    lines.append("")
    lines.append("| left | right | field | n | agree | use |")
    lines.append("|---|---|---|---:|---:|---|")
    for left, right, field, n, agree, ok in cross_notes:
        pct = f"{agree / n:.1%}" if n else "—"
        lines.append(f"| {left} | {right} | {field} | {n} | {pct} | {'yes' if ok else 'no'} |")
    lines.append("")
    lines.append("## Source fields")
    lines.append("")
    lines.append("| source | field | use | n overlap | agree |")
    lines.append("|---|---|---|---:|---:|")
    for (name, field), (ok, n, agree, rate) in sorted(report.items()):
        flag = "yes" if enabled.get((name, field), False) else "no"
        pct = f"{rate:.1%}" if n else "—"
        lines.append(f"| {name} | {field} | {flag} | {n} | {pct} |")
    lines.append("")
    lines.append("## Fills by source and field")
    lines.append("")
    cnt = Counter((src, field) for _, field, _, src in fills)
    lines.append("| source | field | n |")
    lines.append("|---|---|---:|")
    for (src, field), n in sorted(cnt.items()):
        lines.append(f"| {src} | {field} | {n} |")
    if conflicts:
        lines.append("")
        lines.append("## Within-source conflicts (not filled)")
        lines.append("")
        for (src, field), n in sorted(conflicts.items()):
            lines.append(f"- {src} / {field}: {n}")
    lines.append("")
    lines.append("## Missingness after align (new rows only)")
    lines.append("")
    new_rows = [r for r in rows if r["Patient_ID"] not in cln_by_exact]
    lines.append("| field | filled | still blank |")
    lines.append("|---|---:|---:|")
    for c in COLS[1:]:
        blank = sum(1 for r in new_rows if r[c] in (None, ""))
        lines.append(f"| {c} | {len(new_rows) - blank} | {blank} |")
    full_blank = [
        r["Patient_ID"] for r in new_rows
        if all(r[c] in (None, "") for c in COLS[1:])
    ]
    lines.append("")
    lines.append(f"New rows with no clinical value from any accepted source: {len(full_blank)}")
    if unmapped:
        lines.append("")
        lines.append("## 2nd sheet names not linked to a pinyin")
        lines.append("")
        lines.append(", ".join(unmapped))
    lines.append("")
    return "\n".join(lines), full_blank


def write_cln(rows):
    wb = load_workbook(XLSX)
    ws = wb["cln"]
    if ws.max_row:
        ws.delete_rows(1, ws.max_row)
    for col, name in enumerate(COLS, start=1):
        ws.cell(1, col, name)
    for i, row in enumerate(rows, start=2):
        for col, name in enumerate(COLS, start=1):
            val = row[name]
            if val in (None, ""):
                ws.cell(i, col, None)
                continue
            if name in INT_COLS:
                ws.cell(i, col, int(val))
            else:
                ws.cell(i, col, val)
    wb.save(XLSX)
    wb.close()


def fingerprint():
    rad = pd.read_excel(XLSX, sheet_name="rad", usecols=[0, 1], dtype=str)
    qc = pd.read_excel(XLSX, sheet_name="qc", usecols=[0, 1], dtype=str)
    icc = pd.read_excel(XLSX, sheet_name="icc", usecols=[0, 1], dtype=str)
    return (
        tuple(rad.shape), str(rad.iloc[0, 0]), str(rad.iloc[0, 1]), str(rad.iloc[-1, 0]),
        tuple(qc.shape), str(qc.iloc[0, 0]), str(qc.iloc[-1, 0]),
        tuple(icc.shape), str(icc.iloc[0, 0]), str(icc.iloc[0, 1]), str(icc.iloc[-1, 0]),
    )


def verify(rows):
    rad = read_id_col("rad")
    got = read_id_col("cln")
    # header was written as first value if usecols reads header... read_excel uses header
    cln = load_cln()
    ids = cln["Patient_ID"].tolist()
    if ids != rad:
        raise SystemExit(f"ID order mismatch: cln {len(ids)} rad {len(rad)}")
    if list(cln.columns)[:len(COLS)] != COLS:
        raise SystemExit(f"columns changed: {list(cln.columns)}")
    # other sheets still present
    xl = pd.ExcelFile(XLSX)
    if xl.sheet_names != ["cln", "rad", "qc", "icc"]:
        raise SystemExit(f"sheets changed: {xl.sheet_names}")
    qc = read_id_col("qc")
    if qc != rad:
        raise SystemExit("qc IDs no longer match rad")
    print(f"[verify] cln {cln.shape} IDs match rad; qc unchanged; sheets {xl.sheet_names}")
    return got


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    rad_ids = read_id_col("rad")
    cln = load_cln()
    cln_by_exact = cln_lookup(cln)
    missing = [r for r in cln_by_exact if r not in set(rad_ids)]
    if missing:
        raise SystemExit(f"cln IDs not in rad: {missing[:10]}")
    key_of = unique_key_map(rad_ids)
    sources, unmapped, n_amb = build_sources()
    enabled, report = gate_fields(sources, cln_by_exact, key_of)
    cross_notes = cross_enable(sources, enabled)
    rows, fills, conflicts = align(rad_ids, cln_by_exact, sources, enabled, key_of)
    text, full_blank = summarise(
        rad_ids, cln_by_exact, rows, fills, conflicts, report, unmapped, n_amb,
        cross_notes, enabled,
    )
    print(text)
    print(f"\nfully blank new rows: {len(full_blank)}")
    if not args.apply:
        print("\n[dry-run] no file written")
        return

    # original cln cells must survive
    by_new = {r["Patient_ID"]: r for r in rows}
    for rid, old in cln_by_exact.items():
        new = by_new[rid]
        for c in COLS:
            if (old[c] or None) != (new[c] or None):
                raise SystemExit(f"would change existing {rid} {c}: {old[c]!r} -> {new[c]!r}")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = DEL / f"xlm_LG_before_cln_align_{stamp}.xlsx"
    shutil.copy2(XLSX, backup)
    before = fingerprint()
    write_cln(rows)
    after = fingerprint()
    if before != after:
        raise SystemExit(f"rad/qc/icc changed during cln write:\n{before}\n{after}")
    note = REF / "cln_align_rad.md"
    note.write_text(text + f"\nBackup: `{backup}`\n", encoding="utf-8")
    fills_path = REF / "cln_align_rad_fills.csv"
    pd.DataFrame(fills, columns=["Patient_ID", "field", "value", "source"]).to_csv(
        fills_path, index=False, encoding="utf-8-sig",
    )
    verify(rows)
    print(f"[apply] backup {backup}")
    print(f"[apply] note {note}")
    print(f"[apply] fills {fills_path}")


if __name__ == "__main__":
    main()
