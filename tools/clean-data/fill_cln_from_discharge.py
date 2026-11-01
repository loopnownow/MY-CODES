# -*- coding: utf-8 -*-
"""用 Results.7z（出院小结）填补 yt_RDL.xlsx / cln 的空白判读列。

⚠ 只填**文本里明确写了**的值，且只填**原本为空**的单元格。

可靠性（用 cln 里已有的 118 行做过回测，见 `--validate`）
--------------------------------------------------------
| 列 | 回测一致率 | 结论 |
|---|---|---|
| CDK4 | 36/36 = **100%** | ✅ 默认填 |
| MDM2 | 30/32 = **94%** | ✅ 默认填 |
| Dedifferentiated | 31/38 = 82% | ⚠ 需 `--include-dediff` |
| KI67 | 20/27 = 74% | ⚠ 需 `--include-ki67` |
| Reader_1 / Reader_2 | — | ❌ 人工判读，文本推不出 |
| Surrounding involving | 25/41 = 61% | ❌ 影像语义，文本推不出 |
| Tumor density | 15/23 = 65% | ❌ 同上 |
| Satellite nodules | — | ❌ 报告里几乎没有「卫星灶」字样 |

`Recurrence` / `Reader_*` / 三个影像语义列**不动**；原有 118 行里那些
「无复发生存时间/复发_*」的空缺也填不了——`Results.7z` 只覆盖这 109 位候选患者。

运行：
    python yt_rdl_fill_cln_from_results.py --validate   # 只跑回测
    python yt_rdl_fill_cln_from_results.py             # 填 CDK4 / MDM2
    python yt_rdl_fill_cln_from_results.py --include-dediff --include-ki67
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook

import tidy_yt_rdl as PATHS  # noqa: E402

REC_HEAD = re.compile(r"(?m)^\[(\d+)\]\s*(\S+?)[（(]住院[）)]")
RE_KI = re.compile(r"Ki[\s\-]?67\s*[（(]\s*([\d.]+)\s*(?:[-~–—]\s*([\d.]+))?\s*%")
RE_IHC = re.compile(r"(MDM2|CDK4)\s*[（(]\s*([+\-±])\s*[）)]")
RE_AMP = re.compile(r"(MDM2|CDK4)\s*(?:基因)?\s*(扩增|缺失)")


def _files() -> dict:
    out = {}
    for fn in os.listdir(PATHS.RESULTS_TXT):
        out[re.sub(r"^0+(?=\d{9}$)", "", os.path.splitext(fn)[0])] = fn
    return out


def records_sorted(mrn, files) -> list:
    fn = files.get(str(mrn))
    if not fn:
        return []
    t = (PATHS.RESULTS_TXT / fn).read_text(encoding="utf-8", errors="ignore")
    pos = [m.start() for m in REC_HEAD.finditer(t)] + [len(t)]
    out = []
    for i in range(len(pos) - 1):
        rec = t[pos[i]:pos[i + 1]]
        m = re.search(r"入院日期时间:([\d\-\:\s]{10,16})", rec)
        d = pd.to_datetime(m.group(1).strip(), errors="coerce") if m else pd.NaT
        out.append((d, rec))
    return [r for _, r in sorted(out, key=lambda x: (pd.isna(x[0]), x[0]))]


def ext_ihc(t, marker):
    vals = set()
    for m in RE_IHC.finditer(t):
        if m.group(1) == marker:
            vals.add("Positive" if m.group(2) == "+" else "Negative")
    for m in RE_AMP.finditer(t):
        if m.group(1) == marker:
            vals.add("Positive" if m.group(2) == "扩增" else "Negative")
    return vals.pop() if len(vals) == 1 else ""


def ext_ki(t):
    m = RE_KI.search(t)
    if not m:
        return None
    lo = float(m.group(1))
    hi = float(m.group(2)) if m.group(2) else lo
    return round((lo + hi) / 200, 4)


def ext_dediff(t):
    if re.search(r"去分化", t):
        return "Positive"
    if re.search(r"高分化|硬化性|炎症性脂肪", t):
        return "Negative"
    return ""


def extract(mrn, files) -> dict:
    """按住院先后扫描，每列取第一次抽到的值；并记下出处。"""
    res = {"CDK4": ("", ""), "MDM2": ("", ""), "Dedifferentiated": ("", ""), "KI67": ("", "")}
    for rec in records_sorted(mrn, files):
        if not res["CDK4"][0]:
            v = ext_ihc(rec, "CDK4")
            if v:
                res["CDK4"] = (v, _quote(rec, "CDK4"))
        if not res["MDM2"][0]:
            v = ext_ihc(rec, "MDM2")
            if v:
                res["MDM2"] = (v, _quote(rec, "MDM2"))
        if not res["Dedifferentiated"][0]:
            v = ext_dediff(rec)
            if v:
                res["Dedifferentiated"] = (v, _quote(rec, "去分化|高分化"))
        if not res["KI67"][0] and res["KI67"][0] != 0:
            v = ext_ki(rec)
            if v is not None:
                res["KI67"] = (v, _quote(rec, "Ki67"))
        if all(res[c][0] != "" and res[c][0] is not None for c in res):
            break
    return res


def _quote(t, pat, limit=110):
    m = re.search(rf"[^。；\n]{{0,35}}(?:{pat})[^。；\n]{{0,45}}", t)
    return m.group(0).strip()[:limit] if m else ""


def load_mrn_map() -> dict:
    """Patient_ID(首字母) → 病历卡号，取自候选表（含别名）。"""
    fu = pd.read_excel(PATHS.CAND_XLSX, sheet_name="01_候选患者_合并")
    fu["_ini"] = fu["拼音首字母"].astype(str).str.strip()
    m = {r["_ini"]: str(r["病历卡号"]) for _, r in fu.iterrows()}
    for _, r in fu.iterrows():                       # 别名也指向同一患者
        for a in str(r.get("编号别名(合并用)", "")).split("/"):
            a = a.strip()
            if a:
                m.setdefault(a, str(r["病历卡号"]))
    return m


def validate(files, ki67_thresh=0.02) -> None:
    """用 cln 里已有的 118 行回测各列一致率。"""
    cln = pd.read_excel(PATHS.YT_XLSX, sheet_name="cln").iloc[:118]
    ini2mrn = load_mrn_map()
    print("=== 回测（cln 原始 118 行）===")
    for col, tol in [("CDK4", None), ("MDM2", None), ("Dedifferentiated", None), ("KI67", ki67_thresh)]:
        n = ok = 0
        for _, r in cln.iterrows():
            mrn = ini2mrn.get(str(r["Patient_ID"]).strip())
            if not mrn:
                continue
            got = extract(mrn, files)[col][0]
            if got == "" or got is None:
                continue
            want = r[col]
            if want is None or pd.isna(want):
                continue
            n += 1
            if col == "KI67":
                ok += abs(float(want) - float(got)) <= tol
            else:
                ok += str(want).strip() == str(got).strip()
        print(f"  {col:18s} 抽取 {n:3d}  一致 {ok:3d} = {ok/n*100:.0f}%" if n else f"  {col}: 无样本")


def main() -> int:
    ap = argparse.ArgumentParser(description="用出院小结填补 cln 空白判读列")
    ap.add_argument("--validate", action="store_true", help="只跑回测")
    ap.add_argument("--include-dediff", action="store_true", help="同时填 Dedifferentiated（82%）")
    ap.add_argument("--include-ki67", action="store_true", help="同时填 KI67（74%）")
    ap.add_argument("--check", action="store_true", help="只核对当前文件")
    args = ap.parse_args()

    if not PATHS.RESULTS_TXT.exists() or not any(PATHS.RESULTS_TXT.glob("*.txt")):
        print(f"缺少解包后的出院小结目录 {PATHS.RESULTS_TXT}，请先跑 yt_rdl_recurrence.py")
        return 2
    files = _files()

    if args.validate:
        validate(files)
        return 0

    cols = ["CDK4", "MDM2"]
    if args.include_dediff:
        cols.append("Dedifferentiated")
    if args.include_ki67:
        cols.append("KI67")

    ini2mrn = load_mrn_map()
    wb = load_workbook(PATHS.YT_XLSX)
    ws = wb["cln"]
    header = [c.value for c in ws[1]]
    pid_col = header.index("Patient_ID") + 1
    pos = {}
    for c in cols:
        if c in header:
            pos[c] = header.index(c) + 1
        else:
            pos[c] = len([h for h in header if h is not None]) + 1
            ws.cell(row=1, column=pos[c], value=c)
            header.append(c)

    filled, prov = {}, []
    for r in range(2, ws.max_row + 1):
        pid = ws.cell(row=r, column=pid_col).value
        pid = "" if pid is None else str(pid).strip()
        mrn = ini2mrn.get(pid)
        if not mrn or not records_sorted(mrn, files):
            continue
        got = extract(mrn, files)
        for c in cols:
            cur = ws.cell(row=r, column=pos[c]).value
            if cur is not None and str(cur).strip() != "":
                continue                       # ⚠ 只填空，绝不覆盖已有值
            v, q = got[c]
            if v == "" or v is None:
                continue
            ws.cell(row=r, column=pos[c], value=v)
            filled[c] = filled.get(c, 0) + 1
            prov.append({"Patient_ID": pid, "病历卡号": mrn, "列": c, "填入值": v, "原文": q})

    PATHS.ensure_dirs()
    shutil.copy2(PATHS.YT_XLSX, PATHS.BACKUP / PATHS.YT_XLSX.name)
    print(f"[bak] {PATHS.BACKUP / PATHS.YT_XLSX.name}")
    wb.save(PATHS.YT_XLSX)

    print("[ok] 填补：" + "，".join(f"{c}={n}" for c, n in
                                 sorted(filled.items())) if filled else "[ok] 没有可填的空格")
    if prov:
        md = PATHS.REF / "yt_RDL_cln_Results填补说明.md"
        md.write_text("\n".join(
            ["# cln 用 Results.7z 填补说明", "",
             "只填**原文明确写了**的值，且**只填空格**，不覆盖已有值。", "",
             "| Patient_ID | 病历卡号 | 列 | 填入值 | 原文 |", "|---|---|---|---|---|"] +
            [f"| {p['Patient_ID']} | {p['病历卡号']} | {p['列']} | {p['填入值']} | {p['原文']} |"
             for p in prov]), encoding="utf-8")
        print(f"[out] {md}（{len(prov)} 条逐格出处）")
    print(f"[out] {PATHS.YT_XLSX}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
