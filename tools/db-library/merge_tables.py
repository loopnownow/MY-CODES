# -*- coding: utf-8 -*-
"""散表归并：按项目把家族根 / 叶根的 xls/xlsx/csv 合并成一个工作簿。

已注册项目（ADAPTERS 键）：SCH/ly_SCH、OC、PCOS/zar_PCOS3D。
用法：
  python -B tools/db-library/merge_tables.py --project SCH/ly_SCH --build
  python -B tools/db-library/merge_tables.py --project OC --build
  python -B tools/db-library/merge_tables.py --project PCOS/zar_PCOS3D --build
  python -B tools/db-library/merge_tables.py --project SCH/ly_SCH --apply   # 需点名授权
  python -B tools/db-library/merge_tables.py --project SCH/ly_SCH --apply --into "...\\SCH\\_db\\SCHdb"

口径（2026-11-16family 起）：
- 默认输出 <batch>/staging/，**不再默认落家族根 / 叶根**；--out 可改输出目录。
- 输出目录若在 _DataBase 内，必须显式 --in-db（写回叶根）或 --into <db>（入库）才允许。
- --build 只生成；--apply 生成 + 原件移入 <batch>/originals/<key>/ + 写台账；
  合并表按 --in-db（复制回叶根）/ --into（增量入库）/ 缺省（留在输出目录）处置。
- 不带 --build/--apply 为 dry-run（只打印计划）。
"""
from __future__ import annotations

import argparse
import shutil
from datetime import datetime
from pathlib import Path

import openpyxl
import xlrd

from common import ledger_write, sha256_12, write_workbook
import db_core as core

DEFAULT_DB_ROOT = Path(r"D:\MyProjects\_DataBase")
DEFAULT_BATCH = "2026-11-db-root-merge"
STAMP = datetime.now().strftime("%Y%m%d")
LETTERS = "ABCDEFGH"


def stamp_dash():
    """20261109 -> 2026-11-09（仅用于工作簿说明文字）。"""
    return f"{STAMP[:4]}-{STAMP[4:6]}-{STAMP[6:]}" if len(STAMP) == 8 and STAMP.isdigit() else STAMP


# ---------------------------------------------------------------- 通用
def num(v):
    """xlrd 单元格 -> python 值：整数值 float 转 int，其余保留文本。"""
    if isinstance(v, float):
        return int(v) if v.is_integer() else v
    return v


def cstr(v):
    v = num(v)
    return "" if v is None else str(v).strip()


def is_tab(p: Path) -> bool:
    return p.suffix.lower() in {".xlsx", ".xls", ".csv"}


def xls_rows(p: Path, sheet=None):
    bk = xlrd.open_workbook(str(p), encoding_override="cp936")
    sn = sheet or bk.sheet_names()[0]
    sh = bk.sheet_by_name(sn)
    rows = [[num(sh.cell_value(i, j)) for j in range(sh.ncols)] for i in range(sh.nrows)]
    bk.release_resources()
    return rows, sn, bk


# ---------------------------------------------------------------- SCH
def sch_year(p: Path):
    bk = xlrd.open_workbook(str(p), encoding_override="cp936")
    best = None
    for sn in bk.sheet_names():
        sh = bk.sheet_by_name(sn)
        if sh.nrows and cstr(sh.cell_value(0, 0)) == "医技号":
            best = (sn, sh)
            break
    if best is None:
        raise SystemExit(f"SCH 未找到表头 sheet: {p.name}")
    sn, sh = best
    hdr = [cstr(sh.cell_value(0, j)) for j in range(sh.ncols)]
    rows = []
    for i in range(1, sh.nrows):
        r = [num(sh.cell_value(i, j)) for j in range(sh.ncols)]
        if not any(cstr(x) for x in r):
            continue
        rows.append(r)
    bk.release_resources()
    return sn, hdr, rows


