#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Harvest lab/clinical/pathology indicator mentions from 05-manuscript (thesis + manuscripts).

Peer-review numeric plausibility KB only — NOT clinical decision support.
Never invents ref_low/ref_high/mean/sd. Quotes only what appears in source text.

Outputs under %TEMP%\\lab-scratch\\lab-ref-ranges\\ (env SCRATCH_ROOT overrides) and inventory under
D:\\0Grok\\0doc\\_library\\lab-ref-ranges\\inventory\\

Run: python lab_ref_ranges_extract.py
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
import tempfile
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from xml.etree import ElementTree as ET

# docx_extract lives at 05-manuscript/write-paper/ (rehome 2026-10-23)
_CODE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(_CODE_ROOT, "05-manuscript", "write-paper"))
try:
    from docx_extract import extract_text, close_word
except ImportError:
    extract_text = None
    close_word = lambda: None

SHANGHAI = timezone(timedelta(hours=8))
NOW = datetime.now(SHANGHAI).strftime("%Y-%m-%d %H:%M:%S CST")

TIERS = {
    "01": r"D:\00db\_lab\doc-word\05-manuscript\thesis",
    "02": r"D:\00db\_lab\doc-word\05-manuscript\manuscripts",
}
SCRATCH_ROOT = os.environ.get("SCRATCH_ROOT") or os.path.join(tempfile.gettempdir(), "lab-scratch")
OUT_SCRATCH = os.path.join(SCRATCH_ROOT, "lab-ref-ranges")
_LAB_ROOT = Path(__file__).resolve().parents[5]  # tools/<tool>/ -> lab root（公开仓不留绝对盘符）
INV_DIR = os.environ.get("LAB_REF_RANGES_INV") or str(_LAB_ROOT / "0doc" / "_library" / "lab-ref-ranges" / "inventory")
TXT_DIR = os.path.join(OUT_SCRATCH, "txt")
JSONL_PATH = os.path.join(INV_DIR, "indicators-from-01-02.jsonl")
LOG_PATH = os.path.join(INV_DIR, "extraction-log.md")
ALIAS_TSV = os.environ.get("LAB_REF_RANGES_ALIAS") or str(_LAB_ROOT / "0doc" / "_library" / "lab-ref-ranges" / "alias-map.tsv")
GAPS_MD = os.environ.get("LAB_REF_RANGES_GAPS") or str(_LAB_ROOT / "0doc" / "_library" / "lab-ref-ranges" / "inventory" / "gaps-stub.md")

