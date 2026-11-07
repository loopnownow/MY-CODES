"""Fast xlsx reader matches pandas on a small workbook."""
from __future__ import annotations

import numpy as np
import pandas as pd

from modules.utils.xlsx_sheet import (
    _as_series,
    _cell_value,
    read_columns,
    read_header,
    read_sheet,
    workbook_info,
)


def test_empty_text_cells_are_missing_not_levels():
    """空字符串单元格（shared/inline/plain）按缺失处理，不让数值列变 object。"""
    assert _cell_value(b't="s"', b"<v>0</v>", [""]) is None
    assert _cell_value(b't="inlineStr"', b"<is><t></t></is>", []) is None
    assert _cell_value(b"", b"<v></v>", []) is None
    assert _cell_value(b't="s"', b"<v>0</v>", ["positif"]) == "positif"

    series = _as_series([1.5, "", 2.5, None])
    assert pd.api.types.is_numeric_dtype(series)
    assert series.fillna(-1).tolist() == [1.5, -1, 2.5, -1]
    text = _as_series(["Pos", "", "Neg"])
    assert text.tolist() == ["Pos", None, "Neg"]


def test_numeric_column_with_one_empty_cell_stays_numeric(tmp_path):
    """工作簿里某数值列夹一个空串单元格时，read_sheet 仍返回数值列。"""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "rad"
    ws.append(["Patient_ID", "f1", "f2"])
    ws.append(["a", 1.5, 3])
    ws.append(["b", "", 4])
    ws.append(["c", 2.5, 5])
    path = tmp_path / "empty-cell.xlsx"
    wb.save(path)

    got = read_sheet(path, "rad")
    assert pd.api.types.is_numeric_dtype(got["f1"])
    assert np.isnan(got["f1"].to_numpy(dtype=float)[1])
    assert got["f1"].fillna(-1).tolist() == [1.5, -1, 2.5]
    assert got["Patient_ID"].tolist() == ["a", "b", "c"]



def test_read_sheet_matches_pandas(tmp_path):
    path = tmp_path / "tiny.xlsx"
    clin = pd.DataFrame({
        "Patient_ID": ["a", "b", None],
        "Age": [1, 2, None],
        "Group": ["Pos", "Neg", "Pos"],
    })
    rad = pd.DataFrame({
        "Patient_ID": ["a", "b", "c"],
        "f1": [0.5, 1.5, None],
        "f2": [3, 4, 5],
    })
    with pd.ExcelWriter(path) as writer:
        clin.to_excel(writer, sheet_name="cln", index=False)
        rad.to_excel(writer, sheet_name="rad", index=False)

    info = workbook_info(path)
    assert [s["name"] for s in info] == ["cln", "rad"]
    assert info[0]["n_rows"] == 3
    assert info[1]["n_cols"] == 3
    assert read_header(path, "rad") == ["Patient_ID", "f1", "f2"]

    got = read_sheet(path, "cln")
    slow = pd.read_excel(path, sheet_name="cln")
    assert list(got.columns) == list(slow.columns)
    assert got["Patient_ID"].fillna("").tolist() == slow["Patient_ID"].fillna("").astype(str).tolist()
    assert got["Group"].tolist() == slow["Group"].tolist()
    assert got["Age"].fillna(-1).tolist() == slow["Age"].fillna(-1).tolist()

    ids = read_columns(path, "rad", ["Patient_ID", "f1"])
    assert ids["Patient_ID"].tolist() == ["a", "b", "c"]
    assert ids["f1"].fillna(-1).tolist() == [0.5, 1.5, -1]
