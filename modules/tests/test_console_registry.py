"""控制台项目注册表 + 页面/API Origin 守卫（注册机制）。

运行: pytest modules/tests/test_console_registry.py
"""
from __future__ import annotations

import json
import threading
from http.client import HTTPConnection
from pathlib import Path

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


GOLDEN_PAGE = (
    '<head>\n<meta name="console-token" content="__CONSOLE_TOKEN__"/>\n</head>\n'
    '<script>\n'
    'const metaTok = ((document.querySelector(\'meta[name="console-token"]\') || {}).content || "").trim();\n'
    'const ok = (metaTok && metaTok !== "__CONSOLE_TOKEN__");\n'
    'if (t && t !== "__CONSOLE_TOKEN__") pageToken = t;\n'
    '</script>'
)


def test_inject_and_clear_token(tmp_path):
    page = tmp_path / "proj_alpha.html"
    page.write_text(GOLDEN_PAGE, encoding="utf-8")
    assert project_registry.inject_token(page, "abc123") == "meta"
    text = page.read_text(encoding="utf-8")
    assert 'content="abc123"' in text
    # JS 哨兵必须保留占位符：写死 token 后 rotate 就改不到它了
    assert text.count("__CONSOLE_TOKEN__") == 2
    assert "\r\n" not in text
    assert project_registry.inject_token(page, "zzz") == "meta"
    text = page.read_text(encoding="utf-8")
    assert 'content="zzz"' in text and 'content="abc123"' not in text
    assert text.count("__CONSOLE_TOKEN__") == 2
    project_registry.clear_token(page)
    text = page.read_text(encoding="utf-8")
    assert text.count("__CONSOLE_TOKEN__") == 3
    assert "\r\n" not in text


def test_inject_token_inserts_meta_when_absent(tmp_path):
    page = tmp_path / "no_meta.html"
    page.write_text("<html><head>\n</head><body></body></html>", encoding="utf-8")
    assert project_registry.inject_token(page, "abc123") == "inserted"
    assert '<meta name="console-token" content="abc123"/>' in page.read_text(encoding="utf-8")


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
    assert out["action"] == "meta"
    text = page.read_text(encoding="utf-8")
    assert "__CONSOLE_TOKEN__" not in text
    assert "\r\n" not in text
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


def test_flow_chart_page_allows_same_origin_frame():
    """流程图页回 SAMEORIGIN（控制台流程图 tab 是同源 iframe）；项目起始页仍 DENY。"""
    httpd, port = _start_server()
    try:
        status, hdrs, body = _get(port, "/flow_chart.html?project=lya_CTA")
        assert status == 200
        assert "text/html" in hdrs.get("Content-Type", "")
        assert hdrs.get("X-Frame-Options") == "SAMEORIGIN"
        assert hdrs.get("Content-Security-Policy") == "frame-ancestors 'self'"
        assert body.decode("utf-8").strip()
        s2, h2, _b2 = _get(port, "/")
        assert s2 == 200
        assert h2.get("X-Frame-Options") == "DENY"
    finally:
        _stop_server(httpd)


def test_flow_files_route_reports_errors_instead_of_faking_a_list(reg_file, proj_dir):
    """路由接线：/flow_files 与 /flow_spec 不需要 console token（同 /files/），
    缺 project / 项目不可解析 / 缺文件都回 ok:false + 原文，不冒充成功。"""
    _entry, _token = project_registry.register("proj_alpha", proj_dir)
    httpd, port = _start_server()
    try:
        status, _h, body = _get(port, "/flow_files")
        payload = json.loads(body.decode("utf-8"))
        assert status == 200, body.decode("utf-8")
        assert payload["ok"] is False
        assert "project" in payload["error"]
        assert payload["files"] == []

        status2, _h2, body2 = _get(port, "/flow_files?project=proj_alpha")
        payload2 = json.loads(body2.decode("utf-8"))
        assert status2 == 200
        assert payload2["ok"] is False
        assert payload2["error"]

        status3, _h3, body3 = _get(
            port, "/flow_spec?project=proj_alpha&file=Manuscript_alpha_V1.docx"
        )
        payload3 = json.loads(body3.decode("utf-8"))
        assert status3 == 200
        assert payload3["ok"] is False
        assert "未指定手稿文件" not in payload3["error"]
        assert payload3["spec"] is None
    finally:
        _stop_server(httpd)