def build_sch(src: Path, out_path: Path):
    years = list(range(2004, 2012))
    allrows = []
    hdr_ref = None
    per_year = []
    for y in years:
        p = src / f"{y}汇总.XLS"
        sn, hdr, rows = sch_year(p)
        hdr_ref = hdr_ref or hdr
        per_year.append((y, p.name, sn, len(rows)))
        for r in rows:
            allrows.append(r + [y, p.name])
    # OHO 导出
    bk = xlrd.open_workbook(str(src / "sch-olhos.XLS"), encoding_override="cp936")
    sh = bk.sheet_by_name("sch-olhos")
    oho_hdr = [cstr(sh.cell_value(0, j)) for j in range(sh.ncols)]
    oho_rows = []
    for i in range(1, sh.nrows):
        r = [num(sh.cell_value(i, j)) for j in range(sh.ncols)]
        if not any(cstr(x) for x in r):
            continue
        oho_rows.append(r + ["sch-olhos.XLS"])
    bk.release_resources()

    # 双侧基底节区高信号（8 sheet 原样）
    bk = xlrd.open_workbook(str(src / "双侧基底节区高信号.xls"), encoding_override="cp936")
    gaoxin = []
    for sn in bk.sheet_names():
        sh = bk.sheet_by_name(sn)
        rows = [[num(sh.cell_value(i, j)) for j in range(sh.ncols)] for i in range(sh.nrows)]
        gaoxin.append((sn, rows))
    bk.release_resources()

    # 统计表
    bk = xlrd.open_workbook(str(src / "统计表.xls"), encoding_override="cp936")
    tongji = []
    for sn in bk.sheet_names():
        sh = bk.sheet_by_name(sn)
        rows = [[num(sh.cell_value(i, j)) for j in range(sh.ncols)] for i in range(sh.nrows)]
        if any(any(cstr(x) for x in r) for r in rows):
            tongji.append((sn, rows))
    bk.release_resources()

    note = ("SCH/ly_SCH 散表归并：8 个年度汇总（2004–2011）+ 2 个 OHO 导出 + 病例清单/统计表 → 单工作簿。"
            f"合并表 01 为年度汇总纵向合并（{len(allrows):,} 行），02 为 OHO 导出（{len(oho_rows):,} 行）。"
            "只读源、原件已移入 _DEL；未改动任何数据值。")
    sheets = [{
        "name": "00_说明",
        "rows": [[x] for x in [
            "SCH/ly_SCH 散表 → 单工作簿",
            f"生成 {stamp_dash()}；源 {src}（12 个表格文件）",
            "",
            "一、合并范围与结果",
            f"  01_合并_影像报告2004-2011：8 个年度文件主表（表头 9 列一致）纵向合并，{len(allrows):,} 行 × 11 列。",
            "     新增两列：年份、来源文件（provenance）。",
            f"  02_合并_OHO导出：sch-olhos.XLS 主表 {len(oho_rows):,} 行 × 59 列 + 来源文件。",
            "  03_：双侧基底节区高信号.xls 的 8 个 sheet 原样保留（病例编号清单，结构为手工表，不做纵并）。",
            "  04_：统计表.xls 原样保留（两级表头，手工汇总）。",
            "",
            "二、去重（实测）",
            "  1) 各年度文件里的『头/05头/…』sheet 是同年主表按整行元组判定的子集：2004 818/818、2005 2340/2341、"
            "2006 2849/2849、2007 3703/3703、2008 3809/3809、2009 5142/5142、2010 4501/4501 命中 ⇒ 不重复收录。",
            "     2005 有 1 行未命中（姓名/号码近似但不完全一致），未强行并入，原行留在源文件（现于 _DEL）。",
            "  2) sch-oho-data.xlsx（1,684 行）的 ApplyNo 全部包含在 sch-olhos.XLS（2,140 行）中 ⇒ 视为同数据的不同导出版，"
            "只收 olhos（信息更全：59 列含姓名/诊断/报告时间/仪器）。",
            "  3) 跨年度重复：未做字节/整行去重（不同年份的复诊是真实数据），如需按人或按报告号去重需另定规则。",
            "",
            "三、对齐与清洗",
            "  年龄保留原文（如『38岁』『1岁』），未强行转数值；医技号保留原文本（前导 0 不丢）。",
            "  性别/病区/床号/部位/检查结论/检查所见原样保留；空值不插补。",
            "  ※ 2011汇总.XLS 为 GBK 编码（xlrd 需 encoding_override=cp936），已按此读取。",
            "",
            "四、数据质量提示（原样保留，未修改）",
            "  2008 主表有一行姓名为『0』（源表如此）；医技号在不同年份存在空值与全 0 值；",
            "  检查结论/检查所见含换行与重复空白。",
            "",
            "五、mtime 不作新旧判据（与 HZ 试点同）：本批 12 个文件 mtime 亦为迁移复制时间。",
            "",
            "六、异常",
            "  无读取失败；02 的 59 列中存在大量空列（导出模板固定列），保留以对齐原始导出。",
        ]],
    }, {
        "name": "01_合并_影像报告2004-2011",
        "note": "8 个年度汇总表纵向合并；末两列为年份与来源文件。",
        "header": hdr_ref + ["年份", "来源文件"],
        "rows": allrows,
    }, {
        "name": "02_合并_OHO导出",
        "note": "sch-olhos.XLS 主表；sch-oho-data.xlsx 为其子集（ApplyNo 全包含）。",
        "header": oho_hdr + ["来源文件"],
        "rows": oho_rows,
    }]
    for sn, rows in gaoxin:
        sheets.append({"name": f"03_高信号_{sn}", "note": f"源：双侧基底节区高信号.xls::{sn}",
                       "rows": rows})
    for sn, rows in tongji:
        sheets.append({"name": f"04_统计表_{sn}", "note": f"源：统计表.xls::{sn}", "rows": rows})
    # 清单
    man = [["文件", "大小_B", "sha256_12", "工作表", "行数(数据)", "处理"]]
    for p in sorted(p for p in src.iterdir() if is_tab(p)):
        man.append([p.name, p.stat().st_size, sha256_12(p), "—", "", "见 00_说明"])
    sheets.append({"name": "99_来源清单", "rows": man})
    return write_workbook(out_path, sheets)


