# -*- coding: utf-8 -*-
"""GET /flow_spec 端到端：V1.3 式 Methods 的三条 exclusion 进入 canonical。"""
from __future__ import annotations

import json
import threading
from http.client import HTTPConnection
from pathlib import Path

import docx
import pytest

from modules.console import console as cons
from modules.console import http_impl
from modules.console import settings_layer
from modules.console import state as console_state

METHODS = (
    "Materials and Methods",
    "In total, 458 patients were included and divided, in a 7:3 ratio, into a "
    "training cohort (n = 320) and a validation cohort (n = 138). Because of the high "
    "rate of pseudoprogression in the training cohort, downsampling was applied, "
    "yielding a final training cohort of 111 patients, while the validation cohort "
    "remained unchanged (n = 138). The locked analysis file comprised 249 patients "
    "(pseudoprogression, n = 53; non-pseudoprogression, n = 196).",
    "Inclusion criteria: (1) histologically confirmed NSCLC; (2) measurable tumor.",
    "Exclusion criteria: (1) patients who had concurrent other malignancies and had "
    "previously received immunotherapy; (2) CT examinations with suboptimal image "
    "quality or severe artifacts; (3) incomplete clinical, pathological, or CT "
    "imaging records.",
    "Results",
)


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


def test_flow_spec_returns_three_exclusions_without_n(tmp_path, monkeypatch):
    project = tmp_path / "demo"
    project.mkdir()
    document = docx.Document()
    document.add_paragraph("Title")
    for line in METHODS:
        document.add_paragraph(line)
    document.save(project / "Manuscript_demo_V1.docx")

    monkeypatch.setattr(http_impl, "_require_project", lambda value: str(value))
    monkeypatch.setattr(
        http_impl, "project_dir", lambda name, explicit_dir="": project,
    )
    monkeypatch.setattr(settings_layer, "project_dir", lambda name, explicit_dir="": project)
    monkeypatch.setattr(
        settings_layer, "under_project", lambda proj, rel: Path(proj) / str(rel).replace("\\", "/"),
    )
    httpd, port = _start_server()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=20)
        conn.request(
            "GET",
            "/flow_spec?project=demo&file=" + "Manuscript_demo_V1.docx",
            headers={"Origin": f"http://127.0.0.1:{port}"},
        )
        response = conn.getresponse()
        body = response.read().decode("utf-8")
        conn.close()
    finally:
        _stop_server(httpd)

    data = json.loads(body)
    assert response.status == 200 and data["ok"] is True
    canonical = data["canonical"]
    assert canonical["problems"] == []  # 缺 n 的 exclusion 只 warning，不阻断
    spec = canonical["spec"]
    reasons = [item["reason"] for item in spec["criteria"]["exclusion"]]
    assert len(reasons) == 3
    assert all(item["n"] is None for item in spec["criteria"]["exclusion"])
    assert spec["screened"]["n"] == 458
    assert [arm["n"] for arm in spec["presplit"]] == [320, 138]
    assert spec["downsample"][0]["n"] == 209
    assert [cohort["n"] for cohort in spec["cohorts"]] == [111, 138]
    assert spec["analyzed"]["n"] == 249
    assert spec["analyzed"]["pp"] == 53
    assert spec["analyzed"]["non_pp"] == 196

    from modules.flowfigure import audit as flow_audit

    warnings = flow_audit.audit(spec)
    assert warnings and "exclusion" in warnings[0]


def test_flow_spec_lists_missing_fields_when_methods_short(tmp_path, monkeypatch):
    project = tmp_path / "demo"
    project.mkdir()
    document = docx.Document()
    document.add_paragraph("Title")
    for line in ("Materials and Methods", "Inclusion criteria: (1) histologically confirmed NSCLC.", "Results"):
        document.add_paragraph(line)
    document.save(project / "Manuscript_demo_V1.docx")

    monkeypatch.setattr(http_impl, "_require_project", lambda value: str(value))
    monkeypatch.setattr(http_impl, "project_dir", lambda name, explicit_dir="": project)
    monkeypatch.setattr(settings_layer, "project_dir", lambda name, explicit_dir="": project)
    monkeypatch.setattr(
        settings_layer, "under_project", lambda proj, rel: Path(proj) / str(rel).replace("\\", "/"),
    )
    httpd, port = _start_server()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=20)
        conn.request(
            "GET", "/flow_spec?project=demo&file=Manuscript_demo_V1.docx",
            headers={"Origin": f"http://127.0.0.1:{port}"},
        )
        response = conn.getresponse()
        body = response.read().decode("utf-8")
        conn.close()
    finally:
        _stop_server(httpd)

    data = json.loads(body)
    assert response.status == 200 and data["ok"] is True
    assert data["missing"]
    assert "screened n" in data["missing"]