# alias (regex or literal) -> canonical_id, category, name_en, name_zh
# Longer / more specific patterns listed first where order matters in ALIAS_RULES
ALIAS_RULES = [
    # liver
    (r"丙氨酸氨基转移酶|\bALT\b|\bGPT\b|\bSGPT\b", "lab.alt", "lab", "ALT", "丙氨酸氨基转移酶"),
    (r"天冬氨酸氨基转移酶|天门冬氨酸氨基转移酶|\bAST\b|\bGOT\b|\bSGOT\b", "lab.ast", "lab", "AST", "天门冬氨酸氨基转移酶"),
    (r"白蛋白|\bALB\b|\balbumin\b", "lab.alb", "lab", "albumin", "白蛋白"),
    (r"总胆红素|\bTBIL\b|\bTBil\b|\btotal\s+bilirubin\b", "lab.tbil", "lab", "total bilirubin", "总胆红素"),
    (r"直接胆红素|\bDBIL\b|\bDBil\b|\bdirect\s+bilirubin\b", "lab.dbil", "lab", "direct bilirubin", "直接胆红素"),
    (r"间接胆红素|\bIBIL\b|\bindirect\s+bilirubin\b", "lab.ibil", "lab", "indirect bilirubin", "间接胆红素"),
    (r"\bbilirubin\b|胆红素", "lab.bilirubin", "lab", "bilirubin", "胆红素"),
    (r"总蛋白|\bTP\b(?!\s*53)|\btotal\s+protein\b", "lab.tp", "lab", "total protein", "总蛋白"),
    (r"碱性磷酸酶|\bALP\b|\bAKP\b", "lab.alp", "lab", "ALP", "碱性磷酸酶"),
    (r"γ[-‑]?谷氨酰[转基]?转肽酶|\bGGT\b|\bγ-GT\b|\bGGTP\b", "lab.ggt", "lab", "GGT", "γ-谷氨酰基转移酶"),
    # coag
    (r"凝血酶原时间|\bPT\b(?!\s*[-–])", "lab.pt", "lab", "PT", "凝血酶原时间"),
    (r"国际标准化比值|\bINR\b", "lab.inr", "lab", "INR", "国际标准化比值"),
    (r"活化部分凝血活酶时间|部分凝血活酶时间|\bAPTT\b|\bPTT\b", "lab.aptt", "lab", "APTT", "活化部分凝血活酶时间"),
    (r"D[-‑]?二聚体|\bD[-‑]?dimer\b|\bD[-‑]?Dimer\b", "lab.ddimer", "lab", "D-dimer", "D-二聚体"),
    (r"纤维蛋白原|\bFIB\b|\bFbg\b|\bfibrinogen\b", "lab.fibrinogen", "lab", "fibrinogen", "纤维蛋白原"),
    # tumor
    (r"甲胎蛋白|\bAFP\b", "lab.afp", "lab", "AFP", "甲胎蛋白"),
    (r"癌胚抗原|\bCEA\b", "lab.cea", "lab", "CEA", "癌胚抗原"),
    (r"CA[-‑]?19[-‑]?9|糖链抗原\s*19[-‑]?9", "lab.ca199", "lab", "CA19-9", "糖链抗原19-9"),
    (r"CA[-‑]?15[-‑]?3|CA[-‑]?153|糖链抗原\s*15[-‑]?3", "lab.ca153", "lab", "CA15-3", "糖链抗原15-3"),
    (r"CA[-‑]?125|糖链抗原\s*125", "lab.ca125", "lab", "CA125", "糖链抗原125"),
    (r"\bHE4\b|人附睾蛋白\s*4", "lab.he4", "lab", "HE4", "人附睾蛋白4"),
    (r"\bROMA\b", "lab.roma", "lab", "ROMA", "ROMA指数"),
    # special
    (r"血氨|氨浓度|\bammonia\b|\bNH3\b", "lab.ammonia", "lab", "ammonia", "血氨"),
    (r"锰|\bmanganese\b|\bMn\b(?!\s*O)", "lab.manganese", "lab", "manganese", "锰"),
    # renal
    (r"肌酐|\bCREA\b|\bCr\b|\bcreatinine\b", "lab.creatinine", "lab", "creatinine", "肌酐"),
    (r"\beGFR\b|估算肾小球滤过率|肾小球滤过率", "lab.egfr", "lab", "eGFR", "估算肾小球滤过率"),
    (r"尿酸|\bUA\b|\buric\s+acid\b", "lab.ua", "lab", "uric acid", "尿酸"),
    (r"尿素(?:氮)?|\bBUN\b|\bUREA\b", "lab.urea", "lab", "urea", "尿素"),
    # heme
    (r"白细胞(?:计数)?|\bWBC\b|\bleukocyte\b", "lab.wbc", "lab", "WBC", "白细胞计数"),
    (r"血小板(?:计数)?|\bPLT\b|\bplatelet\b", "lab.plt", "lab", "PLT", "血小板计数"),
    (r"中性粒细胞(?:计数|百分比|绝对值)?|\bneutrophil\b|\bNEUT\b|\bNLR\b", "lab.neutrophil", "lab", "neutrophil", "中性粒细胞"),
    (r"淋巴细胞(?:计数|百分比|绝对值)?|\blymphocyte\b|\bLYMPH\b|\bPLR\b", "lab.lymphocyte", "lab", "lymphocyte", "淋巴细胞"),
    (r"血红蛋白|\bHb\b|\bHGB\b|\bhemoglobin\b", "lab.hgb", "lab", "hemoglobin", "血红蛋白"),
    (r"红细胞(?:计数)?|\bRBC\b", "lab.rbc", "lab", "RBC", "红细胞计数"),
    # clinical scores
    (r"Child[-‑]?Pugh|\bCTP\b", "cln.child_pugh", "clinical", "Child-Pugh", "Child-Pugh分级"),
    (r"\bMELD\b", "cln.meld", "clinical", "MELD", "MELD评分"),
    (r"\bBMI\b|体质指数|身体质量指数", "cln.bmi", "clinical", "BMI", "体质指数"),
    (r"收缩压|\bSBP\b", "cln.sbp", "clinical", "SBP", "收缩压"),
    (r"舒张压|\bDBP\b", "cln.dbp", "clinical", "DBP", "舒张压"),
    # pathology categorical
    (r"\bFIGO\b", "pth.figo", "pathology", "FIGO stage", "FIGO分期"),
    (r"\bTNM\b", "pth.tnm", "pathology", "TNM", "TNM分期"),
    (r"淋巴脉管浸润|脉管浸润|\bLVI\b|\bLVSI\b", "pth.lvi", "pathology", "LVI/LVSI", "淋巴脉管浸润"),
    (r"Ki[-‑]?67", "pth.ki67", "pathology", "Ki-67", "Ki-67"),
    (r"\bHER2\b|\bHer-2\b", "pth.her2", "pathology", "HER2", "HER2"),
    (r"\bPD[-‑]?L1\b", "pth.pdl1", "pathology", "PD-L1", "PD-L1"),
    (r"\bEGFR\b", "pth.egfr", "pathology", "EGFR", "EGFR"),
    (r"组织学分级|病理分级|\bgrade\b(?!\s*school)", "pth.grade", "pathology", "histologic grade", "组织学分级"),
]

