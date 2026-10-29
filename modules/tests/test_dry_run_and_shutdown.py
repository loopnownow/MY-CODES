"""Dry-run helpers + console shutdown token guard."""
from __future__ import annotations

import pandas as pd

from modules.utils import dry_run as dr


def test_dry_run_radiomics_and_clinical_plan(capsys):
    dr.set_dry_run(True)
    try:
        from modules.stats.radiomics import run_radiomics
        from modules.stats.clinical import select_clinical

        rad = pd.DataFrame({"Patient_ID": ["a", "b"], "f1": [0.1, 0.2], "f2": [1.0, 2.0]})
        y = pd.Series([0, 1]).to_numpy()
        out = run_radiomics(rad, y, dataset_label="dry")
        assert list(out.features) == []

        clin = pd.DataFrame({"Age": [40, 50]})
        group = pd.Series(["A", "B"])
        cres = select_clinical(clin, y, group, dataset_label="dry", table1_only=True)
        assert list(cres.features) == []
    finally:
        dr.set_dry_run(None)

    text = capsys.readouterr().out
    assert "DRY-RUN [radiomics]" in text
    assert "DRY-RUN [clinical]" in text


def test_listen_table_does_not_confuse_port_suffix():
    from modules.console import console as cons

    text = """
  TCP    127.0.0.1:8765         0.0.0.0:0              LISTENING       37536
  TCP    127.0.0.1:18765        0.0.0.0:0              LISTENING       9
  TCP    [::]:8770              0.0.0.0:0              LISTENING       4
  TCP    127.0.0.1:80           0.0.0.0:0              ESTABLISHED     1
"""
    assert cons._parse_listen_table(text) == {8765: 37536, 18765: 9, 8770: 4}


def test_radconsole_stop_action_and_port_band():
    from modules.console import console as cons

    assert cons.radconsole_action("radconsole://serve?port=8765") == "serve"
    assert cons.radconsole_action("radconsole://stop?lo=8765&hi=8776") == "stop"
    assert cons.radconsole_action("radconsole:stop?lo=1") == "stop"
    assert cons._port_band(1, 99999) == (8765, 8776)
    lo, hi = cons._port_band(80, 100)
    assert lo > hi


def test_stop_console_ports_kills_only_console(monkeypatch):

    from modules.console import console as cons

    from modules.console import serve as srv

    calls = []
    monkeypatch.setattr(srv, "_listen_map", lambda: {8765: 111, 8766: 222, 80: 333})
    monkeypatch.setattr(srv, "_command_lines", lambda pids: {
        111: r"C:\Python\python.exe C:\lab\stats-analysis\modules\console\console.py serve --port 8765",
        222: r"python other.py",
    })

    def fake_run(cmd, **_kw):
        calls.append(list(cmd))

    monkeypatch.setattr(srv.subprocess, "run", fake_run)
    out = cons.stop_console_ports(1, 99999)
    assert out["lo"] == 8765 and out["hi"] == 8776
    assert out["killed"] == [{"port": 8765, "pid": 111}]
    assert out["spared"] == [{"port": 8766, "pid": 222}]
    assert calls == [["taskkill", "/PID", "111", "/F"]]


def test_publish_local_repeats_get():
    import json
    import threading
    import time
    import urllib.request

    from modules.console import console as cons

    port = 18781
    payload = {"ok": True, "port": 8765, "listening": False, "console": False}
    threading.Thread(
        target=lambda: cons.publish_local(payload, port, hold=2),
        daemon=True,
    ).start()
    deadline = time.time() + 3

    def grab():
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except Exception:
                time.sleep(0.05)
        raise AssertionError("8780-style reply did not come up")

    assert grab()["port"] == 8765
    assert grab()["listening"] is False


def test_describe_port_names_console(monkeypatch):

    from modules.console import console as cons

    from modules.console import serve as srv

    monkeypatch.setattr(srv, "_listen_pid", lambda port: 7 if int(port) == 8765 else None)
    monkeypatch.setattr(
        srv, "_command_lines",
        lambda pids: {7: r'"C:\Python\python.exe" C:\lab\stats-analysis\modules\console\console.py serve'},
    )
    info = cons.describe_port(8765)
    assert info["console"] is True and info["name"] == "console.py" and info["pid"] == 7
    assert info["service"] == "本控制台"
    assert cons._service_label(
        r"console.py serve --port 8765 --project demo_study", True,
    ) == "本控制台 · demo_study"
    assert cons.describe_port(8766)["listening"] is False
    assert cons.describe_port(8766)["service"] == ""


