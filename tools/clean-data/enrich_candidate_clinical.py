# -*- coding: utf-8 -*-
"""把 yt_RDL.xlsx 的临床/病理列并入候选表。

新增列（列名沿用 `yt_RDL.xlsx` 的 `cln` 表）：
    Gender, Age, KI67, CDK4, MDM2, Surrounding involving, Tumor density, Satellite nodules

关联方式
--------
候选表只有「姓名」，`yt_RDL.xlsx` 只有 `Patient_ID`（姓名拼音首字母）。因此按
**拼音首字母** 关联，并用 **性别 + 年龄** 做二次校验（年龄取自候选表的 年龄_首次/末次）：

| 匹配可信度 | 判据 | 处理 |
|---|---|---|
| 高 | 性别一致 且 年龄差 ≤ 3 岁 | 填入 |
| 中 | 性别一致 但 年龄差 > 3 岁 | 填入并标注（疑似同名同首字母的他人） |
| 低 | 性别不一致 | **不填**（几乎肯定不是同一人） |
| — | 首字母在 yt_RDL 中不存在 | 留空，`临床资料匹配=未匹配` |

`yt_RDL.xlsx` 的 `cln` 表有 118 行 / 111 个唯一 `Patient_ID`，其中 2 个 ID
（WJM、ZJX）的重复行取值互相矛盾；本脚本取第一次出现的行，并在 QC 中列出。

运行：
    set PYTHONDONTWRITEBYTECODE=1
    python scripts\\yt_rdl_add_clinical.py            # 写回原表（先备份到 work\\backup）
    python scripts\\yt_rdl_add_clinical.py --check    # 只复核，不写
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

import pandas as pd

import tidy_io  # noqa: E402
import tidy_yt_rdl as PATHS  # noqa: E402

ROOT = PATHS.PROJECT_ROOT

DEFAULT_XLSX = PATHS.CAND_XLSX
YT_XLSX = PATHS.YT_XLSX
CLIN_COLS = ["Gender", "Age", "KI67", "CDK4", "MDM2", "Surrounding involving",
             "Tumor density", "Satellite nodules"]
META_COLS = ["临床资料匹配", "匹配可信度", "性别一致", "年龄差(岁)"]
NEW_COLS = CLIN_COLS + META_COLS
AGE_TOL = 3


def pinyin_initials(name: str) -> str:
    try:
        from pypinyin import lazy_pinyin, Style
        return "".join(lazy_pinyin(str(name), style=Style.FIRST_LETTER)).upper()
    except Exception:
        return ""


def load_yt() -> tuple[pd.DataFrame, list[str]]:
    """读 cln 表，按 Patient_ID 去重；返回 (表, 重复且取值冲突的 ID)。"""
    # 只取本课题原始判读队列：回填脚本追加的候选患者行不是判读结果来源
    yt = PATHS.read_yt_cln(cohort_only=True)
    missing = [c for c in CLIN_COLS if c not in yt.columns]
    if missing:
        raise KeyError(f"yt_RDL.xlsx/cln 缺少列：{missing}")
    conflict = []
    for pid, sub in yt.groupby("Patient_ID"):
        if len(sub) > 1 and sub[CLIN_COLS].nunique().max() > 1:
            conflict.append(pid)
    return yt.drop_duplicates("Patient_ID", keep="first").reset_index(drop=True), conflict


def sex_ok(a, b) -> str:
    a, b = str(a).strip(), str(b).strip()
    if not a or a == "nan" or not b or b == "nan":
        return ""
    if a == "男":
        return "是" if b.lower().startswith("m") else "否"
    if a == "女":
        return "是" if b.lower().startswith("f") else "否"
    return ""


def attach(df: pd.DataFrame, yt: pd.DataFrame) -> pd.DataFrame:
    """按拼音首字母关联并回填临床列。"""
    df = df.drop(columns=[c for c in NEW_COLS if c in df.columns]).copy()
    ini = df["姓名"].astype(str).map(lambda n: pinyin_initials(n.split("/")[0].strip()))
    yt_by = {r["Patient_ID"]: r for _, r in yt.iterrows()}

    recs = []
    for (_, r), ini_v in zip(df.iterrows(), ini):
        y = yt_by.get(ini_v)
        rec = {c: "" for c in CLIN_COLS}
        rec.update({"临床资料匹配": "未匹配", "匹配可信度": "", "性别一致": "", "年龄差(岁)": ""})
        if y is not None:
            s_ok = sex_ok(r.get("性别"), y["Gender"])
            ages = [a for a in [r.get("年龄_首次"), r.get("年龄_末次")]
                    if pd.notna(a) and str(a).strip() != ""]
            try:
                ay = float(y["Age"])
            except (TypeError, ValueError):
                ay = None
            d = min((abs(float(a) - ay) for a in ages), default=None) if (ages and ay is not None) else None
            if s_ok == "是" and d is not None and d <= AGE_TOL:
                conf = "高（性别一致，年龄差≤3岁）"
            elif s_ok == "是":
                conf = f"中（性别一致，年龄差{d:.0f}岁，疑似同名同首字母的他人）" if d is not None \
                    else "中（性别一致，年龄不可比）"
            elif s_ok == "否":
                conf = "低（性别不符，不采用）"
            else:
                conf = "中（性别不可比）"
            rec.update({"匹配可信度": conf, "性别一致": s_ok,
                        "年龄差(岁)": "" if d is None else int(d)})
            if conf.startswith(("高", "中")):
                rec.update({c: y[c] for c in CLIN_COLS})
                rec["临床资料匹配"] = "已匹配"
            else:
                rec["临床资料匹配"] = "性别不符-不采用"
        recs.append(rec)
    return pd.concat([df.reset_index(drop=True), pd.DataFrame(recs)], axis=1)


def qc_rows(out: pd.DataFrame, yt: pd.DataFrame, conflict: list[str]) -> list[dict]:
    q = []

    def add(a, b, c=""):
        q.append({"检查项": a, "结果": b, "涉及行数/患者数": c})

    vc = out["临床资料匹配"].value_counts().to_dict()
    add("临床资料匹配（yt_RDL.xlsx/cln）",
        "，".join(f"{k}={v}" for k, v in vc.items()), len(out))
    add("yt_RDL 队列规模", f"cln {len(yt)} 行 / {yt['Patient_ID'].nunique()} 个唯一 Patient_ID", len(yt))
    if conflict:
        add("yt_RDL 内同一 Patient_ID 取值冲突", "/".join(conflict) + "（已取首次出现的行，建议核实原表）",
            len(conflict))
    conf = out.loc[out["临床资料匹配"] == "已匹配", "匹配可信度"]
    add("匹配可信度分布", "，".join(f"{k}={v}" for k, v in conf.value_counts().items()),
        int(conf.str.startswith("高").sum()))
    mid = out[out["匹配可信度"].astype(str).str.startswith("中")]
    if len(mid):
        add("年龄差>3 岁的匹配（需人工核实）",
            "；".join(f"{r['姓名']}({r['病历卡号']}) 差{int(r['年龄差(岁)']) if str(r['年龄差(岁)'])!='' else '?'}岁"
                     for _, r in mid.iterrows()), len(mid))
    non = out[out["临床资料匹配"] == "未匹配"]
    add("未匹配到 yt_RDL 的候选患者",
        "首字母在 cln 中不存在（这批人未入组队列，或姓名写法不同）", len(non))
    # 完整性
    for c in CLIN_COLS:
        filled = int(out[c].astype(str).str.strip().ne("").sum())
        add(f"　{c} 已填行数", f"{filled}/{len(out)}", filled)
    return q


def _norm(v) -> str:
    """写回 Excel 后再读，空串会变 NaN、整数会变 66.0，统一后再比。"""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    s = str(v).strip()
    if s.lower() == "nan":
        return ""
    if re.fullmatch(r"-?\d+\.0", s):
        s = s[:-2]
    return s


def run_check(path: Path) -> int:
    out = pd.read_excel(path, sheet_name="01_候选患者_合并")
    yt, conflict = load_yt()
    print(f"表：{path.name}  行数 {len(out)}")
    miss = [c for c in NEW_COLS if c not in out.columns]
    if miss:
        print("  FAIL  缺少列：", miss)
        return 1
    print("  匹配分布：", out["临床资料匹配"].value_counts().to_dict())
    print("  可信度：", out.loc[out["临床资料匹配"] == "已匹配", "匹配可信度"]
          .str.slice(0, 1).value_counts().to_dict())

    fail = []
    # 1) 重建一遍，逐格比对
    rebuilt = attach(out, yt)
    for c in NEW_COLS:
        a = [_norm(v) for v in out[c].tolist()]
        b = [_norm(v) for v in rebuilt[c].tolist()]
        diff = sum(1 for x, y in zip(a, b) if x != y)
        if diff:
            fail.append(f"{c} 有 {diff} 行与重建不符")
    # 2) 已匹配的行，性别必须一致；年龄差 ≤3 才能是高
    bad = out[(out["临床资料匹配"] == "已匹配") & (out["性别一致"] == "否")]
    if len(bad):
        fail.append(f"{len(bad)} 行性别不符却填了资料")
    hi = out[out["匹配可信度"].astype(str).str.startswith("高")]
    bad2 = hi[pd.to_numeric(hi["年龄差(岁)"], errors="coerce") > AGE_TOL]
    if len(bad2):
        fail.append(f"{len(bad2)} 行年龄差>3 却标为高可信度")
    # 3) 未匹配的行临床列必须为空
    non = out[out["临床资料匹配"] == "未匹配"]
    if len(non) and any(_norm(v) != "" for c in CLIN_COLS for v in non[c].tolist()):
        fail.append("有未匹配行却填了临床资料")
    # 4) 与 yt_RDL 原值逐格一致
    yt_by = {r["Patient_ID"]: r for _, r in yt.iterrows()}
    for _, r in out[out["临床资料匹配"] == "已匹配"].iterrows():
        y = yt_by.get(pinyin_initials(str(r["姓名"]).split("/")[0].strip()))
        for c in CLIN_COLS:
            if _norm(r[c]) != _norm(y[c]):
                fail.append(f"{r['病历卡号']} {c}: 表={r[c]} vs yt={y[c]}")
                break
    print("  重建逐格比对 + 一致性检查：", "通过" if not fail else "失败")
    for f in fail[:10]:
        print("    -", f)
    return 1 if fail else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="把 yt_RDL 临床/病理列并入候选表")
    ap.add_argument("--xlsx", default=str(DEFAULT_XLSX))
    ap.add_argument("--check", action="store_true", help="只复核，不改文件")
    args = ap.parse_args()
    path = Path(args.xlsx)

    if args.check:
        return run_check(path)

    yt, conflict = load_yt()
    xl = pd.ExcelFile(path)
    sheets = {s: xl.parse(s) for s in xl.sheet_names}

    out = attach(sheets["01_候选患者_合并"], yt)
    sheets["01_候选患者_合并"] = out
    if "03_排除患者_其他诊断" in sheets:
        sheets["03_排除患者_其他诊断"] = attach(sheets["03_排除患者_其他诊断"], yt)

    qc = sheets.get("05_QC_键值核算", pd.DataFrame(columns=["检查项", "结果", "涉及行数/患者数"]))
    qc = pd.concat([qc, pd.DataFrame(qc_rows(out, yt, conflict))], ignore_index=True)
    sheets["05_QC_键值核算"] = qc

    backup = PATHS.BACKUP
    backup.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, backup / path.name)
    print(f"[bak] {backup / path.name}")

    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        for name, d in sheets.items():
            d.to_excel(xw, sheet_name=name[:31], index=False)
    from openpyxl import load_workbook
    from openpyxl.styles import Alignment
    wb = load_workbook(path)
    for name, d in sheets.items():
        ws = wb[name[:31]]
        tidy_io.style_header(ws, max(len(d.columns), 1))
        for row in ws.iter_rows(min_row=2):
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=False)
        for i, cn in enumerate(d.columns, start=1):
            ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = min(
                max(len(str(cn)) * 2, 12), 34)
    wb.save(path)

    print(f"[ok] 新增列：{' | '.join(NEW_COLS)}")
    print("     匹配分布：" + "，".join(f"{k}={v}" for k, v in
                                    out["临床资料匹配"].value_counts().items()))
    print("     可信度：" + "，".join(f"{k}={v}" for k, v in
                                   out.loc[out["临床资料匹配"] == "已匹配", "匹配可信度"]
                                   .str.slice(0, 1).value_counts().items()))
    csv = PATHS.CAND_CSV
    out.to_csv(csv, index=False, encoding="utf-8-sig")
    print(f"[out] {path}")
    print(f"[out] {csv}")
    return run_check(path)


if __name__ == "__main__":
    raise SystemExit(main())
