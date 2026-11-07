# -*- coding: utf-8 -*-
"""P4 版式：无 analyzed、去重、横向方法行（折行）、外验指向、文本 override、审计 warning。"""
from __future__ import annotations

import pytest

from modules.flowfigure import audit, layout, spec as spec_mod

BASE = {
    "screened": "Eligible patients (n = 458)",
    "presplit": {
        "training": {"label": "Training set", "n": 320, "pp": 37, "non_pp": 283},
        "test": {"label": "Test set", "n": 138, "pp": 16, "non_pp": 122},
    },
    "downsample": {"n": 209, "reason": "Downsampling", "arm": "Training set"},
    "cohorts": [
        {"id": "cohort-0", "label": "Training Cohort", "n": 111, "pp": 37, "non_pp": 74},
        {"id": "cohort-1", "label": "Test Cohort", "n": 138, "pp": 16, "non_pp": 122},
    ],
    "pipeline": {"steps": [{"kind": "lasso_logistic", "enabled": True},
                           {"kind": "roc", "enabled": True}]},
}


def _build(raw):
    canonical = spec_mod.normalize(raw)
    return canonical, layout.build(canonical)


def test_no_analyzed_box_label_or_merge_line():
    canonical = spec_mod.normalize(BASE)
    geometry = layout.build(canonical)
    assert "analyzed" not in {box["id"] for box in geometry["boxes"]}
    assert "analyzed" not in {label["id"] for label in geometry["labels"]}
    assert canonical["analyzed"]["n"] is None


def test_test_arm_dedup_keeps_training_cohort():
    _canonical, geometry = _build(BASE)
    ids = [box["id"] for box in geometry["boxes"]]
    assert "cohort-0" in ids  # training 侧有 downsample → 保留
    assert "cohort-1" not in ids  # test 臂与 Test Cohort 同 n 且无 downsample → 去重


def test_pipeline_is_horizontal_and_wraps_to_next_row():
    raw = {
        "screened": "Screened (n = 10)",
        "cohorts": [{"id": "cohort-0", "label": "Training Cohort", "n": 10}],
        "pipeline": {"steps": [
            {"kind": "lasso_logistic", "enabled": True},
            {"kind": "nomogram", "enabled": True},
        ]},
    }
    _canonical, geometry = _build(raw)
    first = next(box for box in geometry["boxes"] if box["id"] == "step-0")
    second = next(box for box in geometry["boxes"] if box["id"] == "step-1")
    assert first["x"] < second["x"]
    assert first["y"] == pytest.approx(second["y"])

    many = {
        "screened": "Screened (n = 10)",
        "cohorts": [{"id": "cohort-0", "label": "Training Cohort", "n": 10}],
        "pipeline": {"steps": [
            {"kind": "lasso_logistic", "enabled": True, "label": "A long method row"},
            {"kind": "nomogram", "enabled": True, "label": "Another long method row"},
            {"kind": "calibration", "enabled": True, "label": "Third long method row"},
            {"kind": "roc", "enabled": True, "label": "Fourth long method row"},
        ]},
    }
    _canonical, wrapped = _build(many)
    rows = sorted({round(box["y"], 3) for box in wrapped["boxes"] if box["id"].startswith("step-")})
    assert len(rows) >= 2


def test_external_dashed_leader_targets_first_pipeline_step():
    raw = {
        **BASE,
        "external": [{"id": "external-0", "label": "External A", "n": 50, "pp": 20, "non_pp": 30}],
    }
    _canonical, geometry = _build(raw)
    step = next(box for box in geometry["boxes"] if box["id"] == "step-0")
    target = (step["x"], step["y"] + step["h"] / 2)
    dashed = [edge for edge in geometry["edges"] if edge["kind"] == "dashed"]
    assert any(
        (abs(edge["b"][0] - target[0]) < 1e-6 and abs(edge["b"][1] - target[1]) < 1e-6)
        or (abs(edge["a"][0] - target[0]) < 1e-6 and abs(edge["a"][1] - target[1]) < 1e-6)
        for edge in dashed
    )


def test_text_override_rewraps_and_marks_manual():
    raw = {
        **BASE,
        "overrides": {"boxes": {}, "edges": {}, "hidden": [],
                      "text": {"arm-0": "Manually edited arm text"}},
    }
    _canonical, geometry = _build(raw)
    box = next(item for item in geometry["boxes"] if item["id"] == "arm-0")
    assert box["manual"] is True
    assert "Manually edited arm text" in "".join(box["lines"])


def test_exclusion_without_n_only_warns():
    raw = {
        "screened": "Screened (n = 100)",
        "criteria": {"inclusion": ["x"], "exclusion": [{"reason": "no n here", "n": None}]},
        "cohorts": [{"id": "cohort-0", "label": "Training Cohort", "n": 100}],
    }
    canonical = spec_mod.normalize(raw)
    warnings = audit.audit(canonical)
    assert warnings and "exclusion" in warnings[0]


def test_exclusion_with_wrong_n_still_fails():
    raw = {
        "screened": "Screened (n = 100)",
        "criteria": {"exclusion": [{"reason": "x", "n": 10}]},
        "cohorts": [{"id": "cohort-0", "label": "Training Cohort", "n": 80}],
        "analyzed": {"n": 80},
    }
    with pytest.raises(audit.AuditError, match="n-audit"):
        audit.audit(spec_mod.normalize(raw))
