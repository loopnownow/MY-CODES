"""Fast xlsx reader matches pandas on a small workbook."""
from __future__ import annotations

import pandas as pd

from modules.utils.xlsx_sheet import read_columns, read_header, read_sheet, workbook_info


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
