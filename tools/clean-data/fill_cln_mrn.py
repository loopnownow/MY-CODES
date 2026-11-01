# -*- coding: utf-8 -*-
"""在 yt_RDL.xlsx / cln 的**最后一列**追加「病历卡号」。

数据来源：候选表（`ref/yt_RDL_脂肪肉瘤候选_合并表.xlsx` 的 `01_候选患者_合并`）。
候选表的 `病历卡号` 与 `Results.7z` 的查询号一致，便于把 cln 反查回原始病历。

填哪些行
--------
* 与候选表 `Patient_ID`（姓名拼音首字母）匹配且 **性别+年龄双重吻合（可信度=高）** 的原有行；
* 追加进来的新患者行（按 `Patient_ID` 直接取候选表的病历卡号）；
* 其余原有行（本课题队列里不在这 109 位候选中的患者）**留空**——没有任何来源；
* `ZYZ`（朱义忠，可信度=中、疑似同名他人）同样留空，与随访列的处理保持一致。

注意
----
10 位患者的候选表 `病历卡号` 已被改成 `Results.7z` 查询时用的号（多为门诊号/住院号），
与 `ref/*.xls` 影像报告里的原始病历卡号不同；两者都记录在候选表的
`编号别名(合并用)` 列里。本列写的是**候选表的号**（与 Results 导出一致，便于溯源）。

运行：
    python yt_rdl_add_mrn_col.py --check    # 只核对
    python yt_rdl_add_mrn_col.py            # 写回（先备份到 0del）
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook

import tidy_yt_rdl as PATHS  # noqa: E402

COL = "病历卡号"


def build_map() -> tuple[dict, pd.DataFrame]:
    """返回 (Patient_ID → 病历卡号, 候选表)。"""
    fu = pd.read_excel(PATHS.CAND_XLSX, sheet_name="01_候选患者_合并")
    fu["_ini"] = fu["拼音首字母"].astype(str).str.strip()
    hi = fu[fu["匹配可信度"].astype(str).str.startswith("高")].drop_duplicates("_ini")
    m = {r["_ini"]: r["病历卡号"] for _, r in hi.iterrows()}
    return m, fu


def original_ids() -> set:
    """回填前快照里的 Patient_ID（用于区分「原有行」和「追加行」）。"""
    b = PATHS.BACKUP / PATHS.YT_XLSX.name
    if b.exists():
        return set(pd.read_excel(b, sheet_name="cln")["Patient_ID"].astype(str).str.strip())
    return set()


def compute(cln: pd.DataFrame) -> pd.Series:
    """按 Patient_ID 回填病历卡号。

    区分「原有行」与「追加行」不看快照（快照可能被覆盖），而看候选表自己的
    ``匹配yt_RDL_Patient_ID``：它是在 cln 还是 118 行时算出来的——
    有值 = 原始队列成员（只在高可信度时填），空 = 当初被追加进来的新患者（直接填）。
    """
    fu = pd.read_excel(PATHS.CAND_XLSX, sheet_name="01_候选患者_合并")
    fu["_ini"] = fu["拼音首字母"].astype(str).str.strip()
    hi = fu[fu["匹配可信度"].astype(str).str.startswith("高")].drop_duplicates("_ini")
    m_hi = {r["_ini"]: r["病历卡号"] for _, r in hi.iterrows()}
    m_all = {r["_ini"]: r["病历卡号"] for _, r in fu.iterrows()}
    in_cohort = set(fu.loc[fu["匹配yt_RDL_Patient_ID"].notna() &
                           (fu["匹配yt_RDL_Patient_ID"].astype(str).str.strip() != ""), "_ini"])

    out = []
    for p in cln["Patient_ID"].astype(str).str.strip():
        if p in m_hi and p in in_cohort:        # 原有行 + 高可信度
            out.append(m_hi[p])
        elif p not in in_cohort and p in m_all:  # 当初追加进来的新患者行
            out.append(m_all[p])
        else:                                    # 原有行但没有可靠来源
            out.append("")
    return pd.Series(out, index=cln.index)


def num(v):
    s = "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v).strip()
    if re.fullmatch(r"\d+\.0", s):
        s = s[:-2]
    return s


def run_check(xlsx: Path) -> int:
    cln = pd.read_excel(xlsx, sheet_name="cln")
    fail = []
    if COL not in cln.columns:
        print(f"  FAIL  cln 没有「{COL}」列")
        return 1
    if list(cln.columns)[-1] != COL:
        fail.append(f"「{COL}」不是最后一列（现在是 {list(cln.columns)[-1]}）")
    want = compute(cln)
    got = cln[COL].map(num)
    diff = [i for i, (a, b) in enumerate(zip(want.map(num), got)) if a != b]
    if diff:
        fail.append(f"{len(diff)} 行与重建不符，样例 {[(i, want.iloc[i], got.iloc[i]) for i in diff[:5]]}")
    print(f"  已填 {int(got.ne('').sum())}/{len(cln)} 行；"
          f"空 {int(got.eq('').sum())} 行")
    print("  重建逐格比对：", "通过" if not fail else "失败")
    for f in fail:
        print("    -", f)
    return 1 if fail else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="在 cln 最后一列追加病历卡号")
    ap.add_argument("--xlsx", default=str(PATHS.YT_XLSX))
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    xlsx = Path(args.xlsx)

    if args.check:
        return run_check(xlsx)

    cln = pd.read_excel(xlsx, sheet_name="cln")
    vals = compute(cln)

    PATHS.ensure_dirs()
    backup = PATHS.BACKUP / xlsx.name
    if backup.exists():
        shutil.copy2(backup, PATHS.BACKUP / (xlsx.stem + "_prev.xlsx"))
    shutil.copy2(xlsx, backup)
    print(f"[bak] {backup}")

    # 只用 openpyxl 改 cln 一张表：rad/qc/icc 连单元格样式都不动，
    # 也避免 pandas ExcelWriter 先截断目标文件再回读的坑。
    wb = load_workbook(xlsx)
    ws = wb["cln"]
    header = [c.value for c in ws[1]]
    if COL in header:
        ci = header.index(COL) + 1
    else:
        ci = len([h for h in header if h is not None]) + 1
        ws.cell(row=1, column=ci, value=COL)
    for k, v in enumerate(vals.tolist()):
        ws.cell(row=2 + k, column=ci, value=(v if v != "" else None))
    wb.save(xlsx)

    filled = int(vals.map(num).ne("").sum())
    print(f"[ok] cln 末列追加「{COL}」：{filled}/{len(cln)} 行有值，"
          f"{len(cln) - filled} 行留空（队列中不在这 109 位候选内的患者）")
    print(f"[out] {xlsx}")
    return run_check(xlsx)


if __name__ == "__main__":
    raise SystemExit(main())
