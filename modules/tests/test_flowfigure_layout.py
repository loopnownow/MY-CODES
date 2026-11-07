# -*- coding: utf-8 -*-
"""layout geometry：id 稳定性、page_in 与 16 cm 规则、study 版式的 model/metric id。"""
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


def test_expanded_geometry_has_stable_box_ids_and_page_in():
    raw = {
        "layout": "expanded-split-downsample",
        "screened": "Eligible (n = 100)",
        "presplit": {"training": {"n": 60, "pp": 20, "non_pp": 40},
                     "test": {"n": 40, "pp": 10, "non_pp": 30}},
        "downsample": {"n": 10, "reason": "Downsampled"},
        "analyzed": "90 analyzed",
        "splits": [{"label": "Training Cohort", "n": 50, "pp": 20, "non_pp": 30},
                   {"label": "Test Cohort", "n": 40, "pp": 10, "non_pp": 30}],
    }
    geometry = layout.build(spec_mod.normalize(raw))
    ids = [box["id"] for box in geometry["boxes"]]
    assert ids == ["screened", "arm-0", "arm-1", "downsample-0", "cohort-0", "cohort-1", "analyzed"]
    assert geometry["page_in"][0] == pytest.approx(
        spec_mod.TARGET_WIDTH_CM / spec_mod.CM_PER_INCH, abs=0.05,
    )
    assert geometry["page_cm"][0] <= spec_mod.MAX_WIDTH_CM + 1e-6
    for box in geometry["boxes"]:
        assert set(box) >= {"id", "x", "y", "w", "h", "lines", "fs"}


def test_study_geometry_exposes_pipeline_ids():
    geometry = layout.build(spec_mod.normalize(STUDY_SPEC))
    ids = {box["id"] for box in geometry["boxes"]}
    assert {"screened", "inclusion", "exclusion", "included"} <= ids
    assert {"cohort-training", "cohort-test"} <= ids
    assert {"balance", "lasso", "model-clinical", "model-radiomics"} <= ids
    assert {"metric-roc", "metric-dca", "explain-dag"} <= ids
    assert geometry["page_cm"][0] <= spec_mod.MAX_WIDTH_CM + 1e-6


def test_page_width_grows_with_content_and_warns_above_max():
    wide = {
        "layout": "expanded-split-downsample",
        "screened": "x" * 200,
        "presplit": {"a": {"n": 10}, "b": {"n": 10}},
        "analyzed": "20 analyzed",
        "splits": [{"label": "A", "n": 10}, {"label": "B", "n": 10}],
    }
    geometry = layout.build(spec_mod.normalize(wide))
    assert geometry["page_cm"][0] > spec_mod.TARGET_WIDTH_CM
    if geometry["page_cm"][0] > spec_mod.MAX_WIDTH_CM:
        assert geometry["warnings"]