COMPILED = [(re.compile(pat, re.I), cid, cat, en, zh) for pat, cid, cat, en, zh in ALIAS_RULES]

# stats / cutoff extractors (capture as written; do not compute)
RE_MEAN_SD = re.compile(
    r"([\d.]+)\s*[±\+]\s*([\d.]+)",
)
RE_MEDIAN_IQR = re.compile(
    r"([\d.]+)\s*[（(]\s*([\d.]+)\s*[,，\-–~～]\s*([\d.]+)\s*[)）]",
)
RE_RANGE = re.compile(
    r"([\d.]+)\s*[-–~～至到]\s*([\d.]+)\s*(U/L|u/L|IU/L|g/L|mg/L|μg/L|ug/L|ng/mL|ng/ml|μmol/L|umol/L|mmol/L|×10\^?[9¹²]|×10⁹/L|10\^9/L|s|秒|%|kPa|mmHg)?",
)
RE_CUTOFF = re.compile(
    # Prefer inequality cutoffs; bare "=" often matches (n = 35) or P = 0.001 — demoted in guess_context
    r"(≥|≤|>|<|大于|小于|不低于|不超过|高于|低于)\s*([\d.]+)\s*(U/L|u/L|IU/L|g/L|mg/L|μg/L|ug/L|ng/mL|ng/ml|μmol/L|umol/L|mmol/L|×10\^?[9¹²]|s|秒|%|ng/mL|mmHg)?",
)
RE_CUTOFF_EQ = re.compile(
    r"(?<![PpRr])(?<!\bn\s)(?<!n)(=\s*)([\d.]+)\s*(U/L|u/L|IU/L|g/L|mg/L|μg/L|ug/L|ng/mL|ng/ml|μmol/L|umol/L|mmol/L|×10\^?[9¹²]|s|秒|%|mmHg)",
)
RE_UNIT = re.compile(
    r"\b(U/L|IU/L|g/L|mg/L|μg/L|ug/L|ng/mL|ng/ml|μmol/L|umol/L|mmol/L|×10⁹/L|10\^9/L|s|mmHg|μmol/L)\b",
    re.I,
)


def safe(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "_", os.path.splitext(name)[0])[:120]


def iter_files(tier: str):
    root = TIERS[tier]
    for dirpath, _dns, filenames in os.walk(root):
        # skip revision-history deep noise if nested oddly; still allow direct ms files
        for fn in sorted(filenames):
            ext = os.path.splitext(fn)[1].lower()
            if ext not in {".docx", ".doc"}:
                continue
            # skip Word lock files
            if fn.startswith("~$"):
                continue
            yield os.path.join(dirpath, fn)