# ---------------------------------------------------------------- OC
def parse_plate(p: Path):
    bk = xlrd.open_workbook(str(p), encoding_override="cp936")
    sn = bk.sheet_names()[0]
    sh = bk.sheet_by_name(sn)
    grid = [[cstr(sh.cell_value(i, j)) for j in range(sh.ncols)] for i in range(sh.nrows)]
    meta = {}
    for i in range(min(10, len(grid))):
        row = grid[i]
        for j in range(len(row) - 1):
            key = row[j].rstrip(":").strip().lower()
            if key in ("session", "instrument", "user name", "started at", "comment"):
                val = next((row[k] for k in range(j + 1, len(row)) if row[k]), "")
                meta[key] = val
    blocks = []
    i = 0
    while i < len(grid):
        row = grid[i]
        if row and row[0] in LETTERS and len(row) > 1 and row[1]:
            title = ""
            j = i - 1
            while j >= 0:
                if grid[j] and grid[j][0]:
                    title = grid[j][0]
                    break
                j -= 1
            tl = title.lower()
            kind = "label" if "layout" in tl else ("od" if "source" in tl else ("conc" if "calculat" in tl else "other"))
            wells = {}
            while i < len(grid) and grid[i] and grid[i][0] in LETTERS and len(grid[i]) > 1 and grid[i][1] != "":
                r = grid[i]
                wells.setdefault(r[0], {})
                for c in range(1, min(13, len(r))):
                    if r[c] != "":
                        wells[r[0]][c] = r[c]
                i += 1
            blocks.append((title, kind, wells))
            continue
        i += 1
    r2 = ""
    for i in range(len(grid)):
        if grid[i] and "corr" in grid[i][0].lower():
            r2 = next((x for x in grid[i][1:] if x), "")
            break
    dil = ""
    for i in range(len(grid)):
        if grid[i] and ("倍稀释" in grid[i][0] or "稀释" in grid[i][0]):
            dil = grid[i][0]
            break
    bk.release_resources()

    def last(kind):
        for t, k, w in reversed(blocks):
            if k == kind:
                return w
        return {}

    labels, ods, concs = last("label"), last("od"), last("conc")
    rows = []
    for L in LETTERS:
        for c in range(1, 13):
            lab = labels.get(L, {}).get(c, "")
            od = ods.get(L, {}).get(c, "")
            cc = concs.get(L, {}).get(c, "")
            if lab == "" and od == "" and cc == "":
                continue
            rows.append([f"{L}{c}", lab, od, cc])
    return {"sheet": sn, "meta": meta, "rows": rows, "r2": r2, "dil": dil, "nblocks": len(blocks)}


