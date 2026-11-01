# -*- coding: utf-8 -*-
"""
cat12_xml_extract.py - CAT12 结果 XML 批量提取与整理
====================================================
功能:
  1. 解析 catROI_*.xml : ROI 逐脑区度量 (长表 + 每图谱宽表 + 全部图谱合并总宽表)
  2. 解析 cat_*.xml    : 被试全局指标(组织体积/图像质量/评级/软件版本/错误报告)
  3. 配置置顶 + 模块化 + 软编码 + DRY-RUN 预演模式
用法:
  python cat12_xml_extract.py --input <目录>
  python cat12_xml_extract.py --input <目录> --dry-run
  python cat12_xml_extract.py --input <目录> --output X.xlsx
  python cat12_xml_extract.py --input <目录> --id-regex "Sub\\d+"
说明:
  旧版仅提取 TIV/组织绝对体积/IQR; 本版在此基础上补全 qualitymeasures,
  qualityratings, subjectmeasures, subjectratings, 表面质量, 软件版本,
  源文件信息, catlog 摘要与 error 失败报告。
"""

import argparse
import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pandas as pd

# ====================================================================
# 1. 配置区 ------ 置顶, 按需修改; 命令行可覆盖输出、dry-run、ID 正则
# ====================================================================
# --input 必填。不再默认 L:\00used\SSD_dyf\dingallcases。
OUTPUT_NAME  = "catROI_extracted_results.xlsx"  # 默认输出 <input>/<此文件名>
DRY_RUN      = False       # True = 仅预演不写文件(等价 --dry-run)
NA_AS_MISSING = True       # XML 中 NaN/Inf 视为缺失值(留空), 不写入数值

EXCEL_SHEET_GLOBALS = "Subject_Globals_TIV"  # 全局指标 Sheet 名(兼容旧版)
EXCEL_SHEET_LONG     = "All_Data_Long"        # ROI 长表 Sheet 名
EXCEL_SHEET_ALL_ROIS = "All_ROIs_Wide"        # 全部图谱合并总宽表 Sheet 名
EXCEL_ENGINE        = "openpyxl"

# 向量字段聚合方式: "mean" 取全向量均值 | "raw" 保留 MATLAB 原文本 | "skip" 不提取
VECTOR_AGG = "mean"

# ------ 提取分区开关 ---------------------------------------------------
EXTRACT_FILEDATA = True   # 源图像信息(filedata)
EXTRACT_SOFTWARE = True   # 软件版本(software)
EXTRACT_QUALITY  = True   # 图像质量原始指标(qualitymeasures 等)
EXTRACT_RATINGS  = True   # 质量评级(qualityratings/subjectratings)
EXTRACT_ERRORS   = True   # 处理失败报告(error)
EXTRACT_CATLOG   = True   # catlog 数值摘要(如平均皮质厚度)
INCLUDE_ATLAS_VERSION = True     # ROI 表是否附带图谱版本号
EXPORT_ALL_ROI_WIDE   = True     # 是否额外导出全部图谱合并总宽表(行=被试, 列=所有脑区)
SORT_NUMERIC_SUBJECT  = True  # 被试按 Sub 数字排序(而非字符串)

# ====================================================================
# 2. 软编码 ------ 解析规则与字段映射; 新增字段只需在此追加
# ====================================================================
ROI_XML_GLOB    = "catROI_*.xml"
GLOBAL_XML_GLOB = "cat_*.xml"
ID_REGEX_DEFAULT = r"(Sub\d+|sub-\w+|case\d+)"   # --id-regex 的默认值
SUBJECT_ID_RE   = ID_REGEX_DEFAULT               # 运行时可由 --id-regex 覆盖
SUBJECT_NUM_RE  = r"Sub(\d+)"                    # 被试数字部分(用于排序)

# catROI 根节点中不属于图谱的节点(防御性跳过)
ATLAS_SKIP_TAGS = {
    "filedata", "SPMpreprocessing", "parameter", "qualitymeasures",
    "qualityratings", "ratings_help", "software", "subjectmeasures",
    "subjectratings", "ppe", "catlog", "error", "hardware",
}

