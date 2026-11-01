"""
impute_cln_en.py — 调用 modules 插补 cln 工作单，并将所有工作单列名英文化
═══════════════════════════════════════════════════════════════════
功能：
    1. 读取 data_merged.xlsx（cln + rad 两个工作单）
    2. 按映射字典将两个工作单的列名全部改为英文
    3. 对 cln 调用 modules.utils.u_impute.z_impute 按 Group 分层插补
       （Sex 分类变量：F=0/M=1 编码 → 组内 mice 插补 → 还原英文标签 M/F）
    4. 写出 data_merged_imputed_en.xlsx，含 3 个工作单：
         cln     — 插补后的临床表（英文列名）
         rad     — 影像组学表（英文列名）
         mapping — 列名映射逻辑表（可追溯/审计）

原 data_merged.xlsx 保持不变；新文件可逆可追溯。
═══════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd

_TIDY = Path(__file__).resolve().parent
# 算法库根（modules 包所在 = MY-CODES 仓根；2026-11-01 迁入 tools/clean-data/）
STATS_ROOT = Path(os.environ.get("STATS_ROOT") or Path(__file__).resolve().parents[2])  # 仓根（本脚本在 tools/<tool>/ 下）
if str(STATS_ROOT) not in sys.path:
    sys.path.insert(0, str(STATS_ROOT))
if str(_TIDY) not in sys.path:
    sys.path.insert(0, str(_TIDY))

from modules.utils.u_impute import z_impute  # noqa: E402
from tidy_paths import project_root  # noqa: E402

# ==================== CONFIG ====================
PROJECT_DIR = Path(os.environ.get("LYA_WML_ROOT", str(project_root("lya_WML"))))
INPUT_XLSX = os.environ.get("LYA_WML_INPUT", "lya_WML_merged.xlsx")
OUTPUT_XLSX = os.environ.get("LYA_WML_OUTPUT", "lya_WML.xlsx")
CLN_SHEET = "cln"
RAD_SHEET = "rad"
MAPPING_SHEET = "mapping"
GROUP_COL = "Group"
IMPUTE_METHOD = "mice"
CATEGORICAL_COLS = ["Sex"]   # 分类变量：编码插补后还原英文标签
DRY_RUN = False
# ================================================

# ═════════════════════════════════════════════════════════════════
# §2 列名映射字典（中文 → 英文）
#    结构: {sheet: {原始列名: 英文列名}}
# ═════════════════════════════════════════════════════════════════
CLN_COL_MAP: Dict[str, str] = {
    "PatientID": "PatientID",
    "Group": "Group",
    "lesion_volume_ml": "lesion_volume_ml",
    "number_of_lesions": "number_of_lesions",
    "性别/Sex": "Sex",
    "年龄/Age": "Age",
    "总负荷评分/CSVD burden": "CSVD_burden",
    "脑室旁Fazekas评分/Fazekas score of periventricular WMH": "Fazekas_PVWMH",
    "深部白质Fazekas评分/Fazekas score of deep WMH": "Fazekas_DWMH",
    "左侧MTA评分/MTA score(L)": "MTA_L",
    "右侧MTA评分/MTA score(R)": "MTA_R",
    "MMSE": "MMSE",
}

RAD_COL_MAP: Dict[str, str] = {
    "PatientID": "PatientID",
    "Group": "Group",
    "侧脑室缘白质高信号白质占比(%)": "WMH_ratio_periventricular_edge",
    "侧脑室旁白质高信号白质占比(%)": "WMH_ratio_periventricular",
    "深部白质高信号白质占比(%)": "WMH_ratio_deep",
    "近皮层白质高信号的体积白质占比(%)": "WMH_ratio_juxtacortical",
    "脑白质高信号总占比(%)": "WMH_ratio_total",
    "HBT_海马头_左/HC_head(L)(%)": "HBT_HC_head_L",
    "HBT_海马头_右/HC_head(R)(%)": "HBT_HC_head_R",
    "HBT_海马体_左/HC_body(L)(%)": "HBT_HC_body_L",
    "HBT_海马体_右/HC_body(R)(%)": "HBT_HC_body_R",
    "HBT_海马尾_左/HC_tail(L)(%)": "HBT_HC_tail_L",
    "HBT_海马尾_右/HC_tail(R)(%)": "HBT_HC_tail_R",
    "FS60_海马旁下托_左/HC_Parasubiculum(L)(%)": "FS60_HC_Parasubiculum_L",
    "FS60_海马旁下托_右/HC_Parasubiculum(R)(%)": "FS60_HC_Parasubiculum_R",
    "FS60_海马前下托_左/HC_Presubiculum(L)(%)": "FS60_HC_Presubiculum_L",
    "FS60_海马前下托_右/HC_Presubiculum(R)(%)": "FS60_HC_Presubiculum_R",
    "FS60_海马下托_左/HC_Subiculum(L)(%)": "FS60_HC_Subiculum_L",
    "FS60_海马下托_右/HC_Subiculum(R)(%)": "FS60_HC_Subiculum_R",
    "FS60_海马角1_左/HC_CA1(L)(%)": "FS60_HC_CA1_L",
    "FS60_海马角1_右/HC_CA1(R)(%)": "FS60_HC_CA1_R",
    "FS60_海马角3_左/HC_CA3(L)(%)": "FS60_HC_CA3_L",
    "FS60_海马角3_右/HC_CA3(R)(%)": "FS60_HC_CA3_R",
    "FS60_海马角4_左/HC_CA4(L)(%)": "FS60_HC_CA4_L",
    "FS60_海马角4_右/HC_CA4(R)(%)": "FS60_HC_CA4_R",
    "FS60_侧齿状回_左/HC_GC_DG(L)(%)": "FS60_HC_GC_DG_L",
    "FS60_侧齿状回_右/HC_GC_DG(R)(%)": "FS60_HC_GC_DG_R",
    "FS60_分子层_左/HC_Molecular_layer(L)(%)": "FS60_HC_Molecular_layer_L",
    "FS60_分子层_右/HC_Molecular_layer(R)(%)": "FS60_HC_Molecular_layer_R",
    "FS60_海马杏仁核过渡区_左/HC_HATA(L)(%)": "FS60_HC_HATA_L",
    "FS60_海马杏仁核过渡区_右/HC_HATA(R)(%)": "FS60_HC_HATA_R",
    "FS60_海马伞_左/HC_Fimbria(L)(%)": "FS60_HC_Fimbria_L",
    "FS60_海马伞_右/HC_Fimbria(R)(%)": "FS60_HC_Fimbria_R",
    "FS60_海马尾_左/HC_tail(L)(%)": "FS60_HC_tail_L",
    "FS60_海马尾_右/HC_tail(R)(%)": "FS60_HC_tail_R",
    "FS60_海马裂_左/HC_fissure(L)(%)": "FS60_HC_fissure_L",
    "FS60_海马裂_右/HC_fissure(R)(%)": "FS60_HC_fissure_R",
}

# 分类变量编码说明（写入 mapping 表）
CAT_ENCODING_NOTES: Dict[str, str] = {
    "Sex": "categorical: F=0, M=1 → 组内 mice 插补 → 就近取整还原为英文标签 M/F",
    "Group": "outcome label: HC / MCI (not imputed)",
}


def _variable_type(col: str) -> str:
    """根据列名推断变量类型，用于 mapping 表。"""
    if col == "PatientID":
        return "identifier"
    if col == "Group":
        return "outcome"
    if col in CATEGORICAL_COLS:
        return "categorical"
    if col in ("CSVD_burden", "Fazekas_PVWMH", "Fazekas_DWMH", "MTA_L", "MTA_R"):
        return "ordinal"
    return "numeric"


def _build_mapping_rows() -> List[Tuple[str, str, str, str, str]]:
    """构建 mapping 表行：(sheet, original, english, type, encoding_note)。"""
    rows: List[Tuple[str, str, str, str, str]] = []
    for sheet, col_map in (("cln", CLN_COL_MAP), ("rad", RAD_COL_MAP)):
        for orig, eng in col_map.items():
            note = CAT_ENCODING_NOTES.get(eng, "")
            rows.append((sheet, orig, eng, _variable_type(eng), note))
    return rows


def _rename_columns(df: pd.DataFrame, col_map: Dict[str, str], sheet: str) -> pd.DataFrame:
    """按映射重命名列；校验无遗漏、无重复英文名。"""
    missing = [c for c in col_map if c not in df.columns]
    if missing:
        raise ValueError(f"[{sheet}] 映射字典含表中不存在的列: {missing}")
    extra = [c for c in df.columns if c not in col_map]
    if extra:
        raise ValueError(f"[{sheet}] 表中存在未映射的列: {extra}")
    out = df.rename(columns=col_map)
    dup = [c for c in out.columns if out.columns.tolist().count(c) > 1]
    if dup:
        raise ValueError(f"[{sheet}] 重命名后出现重复列名: {dup}")
    return out


def _has_non_ascii(cols: List[str]) -> List[str]:
    """返回含非 ASCII 字符（中文等）的列名。"""
    return [c for c in cols if re.search(r"[^\x00-\x7F]", c)]


def main() -> None:
    in_path = Path(INPUT_XLSX)
    out_path = Path(OUTPUT_XLSX)
    if not in_path.is_absolute():
        in_path = PROJECT_DIR / in_path
    if not out_path.is_absolute():
        out_path = PROJECT_DIR / out_path
    print("═══ IMPUTE CLN + ENGLISH COLUMN NAMES ═══")
    print(f"  Project: {PROJECT_DIR}")
    print(f"  Input : {in_path}")
    print(f"  Output: {out_path}")
    if DRY_RUN:
        print("  DRY_RUN=True — stop before read/write")
        return

    # ── 读取原始表 ──
    cln_raw = pd.read_excel(in_path, sheet_name=CLN_SHEET)
    rad_raw = pd.read_excel(in_path, sheet_name=RAD_SHEET)
    print(f"  cln: {cln_raw.shape[0]} rows x {cln_raw.shape[1]} cols")
    print(f"  rad: {rad_raw.shape[0]} rows x {rad_raw.shape[1]} cols")

    # ── 列名英文化 ──
    cln = _rename_columns(cln_raw, CLN_COL_MAP, CLN_SHEET)
    rad = _rename_columns(rad_raw, RAD_COL_MAP, RAD_SHEET)

    bad_cln = _has_non_ascii(list(cln.columns))
    bad_rad = _has_non_ascii(list(rad.columns))
    if bad_cln or bad_rad:
        raise ValueError(f"仍有非英文列名: cln={bad_cln}, rad={bad_rad}")
    print("  Column names → English: OK (no non-ASCII)")

    # ── 插补前缺失统计 ──
    print("\n  Missing before imputation (cln):")
    miss_before = cln.isna().sum()
    print(miss_before[miss_before > 0].to_string())

    # ── 调用 modules 插补（按 Group 分层；Sex 分类编码插补还原）──
    imputed = z_impute(
        cln,
        group_col=GROUP_COL,
        method=IMPUTE_METHOD,
        include_categorical=True,
        categorical_cols=CATEGORICAL_COLS,
        align_decimals=True,
        verbose=True,
    )
    if imputed is None:
        raise RuntimeError("z_impute 返回 None，插补未执行")

    # ── 插补后缺失统计 ──
    print("\n  Missing after imputation (cln):")
    miss_after = imputed.isna().sum()
    print(miss_after[miss_after > 0].to_string() if (miss_after > 0).any()
          else "  (none)")

    # ── 分类变量英文标签校验 ──
    sex_vals = sorted(imputed["Sex"].dropna().unique())
    print(f"\n  Sex values after imputation: {sex_vals}")
    if not set(sex_vals).issubset({"M", "F"}):
        raise ValueError(f"Sex 插补后出现非英文标签: {sex_vals}")

    # ── 构建 mapping 表 ──
    mapping_rows = _build_mapping_rows()
    mapping = pd.DataFrame(
        mapping_rows,
        columns=["Sheet", "Original_Column", "English_Column",
                 "Variable_Type", "Encoding_Note"],
    )

    # ── 写出 3 个工作单 ──
    with pd.ExcelWriter(out_path, engine="openpyxl") as xw:
        imputed.to_excel(xw, sheet_name=CLN_SHEET, index=False)
        rad.to_excel(xw, sheet_name=RAD_SHEET, index=False)
        mapping.to_excel(xw, sheet_name=MAPPING_SHEET, index=False)

    print(f"\n  Done → {out_path.resolve()}")
    print(f"  Sheets: {CLN_SHEET} (imputed), {RAD_SHEET}, {MAPPING_SHEET} "
          f"({len(mapping)} mapping rows)")


if __name__ == "__main__":
    main()
