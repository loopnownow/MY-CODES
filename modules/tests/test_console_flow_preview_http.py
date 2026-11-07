# -*- coding: utf-8 -*-
"""POST /api/flow_preview：token 守卫 + 转发 spec/overrides（monkeypatch 掉真实渲染）。"""
from __future__ import annotations

import json
import threading
from http.client import HTTPConnection
from pathlib import Path

import pytest

from modules.console import console as cons
from modules.console import http_impl
from modules.console import state as console_state
from modules.console.state import CONSOLE_TOKEN


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


def _post(port: int, path: str, payload: dict, token: str | None = None,
          origin: str | None = None) -> tuple[int, str]:
    conn = HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Console-Token"] = token
        if origin:
            headers["Origin"] = origin
        conn.request("POST", path, body=json.dumps(payload), headers=headers)
        response = conn.getresponse()
        return response.status, response.read().decode("utf-8", "replace")
    finally:
        conn.close()


def test_preview_post_forwards_spec_and_overrides(monkeypatch):
    captured = {}

    def fake(proj, spec, overrides):
        captured.update(project=str(proj), spec=spec, overrides=overrides)
        return {
            "ok": True,
            "png_base64": "",
            "geometry": {"page_in": [6.3, 8.0], "boxes": [], "edges": [], "warnings": []},
            "warnings": [],
            "page_cm": [16.0, 20.0],
        }

    monkeypatch.setattr(http_impl.flow_figure, "preview_figure1", fake)
    monkeypatch.setattr(http_impl, "_require_project", lambda value: str(value))
    monkeypatch.setattr(
        http_impl, "project_dir", lambda name, explicit_dir="": Path("C:/fake") / str(name),
    )
    spec = {"layout": "expanded-split-downsample"}
    overrides = {"boxes": {"analyzed": {"x": 0.5, "y": 0.5}}}
    httpd, port = _start_server()
    try:
        status, body = _post(
            port, "/api/flow_preview",
            {"project": "demo", "spec": spec, "overrides": overrides},
            token=CONSOLE_TOKEN, origin=f"http://127.0.0.1:{port}",
        )
    finally:
        _stop_server(httpd)
    assert status == 200
    data = json.loads(body)
    assert data["ok"] is True
    assert captured["spec"] == spec
    assert captured["overrides"] == overrides


def test_preview_post_without_token_is_rejected():
    httpd, port = _start_server()
    try:
        status, body = _post(
            port, "/api/flow_preview",
            {"project": "demo", "spec": {"layout": "expanded-split-downsample"}},
            origin=f"http://127.0.0.1:{port}",
        )
    finally:
        _stop_server(httpd)
    assert status == 403
    assert "token" in json.loads(body)["error"]


def test_save_design_post_forwards_spec(monkeypatch):
    captured = {}

    def fake(proj, spec):
        captured.update(project=str(proj), spec=spec)
        return {"draft_rel": "ref/pipeline/figure1_strobe.draft.json", "saved_at": "2026-11-07 21:00:00"}

    monkeypatch.setattr(http_impl.flow_figure, "save_design", fake)
    monkeypatch.setattr(http_impl, "_require_project", lambda value: str(value))
    monkeypatch.setattr(
        http_impl, "project_dir", lambda name, explicit_dir="": Path("C:/fake") / str(name),
    )
    spec = {"layout": "expanded-split-downsample", "screened": {"text": "x", "n": 1}}
    httpd, port = _start_server()
    try:
        status, body = _post(
            port, "/api/flow_save_design", {"project": "demo", "spec": spec},
            token=CONSOLE_TOKEN, origin=f"http://127.0.0.1:{port}",
        )
    finally:
        _stop_server(httpd)
    assert status == 200
    assert json.loads(body)["ok"] is True
    assert captured["spec"] == spec


def test_discard_design_post_returns_flag(monkeypatch):
    monkeypatch.setattr(
        http_impl.flow_figure, "discard_design",
        lambda proj: {"draft_rel": "ref/pipeline/figure1_strobe.draft.json", "discarded": True},
    )
    monkeypatch.setattr(http_impl, "_require_project", lambda value: str(value))
    monkeypatch.setattr(
        http_impl, "project_dir", lambda name, explicit_dir="": Path("C:/fake") / str(name),
    )
    httpd, port = _start_server()
    try:
        status, body = _post(
            port, "/api/flow_discard_design", {"project": "demo"},
            token=CONSOLE_TOKEN, origin=f"http://127.0.0.1:{port}",
        )
    finally:
        _stop_server(httpd)
    assert status == 200
    assert json.loads(body)["discarded"] is True
