"""数据预览页布局：cln 只预览 5 行、ID 列一行多列、三段文件卡同款、配置页不再有排除名单。"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from modules.console.preview import _PREVIEW_HEAD_ROWS, _info_from_frame

ROOT = Path(__file__).resolve().parents[2]
CONSOLE = ROOT / "modules" / "console"
UI = (CONSOLE / "console_ui.js").read_text(encoding="utf-8")
CSS = (CONSOLE / "console.css").read_text(encoding="utf-8")
HTML = (CONSOLE / "console.html").read_text(encoding="utf-8")
PREVIEW = (CONSOLE / "preview.py").read_text(encoding="utf-8")


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
    css = CSS.replace(" ", "")
    assert "#pvDataForm.field-group.ids>.grp-rest{grid-auto-flow:column" in css
    assert "grid-auto-columns:minmax(0,1fr)}" in css


def test_file_cards_share_one_head_layout():
    """主数据（console.html）/ 外验 / 排除名单（console_ui.js）都用 .pv-file-card，头行同一套排布。"""
    assert ".pv-file-head{display:flex" in CSS.replace(" ", "")
    assert UI.count('class="pv-file-head"') + HTML.count('class="pv-file-head"') >= 3
    # 4 列表格与主表前四列同宽，三段左缘对齐。
    assert ".pv-role.pv-role-4" in CSS
    assert (
        "grid-template-columns:minmax(6em,1.1fr) 5.5em minmax(7em,1fr) minmax(7em,1fr)"
    ) in CSS