# ----- subjectmeasures 标量字段: 输出列名 -> XML 标签
SM_SCALAR_FIELDS = {
    "TIV_mm3":         "vol_TIV",
    "WMH_Vol_mm3":     "vol_abs_WMH",
    "WMH_rel":         "vol_rel_WMH",
    "Surface_TSA_mm2": "surf_TSA",
    "EC_abs":          "EC_abs",
    "Defect_Size":     "defect_size",
}
# ----- subjectmeasures 向量字段(受 VECTOR_AGG 控制)
SM_VECTOR_FIELDS = {
    "Dist_Thickness":           "dist_thickness",
    "Dist_Thickness_kmeans":    "dist_thickness_kmeans",
    "Dist_Thickness_kmeans_i3": "dist_thickness_kmeans_inner3",
    "Dist_Thickness_kmeans_o2": "dist_thickness_kmeans_outer2",
}
# ----- subjectmeasures>qualitymeasures 表面质量标量字段
SURFACE_QUALITY_FIELDS = {
    "Surf_EulerNumber":   "SurfaceEulerNumber",
    "Surf_DefectArea":    "SurfaceDefectArea",
    "Surf_DefectNumber":  "SurfaceDefectNumber",
    "Surf_IntensityRMSE": "SurfaceIntensityRMSE",
    "Surf_PositionRMSE":  "SurfacePositionRMSE",
}
# ----- 顶层 qualitymeasures 标量/向量/文本字段
QM_SCALAR_FIELDS = {
    "Image_NCR":       "NCR",
    "Image_ICR":       "ICR",
    "Image_Contrast":  "contrast",
    "Image_ContrastR": "contrastr",
    "Image_Res_RMS":   "res_RMS",
    "Image_Res_BB":    "res_BB",
    "Image_Res_ECR":   "res_ECR",
}
QM_VECTOR_FIELDS = {
    "Image_Res_VxVol": "res_vx_vol",
    "Image_Tissue_mn": "tissue_mn",
    "Image_Tissue_sd": "tissue_std",
}
QM_TEXT_FIELDS = {"Image_TissueWeighting": "tissue_weighting"}
# ----- qualityratings 质量评级字段(1->较差, 5->优秀)
QR_SCALAR_FIELDS = {
    "Image_Quality_IQR":    "IQR",
    "Image_Quality_SIQR":   "SIQR",
    "Image_Quality_SIQR3rms2":   "SIQR3rms2",
    "Image_Quality_SIQR4rms2":   "SIQR4rms2",
    "Image_Quality_SIQR4rms8":   "SIQR4rms8",
    "Rating_NCR":      "NCR",
    "Rating_ICR":      "ICR",
    "Rating_Contrast": "contrast",
    "Rating_Res_RMS":  "res_RMS",
    "Rating_Res_ECR":  "res_ECR",
    "Rating_Res_BB":   "res_BB",
    "Rating_EulerN":   "SurfaceEulerNumber",
}
# ----- subjectratings 被试评级字段(Rating_CGW 为向量, 受 VECTOR_AGG 控制)
SR_SCALAR_FIELDS = {
    "Rating_TIV": "vol_TIV",
    "Rating_TSA": "surf_TSA",
    "Rating_WMH": "vol_rel_WMH",
    "SQR":        "SQR",
}
SR_VECTOR_FIELDS = {"Rating_CGW": "vol_rel_CGW"}
# ----- filedata / software 字段
FILEDATA_FIELDS = {
    "Src_File":  "file",
    "Src_FName": "fname",
    "Src_Path":  "path",
}
SOFTWARE_FIELDS = {
    "Soft_System":         "system",
    "Soft_VersionCat":     "version_cat",
    "Soft_VersionSPM":     "version_spm",
    "Soft_VersionMatlab":  "version_matlab",
    "Soft_RevisionCat":    "revision_cat",
    "Soft_VersionSegment": "version_segment",
}
# ----- catlog 数值摘要: 列名 -> 正则(取首个捕获组)
CATLOG_PATTERNS = {"AvgThickness_mm": r"Average thickness:\s*([\d.]+)"}
# ----- vol_abs_CGW / vol_rel_CGW 前 N 个元素解析为固定列
CGW_ABS_COLS = ["CSF_Vol_mm3", "GM_Vol_mm3", "WM_Vol_mm3"]
CGW_REL_COLS = ["CSF_rel", "GM_rel", "WM_rel"]
CGW_N_FIRST  = 3

# ====================================================================
# 3. 通用工具函数
# ====================================================================
def _configure_stdio_utf8() -> None:
    """Windows 控制台尽量用 UTF-8。写法与 modules/pipeline.py 相同。"""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            continue
        try:
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception as e:
            sys.__stderr__.write(f"  ⚠ stdio utf8 reconfigure: {e}\n")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def to_float(text: str | None) -> float | None:
    """文本 -> float; 空串/无法解析/NaN/Inf 均返回 None(依 NA_AS_MISSING)。"""
    if text is None:
        return None
    t = text.strip()
    if not t:
        return None
    low = t.lower()
    if low in ("nan", "inf", "+inf", "-inf"):
        return None if NA_AS_MISSING else float(low)
    try:
        return float(t)
    except ValueError:
        return None


