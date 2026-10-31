"""Console Origin parsing, CORS, and token exposure (P1)."""
from __future__ import annotations

import json
import threading
from http.client import HTTPConnection

import pytest

from modules.console import paths as console_paths
from modules.console import console as cons
from modules.console import state as console_state
from modules.console.state import CONSOLE_TOKEN


@pytest.mark.parametrize(
    "origin,expected",
    [
        ("", False),
        ("null", False),
        ("http://127.0.0.1:8765", True),
        ("http://localhost:8765", True),
        ("http://127.0.0.1", True),
        ("http://localhost.evil.example:8765", False),
        ("http://127.0.0.1.evil.example:8765", False),
        ("https://127.0.0.1:8765", False),
        ("http://127.0.0.1:9999", False),
        ("http://evil.com:8765", False),
    ],
)
def test_allow_browser_origin_matrix(origin: str, expected: bool):
    assert console_paths.allow_browser_origin(origin) is expected


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
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, port


def _stop_server(httpd):
    httpd.shutdown()
    httpd.server_close()
    console_state.SERVE_PORT = None


def test_health_never_returns_token():
    httpd, port = _start_server()
    try:
        conn = HTTPConnection(cons.HOST, port, timeout=3)
        for origin in ("http://127.0.0.1:8765", "null", ""):
            headers = {"Origin": origin} if origin else {}
            conn.request("GET", "/api/health", headers=headers)
            resp = conn.getresponse()
            body = json.loads(resp.read().decode("utf-8"))
            assert resp.status == 200 and body.get("ok")
            assert "token" not in body
        conn.close()
    finally:
        _stop_server(httpd)


def test_untrusted_origin_cannot_read_projects_or_settings():
    httpd, port = _start_server()
    try:
        conn = HTTPConnection(cons.HOST, port, timeout=3)
        evil = "http://localhost.evil.example:8765"
        for path in ("/api/projects", "/api/settings?project=demo"):
            conn.request("GET", path, headers={"Origin": evil})
            resp = conn.getresponse()
            resp.read()
            assert resp.status == 403
        conn.request("GET", "/api/projects", headers={"Origin": "null"})
        resp = conn.getresponse()
        resp.read()
        assert resp.status == 403
        conn.close()
    finally:
        _stop_server(httpd)


def test_loopback_origin_projects_requires_token():
    httpd, port = _start_server()
    try:
        conn = HTTPConnection(cons.HOST, port, timeout=3)
        origin = f"http://127.0.0.1:{port}"
        conn.request("GET", "/api/projects", headers={"Origin": origin})
        resp = conn.getresponse()
        resp.read()
        assert resp.status == 403

        conn.request(
            "GET",
            "/api/projects",
            headers={"Origin": origin, "X-Console-Token": CONSOLE_TOKEN},
        )
        ok = conn.getresponse()
        body = json.loads(ok.read().decode("utf-8"))
        assert ok.status == 200 and body.get("ok") is not False
        conn.close()
    finally:
        _stop_server(httpd)


def test_start_html_embeds_token_for_loopback():
    httpd, port = _start_server()
    try:
        conn = HTTPConnection(cons.HOST, port, timeout=3)
        origin = f"http://127.0.0.1:{port}"
        conn.request("GET", "/start.html", headers={"Origin": origin})
        resp = conn.getresponse()
        html = resp.read().decode("utf-8")
        assert resp.status == 200
        assert CONSOLE_TOKEN in html
        assert "__CONSOLE_TOKEN__" not in html
        conn.close()
    finally:
        _stop_server(httpd)