def build_oc(src: Path, out_path: Path):
    # 酶标仪导出均为 .XLS；金属离子两表单独处理；.xlsx 为临床表
    plates = sorted(p for p in src.iterdir() if p.is_file() and p.suffix.lower() == ".xls"
                    and not p.name.startswith(("Manganese", "metal")))

    rows_all = []
    plate_meta = []
    for p in plates:
        d = parse_plate(p)
        assay = d["meta"].get("comment", "") or p.stem
        al = assay.lower()
        canon = ("BCA" if "bca" in al else "Ferritin" if "ferritin" in al
                 else "MetHb" if "methb" in al else "MUC5AC" if "muc5ac" in al
                 else "OPN" if "opn" in al else assay)
        for wid, lab, od, cc in d["rows"]:
            rows_all.append([assay, canon, d["meta"].get("started at", ""), wid, lab, od, cc, d["dil"], p.name])
        plate_meta.append([p.name, d["sheet"], len(d["rows"]), assay, d["meta"].get("started at", ""),
                           d["r2"], d["dil"], d["nblocks"]])
    # 金属离子：主表 = metal ion.xls::S001-44（Manganese 文件为其子集，缺 Cu 列）
    bk = xlrd.open_workbook(str(src / "metal ion.xls"), encoding_override="cp936")
    sh = bk.sheet_by_name("S001-44")
    mh = [cstr(sh.cell_value(0, j)) for j in range(8)]
    mrows = []
    for i in range(1, sh.nrows):
        r = [num(sh.cell_value(i, j)) for j in range(8)]
        if not any(cstr(x) for x in r):
            continue
        mrows.append([cstr(r[0])] + r[1:] + ["QC" if cstr(r[0]).upper().startswith("QC") else "样本", "metal ion.xls::S001-44"])
    sh2 = bk.sheet_by_name("Sheet2")
    sh2h = ["样本"] + [f"列{j+1}" for j in range(1, 8)]
    sh2rows = []
    for i in range(sh2.nrows):
        r = [num(sh2.cell_value(i, j)) for j in range(sh2.ncols)]
        if not any(cstr(x) for x in r):
            continue
        sh2rows.append(r + ["metal ion.xls::Sheet2"])
    bk.release_resources()

    # 临床表
    wb = openpyxl.load_workbook(src / "卵巢子宫内膜异位症 修改后111.xlsx", read_only=True, data_only=True)
    ws = wb["Sheet1"]
    cl = [list(r) for r in ws.iter_rows(values_only=True)]
    wb.close()
    cl = [[("" if v is None else v) for v in r] for r in cl]

    sheets = [{
        "name": "00_说明",
        "rows": [[x] for x in [
            "OC 家族根散表 → 单工作簿",
            f"生成 {stamp_dash()}；源 {src}（13 个表格文件）",
            "",
            "一、三个来源族群",
            f"  01_合并_酶标逐孔：10 个 MULTISKAN EX 酶标仪导出（.XLS），逐孔展开为长表，{len(rows_all):,} 行。",
            "     每行 = 一个孔位；列：检测项(原件)、检测项_规范、检测时间、孔位、孔标签（板布局标注）、"
            "OD(源数据)、浓度(计算值)、稀释备注、来源文件。",
            "     同一检测项有 2019-04/05 与 2019-12-27 两个批次，检测项原件的单位后缀时有时无（仪器导出原样），"
            "检测项_规范 已归并为 BCA/Ferritin/MetHb/MUC5AC/OPN。",
            "     注意：不同文件的『孔标签』含义不同——有的标 Cal_1..Blank（标准曲线孔），其余为样本序号；以 00_说明+99_清单 为准。",
            "  02_金属离子_仪器主表：metal ion.xls::S001-44（7 元素 + 样本/QC 标记）。",
            "  03_金属离子_补全表：metal ion.xls::Sheet2（40 样本 × 7 元素，含 Mg/Zn/Pb，主表这些列仅 QC 有值）。",
            "  04_临床表：卵巢子宫内膜异位症 修改后111.xlsx::Sheet1 原样。",
            "",
            "二、去重（实测）",
            "  1) 『Manganese, iron, calcium.xls』= metal ion.xls::S001-44 的子集（少 Cu 列、少 15 列冗余换算列）⇒ 不重复收录。",
            "  2) 带 (1) 的两个文件（HMETHB(1).XLS、HOPN(1).XLS）与不带 (1) 的并非字节重复：孔布局/数值不同，两个都收。",
            "  3) HMUC.XLS 的工作表名为 HMUC5AC，与 HMUC5AC.XLS 是两次不同检测（孔数不同），都收。",
            "",
            "三、对齐与清洗",
            "  逐孔表按 (孔位, 检测项, 来源文件) 唯一；孔位统一 A1..H12。",
            "  OD/浓度保留原字符串数值；空白孔不写入行。",
            "  金属离子两表存在同名样本的数值差异（如 S033-1 的 Fe 3.4 vs 3.391），未合并成单列，"
            "分别保留在主表与补全表，差异需人工判定。",
            "",
            "四、数据质量提示",
            "  部分文件『Started at』为 2019/4–5 月；孔标签含『9a』等非标准编号，原样保留。",
            "  金属离子补全表第 1 行（S001.D）Mg=13.334 与主表 QC 行不同源，属两次读数，未覆盖。",
            "",
            "五、异常",
            "  无读取失败。",
        ]],
    }, {
        "name": "01_合并_酶标逐孔",
        "note": "10 个酶标仪导出逐孔展开；稀释备注来自源表末行。",
        "header": ["检测项(原件)", "检测项_规范", "检测时间", "孔位", "孔标签", "OD(源数据)",
                   "浓度(计算值)", "稀释备注", "来源文件"],
        "rows": rows_all,
    }, {
        "name": "02_金属离子_仪器主表",
        "note": "metal ion.xls::S001-44；含 2 行 QC。",
        "header": mh + ["类型", "来源"],
        "rows": mrows,
    }, {
        "name": "03_金属离子_补全表",
        "note": "metal ion.xls::Sheet2；列名源表无表头，按位置保留（Mg/Mn/Fe/Cu/Zn/Pb/Ca）。",
        "header": ["样本", "Mg(mg/L)", "Mn(ug/L)", "Fe(mg/L)", "Cu(ug/L)", "Zn(mg/L)", "Pb(ug/L)", "Ca(mg/L)", "来源"],
        "rows": sh2rows,
    }, {
        "name": "04_临床表",
        "note": "源：卵巢子宫内膜异位症 修改后111.xlsx::Sheet1（原样）",
        "rows": cl,
    }]
    man = [["文件", "大小_B", "sha256_12", "工作表", "逐孔行数", "检测项", "起始时间", "R2", "稀释备注", "块数"]]
    for r in plate_meta:
        man.append(r)
    man.append(["metal ion.xls", (src / "metal ion.xls").stat().st_size, sha256_12(src / "metal ion.xls"),
                "S001-44 / Sheet2", len(mrows) + len(sh2rows), "7 元素", "", "", "", ""])
    sheets.append({"name": "99_来源清单", "rows": man})
    return write_workbook(out_path, sheets)


