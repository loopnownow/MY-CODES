"""Build the imaging-scanner registry workbook.

Input : scanner_registry_rows.py (hand-curated rows, evidence quoted from 0doc)
Output: D:\\0Grok\\0doc\\_library\\scanner-registry\\scanner-parameter-registry.xlsx  (restored t1553u; was briefly under templates)

Every row is one sequence of one device at one centre of one study. Values are
copied from the source sentence named in the row; nothing is inferred. Missing
facts stay "未标注" and appear in sheet 05_gaps.

Run: python lab-reference/scanner_registry_build.py
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from collections import OrderedDict

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.table import Table, TableStyleInfo

from scanner_registry_rows import COLS, DICT_ROWS, GAPS, ROWS, SOURCES

OUT_DIR = Path(os.environ.get("SCANNER_REGISTRY_DIR") or Path(__file__).resolve().parents[4] / "_library" / "scanner-registry")
OUT_XLSX = os.path.join(OUT_DIR, "scanner-parameter-registry.xlsx")  # live workbook under _library\scanner-registry

SEP = "||"
HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
HEADER_FONT = Font(color="FFFFFF", bold=True, size=10)


def parse_rows():
    """Parse one pipe-delimited "key=value" line per record.

    Format: rec_id|modality=MRI|study_id=...|quote=...
    Missing keys stay empty, so rows only carry the fields they actually have.
    """
    out = []
    for line in ROWS.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = re.split(r"\|(?=[a-z][a-z_]*=)", line)
        row = {key: "" for key, _label, _width in COLS}
        row["rec_id"] = parts[0].strip()
        for chunk in parts[1:]:
            if not chunk.strip():
                continue
            key, _sep, value = chunk.partition("=")
            key = key.strip()
            if key not in row:
                raise KeyError(f"unknown key '{key}' in row {row['rec_id']}")
            row[key] = value.strip()
        out.append(row)
    return out


def add_sheet(wb, title, header, rows, widths=None, freeze="A2", table_name=None):
    ws = wb.create_sheet(title)
    ws.append(header)
    for cell in ws[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(vertical="center", horizontal="center", wrap_text=True)
    for row in rows:
        ws.append(row)
    for idx, _name in enumerate(header, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = (widths or {}).get(idx - 1, 16)
    ws.freeze_panes = freeze
    if rows:
        if table_name:
            ref = f"A1:{get_column_letter(len(header))}{len(rows) + 1}"
            tbl = Table(displayName=table_name, ref=ref)
            tbl.tableStyleInfo = TableStyleInfo(name="TableStyleLight9", showRowStripes=True)
            ws.add_table(tbl)
        else:
            ws.auto_filter.ref = f"A1:{get_column_letter(len(header))}{len(rows) + 1}"
    ws.row_dimensions[1].height = 42
    return ws


def dedupe(data, keys):
    """Unique rows by keys, keeping the first occurrence."""
    seen = OrderedDict()
    for row in data:
        key = tuple(row.get(k, "") for k in keys)
        if key not in seen:
            seen[key] = row
    return list(seen.values())


def build():
    data = parse_rows()
    wb = Workbook()
    wb.remove(wb.active)

    # --- sheet 1: query table -------------------------------------------------
    header = [label for _key, label, _w in COLS]
    widths = {i: w for i, (_k, _l, w) in enumerate(COLS)}
    rows = [[row[key] for key, _l, _w in COLS] for row in data]
    ws = add_sheet(wb, "01_query", header, rows, widths, freeze="D2", table_name="registry")
    for row_cells in ws.iter_rows(min_row=2):
        for cell in row_cells:
            cell.alignment = Alignment(vertical="top", wrap_text=True)

    # --- sheet 2: devices -----------------------------------------------------
    dev_rows = []
    for row in dedupe(data, ["study_id", "center", "model", "manufacturer"]):
        dev_rows.append([
            row["study_id"], row["modality"], row["year"], row["center"],
            row["hospital_raw"], row["model"], row["manufacturer"],
            row["origin_raw"], row["origin_norm"], row["field_probe"],
            row["hardware"], row["source_file"], row["verify"],
        ])
    add_sheet(
        wb, "02_devices",
        ["研究ID", "模态", "年份", "中心代号", "单位(原文)", "型号", "厂家",
         "生产地(原文)", "生产地(规范)", "场强/排数/探头", "线圈/其他硬件",
         "来源文件", "核对状态"],
        dev_rows,
        widths={0: 26, 1: 8, 2: 8, 3: 10, 4: 26, 5: 26, 6: 16, 7: 26, 8: 22,
                9: 18, 10: 20, 11: 46, 12: 12},
    )

    # --- sheet 3: contrast + timing ------------------------------------------
    ct_rows = []
    for row in dedupe(data, ["study_id", "center"]):
        if not (row["contrast_raw"] or row["phase"] or row["phase_delay"]):
            continue
        ct_rows.append([
            row["study_id"], row["modality"], row["center"], row["model"],
            row["contrast_raw"], row["contrast_generic"], row["dose"],
            row["dose_unit"], row["flow_rate"], row["flow_unit"], row["injection"],
            row["phase"], row["phase_delay"], row["delay_unit"], row["quote"],
            row["source_file"],
        ])
    add_sheet(
        wb, "03_contrast_timing",
        ["研究ID", "模态", "中心代号", "型号", "对比剂(原文)", "对比剂通用名",
         "剂量", "剂量单位", "流速", "流速单位", "注射方式/部位", "期相",
         "期相延迟时间", "时间单位", "原文摘录", "来源文件"],
        ct_rows,
        widths={0: 26, 1: 8, 2: 10, 3: 22, 4: 26, 5: 18, 6: 12, 7: 12, 8: 10,
                9: 10, 10: 22, 11: 14, 12: 14, 13: 10, 14: 60, 15: 46},
    )

    # --- sheet 4: sources ------------------------------------------------------
    add_sheet(wb, "04_sources", ["层级", "文件", "角色", "说明"], SOURCES,
              widths={0: 8, 1: 72, 2: 12, 3: 56})

    # --- sheet 5: gaps ---------------------------------------------------------
    add_sheet(wb, "05_gaps", ["研究ID", "缺失项", "原文情况", "建议"], GAPS,
              widths={0: 26, 1: 22, 2: 56, 3: 56})

    # --- sheet 6: dictionary ---------------------------------------------------
    add_sheet(wb, "06_dictionary", ["类别", "标准值", "说明"], DICT_ROWS,
              widths={0: 14, 1: 24, 2: 70})

    dv_mod = DataValidation(type="list", formula1='"US,CT,MRI"', allow_blank=True)
    ws.add_data_validation(dv_mod)
    dv_mod.add(f"B2:B{len(rows) + 1}")

    os.makedirs(OUT_DIR, exist_ok=True)
    wb.save(OUT_XLSX)
    print(f"rows={len(rows)} devices={len(dev_rows)} contrast={len(ct_rows)} -> {OUT_XLSX}")


if __name__ == "__main__":
    build()

