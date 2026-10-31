"""控制台项目注册表 + 页面/API Origin 守卫（注册机制）。

运行: pytest modules/tests/test_console_registry.py
"""
from __future__ import annotations

import threading
from http.client import HTTPConnection

import pytest

from modules.console import const as console_const
from modules.console import console as cons
from modules.console import registry as project_registry
from modules.console import state as console_state
from modules.console.state import CONSOLE_TOKEN


@pytest.fixture()
def reg_file(tmp_path, monkeypatch):
    target = tmp_path / "console.projects.json"
    monkeypatch.setattr(console_const, "REGISTRY_PATH", target, raising=True)
    return target


@pytest.fixture()
def proj_dir(tmp_path):
    proj = tmp_path / "proj_alpha"
    proj.mkdir()
    return proj


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


def _get(port, path, headers=None):
    conn = HTTPConnection(cons.HOST, port, timeout=5)
    conn.request("GET", path, headers=headers or {})
    resp = conn.getresponse()
    body = resp.read()
    hdrs = dict(resp.getheaders())
    conn.close()
    return resp.status, hdrs, body


def test_norm_dir_matches_dot_path(proj_dir):
    assert project_registry.norm_dir(proj_dir) == project_registry.norm_dir(str(proj_dir) + "\\.")


def test_register_lookup_unregister(reg_file, proj_dir):
    entry, token = project_registry.register("proj_alpha", proj_dir)
    assert token and entry.token_sha256 == project_registry.token_hash(token)
    assert reg_file.is_file()
    assert project_registry.lookup(token).project == "proj_alpha"
    assert project_registry.lookup("wrong-token") is None
    assert project_registry.unregister("proj_alpha") is True
    assert project_registry.lookup(token) is None
    assert project_registry.unregister("proj_alpha") is False


def test_register_rotate_issues_new_token(reg_file, proj_dir):
    _entry, first = project_registry.register("proj_alpha", proj_dir)
    _entry2, kept = project_registry.register("proj_alpha", proj_dir)
    assert kept == ""
    _entry3, rotated = project_registry.register("proj_alpha", proj_dir, rotate=True)
    assert rotated and rotated != first
    assert project_registry.lookup(first) is None
    assert project_registry.lookup(rotated).project == "proj_alpha"


def test_register_rejects_unknown_scope(reg_file, proj_dir):
    with pytest.raises(ValueError):
        project_registry.register("proj_alpha", proj_dir, scopes=["root"])


def test_listing_flags_stale_dir(reg_file, proj_dir):
    project_registry.register("proj_alpha", proj_dir)
    assert project_registry.listing()[0]["dir_ok"] is True
    proj_dir.rmdir()
    assert project_registry.listing()[0]["dir_ok"] is False


def test_inject_and_clear_token(tmp_path):
    page = tmp_path / "proj_alpha.html"
    page.write_text("<head>\n<meta name=\"console-token\" content=\"__CONSOLE_TOKEN__\"/>\n</head>",
                    encoding="utf-8")
    assert project_registry.inject_token(page, "abc123") == "placeholder"
    text = page.read_text(encoding="utf-8")
    assert 'content="abc123"' in text and "__CONSOLE_TOKEN__" not in text
    assert project_registry.inject_token(page, "zzz") == "meta"
    assert 'content="zzz"' in page.read_text(encoding="utf-8")
    project_registry.clear_token(page)
    assert "__CONSOLE_TOKEN__" in page.read_text(encoding="utf-8")


def test_register_project_writes_token_into_page(reg_file, tmp_path, monkeypatch):
    proj = tmp_path / "proj_beta"
    proj.mkdir()
    page = proj / "proj_beta.html"
    page.write_text(
        '<head>\n<meta name="console-token" content="__CONSOLE_TOKEN__"/>\n</head>',
        encoding="utf-8",
    )
    monkeypatch.setattr(cons, "project_dir", lambda name, explicit="": proj)
    out = cons.register_project("proj_beta", str(proj), rotate=True)
    assert out["ok"] is True
    assert out["token_written"] is True
    assert out["action"] == "placeholder"
    assert "__CONSOLE_TOKEN__" not in page.read_text(encoding="utf-8")
    entry = project_registry.get("proj_beta")
    assert entry is not None
    assert entry.dir_key == project_registry.dir_key(proj)


def test_radconsole_action_register_routes():
    assert cons.radconsole_action("radconsole://register?project=x") == "register"
    assert cons.radconsole_action("radconsole://unregister?project=x") == "unregister"
    assert cons.radconsole_action("radconsole://serve?port=8765") == "serve"


def test_page_navigation_without_origin_allowed():
    httpd, port = _start_server()
    try:
        status, hdrs, body = _get(port, "/")
        assert status == 200
        assert "text/html" in hdrs.get("Content-Type", "")
        assert hdrs.get("X-Frame-Options") == "DENY"
        assert "__CONSOLE_TOKEN__" not in body.decode("utf-8")
    finally:
        _stop_server(httpd)


def test_page_with_foreign_origin_rejected():
    httpd, port = _start_server()
    try:
        status, _hdrs, body = _get(port, "/", {"Origin": "http://evil.example"})
        assert status == 403
        assert "origin not allowed" in body.decode("utf-8")
    finally:
        _stop_server(httpd)


def test_api_without_origin_rejected():
    """同域 fetch 常不带 Origin；无 token 仍拒绝。有 token + 环回 Host 则放行。"""
    httpd, port = _start_server()
    try:
        status, _hdrs, body = _get(port, "/api/projects")
        assert status == 403
        msg = body.decode("utf-8")
        assert ("invalid console token" in msg) or ("origin not allowed" in msg)
        status2, _hdrs2, body2 = _get(
            port, "/api/projects", {"X-Console-Token": CONSOLE_TOKEN}
        )
        assert status2 == 200, body2.decode("utf-8")
    finally:
        _stop_server(httpd)


def test_api_null_origin_without_token_rejected():
    httpd, port = _start_server()
    try:
        status, _hdrs, body = _get(port, "/api/projects", {"Origin": "null"})
        assert status == 403
        assert "origin not allowed" in body.decode("utf-8")
    finally:
        _stop_server(httpd)


def test_api_null_origin_with_registered_token_allowed(reg_file, proj_dir):
    _entry, token = project_registry.register("proj_alpha", proj_dir)
    httpd, port = _start_server()
    try:
        status, _hdrs, body = _get(
            port, "/api/projects", {"Origin": "null", "X-Console-Token": token}
        )
        assert status == 200, body.decode("utf-8")
        assert '"ok": true' in body.decode("utf-8").replace("'", '"')
    finally:
        _stop_server(httpd)


def test_api_null_origin_project_scope_mismatch(reg_file, proj_dir):
    _entry, token = project_registry.register("proj_alpha", proj_dir)
    httpd, port = _start_server()
    try:
        status, _hdrs, body = _get(
            port, "/api/settings?project=other_proj",
            {"Origin": "null", "X-Console-Token": token},
        )
        assert status == 403
        assert "project scope mismatch" in body.decode("utf-8")
    finally:
        _stop_server(httpd)


def test_api_null_origin_stale_dir_rejected(reg_file, proj_dir):
    _entry, token = project_registry.register("proj_alpha", proj_dir)
    proj_dir.rmdir()
    httpd, port = _start_server()
    try:
        status, _hdrs, body = _get(
            port, "/api/projects", {"Origin": "null", "X-Console-Token": token}
        )
        assert status == 403
        assert "registration stale" in body.decode("utf-8")
    finally:
        _stop_server(httpd)