def parse_matlab_tokens(text: str | None) -> list[str]:
    """MATLAB 数组/矩阵字符串 -> 原始 token 列表(保留原样)。
    支持 ; , 空格 换行 分隔, 自动展平矩阵, 兼容 [ ] 包裹。"""
    if not text:
        return []
    return [t for t in re.split(r"[\[\];,\r\n\t\s]+", text.strip()) if t]


def parse_matlab_vector(text: str | None) -> list[float | None]:
    """MATLAB 数组/矩阵字符串 -> 数值列表, 无法解析的元素为 None。"""
    return [to_float(t) for t in parse_matlab_tokens(text)]


def aggregate_vector(tokens: list[float | None]) -> Any:
    """按 VECTOR_AGG 聚合数值向量 (mean 取均值 / raw 保留文本)。"""
    nums = [v for v in tokens if v is not None]
    if not nums:
        return None
    if VECTOR_AGG == "mean":
        return sum(nums) / len(nums)
    if VECTOR_AGG == "raw":
        return " ".join(str(v) for v in tokens)
    return None  # "skip"


def extract_leaf(node: ET.Element | None) -> str | None:
    """叶子节点文本(去首尾空白); 空串或 None 返回 None。"""
    if node is None or node.text is None:
        return None
    t = node.text.strip()
    return t or None


def scalar_fields(data: dict, parent: ET.Element | None,
                  mapping: dict[str, str]) -> None:
    """按映射 {输出列: XML标签} 提取标量字段; 数值转 float, 否则保留原文本。"""
    if parent is None:
        return
    for col, tag in mapping.items():
        val = extract_leaf(parent.find(tag))
        if val is not None:
            f = to_float(val)
            data[col] = f if f is not None else val


def vector_fields(data: dict, parent: ET.Element | None,
                  mapping: dict[str, str]) -> None:
    """按映射提取向量字段, 依 VECTOR_AGG 聚合。"""
    if parent is None or VECTOR_AGG == "skip":
        return
    for col, tag in mapping.items():
        val = extract_leaf(parent.find(tag))
        if val is not None:
            data[col] = aggregate_vector(parse_matlab_vector(val))

# ====================================================================
# 4. XML 解析器
# ====================================================================
def extract_subject_id(path: Path) -> str:
    """从文件名(优先)或父目录名提取被试 ID; 均失败则用文件名主干。"""
    for cand in (path.name, path.parent.name):
        m = re.search(SUBJECT_ID_RE, cand, re.IGNORECASE)
        if m:
            return m.group(1)
    return path.stem


def parse_catroi_xml(file_path: Path) -> list[dict]:
    """解析 catROI_*.xml: 抽取每个图谱的 ROI ids/names + 全部度量(Vgm/Vwm/Vcsf…)。"""
    try:
        root = ET.parse(file_path).getroot()
    except Exception as exc:
        print(f"  [跳过] 无法解析 {file_path.name}: {exc}")
        return []

    subject_id = extract_subject_id(file_path)
    records: list[dict] = []

    for atlas in root:
        if atlas.tag in ATLAS_SKIP_TAGS:
            continue

        # ROI 名称列表(<names><item>…)
        names_node = atlas.find("names")
        roi_names = (
            [extract_leaf(it) or "" for it in names_node.findall("item")]
            if names_node is not None else []
        )
        # ROI ID 列表(保留原始字符串, 如 "1","11")
        ids_node = atlas.find("ids")
        roi_ids = parse_matlab_tokens(extract_leaf(ids_node)) if ids_node is not None else []
        # 图谱版本
        version = extract_leaf(atlas.find("version"))

        data_node = atlas.find("data")
        if data_node is None:
            continue

        for measure in data_node:               # Vgm / Vwm / Vcsf …
            values = parse_matlab_vector(extract_leaf(measure))
            width = max(len(roi_names), len(roi_ids), len(values))
            base = {"Subject_ID": subject_id, "FileName": file_path.name,
                    "Atlas": atlas.tag}
            if INCLUDE_ATLAS_VERSION:
                base["Atlas_Version"] = version
            base.update({"ROI_ID": None, "ROI_Name": None,
                         "Measure": measure.tag, "Value": None})
            for i in range(width):
                row = dict(base)
                row["ROI_ID"] = roi_ids[i] if i < len(roi_ids) else str(i + 1)
                row["ROI_Name"] = (roi_names[i] if i < len(roi_names)
                                    and roi_names[i] else f"ROI_{i + 1}")
                row["Value"] = values[i] if i < len(values) else None
                records.append(row)
    return records