# ── 重新登记幂等：页面 token 仍有效时不轮换（多标签安全）─────────────
PROJECT_PAGE = (
    (Path(__file__).resolve().parents[1] / "console" / "project.html").read_text(
        encoding="utf-8"
    )
)


def _page_token(page: Path) -> str:
    import re

    m = re.search(
        r'<meta\s+name="console-token"\s+content="([^"]*)"',
        page.read_text(encoding="utf-8"),
    )
    return m.group(1) if m else ""


def _fake_project(tmp_path: Path, name: str, monkeypatch) -> Path:
    proj = tmp_path / name
    proj.mkdir()
    page = proj / f"{name}.html"
    page.write_text(
        '<head>\n<meta name="console-token" content="__CONSOLE_TOKEN__"/>\n</head>',
        encoding="utf-8",
    )
    monkeypatch.setattr(cons, "project_dir", lambda project, explicit="": proj)
    return proj


def test_starter_token_valid_rejects_unregistered_project(reg_file, tmp_path, monkeypatch):
    proj = _fake_project(tmp_path, "proj_eps", monkeypatch)
    assert cons.starter_token_valid("proj_eps", str(proj)) is False


def test_register_from_page_keeps_token_when_page_still_valid(reg_file, tmp_path, monkeypatch):
    """页面 token 与注册表一致 → 只确认登记，不换 token（不会打翻其它标签页）。"""
    proj = _fake_project(tmp_path, "proj_gamma", monkeypatch)

    first = cons.register_from_page("proj_gamma", str(proj))
    assert first["token_written"] is True
    token = _page_token(proj / "proj_gamma.html")
    assert project_registry.lookup(token).project == "proj_gamma"
    before = (proj / "proj_gamma.html").read_text(encoding="utf-8")

    second = cons.register_from_page("proj_gamma", str(proj))
    assert second["token_written"] is False
    assert _page_token(proj / "proj_gamma.html") == token
    assert (proj / "proj_gamma.html").read_text(encoding="utf-8") == before
    assert project_registry.lookup(token).project == "proj_gamma"


def test_register_from_page_rotates_when_page_token_is_stale(reg_file, tmp_path, monkeypatch):
    """页面 meta 里是失效 token（旧标签页 / 被外部换过）→ 才重发 token。"""
    proj = _fake_project(tmp_path, "proj_delta", monkeypatch)
    first = cons.register_from_page("proj_delta", str(proj))
    assert first["token_written"] is True
    page = proj / "proj_delta.html"
    old = _page_token(page)
    page.write_text(
        page.read_text(encoding="utf-8").replace(old, "stale-token-not-in-registry"),
        encoding="utf-8",
    )

    third = cons.register_from_page("proj_delta", str(proj))
    assert third["token_written"] is True
    new = _page_token(page)
    assert new != "stale-token-not-in-registry"
    assert project_registry.lookup(old) is None
    assert project_registry.lookup(new).project == "proj_delta"


def test_start_page_recovers_from_stale_token_instead_of_showing_the_403():
    """file:// 页 token 失效时不得把裸 403 显示给用户，自动落到同源控制台页。"""
    assert "function tokenDeniedText(" in PROJECT_PAGE
    assert "function recoverFromTokenDenied(" in PROJECT_PAGE
    assert "本页 token 已失效，正在进入同源控制台" in PROJECT_PAGE
    for call in (
        'recoverFromTokenDenied(e, msg, "进入控制台失败")',
        'recoverFromTokenDenied(e, loadMsg, "加载失败")',
        'recoverFromTokenDenied(e, loadMsg, "选择失败")',
        'recoverFromTokenDenied(e, loadMsg, "清空失败")',
    ):
        assert call in PROJECT_PAGE
    # 两处 JS 哨兵 + 一处 meta 占位符仍在（rotate 只能改 meta）
    assert PROJECT_PAGE.count("__CONSOLE_TOKEN__") == 3


def test_start_page_register_reports_when_nothing_was_rotated():
    assert "j.token_written" in PROJECT_PAGE
    assert "本页 token 仍有效，无需刷新" in PROJECT_PAGE
