# -*- coding: utf-8 -*-
"""流程图页新契约：单一源 v2 + /api/flow_preview；旧编辑器只保留 legacy.html。"""
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
    assert '"/files/"' in html
    assert '"/flow_files?project="' in html
    assert '"/flow_spec?project="' in html
    assert "X-Console-Token" in html


def test_page_exposes_the_v2_form_and_preview():
    html = _read(STROBE)
    for element_id in (
        "layout", "screened-text", "screened-n", "inclusion", "exclusion",
        "presplit-rows", "downsample-rows", "cohort-rows",
        "analyzed-text", "analyzed-n", "pos-name", "notes",
        "preview-img", "handles", "tune", "reset", "btn-save", "btn-fill",
    ):
        assert f'id="{element_id}"' in html, element_id
    assert "微调版式" in html
    assert "恢复默认版式" in html
    assert "未生成" in html and "按手稿重做" in html
    assert "已按 " in html and "请核对后保存" in html


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
    assert "ref/Figure1_flow.json" in legacy


def test_server_serves_legacy_on_flag_and_exposes_preview_route():
    http = _read(HTTP)
    assert '"legacy.html"' in http
    assert 'qs.get("legacy")' in http
    assert '"/api/flow_preview"' in http
    assert "flow_figure.preview_figure1" in http


def test_preview_helper_is_exported():
    from modules.console import flow_figure

    assert callable(flow_figure.preview_figure1)
    assert callable(flow_figure.render_figure1)
    assert callable(flow_figure.read_figure1_state)


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
        assert "SCI Figure 1 · 研究流程图" in legacy
        assert "localStorage" in legacy
        assert "bootLegacyFigure1" in legacy
    finally:
        _stop_server(httpd)