def docx_xml_text(path: str) -> str:
    """Fast plain text from word/document.xml (docx only)."""
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml")
    # strip tags crudely but keep spaces between runs
    text = re.sub(rb"</w:p>", b"\n", xml)
    text = re.sub(rb"</w:tc>", b"\t", text)
    text = re.sub(rb"<[^>]+>", b"", text)
    return text.decode("utf-8", "ignore")


def window(text: str, start: int, end: int, radius: int = 160) -> str:
    a = max(0, start - radius)
    b = min(len(text), end + radius)
    return re.sub(r"\s+", " ", text[a:b]).strip()


def parse_numerics(snippet: str) -> dict:
    """Extract quoted numeric fragments only; leave nulls when absent."""
    out = {
        "mean": None,
        "sd": None,
        "median": None,
        "q1": None,
        "q3": None,
        "ref_low": None,
        "ref_high": None,
        "cutoff": None,
        "cutoff_direction": None,
        "unit_as_reported": None,
        "numeric_raw": None,
    }
    u = RE_UNIT.search(snippet)
    if u:
        out["unit_as_reported"] = u.group(1)
    ms = RE_MEAN_SD.search(snippet)
    if ms:
        out["mean"] = ms.group(1)
        out["sd"] = ms.group(2)
        out["numeric_raw"] = ms.group(0)
    mi = RE_MEDIAN_IQR.search(snippet)
    if mi and out["mean"] is None:
        out["median"] = mi.group(1)
        out["q1"] = mi.group(2)
        out["q3"] = mi.group(3)
        out["numeric_raw"] = mi.group(0)
    # institutional RI phrasing: 参考范围 / 正常值 / 参考区间
    if re.search(r"参考(?:范围|区间|值)|正常(?:范围|值)|reference\s+(?:range|interval)", snippet, re.I):
        rr = RE_RANGE.search(snippet)
        if rr:
            out["ref_low"] = rr.group(1)
            out["ref_high"] = rr.group(2)
            if rr.group(3):
                out["unit_as_reported"] = rr.group(3)
            out["numeric_raw"] = (out["numeric_raw"] or "") + " | RI:" + rr.group(0)
    co = RE_CUTOFF.search(snippet)
    if co:
        direction = co.group(1)
        dir_map = {
            "大于": ">", "小于": "<", "不低于": "≥", "不超过": "≤",
            "高于": ">", "低于": "<",
        }
        out["cutoff_direction"] = dir_map.get(direction, direction)
        out["cutoff"] = co.group(2)
        if co.group(3):
            out["unit_as_reported"] = out["unit_as_reported"] or co.group(3)
        out["numeric_raw"] = (out["numeric_raw"] or "") + " | cut:" + co.group(0)
    else:
        # Equality cutoff only when a lab unit is attached (avoids n= / P=)
        co2 = RE_CUTOFF_EQ.search(snippet)
        if co2:
            out["cutoff_direction"] = "="
            out["cutoff"] = co2.group(2)
            if co2.group(3):
                out["unit_as_reported"] = out["unit_as_reported"] or co2.group(3)
            out["numeric_raw"] = (out["numeric_raw"] or "") + " | cut:" + co2.group(0)
    return out


def guess_context(snippet: str, has_cutoff: bool, has_ri: bool, has_desc: bool = False) -> str:
    """Context guess. Prefer cohort_* when Table-1 descriptive stats present.

    True study_cutoff only when a dichotomization/diagnostic threshold is evident
    and not a p-value / (n=) / correlation fragment. Build-time reclassify is SSOT.
    """
    if has_ri:
        return "ri_normal"
    if has_desc:
        if re.search(r"对照|control|healthy|健康|正常组", snippet, re.I):
            return "cohort_control"
        return "cohort_disease"
    if has_cutoff:
        # demote p-values and correlation coefficients
        if re.search(r"[Pp]\s*[=＜<]|P值|ICC\s*>=?|\bR2?\s*=", snippet):
            if re.search(r"阈值|截断|cutoff|threshold|诊断标准|定义为", snippet, re.I):
                return "study_cutoff"
            return "cohort_disease"
        return "study_cutoff"
    if re.search(r"对照|control|healthy|健康", snippet, re.I):
        return "cohort_control"
    if re.search(r"患者|disease|病例|病例组|观察组|肿瘤|癌|子痫|肝性脑病|MHE", snippet, re.I):
        return "cohort_disease"
    return "cohort_disease"  # default for Table-1-like; curator reclassifies