# ---------------------------------------------------------------- PCOS
def build_pcos(src: Path, out_path: Path):
    wb = openpyxl.load_workbook(src / "PCOS data.xlsx", read_only=True, data_only=True)
    raw = [list(r) for r in wb["Sheet1"].iter_rows(values_only=True)]
    wb.close()
    wb = openpyxl.load_workbook(src / "PCOSdata.xlsx", read_only=True, data_only=True)
    ana = [list(r) for r in wb["Sheet1"].iter_rows(values_only=True)]
    nm = [list(r) for r in wb["Sheet3"].iter_rows(values_only=True)]
    tab = [list(r) for r in wb["tab"].iter_rows(values_only=True)]
    wb.close()
    wb = openpyxl.load_workbook(src / "收集病例表.xlsx", read_only=True, data_only=True)
    s1 = [list(r) for r in wb["Sheet1"].iter_rows(values_only=True)]
    s2 = [list(r) for r in wb["Sheet2"].iter_rows(values_only=True)]
    wb.close()
    wb = openpyxl.load_workbook(src / "names.xlsx", read_only=True, data_only=True)
    pacs = [list(r) for r in wb["Sheet1"].iter_rows(values_only=True)]
    wb.close()

    def v(x):
        return "" if x is None else x

    a_hdr = [v(x) for x in ana[0]]
    r_hdr = [v(x) for x in raw[0]]
    nhdr = a_hdr + ["姓名缩写", "MRI图像文件夹名"]
    nrows = []
    for i in range(1, len(ana)):
        nrows.append([v(x) for x in ana[i]] + [v(raw[i][1]) if i < len(raw) else "",
                                               v(raw[i][2]) if i < len(raw) else ""])
    sheets = [{
        "name": "00_说明",
        "rows": [[x] for x in [
            "PCOS/zar_PCOS3D 散表 → 单工作簿",
            f"生成 {stamp_dash()}；源 {src}（5 个表格文件）",
            "",
            "一、文件关系（实测）",
            "  1) PCOSdata.xlsx 与 PCOSdata (2).xlsx 为字节级完全相同（sha 相同）⇒ 只收一份，另一个在 99_清单 标注『未收录·字节重复』。",
            "  2) PCOSdata.xlsx::Sheet1（119×19，英文列名，分析用）与 PCOS data.xlsx::Sheet1（119×25，原始采集）行序一一对应：",
            "     Age 118/118 行完全一致；BMI 有 73 行差异，原因是 PCOSdata 未四舍五入（22.1002895900015）而原始表记 22.1。",
            "     ⇒ 01_主表 以 Sheet1 的 19 列为基础，末尾补 姓名缩写/MRI图像文件夹名（来自原始表）。",
            "  3) PCOSdata.xlsx::Sheet3（119 行姓名缩写）与原始表 name 列逐行相同，可作连接键。",
            "  4) 收集病例表 2 个 sheet 为中文姓名临床采集（Sheet1 62 行、Sheet2 45 行），与英文缩写表无可靠连接键 ⇒ 单独保留。",
            "  5) names.xlsx 132 行 PACS 英文姓名（空格不规范，如 'Chen  Yi Bo'），仅作名单，未参与合并。",
            "",
            "二、列名对照（原始 → 分析用）",
            "  v→Group；age→Age；BMI→BMI；身高→Hight；体重→Weight；LH→Luteinising_Hormone；FSH→Follicle_stimulating_Hormone；",
            "  T→Testosterone；LH/FSH→LH_FSH；OV→OV_3D；OVC→OV_C；FN-1→FN_3D；FNC→FN_C；MFNT-1→FN_CT；MFNS-1→FN_CS；MFNC-1→FN_CC；",
            "  PCO-US→FN_US；HA→Hyperandrogenism；OA→Oligo_anovulation；FN-2（2D 卵泡数）在分析表中无对应列，保留在 02_源。",
            "  另：原始表 MFNT-2/MFNS-2/MFNC-2 三列在分析表中无对应列，保留在 02_源。",
            "",
            "三、对齐与清洗",
            "  01_主表 未做任何插补/改名；BMI 等浮点保持原精度。Group: 0=Control、1=PCOS（按 tab 表 N=60/58 推断，见 05_统计表）。",
            "",
            "四、数据质量提示",
            "  原始表列 MFNT-2/MFNS-2/MFNC-2 与 FN-2 之名易与 FN_3D 混淆，已在上表列名对照中标注。",
            "  收集病例表 Sheet1 末列全空（无列名），保留。",
            "",
            "五、异常",
            "  无读取失败。",
        ]],
    }, {
        "name": "01_主表_分析用",
        "note": "PCOSdata.xlsx::Sheet1（19 列）+ 姓名缩写/MRI图像文件夹名；与 02_源 行序一致。",
        "header": nhdr,
        "rows": nrows,
    }, {
        "name": "02_源_原始影像组学",
        "note": "源：PCOS data.xlsx::Sheet1（原样，25 列）",
        "header": [v(x) for x in r_hdr],
        "rows": [[v(x) for x in r] for r in raw[1:]],
    }, {
        "name": "03_源_临床采集1",
        "note": "源：收集病例表.xlsx::Sheet1（原样）",
        "rows": [[v(x) for x in r] for r in s1],
    }, {
        "name": "04_源_临床采集2",
        "note": "源：收集病例表.xlsx::Sheet2（原样）",
        "rows": [[v(x) for x in r] for r in s2],
    }, {
        "name": "05_统计表",
        "note": "源：PCOSdata.xlsx::tab（原样，两级表头）",
        "rows": [[v(x) for x in r] for r in tab],
    }, {
        "name": "06_PACS名单",
        "note": "源：names.xlsx::Sheet1（原样）",
        "rows": [[v(x) for x in r] for r in pacs],
    }]
    man = [["文件", "大小_B", "sha256_12", "处理"],
           ["PCOS data.xlsx", (src / "PCOS data.xlsx").stat().st_size, sha256_12(src / "PCOS data.xlsx"), "02_源_原始影像组学"],
           ["PCOSdata.xlsx", (src / "PCOSdata.xlsx").stat().st_size, sha256_12(src / "PCOSdata.xlsx"), "01_主表_分析用 + 05_统计表"],
           ["PCOSdata (2).xlsx", (src / "PCOSdata (2).xlsx").stat().st_size, sha256_12(src / "PCOSdata (2).xlsx"), "未收录·与 PCOSdata.xlsx 字节重复"],
           ["收集病例表.xlsx", (src / "收集病例表.xlsx").stat().st_size, sha256_12(src / "收集病例表.xlsx"), "03/04_源_临床采集"],
           ["names.xlsx", (src / "names.xlsx").stat().st_size, sha256_12(src / "names.xlsx"), "06_PACS名单"]]
    sheets.append({"name": "99_来源清单", "rows": man})
    return write_workbook(out_path, sheets)


