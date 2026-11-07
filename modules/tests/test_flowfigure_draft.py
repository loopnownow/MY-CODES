# -*- coding: utf-8 -*-
"""设计草稿：保存/读取/丢弃，且不产生正式图副作用。"""
from __future__ import annotations

import json

from modules.console import flow_figure
from modules.flowfigure import spec as spec_mod

SPEC = {
    "layout": "expanded-split-downsample",
    "screened": "Eligible patients (n = 100)",
    "inclusion": ["Inclusion one"],
    "presplit": {"training": {"label": "Training", "n": 60, "pp": 20, "non_pp": 40},
                 "test": {"label": "Test", "n": 40, "pp": 10, "non_pp": 30}},
    "downsample": {"n": 10, "reason": "Downsampled"},
    "analyzed": "90 patients analyzed (30 positive vs 60 negative)",
    "splits": [{"label": "Training Cohort", "n": 50, "pp": 20, "non_pp": 30},
               {"label": "Test Cohort", "n": 40, "pp": 10, "non_pp": 30}],
}


def _project(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    return proj


def _write_results(proj):
    res = proj / "Results_demo"
    res.mkdir()
    (res / "Results_demo.html").write_text(
        '<html><body><section><h2>A. All Data — Baseline</h2></section></body></html>',
        encoding="utf-8",
    )
    return res


def test_save_design_writes_draft_only(tmp_path):
    proj = _project(tmp_path)
    res = _write_results(proj)
    before_html = (res / "Results_demo.html").read_text(encoding="utf-8")

    info = flow_figure.save_design(proj, SPEC)

    draft_file = proj / flow_figure.FIG1_DRAFT_REL
    assert draft_file.is_file()
    payload = json.loads(draft_file.read_text(encoding="utf-8"))
    assert payload["spec"] == spec_mod.normalize(SPEC)
    assert payload["saved_at"] == info["saved_at"]
    # 正式 spec / PNG / PDF / Results 一切不动
    assert not (proj / flow_figure.FIG1_SPEC_REL).exists()
    assert not (res / "PNG").exists()
    assert (res / "Results_demo.html").read_text(encoding="utf-8") == before_html


def test_state_exposes_draft_with_stale_and_generated(tmp_path):
    proj = _project(tmp_path)
    res = _write_results(proj)
    flow_figure.save_design(proj, SPEC)

    state = flow_figure.read_figure1_state(proj)
    assert state["draft"] is not None
    assert state["draft"]["spec"] == spec_mod.normalize(SPEC)
    assert state["draft"]["stale"] is True
    assert state["draft"]["generated"] is False

    flow_figure.render_figure1(proj, SPEC)
    state = flow_figure.read_figure1_state(proj)
    assert state["draft"]["generated"] is True
    assert state["draft"]["stale"] is False
    assert (res / "PNG" / "figure1_flow.png").is_file()


def test_state_without_draft_and_without_formal_spec_raises(tmp_path):
    import pytest

    proj = _project(tmp_path)
    flow_figure.save_design(proj, SPEC)
    flow_figure.discard_design(proj)
    with pytest.raises(FileNotFoundError, match="figure1_strobe.json"):
        flow_figure.read_figure1_state(proj)


def test_state_without_draft_after_generate_has_none(tmp_path):
    proj = _project(tmp_path)
    _write_results(proj)
    flow_figure.render_figure1(proj, SPEC)
    assert flow_figure.read_figure1_state(proj)["draft"] is None


def test_discard_design_is_idempotent(tmp_path):
    proj = _project(tmp_path)
    _write_results(proj)
    first = flow_figure.discard_design(proj)
    assert first["discarded"] is False

    flow_figure.save_design(proj, SPEC)
    assert (proj / flow_figure.FIG1_DRAFT_REL).is_file()
    second = flow_figure.discard_design(proj)
    assert second["discarded"] is True
    assert not (proj / flow_figure.FIG1_DRAFT_REL).exists()