def scan_text(text: str, source_path: str, tier: str, method: str):
    """Yield mention dicts for one document text."""
    seen_local = set()
    for cre, cid, cat, en, zh in COMPILED:
        for m in cre.finditer(text):
            snip = window(text, m.start(), m.end())
            key = (cid, snip[:80])
            if key in seen_local:
                continue
            seen_local.add(key)
            nums = parse_numerics(snip)
            has_ri = nums["ref_low"] is not None
            has_cut = nums["cutoff"] is not None
            has_desc = nums["mean"] is not None or nums["median"] is not None
            ctx = guess_context(snip, has_cut, has_ri, has_desc)
            if cat == "pathology":
                ctx = "path_category"
            alias_seen = m.group(0)
            yield {
                "canonical_id": cid,
                "name_en": en,
                "name_zh": zh,
                "category": cat,
                "alias_seen": alias_seen,
                "unit_as_reported": nums["unit_as_reported"],
                "source_tier": "local_thesis" if tier == "01" else "local_manuscript",
                "source_path": source_path,
                "source_locus": None,
                "extract_method": method,
                "context_guess": ctx,
                "snippet": snip[:400],
                "mean": nums["mean"],
                "sd": nums["sd"],
                "median": nums["median"],
                "q1": nums["q1"],
                "q3": nums["q3"],
                "ref_low": nums["ref_low"],
                "ref_high": nums["ref_high"],
                "cutoff": nums["cutoff"],
                "cutoff_direction": nums["cutoff_direction"],
                "numeric_raw": nums["numeric_raw"],
                "extracted_at_shanghai": NOW,
                "confidence": "quoted" if nums["numeric_raw"] else "mapped",
            }