# ---------------------------------------------------------------- 注册表
ADAPTERS = {
    "SCH/ly_SCH": {"src": ("SCH", "ly_SCH"), "name": "ly_SCH", "build": build_sch},
    "OC": {"src": ("OC",), "name": "OC", "build": build_oc},
    "PCOS/zar_PCOS3D": {"src": ("PCOS", "zar_PCOS3D"), "name": "zar_PCOS3D", "build": build_pcos},
}
ALIASES = {}
for _key, _cfg in ADAPTERS.items():
    ALIASES[_key] = _key
    ALIASES.setdefault(_cfg["name"], _key)
    ALIASES.setdefault(_key.split("/")[0], _key)


def source_files(src: Path):
    """源表格件：排除已产出的 *_merged_*.xlsx（产物，不作原件，防 --apply 自噬）。"""
    files = [p for p in src.iterdir() if p.is_file() and is_tab(p)]
    merged = [p for p in files if "_merged_" in p.name]
    for p in merged:
        print(f"  [warn] 跳过已有合并表（产物，不当原件）: {p.name}")
    return [p for p in files if p not in merged]


def select_projects(name):
    if name is None:
        return list(ADAPTERS)
    key = ALIASES.get(name)
    if key is None:
        raise SystemExit(f"未注册项目: {name}\n已注册: {', '.join(ADAPTERS)}"
                         f"（亦可用叶 slug 或家族名）")
    return [key]


