"""
u_outlier_detection.py — 组内 mean±k·SD 离群值检测 + 着色 Excel
═══════════════════════════════════════════════════════════════════
功能：按 Group 分层，对每个临床指标计算 mean ± k·SD 参考范围，标记
      超出范围的离群值（升高/降低），并生成着色 Excel 文件。

着色规则：
    红 (#FF6B6B) : val > mean + k_high * SD  (升高)
    绿 (#51CF66) : val < mean - k_low  * SD  (降低)

统计方法：
    - 默认上限 = mean + 3SD（覆盖约 99.7% 正态分布）
    - 默认下限 = mean - 2SD（覆盖约 95.4% 正态分布）
    - 按 Group 分层计算，各组独立阈值

参考范围表：
    REFERENCE_TABLE 可赋一个含 "indicator" 列的 DataFrame，用于限制
    分析指标。若为空，则分析目标列范围内的全部数值列。
═══════════════════════════════════════════════════════════════════
"""

from __future__ import annotations
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font, Border, Side, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.utils.dataframe import dataframe_to_rows

# ═════════════════════════════════════════════════════════════════
# §1  顶层配置参数
# ═════════════════════════════════════════════════════════════════
INPUT = "braindata.xlsx"          # 数据源 Excel 文件
CLIN_SHEET = "clin"               # 临床特征工作表名
OUT_DIR = "."                     # 输出目录（工程根；多结局时由 pipeline 设为子工程名）
OUTLIER_SD_MULT_HIGH = 3          # 上限 SD 倍数 — val > mean + 3*SD 标记为升高
OUTLIER_SD_MULT_LOW = 2           # 下限 SD 倍数 — val < mean - 2*SD 标记为降低
OUTLIER_BY_GROUP = True           # 是否分组独立计算阈值 (True=组内分别, False=全样本统一)
GROUP_MAP = {"CKD": "CKD", "non-CKD": "non-CKD"}  # Group 列值 → 标签映射
CLIN_COL_START = "Gender"         # 分析起始列名（包含）
CLIN_COL_END = "Urinary albumin"  # 分析结束列名（包含）
COLOR_HIGH = "FFFF6B6B"           # 升高着色（红，ARGB）
COLOR_LOW = "FF51CF66"            # 降低着色（绿，ARGB）

# 参考范围数据库占位符（见模块顶部说明）。若你有 z_reference_range.py
# 的等价实现，可在此赋值为一个含 "indicator" 列的 DataFrame。
REFERENCE_TABLE: Optional[pd.DataFrame] = None


def _flag_sd(x: np.ndarray, k_high: float = 3, k_low: float = 2) -> Dict[str, float]:
    """计算 mean ± k*SD 阈值。"""
    x = x[np.isfinite(x)]
    if len(x) < 3:
        return {"low": np.nan, "high": np.nan}
    return {"low": float(x.mean() - k_low * x.std(ddof=1)),
            "high": float(x.mean() + k_high * x.std(ddof=1))}


def _get_clin_cols(clin_df: pd.DataFrame, start: str, end: str) -> List[str]:
    all_cols = list(clin_df.columns)
    if start not in all_cols or end not in all_cols:
        raise ValueError(f"起止列 '{start}' / '{end}' 必须都存在于数据中")
    i0, i1 = all_cols.index(start), all_cols.index(end)
    return all_cols[i0:i1 + 1]


def _build_colored_clin_xlsx(clin_df: pd.DataFrame, target_cols: List[str],
                              ckd_idx: np.ndarray, nonckd_idx: np.ndarray,
                              k_high: float, k_low: float, out_dir: str = OUT_DIR) -> None:
    """生成着色 Excel 文件：对每个目标指标，按组计算阈值，超出范围的单元格着色。"""
    cols = [c for c in clin_df.columns if not str(c).startswith("..")]
    n_row, n_cols = len(clin_df), len(cols)

    wb = Workbook()
    ws = wb.active
    ws.title = "clin"
    ws.sheet_view.showGridLines = False

    for row in dataframe_to_rows(clin_df[cols], index=False, header=True):
        ws.append(row)

    header_font = Font(bold=True)
    thin_border = Border(bottom=Side(style="thin"))
    all_border = Border(top=Side(style="thin", color="CCCCCC"), bottom=Side(style="thin", color="CCCCCC"),
                         left=Side(style="thin", color="CCCCCC"), right=Side(style="thin", color="CCCCCC"))
    for j in range(1, n_cols + 1):
        ws.cell(row=1, column=j).font = header_font
        ws.cell(row=1, column=j).border = thin_border
    for i in range(1, n_row + 2):
        for j in range(1, n_cols + 1):
            existing = ws.cell(row=i, column=j).border
            ws.cell(row=i, column=j).border = all_border if i > 1 else thin_border

    fill_high = PatternFill(start_color=COLOR_HIGH, end_color=COLOR_HIGH, fill_type="solid")
    fill_low = PatternFill(start_color=COLOR_LOW, end_color=COLOR_LOW, fill_type="solid")

    for ind in [c for c in target_cols if c in cols]:
        j = cols.index(ind) + 1
        x_ckd = pd.to_numeric(clin_df[ind].iloc[ckd_idx], errors="coerce").to_numpy()
        x_nonckd = pd.to_numeric(clin_df[ind].iloc[nonckd_idx], errors="coerce").to_numpy()
        th_ckd = _flag_sd(x_ckd, k_high, k_low)
        th_nckd = _flag_sd(x_nonckd, k_high, k_low)
        if np.isnan(th_ckd["high"]) and np.isnan(th_nckd["high"]):
            continue

        ckd_set = set(ckd_idx.tolist())
        for i in range(n_row):
            val = pd.to_numeric(pd.Series([clin_df[ind].iloc[i]]), errors="coerce").iloc[0]
            if not np.isfinite(val):
                continue
            th = th_ckd if i in ckd_set else th_nckd
            excel_row = i + 2  # +1 表头 +1 索引偏移
            if not np.isnan(th["high"]) and val > th["high"]:
                ws.cell(row=excel_row, column=j).fill = fill_high
            elif not np.isnan(th["low"]) and val < th["low"]:
                ws.cell(row=excel_row, column=j).fill = fill_low

    for j, col in enumerate(cols, start=1):
        max_len = max([len(str(col))] + [len(str(v)) for v in clin_df[col].astype(str)])
        ws.column_dimensions[get_column_letter(j)].width = min(max(max_len + 2, 8), 40)

    import os
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "clin_colored.xlsx")
    wb.save(out_path)
    print(f"  ✓ {out_path} ({n_row} 行 × {n_cols} 列)")