def parse_cat_global_xml(file_path: Path) -> dict:
    """解析 cat_*.xml: 全局组织体积/图像质量/评级/软件/源文件/错误报告等信息。
    所有文件(含处理失败报告)均保留一行, 便于溯源。"""
    rec = {
        "Subject_ID": extract_subject_id(file_path),
        "FileName": file_path.name,
        "Process_Status": "OK",
    }
    try:
        root = ET.parse(file_path).getroot()
    except Exception as exc:
        rec["Process_Status"] = "PARSE_ERROR"
        rec["Error_Detail"] = str(exc)[:200]
        return rec

    # --- error 失败报告(MATLAB 崩溃等): 仅保留 message + 首帧堆栈
    err = root.find("error")
    if err is not None:
        rec["Process_Status"] = "ERROR"
        items = [extract_leaf(it) for it in err.findall("item")]
        items = [it for it in items if it]
        if items:
            rec["Error_Type"] = items[0]
            rec["Error_Detail"] = items[1] if len(items) > 1 else items[0]

    # --- filedata 源文件信息
    if EXTRACT_FILEDATA:
        scalar_fields(rec, root.find("filedata"), FILEDATA_FIELDS)
    # --- software 软件版本
    if EXTRACT_SOFTWARE:
        scalar_fields(rec, root.find("software"), SOFTWARE_FIELDS)
    # --- subjectmeasures + qualitymeasures 质量原始指标
    if EXTRACT_QUALITY:
        sm = root.find("subjectmeasures")
        if sm is not None:
            scalar_fields(rec, sm, SM_SCALAR_FIELDS)
            vector_fields(rec, sm, SM_VECTOR_FIELDS)
            # 组织绝对/相对体积: 取前 N 个元素映射为固定列
            for col_list, src in ((CGW_ABS_COLS, "vol_abs_CGW"),
                                  (CGW_REL_COLS, "vol_rel_CGW")):
                vals = parse_matlab_vector(extract_leaf(sm.find(src)))
                for i, col in enumerate(col_list[:CGW_N_FIRST]):
                    if i < len(vals) and vals[i] is not None:
                        rec[col] = vals[i]
            # 表面质量(subjectmeasures>qualitymeasures)
            scalar_fields(rec, sm.find("qualitymeasures"),
                          SURFACE_QUALITY_FIELDS)
        # 顶层 qualitymeasures(NCR/ICR/contrast/res_* 等)
        qm = root.find("qualitymeasures")
        scalar_fields(rec, qm, QM_SCALAR_FIELDS)
        vector_fields(rec, qm, QM_VECTOR_FIELDS)
        scalar_fields(rec, qm, QM_TEXT_FIELDS)
    # --- qualityratings + subjectratings 评级
    if EXTRACT_RATINGS:
        scalar_fields(rec, root.find("qualityratings"), QR_SCALAR_FIELDS)
        sr = root.find("subjectratings")
        scalar_fields(rec, sr, SR_SCALAR_FIELDS)
        vector_fields(rec, sr, SR_VECTOR_FIELDS)
    # --- catlog 数值摘要(如平均厚度)
    if EXTRACT_CATLOG:
        log = root.find("catlog")
        if log is not None:
            texts = [extract_leaf(it) for it in log.findall("item")]
            texts = [t for t in texts if t]
            for col, pat in CATLOG_PATTERNS.items():
                for t in reversed(texts):           # 取最后一次匹配(汇总值)
                    m = re.search(pat, t)
                    if m:
                        rec[col] = to_float(m.group(1))
                        break
    return rec


# ====================================================================
# 5. Excel 导出
# ====================================================================
def sanitize_sheet_name(name: str, existing_names: set[str]) -> str:
    """生成合法 Excel Sheet 名(<=31字符, 移除非法字符, 防止重名)。"""
    clean = re.sub(r"[\\/*?:\[\]]", "_", name)[:30]
    unique = clean
    counter = 1
    while unique in existing_names:
        unique = f"{clean[:27]}_{counter}"
        counter += 1
    existing_names.add(unique)
    return unique


