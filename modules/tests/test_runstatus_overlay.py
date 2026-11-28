"""B1/B2/B8: RunStatus accounting, report banner, overlay key whitelist."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pandas as pd

from modules import config as cfg
from modules.pipeline import (
    CRITICAL_STEPS,
    RunStatus,
    _apply_console_overlay,
    _overlay_unknown_keys,
)
from modules.stats.report import generate_results_html


def test_guard_forced_failure_is_not_ok():
    st = RunStatus()
    with st.guard("table2_all"):
        raise RuntimeError("synthetic table2 failure")
    assert st.status != "OK"
    assert st.status == "FAILED"
    assert any(x["name"] == "table2_all" for x in st.failures)


def test_external_load_failure_is_critical():
    st = RunStatus()
    with st.guard("external_load"):
        raise FileNotFoundError("missing ext.xlsx")
    assert "external_load" in CRITICAL_STEPS
    assert st.status == "FAILED"
    assert "missing ext.xlsx" in st.as_dict()["failures"][0]["error"]


def test_forced_step_failure_appears_in_html_report(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "DRY_RUN", False, raising=False)
    monkeypatch.setattr(cfg, "DO_HEATMAP", False, raising=False)
    monkeypatch.setattr(cfg, "DO_NRI", False, raising=False)
    monkeypatch.setattr(cfg, "DO_CORR", False, raising=False)
    monkeypatch.setattr(cfg, "DO_PARTIAL", False, raising=False)
    monkeypatch.setattr(cfg, "GROUP_KEEP", [], raising=False)
    st = RunStatus()
    with st.guard("table2_all"):
        raise RuntimeError("synthetic table2 failure")
    assert st.status != "OK"
    layer = SimpleNamespace()
    data = SimpleNamespace(all=layer, train=layer, test=layer)
    html = generate_results_html(
        data,
        {"clin": SimpleNamespace(table1=pd.DataFrame()), "rad": None},
        run_status=st.as_dict(),
        out_dir=str(tmp_path),
    )
    assert "synthetic table2 failure" in html
    assert "Run status: FAILED" in html
    written = list(tmp_path.glob("*.html"))
    assert written
    body = written[0].read_text(encoding="utf-8")
    assert "synthetic table2 failure" in body


def test_overlay_rejects_unknown_keys(tmp_path, monkeypatch):
    overlay = {"overlay": {"DO_CORR": False, "DO_DCAA": True}}
    path = tmp_path / "overlay.json"
    path.write_text(json.dumps(overlay), encoding="utf-8")
    monkeypatch.setenv("RAD_CONSOLE_OVERLAY", str(path))
    old_corr = getattr(cfg, "DO_CORR")
    try:
        unknown = _apply_console_overlay()
        assert "DO_DCAA" in unknown
        assert "DO_DCAA" in _overlay_unknown_keys
        assert not hasattr(cfg, "DO_DCAA")
        assert getattr(cfg, "DO_CORR") is False
        st = RunStatus()
        for key in unknown:
            st.warn(
                f"overlay:{key}",
                f"unknown overlay key {key!r} was ignored",
                critical=False,
            )
        assert st.status == "INCOMPLETE"
        assert "DO_DCAA" in st.as_dict()["failures"][0]["error"]
    finally:
        cfg.DO_CORR = old_corr
        from modules.config import settings as S
        S.DO_CORR = old_corr
