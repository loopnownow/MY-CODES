# -*- coding: utf-8 -*-
"""三份历史 spec（expanded / published-a / study）统一版式渲染回归。"""
from __future__ import annotations

import numpy as np
from PIL import Image

from modules.flowfigure import layout, render_mpl, spec as spec_mod

EXPANDED = {
    "layout": "expanded-split-downsample",
    "screened": "Eligible patients (n = 458)",
    "inclusion": ["Inclusion one", "Inclusion two"],
    "presplit": {
        "training": {"label": "Training set", "n": 320, "pp": 37, "non_pp": 283},
        "test": {"label": "Test set", "n": 138, "pp": 16, "non_pp": 122},
    },
    "downsample": {"n": 209, "reason": "Training-set downsampling"},
    "analyzed": "249 patients analyzed (53 pseudoprogression vs 196 non-pseudoprogression)",
    "pos_name": "pseudoprogression",
    "splits": [
        {"label": "Training Cohort", "n": 111, "pp": 37, "non_pp": 74},
        {"label": "Test Cohort", "n": 138, "pp": 16, "non_pp": 122},
    ],
}

PUBLISHED = {
    "screened": "458 patients screened (January 2020 to December 2024)",
    "inclusion": ["Histologically confirmed carcinoma"],
    "exclusion": [{"reason": "Incomplete imaging", "n": 20}],
    "analyzed": "438 patients analyzed",
    "splits": [{"label": "Training Cohort", "n": 300}, {"label": "Validation Cohort", "n": 138}],
}

STUDY = {
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


def _render(raw, out):
    canonical = spec_mod.normalize(raw)
    spec_mod.validate(canonical)
    geometry = layout.build(canonical)
    render_mpl.render(geometry, out, quiet=True)
    return canonical, geometry


def test_expanded_spec_renders_all_nodes(tmp_path):
    _canonical, geometry = _render(EXPANDED, tmp_path / "expanded.png")
    ids = {box["id"] for box in geometry["boxes"]}
    assert {"screened", "arm-0", "arm-1", "downsample-0", "cohort-0"} <= ids
    assert "cohort-1" not in ids  # Test set 与 Test Cohort 同 n，去重
    assert "analyzed" not in ids
    assert not any(label["id"] == "analyzed" for label in geometry["labels"])


def test_published_spec_renders_criteria_and_cohorts(tmp_path):
    _canonical, geometry = _render(PUBLISHED, tmp_path / "published.png")
    ids = {box["id"] for box in geometry["boxes"]}
    assert {"screened", "inclusion", "exclusion", "cohort-0", "cohort-1"} <= ids


def test_study_spec_renders_pipeline_steps(tmp_path):
    _canonical, geometry = _render(STUDY, tmp_path / "study.png")
    ids = {box["id"] for box in geometry["boxes"]}
    assert {"screened", "inclusion", "exclusion", "included"} <= ids
    assert {"balance", "model-clinical", "model-radiomics", "explain-dag"} <= ids
    assert any(box_id.startswith("step-") for box_id in ids)


def test_rendered_png_is_white_background_and_reasonable_size(tmp_path):
    out = tmp_path / "figure.png"
    _render(EXPANDED, out)
    image = Image.open(out)
    array = np.asarray(image.convert("L"))
    assert array.shape[0] > 100 and array.shape[1] > 100
    assert (array >= 245).sum() > array.size * 0.8