def test_stop_from_query_is_one_port(monkeypatch):

    from modules.console import console as cons

    from modules.console import serve as srv

    calls = []
    monkeypatch.setattr(
        srv, "stop_console_port",
        lambda port: calls.append(port) or {"ok": True, "port": port, "killed": True},
    )
    missing = cons.stop_from_query({})
    assert missing["ok"] is False and missing["killed"] is False and calls == []
    ranged = cons.stop_from_query({"lo": ["8765"], "hi": ["8776"]})
    assert ranged["ok"] is False and calls == []
    got = cons.stop_from_query({"port": ["8765"]})
    assert calls == [8765] and got["killed"] is True


def test_stop_console_port_refuses_other_program(monkeypatch):

    from modules.console import console as cons

    from modules.console import serve as srv

    monkeypatch.setattr(srv, "describe_port", lambda port: {
        "ok": True, "port": port, "listening": True, "console": False,
        "pid": 9, "name": "svchost.exe", "error": "",
    })
    calls = []
    monkeypatch.setattr(srv.subprocess, "run", lambda *args, **kwargs: calls.append(args))
    out = cons.stop_console_port(8765)
    assert out["killed"] is False and out["console"] is False and calls == []
    outside = cons.stop_console_port(80)
    assert outside["killed"] is False and calls == []


def test_exact_serve_does_not_reclaim_or_hop(monkeypatch):
    import pytest
    from modules.console import console as cons

    monkeypatch.setattr(cons, "register_protocol", lambda: None)
    monkeypatch.setattr(cons, "_tcp_open", lambda port: True)
    monkeypatch.setattr(cons, "_already_serving", lambda port: False)
    seen = {}

    def describe(port):
        seen["port"] = port
        return {"listening": True, "console": True, "pid": 5, "name": "console.py"}

    monkeypatch.setattr(cons, "describe_port", describe)
    published = []
    monkeypatch.setattr(
        cons, "publish_local",
        lambda payload, port=8780: published.append(payload) or True,
    )
    reclaimed = []
    hopped = []
    monkeypatch.setattr(cons, "_reclaim_dead_port", lambda port: reclaimed.append(port))
    monkeypatch.setattr(cons, "pick_port", lambda port: hopped.append(port) or port)
    with pytest.raises(SystemExit) as ei:
        cons.cmd_serve(8765, open_browser=False, exact=True)
    assert ei.value.code == 2
    assert seen.get("port") == 8765
    assert reclaimed == [] and hopped == []
    assert published and published[0].get("console") is True and published[0].get("ok") is False


def test_shutdown_not_token_safe():
    from modules.console import console as cons

    assert "/api/shutdown" not in cons._TOKEN_SAFE_GET
    assert "/api/health" in cons._TOKEN_SAFE_GET
    assert "/console.css" in cons._TOKEN_SAFE_GET
    assert "/console_schema.js" in cons._TOKEN_SAFE_GET
    assert "/console_api.js" in cons._TOKEN_SAFE_GET
    assert "/console_ui.js" in cons._TOKEN_SAFE_GET


def test_shutdown_requires_token_http():
    """Local HTTP: health returns token; shutdown without it is 403."""
    import json
    import threading


    from http.client import HTTPConnection

    from modules.console import console as cons

    httpd = cons.ConsoleServer((cons.HOST, 0), cons.Handler)
    port = httpd.server_address[1]
    cons._HTTPD = httpd
    cons._SERVE_PORT = port
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        conn = HTTPConnection(cons.HOST, port, timeout=3)
        conn.request("GET", "/api/health", headers={"Origin": "http://127.0.0.1"})
        resp = conn.getresponse()
        body = json.loads(resp.read().decode("utf-8"))
        assert resp.status == 200 and body.get("ok")
        tok = body.get("token")
        assert tok

        conn.request("GET", "/api/shutdown")
        bad = conn.getresponse()
        bad.read()
        assert bad.status == 403

        conn.request(
            "GET", "/api/shutdown",
            headers={"X-Console-Token": tok, "Origin": "http://127.0.0.1"},
        )
        ok = conn.getresponse()
        ok_body = json.loads(ok.read().decode("utf-8"))
        assert ok.status == 200 and ok_body.get("stopping")
        conn.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
        cons._HTTPD = None
        cons._SERVE_PORT = None


def test_console_static_assets_exist():
    from modules.console import console as cons

    assert cons.CSS_PATH.is_file()
    for rel, path in cons.JS_ASSETS.items():
        if rel == "/console_app.js":
            continue  # optional shim
        assert path.is_file(), rel
    html = cons.HTML_PATH.read_text(encoding="utf-8")
    assert "console_schema.js" in html
    assert "console_api.js" in html
    assert "console_ui.js" in html
    assert "Handler" in dir(cons)
    assert "ConsoleServer" in dir(cons)


def test_icc_panel_hint_present():
    from modules.console.setting_meta import ICC_PANEL_HINT, ICC_KEY_TIPS

    assert "outcome-blind" in ICC_PANEL_HINT
    assert "outcome-blind" in ICC_KEY_TIPS["DO_ICC_FILTER"]