def build_global_frame(records: list[dict]) -> pd.DataFrame:
    """统一列序(按首次出现), 可选按被试数字排序。"""
    cols, seen = [], set()
    for r in records:
        for k in r:
            if k not in seen:
                seen.add(k)
                cols.append(k)
    df = pd.DataFrame(records, columns=cols)
    if SORT_NUMERIC_SUBJECT and "Subject_ID" in df:
        df["_sn"] = pd.to_numeric(
            df["Subject_ID"].str.extract(SUBJECT_NUM_RE)[0], errors="coerce")
        df = df.sort_values(["_sn", "Subject_ID"], na_position="last").reset_index(drop=True)
        df = df.drop(columns="_sn")
    return df


def build_roi_frame(records: list[dict]) -> pd.DataFrame:
    """统一列序构建 ROI 长表 DataFrame。"""
    cols, seen = [], set()
    for r in records:
        for k in r:
            if k not in seen:
                seen.add(k)
                cols.append(k)
    return pd.DataFrame(records, columns=cols)


def export_to_excel(roi_records: list[dict], global_records: list[dict],
                    out_path: Path) -> list[str]:
    """写 Excel: 全局指标 Sheet + ROI 长表 + 每图谱宽表; 返回已写 Sheet 清单。"""
    written: list[str] = []
    with pd.ExcelWriter(out_path, engine=EXCEL_ENGINE) as writer:
        if global_records:
            build_global_frame(global_records).to_excel(
                writer, sheet_name=EXCEL_SHEET_GLOBALS, index=False)
            written.append(EXCEL_SHEET_GLOBALS)
        if not roi_records:
            return written

        df_roi = build_roi_frame(roi_records)
        used = {EXCEL_SHEET_GLOBALS, EXCEL_SHEET_LONG}
        df_roi.to_excel(writer, sheet_name=EXCEL_SHEET_LONG, index=False)
        written.append(EXCEL_SHEET_LONG)

        # 每 Atlas 一张矩阵宽表(行=被试, 列=脑区_度量)
        for atlas_name, group in df_roi.groupby("Atlas", sort=True):
            g = group.copy()
            g["Feature"] = g["ROI_Name"].astype(str) + "_" + g["Measure"]
            pivot = g.pivot_table(index=["Subject_ID", "FileName"],
                                  columns="Feature", values="Value",
                                  aggfunc="first")
            pivot.sort_index(axis=1, inplace=True)
            title = sanitize_sheet_name(str(atlas_name), used)
            pivot.to_excel(writer, sheet_name=title)
            written.append(title)

        # 全部图谱合并总宽表: 行=被试, 列=Atlas_ROI_Name_Measure
        if EXPORT_ALL_ROI_WIDE:
            all_wide = df_roi.copy()
            all_wide["Feature"] = (all_wide["Atlas"] + "_"
                                   + all_wide["ROI_Name"].astype(str)
                                   + "_" + all_wide["Measure"])
            pivot_all = all_wide.pivot_table(
                index=["Subject_ID", "FileName"],
                columns="Feature", values="Value", aggfunc="first")
            pivot_all.sort_index(axis=1, inplace=True)
            title = sanitize_sheet_name(EXCEL_SHEET_ALL_ROIS, used)
            pivot_all.to_excel(writer, sheet_name=title)
            written.append(title)
    return written