def main():
    os.makedirs(TXT_DIR, exist_ok=True)
    os.makedirs(INV_DIR, exist_ok=True)
    mentions = []
    file_log = []
    alias_hits = Counter()
    canon_docs = defaultdict(set)

    for tier in ("01", "02"):
        for path in iter_files(tier):
            name = os.path.basename(path)
            ext = os.path.splitext(path)[1].lower()
            text = ""
            method = ""
            note = ""
            try:
                if ext == ".docx":
                    text = docx_xml_text(path)
                    method = "document.xml"
                    # also dump via python-docx for cleaner tables if available
                    try:
                        import docx as _docx
                        d = _docx.Document(path)
                        paras = [p.text for p in d.paragraphs]
                        for table in d.tables:
                            for row in table.rows:
                                paras.append("\t".join(c.text.strip() for c in row.cells))
                        text2 = "\n".join(paras)
                        if len(text2) > len(text) * 0.5:
                            text = text2
                            method = "python-docx+xml"
                    except Exception as ex:
                        note = f"python-docx fallback skipped: {ex}"
                elif ext == ".doc":
                    if extract_text is None:
                        note = "ERR no docx_extract for .doc"
                        file_log.append([tier, name, path, 0, note])
                        print(f"[SKIP .doc] {name}")
                        continue
                    text = extract_text(path)
                    method = "Word-COM"
                else:
                    continue
            except Exception as exc:
                file_log.append([tier, name, path, 0, "ERR " + str(exc)[:200]])
                print(f"[ERR] {tier} {name}: {exc}")
                continue

            out_txt = os.path.join(TXT_DIR, f"{tier}__{safe(name)}.txt")
            with open(out_txt, "w", encoding="utf-8") as fh:
                fh.write(text)
            file_log.append([tier, name, path, len(text), note or method])
            print(f"[ok] {tier} {len(text):>8} {method:16} {name[:70]}")

            for ment in scan_text(text, path, tier, method):
                mentions.append(ment)
                alias_hits[ment["alias_seen"]] += 1
                canon_docs[ment["canonical_id"]].add(path)

    # write jsonl
    with open(JSONL_PATH, "w", encoding="utf-8") as fh:
        for m in mentions:
            fh.write(json.dumps(m, ensure_ascii=False) + "\n")

    # alias-map.tsv (canonical merge)
    # collect aliases per canonical
    alias_by_canon = defaultdict(set)
    for m in mentions:
        alias_by_canon[m["canonical_id"]].add(m["alias_seen"])
    # also seed from rules
    for _pat, cid, cat, en, zh in ALIAS_RULES:
        alias_by_canon[cid].add(en)
        alias_by_canon[cid].add(zh)

    with open(ALIAS_TSV, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh, delimiter="\t")
        w.writerow(["alias", "canonical_id", "category", "name_en", "name_zh", "docs_hit", "notes"])
        meta = {cid: (cat, en, zh) for _p, cid, cat, en, zh in ALIAS_RULES}
        for cid in sorted(alias_by_canon.keys()):
            cat, en, zh = meta.get(cid, ("lab", cid, cid))
            docs = len(canon_docs.get(cid, []))
            for alias in sorted(alias_by_canon[cid], key=lambda x: (len(x), x)):
                w.writerow([alias, cid, cat, en, zh, docs, ""])

    # gaps stub: canons seen but no RI quoted
    with open(GAPS_MD, "w", encoding="utf-8") as fh:
        fh.write("# Gaps stub (P0) — indicators seen without fillable RI from 01/02\n\n")
        fh.write(f"**Extracted_at (Asia/Shanghai):** {NOW}\n\n")
        fh.write("| canonical_id | docs | has_any_numeric | has_quoted_RI | note |\n|---|---:|---|---|---|\n")
        by_c = defaultdict(list)
        for m in mentions:
            by_c[m["canonical_id"]].append(m)
        for cid in sorted(by_c.keys()):
            ms = by_c[cid]
            has_num = any(m["numeric_raw"] for m in ms)
            has_ri = any(m["ref_low"] for m in ms)
            note = "need lit/SOP RI" if not has_ri else "local RI quote present — verify"
            fh.write(f"| {cid} | {len(canon_docs[cid])} | {has_num} | {has_ri} | {note} |\n")

    # extraction log
    with open(LOG_PATH, "w", encoding="utf-8") as fh:
        fh.write("# Lab-ref-ranges extraction log\n\n")
        fh.write(f"**Extracted_at (Asia/Shanghai):** {NOW}\n\n")
        fh.write("**Banner:** peer-review numeric plausibility only — not clinical decision support.\n\n")
        fh.write(f"- Files attempted: {len(file_log)}\n")
        ok = [r for r in file_log if not str(r[4]).startswith("ERR") and not str(r[4]).startswith("SKIP")]
        err = [r for r in file_log if str(r[4]).startswith("ERR")]
        fh.write(f"- Files OK: {len(ok)}\n")
        fh.write(f"- Files ERR: {len(err)}\n")
        fh.write(f"- Mention rows: {len(mentions)}\n")
        fh.write(f"- Unique canonical_ids: {len(canon_docs)}\n\n")
        fh.write("## Per-file\n\n")
        fh.write("| tier | chars | method/note | file |\n|---|---:|---|---|\n")
        for tier, name, path, chars, note in file_log:
            fh.write(f"| {tier} | {chars} | {note} | {name} |\n")
        if err:
            fh.write("\n## Errors\n\n")
            for r in err:
                fh.write(f"- `{r[1]}`: {r[4]}\n")

    with open(os.path.join(OUT_SCRATCH, "index.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["tier", "file", "abspath", "chars", "note"])
        w.writerows(file_log)

    print(f"\nDONE mentions={len(mentions)} canons={len(canon_docs)} -> {JSONL_PATH}")
    close_word()


if __name__ == "__main__":
    main()

