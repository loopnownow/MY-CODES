"""QC section 1 (ID alignment) must follow modality blocks, not a phantom rad sheet."""
from __future__ import annotations

import pandas as pd

from modules.config.defaults import DEFAULT_SETTINGS
from modules.utils.u_qc import _qc_block_sheets, write_project_data_qc


def _settings(blocks: list[dict], rad_sheet: str = "rad") -> dict:
    """写 QC 用的最小设置（其余取默认值）。"""
    s = dict(DEFAULT_SETTINGS)
    s.update(
        {
            "RAD_SHEET": rad_sheet,
            "CLIN_SHEET": "cln",
            "CLIN_ID_COL": "Patient_ID",
            "RAD_ID_COL": "Patient_ID",
            "OUTCOME_COLS": ["Stroke"],
            "MODALITY_BLOCKS": blocks,
        }
    )
    return s


def _workbook(path):
    """小工作簿：cln + CTA + US（无 'rad' 表）。"""
    cln = pd.DataFrame(
        {
            "Patient_ID": ["1", "2", "3"],
            "Stroke": ["Stroke", "non_Stroke", "non_Stroke"],
        }
    )
    cta = pd.DataFrame({"Patient_ID": ["1", "2", "3"], "feat_a": [1.0, 2.0, 3.0]})
    us = pd.DataFrame({"Patient_ID": ["1", "2", "9"], "feat_b": [1.0, 2.0, 3.0]})
    with pd.ExcelWriter(path) as xw:
        cln.to_excel(xw, sheet_name="cln", index=False)
        cta.to_excel(xw, sheet_name="CTA", index=False)
        us.to_excel(xw, sheet_name="US", index=False)
    return path


def test_qc_block_sheets_reads_enabled_blocks():
    blocks = [
        {"name": "CTA", "sheet": "CTA", "enabled": True},
        {"name": "US", "sheet": "US", "enabled": False},
        {"name": "PET", "enabled": True},
        {"name": ""},
    ]
    assert _qc_block_sheets({"MODALITY_BLOCKS": blocks}) == [("CTA", "CTA"), ("PET", "PET")]
    assert _qc_block_sheets({}) == []
    assert _qc_block_sheets({"MODALITY_BLOCKS": "CTA"}) == []


def test_id_alignment_lists_blocks_when_no_rad_sheet(tmp_path):
    xlsx = _workbook(tmp_path / "mini.xlsx")
    out = write_project_data_qc(
        xlsx,
        settings=_settings([{"name": "CTA", "sheet": "CTA"}, {"name": "US", "sheet": "US"}]),
        out_dir=tmp_path,
        out_html="qc.html",
    )
    html = (tmp_path / "ref" / "qc.html").read_text(encoding="utf-8")
    assert out is not None
    assert "block CTA" in html and "block US" in html
    assert "in clin" in html
    assert "rad only" not in html
    assert "<td>2</td>" in html  # US 只有 2 个 ID 落在 clin 里


def test_id_alignment_keeps_single_rad_table_when_sheet_exists(tmp_path):
    xlsx = tmp_path / "mini.xlsx"
    with pd.ExcelWriter(xlsx) as xw:
        pd.DataFrame({"Patient_ID": ["1", "2"], "Stroke": ["Stroke", "non_Stroke"]}).to_excel(
            xw, sheet_name="cln", index=False
        )
        pd.DataFrame({"Patient_ID": ["1", "2"], "feat_a": [1.0, 2.0]}).to_excel(
            xw, sheet_name="rad", index=False
        )
    write_project_data_qc(
        xlsx,
        settings=_settings([], rad_sheet="rad"),
        out_dir=tmp_path,
        out_html="qc.html",
    )
    html = (tmp_path / "ref" / "qc.html").read_text(encoding="utf-8")
    assert "rad only" in html
    assert "block CTA" not in html