# ====================================================================
# 6. 主流程
# ====================================================================
def report_dryrun(input_dir: Path, out_path: Path, roi_files: list[Path],
                  roi_records: list[dict], glob_records: list[dict]) -> None:
    """DRY-RUN 预演报告: 展示扫描/解析统计与导出计划, 不写任何文件。"""
    line = "=" * 64
    print("\n" + line)
    print("[DRY-RUN] 预演模式 - 以下仅为计划, 不会写入任何文件")
    print(f"  扫描目录 : {input_dir}")
    print(f"  计划输出 : {out_path}")
    print(f"  catROI   : {len(roi_files)} 个文件 -> {len(roi_records)} 条 ROI 记录")
    if roi_records:
        atlases = {}
        for r in roi_records:
            atlases.setdefault(r["Atlas"], set()).add(r["Measure"])
        print("    图谱分布:")
        for name in sorted(atlases):
            ms = ", ".join(sorted(atlases[name]))
            print(f"      - {name}: measures = {ms}")
        df_roi = build_roi_frame(roi_records)
        print("    长表列  :", ", ".join(df_roi.columns))
        print("    长表预览(前2行):")
        print(df_roi.head(2).to_string(index=False))
        if EXPORT_ALL_ROI_WIDE:
            all_w = df_roi.copy()
            all_w["Feature"] = (all_w["Atlas"] + "_"
                                + all_w["ROI_Name"].astype(str)
                                + "_" + all_w["Measure"])
            p_all = all_w.pivot_table(index=["Subject_ID", "FileName"],
                                      columns="Feature", values="Value",
                                      aggfunc="first")
            n_sub, n_feat = p_all.shape[0], p_all.shape[1]
            print(f"    合并总宽表: {n_sub} 行 × {n_feat} 列 → Sheet '{EXCEL_SHEET_ALL_ROIS}'")
    print(f"  cat_*.xml: {len(glob_records)} 个报告文件 -> 全局表 {len(glob_records)} 行")
    df = build_global_frame(glob_records)
    print("    状态分布:", dict(df["Process_Status"].value_counts()))
    print("    列数    :", len(df.columns))
    print("    列清单  :", ", ".join(map(str, df.columns)))
    print("    全局表预览(前2行):")
    print(df.head(2).to_string(index=False))
    print(line)
    print("[DRY-RUN] 完成 - 未写入任何文件")


def main() -> None:
    _configure_stdio_utf8()
    ap = argparse.ArgumentParser(
        description="CAT12 XML 批量提取 (默认配置见文件顶部配置区)")
    ap.add_argument("--input", type=Path, required=True, help="扫描根目录")
    ap.add_argument("--output", type=Path, default=None,
                    help="输出 Excel 路径；默认 <input>/catROI_extracted_results.xlsx")
    ap.add_argument("--id-regex", default=ID_REGEX_DEFAULT,
                    help="受试者 ID 正则；默认 (Sub\\d+|sub-\\w+|case\\d+)")
    ap.add_argument("--dry-run", action="store_true", help="仅预演, 不写文件")
    args = ap.parse_args()

    global SUBJECT_ID_RE
    SUBJECT_ID_RE = args.id_regex
    input_dir = args.input
    out_path = args.output or (input_dir / OUTPUT_NAME)
    dry_run = DRY_RUN or args.dry_run

    if not input_dir.is_dir():
        print(f"[错误] 目录不存在: {input_dir}")
        sys.exit(1)

    print(f"扫描路径: {input_dir}")

    # 1. 发现文件(递归), 排除 catROI 被 glob 重叠计入
    roi_files = sorted(input_dir.rglob(ROI_XML_GLOB))
    glob_files = sorted(f for f in input_dir.rglob(GLOBAL_XML_GLOB)
                        if not f.name.startswith("catROI_"))
    print(f"找到 {len(roi_files)} 个 {ROI_XML_GLOB}, "
          f"{len(glob_files)} 个 {GLOBAL_XML_GLOB}")

    if not roi_files and not glob_files:
        print("未找到可解析的 XML 文件, 请检查路径。")
        return

    # 2. 解析 ROI 数据与全局报告
    roi_records: list[dict] = []
    for fp in roi_files:
        roi_records.extend(parse_catroi_xml(fp))

    glob_records: list[dict] = []
    for fp in glob_files:
        glob_records.append(parse_cat_global_xml(fp))
    status = {
        st: sum(1 for r in glob_records if r["Process_Status"] == st)
        for st in ("OK", "ERROR", "PARSE_ERROR", "UNKNOWN")
    }
    print(f"全局报告解析: OK={status['OK']}, ERROR={status['ERROR']}, "
          f"解析失败={status['PARSE_ERROR']} (共 {len(glob_records)}) ")
    print(f"ROI 数据解析完成: {len(roi_records)} 条记录")

    # 3. DRY-RUN: 仅预演, 不写文件
    if dry_run:
        report_dryrun(input_dir, out_path, roi_files, roi_records, glob_records)
        return

    # 4. 导出 Excel
    print("\n开始导出 Excel ...")
    written = export_to_excel(roi_records, glob_records, out_path)
    print(f"提取完成 -> {out_path}")

    # 5. 回读校验
    try:
        import openpyxl
        wb = openpyxl.load_workbook(out_path, read_only=True)
        print("回读校验 Sheet 清单:")
        for s in wb.sheetnames:
            ws = wb[s]
            print(f"  Sheet '{s}': {max(0, ws.max_row - 1)} 数据行 x {ws.max_column} 列")
        wb.close()
    except Exception as exc:
        print(f"  (回读校验跳过: {exc})")


if __name__ == "__main__":
    main()
