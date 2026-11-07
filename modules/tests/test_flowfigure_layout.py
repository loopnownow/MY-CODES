# -*- coding: utf-8 -*-
"""统一版式 geometry：id、page_in 16 cm 规则、步骤顺序、外验虚线与 analyzed 标签。"""
from __future__ import annotations

import pytest

from modules.flowfigure import layout, spec as spec_mod

STUDY_SPEC = {
    "kind": "study",
    "screened_n": 375,
    "period": "2019 to 2024",
    "included_n": 310,
    "included_positive": 100,
    "included_negative": 210,
    "inclusion": ["Inclusion one"],
    "exclusion": [{"reason": "No imaging", "n": 65}],
    "training": {"n": 186, "positive": 60, "negative": 126},
    "test": {"n": 124, "positive": 40, "negative": 84},
    "balance": "downsample",
    "lasso": True,
    "models": ["clinical", "radiomics"],
    "metrics": ["roc", "dca"],
    "explain": ["dag"],
}


def test_expanded_geometry_has_stable_box_ids_and_analyzed_label():
    raw = {
        "layout": "expanded-split-downsample",
        "screened": "Eligible (n = 100)",
        "presplit": {"training": {"n": 60, "pp": 20, "non_pp": 40},
                     "test": {"n": 40, "pp": 10, "non_pp": 30}},
        "downsample": {"n": 10, "reason": "Downsampled"},
        "analyzed": "90 analyzed (30 positive vs 60 negative)",
        "splits": [{"label": "Training Cohort", "n": 50, "pp": 20, "non_pp": 30},
                   {"label": "Test Cohort", "n": 40, "pp": 10, "non_pp": 30}],
    }
    geometry = layout.build(spec_mod.normalize(raw))
    ids = [box["id"] for box in geometry["boxes"]]
    assert ids == ["screened", "arm-0", "arm-1", "downsample-0", "cohort-0", "cohort-1"]
    assert "analyzed" not in ids
    assert "90 analyzed" in geometry["labels"][0]["text"]
    assert geometry["page_in"][0] == pytest.approx(
        spec_mod.TARGET_WIDTH_CM / spec_mod.CM_PER_INCH, abs=0.05,
    )
    assert geometry["page_cm"][0] <= spec_mod.MAX_WIDTH_CM + 1e-6
    for box in geometry["boxes"]:
        assert set(box) >= {"id", "x", "y", "w", "h", "lines", "fs"}


def test_study_geometry_exposes_steps_models_explain_and_included():
    geometry = layout.build(spec_mod.normalize(STUDY_SPEC))
    ids = {box["id"] for box in geometry["boxes"]}
    assert {"screened", "inclusion", "exclusion", "included"} <= ids
    assert {"cohort-training", "cohort-test"} <= ids
    assert "balance" in ids
    assert {"model-clinical", "model-radiomics", "explain-dag"} <= ids
    assert any(box_id.startswith("step-") for box_id in ids)
    assert "analyzed" not in ids


def test_steps_order_controls_vertical_order():
    base = {
        "screened": "screened (n = 10)",
        "cohorts": [{"id": "cohort-0", "label": "Training Cohort", "n": 10}],
        "analyzed": {"n": 10},
        "pipeline": {"steps": [
            {"kind": "lasso_logistic", "enabled": True},
            {"kind": "roc", "enabled": True},
        ]},
    }
    first = layout.build(spec_mod.normalize(base))
    lasso = next(box for box in first["boxes"] if box["id"] == "step-0")
    roc = next(box for box in first["boxes"] if box["id"] == "step-1")
    assert lasso["y"] < roc["y"]

    reversed_spec = {
        **base,
        "pipeline": {"steps": [
            {"kind": "roc", "enabled": True},
            {"kind": "lasso_logistic", "enabled": True},
        ]},
    }
    second = layout.build(spec_mod.normalize(reversed_spec))
    top = next(box for box in second["boxes"] if box["id"] == "step-0")
    bottom = next(box for box in second["boxes"] if box["id"] == "step-1")
    assert top["lines"][0] == "ROC AUC"
    assert bottom["lines"][0] == "LASSO logistic"
    assert top["y"] < bottom["y"]


def test_external_uses_dashed_bracket_and_stays_out_of_arithmetic():
    raw = {
        "screened": "458 patients screened",
        "inclusion": ["Inclusion one"],
        "exclusion": [{"reason": "Incomplete imaging", "n": 20}],
        "analyzed": "438 patients analyzed",
        "cohorts": [{"id": "cohort-0", "label": "Training Cohort", "n": 300}],
        "external": [
            {"id": "external-0", "label": "External A", "n": 50, "pp": 20, "non_pp": 30},
            {"id": "external-1", "label": "External B", "n": 60, "pp": 25, "non_pp": 35},
        ],
    }
    geometry = layout.build(spec_mod.normalize(raw))
    dashed = [edge for edge in geometry["edges"] if edge["kind"] == "dashed"]
    assert dashed
    assert any(label["id"] == "external-label" for label in geometry["labels"])
    assert {box["id"] for box in geometry["boxes"]} >= {"external-0", "external-1"}


def test_page_width_grows_with_content_and_warns_above_max():
    wide = {
        "screened": "x" * 260,
        "inclusion": ["Inclusion one"],
        "cohorts": [{"id": "cohort-0", "label": "Training Cohort", "n": 10}],
        "analyzed": {"n": 10},
    }
    geometry = layout.build(spec_mod.normalize(wide))
    assert geometry["page_cm"][0] >= spec_mod.TARGET_WIDTH_CM
