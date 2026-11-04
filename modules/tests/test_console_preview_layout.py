"""数据预览页布局：cln 只预览 5 行、ID 列一行多列、三段文件卡同款、配置页不再有排除名单。"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from modules.console.preview import _PREVIEW_HEAD_ROWS, _info_from_frame

ROOT = Path(__file__).resolve().parents[2]
CONSOLE = ROOT / "modules" / "console"
UI = (CONSOLE / "console_ui.js").read_text(encoding="utf-8")
CSS = (CONSOLE / "console.css").read_text(encoding="utf-8")
HTML = (CONSOLE / "console.html").read_text(encoding="utf-8")
PREVIEW = (CONSOLE / "preview.py").read_text(encoding="utf-8")
CSS_TIGHT = re.sub(r"\s+", "", CSS)


def test_preview_head_rows_is_five():
    df = pd.DataFrame({f"c{i}": range(12) for i in range(3)})
    info = _info_from_frame(df, "cln")
    assert _PREVIEW_HEAD_ROWS == 5
    assert len(info["head"]) == 5
    assert info["n_rows"] == 12
    # cln（以及 qc / icc）都走默认行数，代码里不再写死 12。
    assert "_info_from_frame(cdf, ch)" in PREVIEW
    assert "max_rows=12" not in PREVIEW


def test_config_form_skips_exclude_section_but_preview_keeps_it():
    assert 'if (f.section === "exclude") continue;' in UI
    assert 'id="pvExclude"' in HTML
    assert "function excludeRowsHtml" in UI


def test_ids_panel_is_one_row_multi_column():
    # #pvIds 里没有 .grp-rest 包裹，规则必须直接落在 .field-group.ids 上。
    assert "#pvDataForm.field-group.ids{display:grid;grid-auto-flow:column" in CSS_TIGHT
    assert "grid-auto-columns:minmax(0,1fr)" in CSS_TIGHT


def test_clinical_prep_and_filter_are_one_row_multi_column():
    assert (
        '[data-sec="clinprep"].sec-body.clinprep-body{display:grid;grid-auto-flow:column'
        in CSS_TIGHT
    )
    assert (
        '[data-sec="clin"].sec-body.clin-body{display:grid;grid-auto-flow:column'
        in CSS_TIGHT
    )
    # 后写的两列/四列旧规则必须已经改掉，否则会盖掉一行多列
    assert "clinprep-body{grid-template-columns:repeat(4" not in CSS_TIGHT
    assert "clin-body{grid-template-columns:repeat(2" not in CSS_TIGHT


def test_save_and_restart_buttons_sit_in_header_after_tabs():
    assert HTML.index('id="tabBtnFlow"') < HTML.index('id="btnPvSave"')
    assert HTML.index('id="btnPvSave"') < HTML.index('id="btnPvRestart"')
    assert 'class="hdr-actions"' in HTML
    # 不再留在「操作」卡片里
    card = HTML[HTML.index('id="pvActionCard"'):HTML.index('id="pvActionCard"') + 2000]
    assert 'id="btnPvSave"' not in card and 'id="btnPvRestart"' not in card
    assert ".hdr-actions{" in CSS_TIGHT


def test_file_cards_share_one_head_layout():
    """主数据（console.html）/ 外验 / 排除名单（console_ui.js）都用 .pv-file-card，头行同一套排布。"""
    assert ".pv-file-head{display:flex" in CSS.replace(" ", "")
    assert UI.count('class="pv-file-head"') + HTML.count('class="pv-file-head"') >= 3
    # 4 列表格与主表前四列同宽，三段左缘对齐。
    assert ".pv-role.pv-role-4" in CSS
    assert (
        "grid-template-columns:minmax(6em,1.1fr) 5.5em minmax(7em,1fr) minmax(7em,1fr)"
    ) in CSS