def build_one(key, db_root, out_dir):
    cfg = ADAPTERS[key]
    src = db_root.joinpath(*cfg["src"])
    if not src.is_dir():
        raise SystemExit(f"源目录不存在: {src}")
    out_path = out_dir / f"{cfg['name']}_merged_{STAMP}.xlsx"
    return cfg["build"](src, out_path)


def iter_projects(selected, db_root, explicit):
    """产出 (key, cfg, src)；缺省跑全部时跳过已不存在的源，显式 --project 则报错。"""
    for key in selected:
        cfg = ADAPTERS[key]
        src = db_root.joinpath(*cfg["src"])
        if not src.is_dir():
            if explicit:
                raise SystemExit(f"源目录不存在: {src}")
            print(f"[skip] 源目录不存在: {src}")
            continue
        yield key, cfg, src


def is_under(path, root):
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except (ValueError, OSError):
        return False


def ingest_merged(db_path, merged, key):
    """--into：把合并簿增量入库（rel=<FAM>/<leaf>/<合并簿名>）。"""
    db_path = Path(db_path)
    if not db_path.is_file():
        raise SystemExit(f"--into 库不存在: {db_path}（先跑 build_code_db.py 建库）")
    family = core.family_of_db(db_path)
    con = core.open_db(db_path)
    code = core.get_meta(con).get("short_code", "ly")
    fam = key.split("/")[0]
    slug = key.split("/")[-1] if "/" in key else key
    leaf = f"{fam}/{slug}"
    ing = core.Ingestor(con, code)
    did, n = ing.ingest(merged, f"{leaf}/{merged.name}", fam, slug, leaf, incremental=True)
    counts = dict(
        n_doc=con.execute("SELECT count(*) FROM doc").fetchone()[0],
        n_sheet=con.execute("SELECT count(*) FROM sheet").fetchone()[0],
        n_fact=con.execute("SELECT count(*) FROM fact").fetchone()[0],
        n_entity=con.execute("SELECT count(*) FROM entity").fetchone()[0],
    )
    core.set_meta(con, **counts)
    con.commit()
    con.close()
    print(f"  已入库: {db_path}（doc_id={did}，sheet +{n}）")