def outlier_detection_analysis(file: str = INPUT, sd_mult_high: float = OUTLIER_SD_MULT_HIGH,
                                sd_mult_low: float = OUTLIER_SD_MULT_LOW,
                                by_group: bool = OUTLIER_BY_GROUP,
                                group_map: Dict[str, str] = GROUP_MAP) -> Dict[str, pd.DataFrame]:
    """组内 mean±k·SD 离群值检测：控制台汇总 + 着色 Excel。"""
    print("\n═══ OUTLIER DETECTION ═══")
    print(f"  上限: mean+{sd_mult_high}SD | 下限: mean-{sd_mult_low}SD | by_group={by_group}")

    clin = pd.read_excel(file, sheet_name=CLIN_SHEET)
    clin["..label"] = clin["Group"].astype(str).map(group_map)
    print(f"  CKD={int((clin['..label'] == 'CKD').sum())}, "
          f"non-CKD={int((clin['..label'] == 'non-CKD').sum())} (N={len(clin)})")

    target = _get_clin_cols(clin, CLIN_COL_START, CLIN_COL_END)
    if REFERENCE_TABLE is not None and "indicator" in REFERENCE_TABLE.columns:
        target = [t for t in target if t in set(REFERENCE_TABLE["indicator"])]
    print(f"  指标数: {len(target)}")

    ckd_idx = np.where(clin["..label"] == "CKD")[0]
    nonckd_idx = np.where(clin["..label"] == "non-CKD")[0]

    rows = []
    for ind in target:
        x_ckd = pd.to_numeric(clin[ind].iloc[ckd_idx], errors="coerce").to_numpy()
        x_non = pd.to_numeric(clin[ind].iloc[nonckd_idx], errors="coerce").to_numpy()
        N_ckd, N_non = len(x_ckd), len(x_non)

        if by_group:
            th_ckd = _flag_sd(x_ckd, sd_mult_high, sd_mult_low)
            th_non = _flag_sd(x_non, sd_mult_high, sd_mult_low)
        else:
            th_all = _flag_sd(np.concatenate([x_ckd, x_non]), sd_mult_high, sd_mult_low)
            th_ckd = th_non = th_all

        def _lo(x, th): return int(np.sum(x < th["low"])) if not np.isnan(th["low"]) else 0
        def _hi(x, th): return int(np.sum(x > th["high"])) if not np.isnan(th["high"]) else 0
        def _pct_hi(n, N): return f"{n} [SD]+ ({100*n/N:.1f}%)" if n > 0 else "-"
        def _pct_lo(n, N): return f"{n} [SD]- ({100*n/N:.1f}%)" if n > 0 else "-"
        def _rng(th): return f"[{th['low']:.1f}, {th['high']:.1f}]" if not np.isnan(th["low"]) else "-"

        rows.append({
            "Indicator": ind,
            "CKD_Threshold": _rng(th_ckd), "nonCKD_Threshold": _rng(th_non),
            "CKD_High": _pct_hi(_hi(x_ckd, th_ckd), N_ckd),
            "CKD_Low": _pct_lo(_lo(x_ckd, th_ckd), N_ckd),
            "nonCKD_High": _pct_hi(_hi(x_non, th_non), N_non),
            "nonCKD_Low": _pct_lo(_lo(x_non, th_non), N_non),
        })

    df = pd.DataFrame(rows)
    print(f"  ── 汇总 (上限: mean+{sd_mult_high}SD; 下限: mean-{sd_mult_low}SD) ──")
    print(df.to_string(index=False))

    _build_colored_clin_xlsx(clin, target, ckd_idx, nonckd_idx, sd_mult_high, sd_mult_low)

    print("═══ OUTLIER DETECTION DONE ═══\n")
    return {"sd_summary": df}
