# -*- coding: utf-8 -*-
"""流程图页新契约：v3 表单 + 头部按钮 + 适应/缩放 + 撤销 + 草稿；旧编辑器只在 legacy.html。"""
from __future__ import annotations

import threading
from http.client import HTTPConnection
from pathlib import Path

import pytest

from modules.console import console as cons
from modules.console import state as console_state
from modules.console.state import CONSOLE_TOKEN

ROOT = Path(__file__).resolve().parents[2]
CONSOLE = ROOT / "modules" / "console"
STROBE = CONSOLE / "strobe" / "index.html"
LEGACY = CONSOLE / "strobe" / "legacy.html"
HTTP = CONSOLE / "http_impl.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_page_keeps_the_console_contract():
    html = _read(STROBE)
    assert "<title>SCI Figure 1 · 研究流程图</title>" in html
    assert 'name="console-token"' in html
    assert "__CONSOLE_TOKEN__" in html
    assert "window.parent" in html
    assert '"/flow_figure?project="' in html
    assert '"/api/flow_figure"' in html
    assert '"/api/flow_preview"' in html
    assert '"/api/flow_save_design"' in html
    assert '"/api/flow_discard_design"' in html
    assert '"/files/"' in html
    assert '"/flow_files?project="' in html
    assert '"/flow_spec?project="' in html
    assert "X-Console-Token" in html


def test_header_has_six_buttons_and_conditional_extras():
    html = _read(STROBE)
    header = html[html.index("<header>"):html.index("</header>")]
    main_ids = ["btn-undo", "btn-save-design", "btn-reload", "btn-save", "btn-tune", "btn-fill"]
    labels = ["撤销", "保存", "加载", "生成 Fig 1", "微调", "填写"]
    for element_id, label in zip(main_ids, labels):
        assert f'id="{element_id}"' in header, element_id
        assert ">" + label + "<" in header, label
    for conditional in ("btn-discard", "btn-reset", "btn-clear-text"):
        assert f'id="{conditional}"' in header
        assert f'id="{conditional}" hidden' in header
    assert 'id="manuscript"' not in header
    assert 'id="manuscript"' in html[html.index("<aside>"):html.index("</aside>")]
    assert "恢复默认版式" in header and "放弃草稿" in header and "清除手动文字" in header


def test_page_layout_uses_flex_heights_not_calc_vh():
    html = _read(STROBE)
    assert "flex-direction:column" in html
    assert "flex:1 1 auto" in html
    assert "min-height:0" in html
    assert "calc(100vh" not in html
    assert 'id="canvas"' in html and 'id="canvas-inner"' in html


def test_zoom_controls_float_in_preview():
    html = _read(STROBE)
    preview = html[html.index('class="preview-col"'):]
    assert "zoom-float" in preview
    assert all(f'id="{element_id}"' in preview for element_id in (
        "btn-zoom-out", "btn-zoom-in", "btn-fit", "zoom-label",
    ))


def test_zoom_and_fit_controls():
    html = _read(STROBE)
    assert "wheel" in html
    assert "preventDefault" in html
    assert "ZOOM_MIN" in html and "ZOOM_MAX" in html and "ZOOM_STEP" in html
    assert "applyZoom" in html and "fitWindow" in html
    assert "requestAnimationFrame" in html
    assert "renderHandles" in html


def test_undo_stack_and_shortcut():
    html = _read(STROBE)
    assert "MAX_UNDO" in html
    assert "commitHistory" in html and "undo" in html
    assert "keydown" in html
    assert "ctrlKey" in html
    assert "TEXTAREA" in html


def test_tune_double_click_text_edit():
    html = _read(STROBE)
    assert "dblclick" in html
    assert "editText" in html
    assert 'overrides.text' in html
    assert "清除手动文字" in html


def test_draft_ui_and_badge():
    html = _read(STROBE)
    assert "draft-badge" in html
    assert "草稿（" in html
    assert "saveDesign" in html and "discardDraft" in html
    assert "saved_at" in html
    assert "generated" in html and "stale" in html


def test_form_has_all_v3_groups_and_no_layout_selector():
    html = _read(STROBE)
    for element_id in (
        "screened-text", "inclusion", "exclusion", "included-text",
        "presplit-rows", "downsample-rows", "cohort-rows", "external-rows",
        "step-rows", "analyzed-label", "analyzed-n", "pos-name", "notes",
    ):
        assert f'id="{element_id}"' in html, element_id
    assert 'id="layout"' not in html
    assert "版式 layout" not in html
    assert "仅审计，不画" in html


def test_page_drops_the_legacy_editor():
    html = _read(STROBE)
    for token in (
        "localStorage", "strobe-flowchart-v2", "bootLegacyFigure1",
        "ref/Figure1_flow.json", "paintCanvas", "layoutStudy", "importJSON",
    ):
        assert token not in html, token


def test_legacy_page_keeps_the_old_editor_for_comparison():
    legacy = _read(LEGACY)
    assert "<title>SCI Figure 1 · 研究流程图</title>" in legacy
    assert "localStorage" in legacy
    assert "bootLegacyFigure1" in legacy
    assert "paintCanvas" in legacy


def test_server_serves_legacy_on_flag_and_exposes_routes():
    http = _read(HTTP)
    assert '"legacy.html"' in http
    assert 'qs.get("legacy")' in http
    for route in ('"/api/flow_preview"', '"/api/flow_save_design"', '"/api/flow_discard_design"'):
        assert route in http, route
    assert "flow_figure.preview_figure1" in http


def test_preview_helper_is_exported():
    from modules.console import flow_figure

    assert callable(flow_figure.preview_figure1)
    assert callable(flow_figure.render_figure1)
    assert callable(flow_figure.read_figure1_state)
    assert callable(flow_figure.save_design)
    assert callable(flow_figure.discard_design)


def _start_server():
    httpd = None
    port = 0
    for candidate in range(cons.PORT_LO, cons.PORT_HI + 1):
        try:
            httpd = cons.ConsoleServer((cons.HOST, candidate), cons.Handler)
            port = candidate
            break
        except OSError:
            continue
    if httpd is None:
        pytest.skip("no free port in console band")
    console_state.SERVE_PORT = port
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, port


def _stop_server(httpd):
    httpd.shutdown()
    httpd.server_close()
    console_state.SERVE_PORT = None


def _get(port: int, path: str) -> tuple[int, str]:
    conn = HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request("GET", path)
        response = conn.getresponse()
        return response.status, response.read().decode("utf-8", "replace")
    finally:
        conn.close()


def test_strobe_route_serves_new_page_and_legacy_on_flag():
    httpd, port = _start_server()
    try:
        status, body = _get(port, "/strobe")
        assert status == 200
        assert "SCI Figure 1 · 研究流程图" in body
        assert "localStorage" not in body
        assert CONSOLE_TOKEN in body

        status, legacy = _get(port, "/strobe?legacy=1")
        assert status == 200
        assert "localStorage" in legacy
        assert "bootLegacyFigure1" in legacy
    finally:
        _stop_server(httpd)