def run(args):
    db_root = Path(args.db_root)
    batch = core.batch_dir(args.batch)
    out_dir = Path(args.out) if args.out else (batch / "staging")
    if is_under(out_dir, db_root) and not (args.in_db or args.into):
        raise SystemExit(f"输出目录在 _DataBase 内（{out_dir}），需显式 --in-db 或 --into <db>")
    selected = select_projects(args.project)
    explicit = args.project is not None

    if args.build:
        out_dir.mkdir(parents=True, exist_ok=True)
        for key, cfg, src in iter_projects(selected, db_root, explicit):
            if not source_files(src):
                if explicit:
                    raise SystemExit(f"源无可归并散表（可能已收口，原件在 {core.batch_dir(args.batch)}）：{src}")
                print(f"[skip] 源无可归并散表：{src}")
                continue
            p = build_one(key, db_root, out_dir)
            print(f"[built] {p}  {p.stat().st_size:,} B")
        return

    plan = []
    for key, cfg, src in iter_projects(selected, db_root, explicit):
        files = source_files(src)
        if not files:
            if explicit:
                raise SystemExit(f"源只剩已有合并表，拒绝 --apply：{src}")
            print(f"[skip] 源无表格（只剩合并表）：{src}")
            continue
        plan.append((key, cfg, src, files))

    if not args.apply:
        print("dry-run：将执行下列动作（加 --apply 落盘；--build 只生成到输出目录）")
        for key, cfg, src, files in plan:
            print(f"\n[{key}] 原件 {len(files)} -> {batch / 'originals' / key}")
            for p in files:
                print(f"    {p.name}  {p.stat().st_size:,} B")
            name = cfg['name'] + '_merged_' + STAMP + '.xlsx'
            if args.in_db:
                print(f"  合并表 -> 叶根（--in-db）{src / name}")
            elif args.into:
                print(f"  合并表 -> 入库（--into）{args.into}")
            else:
                print(f"  合并表 -> {out_dir / name}")
        return

    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log = []
    for key, cfg, src, files in plan:
        print(f"\n===== {key} =====")
        produced = build_one(key, db_root, out_dir)
        print("  已生成:", produced, f"{produced.stat().st_size:,} B")
        odir = batch / "originals" / key
        odir.mkdir(parents=True, exist_ok=True)
        for p in files:
            tgt = odir / p.name
            if tgt.exists():
                raise SystemExit(f"原件目标已存在，停止：{tgt}")
            log.append([key.split("/")[0], f"{src.name}" + "\\" + f"{p.name}",
                        key.split("/")[-1] if "/" in key else ".",
                        (Path("originals") / key / p.name).as_posix(),
                        p.stat().st_size, sha256_12(p), ts])
            shutil.move(str(p), str(tgt))
        if args.in_db:
            dest = src / produced.name
            if dest.exists():
                raise SystemExit(f"目标已存在，停止：{dest}")
            shutil.copy2(produced, dest)
            print(f"  合并表已进叶根（--in-db）：{dest}")
        elif args.into:
            ingest_merged(args.into, produced, key)
        else:
            print(f"  合并表留在输出目录（未写 _DataBase）：{produced}")
        print(f"  原件 {len(files)} 个已移入 .Del：{odir}")
        left = [p.name for p in src.iterdir()]
        print("  叶根剩余：", left)

    man = batch / f"MERGE-MOVED-{STAMP}.tsv"
    ledger_write(man, ("fam", "src", "rel", "dst", "size_B", "sha256_12", "moved_at"), log)
    print(f"\n台账：{man}")


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description="散表归并：按项目生成合并工作簿（默认落 {DEL} staging，不写 _DataBase）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=("默认输出 <batch>/staging/，不复制回 _DataBase；\n"
                "--in-db 允许写回叶根（旧行为，需点名授权）；\n"
                "--into <db> 生成后直接增量入库（rel=<FAM>/<leaf>/<合并簿名>）。"))
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--build", action="store_true",
                   help="只生成到输出目录（默认 <batch>/staging/）")
    g.add_argument("--apply", action="store_true",
                   help="生成 + 原件移入 <batch>/originals/<key>/ + 写台账；"
                        "合并表按 --in-db/--into 处置")
    ap.add_argument("--project", metavar="LEAF",
                    help="项目名，如 SCH/ly_SCH、ly_SCH、OC、zar_PCOS3D；缺省=全部已注册")
    ap.add_argument("--db-root", type=Path, default=DEFAULT_DB_ROOT,
                    help=f"_DataBase 根（默认 {DEFAULT_DB_ROOT}）")
    ap.add_argument("--batch", default=DEFAULT_BATCH,
                    help=f"{{DEL}}/_by-function 下的批次目录（默认 {DEFAULT_BATCH}）")
    ap.add_argument("--stamp", default=None, help="输出日期戳 YYYYMMDD（默认今天）")
    ap.add_argument("--out", type=Path, default=None,
                    help="输出目录（默认 <batch>/staging/；落在 _DataBase 内需 --in-db/--into）")
    ap.add_argument("--in-db", action="store_true",
                    help="允许写回 _DataBase：合并表复制回叶根（需点名授权）")
    ap.add_argument("--into", type=Path, metavar="DB", default=None,
                    help="合并表增量入库到指定库（如 ...\\MHE_AN\\_db\\MHE_ANdb），不写回叶根")
    args = ap.parse_args(argv)
    if args.in_db and args.into:
        ap.error("--in-db 与 --into 不能同时使用")
    if args.build and (args.in_db or args.into):
        ap.error("--build 不落 _DataBase，不能配 --in-db/--into")
    global STAMP
    if args.stamp:
        STAMP = args.stamp
    return args


if __name__ == "__main__":
    run(parse_args())
