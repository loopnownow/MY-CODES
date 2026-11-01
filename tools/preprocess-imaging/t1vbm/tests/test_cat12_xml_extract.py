# -*- coding: utf-8 -*-
"""cat12_xml_extract：假 CAT12 XML 的 TIV、GM 体积、ROI 行数和 sheet 名。"""
import subprocess
import sys
from pathlib import Path

import pandas as pd

SCRIPT = Path(__file__).resolve().parents[1] / "cat12_xml_extract.py"
sys.path.insert(0, str(SCRIPT.parent))

import cat12_xml_extract as cx  # noqa: E402

GLOBAL_XML = """<?xml version="1.0" encoding="UTF-8"?>
<S>
  <subjectmeasures>
    <vol_TIV>1500.5</vol_TIV>
    <vol_abs_CGW>[100.0 650.25 400.0]</vol_abs_CGW>
  </subjectmeasures>
  <qualityratings>
    <IQR>2.5</IQR>
  </qualityratings>
</S>
"""

ROI_XML = """<?xml version="1.0" encoding="UTF-8"?>
<S>
  <neuromorphometrics>
    <names>
      <item>RegionA</item>
      <item>RegionB</item>
    </names>
    <ids>[1;2]</ids>
    <data>
      <Vgm>[1.5 2.5]</Vgm>
      <Vwm>[0.4 0.6]</Vwm>
    </data>
  </neuromorphometrics>
</S>
"""


def _write_fixture(folder: Path) -> None:
    folder.mkdir()
    (folder / "cat_Sub01.xml").write_text(GLOBAL_XML, encoding="utf-8")
    (folder / "catROI_Sub01.xml").write_text(ROI_XML, encoding="utf-8")


def test_tiv_gm_roi_sheets(tmp_path, monkeypatch):
    src = tmp_path / "in"
    _write_fixture(src)
    out = tmp_path / "result.xlsx"
    monkeypatch.setattr(
        sys, "argv",
        ["cat12_xml_extract.py", "--input", str(src), "--output", str(out)],
    )
    cx.main()

    assert out.is_file()
    sheets = pd.ExcelFile(out).sheet_names
    assert sheets == [
        "Subject_Globals_TIV",
        "All_Data_Long",
        "neuromorphometrics",
        "All_ROIs_Wide",
    ]
    glob = pd.read_excel(out, sheet_name="Subject_Globals_TIV")
    assert list(glob["Subject_ID"]) == ["Sub01"]
    assert glob.loc[0, "TIV_mm3"] == 1500.5
    assert glob.loc[0, "GM_Vol_mm3"] == 650.25
    long = pd.read_excel(out, sheet_name="All_Data_Long")
    assert len(long) == 4
    assert set(long["Measure"]) == {"Vgm", "Vwm"}


def test_dry_run_does_not_write(tmp_path, monkeypatch):
    src = tmp_path / "in"
    _write_fixture(src)
    monkeypatch.setattr(
        sys, "argv",
        ["cat12_xml_extract.py", "--input", str(src), "--dry-run"],
    )
    cx.main()
    assert list(src.glob("*.xlsx")) == []


def test_help_and_input_required():
    help_run = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True, text=True, check=False,
    )
    assert help_run.returncode == 0
    assert "--input" in help_run.stdout
    assert "--id-regex" in help_run.stdout
    missing = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True, text=True, check=False,
    )
    assert missing.returncode != 0
