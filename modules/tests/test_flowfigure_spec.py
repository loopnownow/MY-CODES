# -*- coding: utf-8 -*-
"""Figure 1 spec v2：旧 spec 映射、未知键保留、幂等、校验与审计。"""
from __future__ import annotations

import copy
import json

import pytest

from modules.flowfigure import audit, spec

LEGACY_EXPANDED = {
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
    "notes": "keep me",
    "custom_flag": {"a": 1},
}

LEGACY_PUBLISHED = {
    "screened": "458 patients screened (January 2020 to December 2024)",
    "inclusion": ["Inclusion one"],
    "exclusion": [{"reason": "Incomplete imaging", "n": 20}],
    "analyzed": "438 patients analyzed",
    "splits": [{"label": "Training Cohort", "n": 300}, {"label": "Validation Cohort", "n": 138}],
}

STUDY_SPEC = {
    "kind": "study",
    "source": "materials-and-methods",
    "period": "2019 to 2024",
    "screened_n": 375,
    "included_n": 310,
    "included_positive": 100,
    "included_negative": 210,
    "inclusion": ["Inclusion one"],
    "exclusion": [{"reason": "No imaging", "n": 65}],
    "training": {"n": 186, "positive": 60, "negative": 126},
    "test": {"n": 124, "positive": 40, "negative": 84},
    "balance": "downsample",
    "lasso": True,
    "models": ["clinical"],
    "metrics": ["roc"],
    "explain": [],
}


def test_normalize_maps_legacy_expanded_shape():
    out = spec.normalize(LEGACY_EXPANDED)
    assert out["schema_version"] == spec.SCHEMA_VERSION
    assert out["layout"] == spec.LAYOUT_EXPANDED
    assert out["screened"] == {"text": "Eligible patients (n = 458)", "n": 458}
    assert [arm["id"] for arm in out["presplit"]] == ["arm-0", "arm-1"]
    assert out["presplit"][0]["label"] == "Training set"
    assert [entry["arm"] for entry in out["downsample"]] == ["arm-0"]
    assert out["downsample"][0]["n"] == 209
    assert [cohort["id"] for cohort in out["cohorts"]] == ["cohort-0", "cohort-1"]
    assert out["analyzed"]["n"] == 249
    assert out["criteria"]["inclusion"] == ["Inclusion one", "Inclusion two"]
    assert out["font_size"] == spec.DEFAULT_FONT_SIZE_PT
    assert out["overrides"] == {"boxes": {}, "edges": {}, "hidden": []}


def test_normalize_is_idempotent_and_keeps_unknown_keys():
    once = spec.normalize(copy.deepcopy(LEGACY_EXPANDED))
    twice = spec.normalize(copy.deepcopy(once))
    assert twice == once
    assert once["custom_flag"] == {"a": 1}
    assert once["layout"] == spec.LAYOUT_EXPANDED
    assert once["notes"] == "keep me"


def test_normalize_keeps_unknown_entry_keys():
    raw = copy.deepcopy(LEGACY_EXPANDED)
    raw["presplit"]["training"]["flag"] = "keep"
    out = spec.normalize(raw)
    assert out["presplit"][0]["flag"] == "keep"


def test_normalize_rejects_unknown_layout():
    raw = copy.deepcopy(LEGACY_EXPANDED)
    raw["layout"] = "expanded"
    with pytest.raises(ValueError, match="未知 spec.layout"):
        spec.normalize(raw)


def test_normalize_study_shape_and_audit():
    out = spec.normalize(STUDY_SPEC)
    assert out["layout"] == spec.LAYOUT_STUDY
    assert out["screened"]["n"] == 375
    assert "375 patients screened" in out["screened"]["text"]
    assert out["analyzed"]["n"] == 310
    assert [cohort["id"] for cohort in out["cohorts"]] == ["cohort-training", "cohort-test"]
    assert out["cohorts"][0]["pp"] == 60
    assert out["pipeline"]["balance"] == "downsample"
    assert out["pipeline"]["models"] == ["clinical"]
    assert out["period"] == "2019 to 2024"
    audit.audit(out)
    assert spec.normalize(out) == out


def test_normalize_published_shape_and_audit():
    out = spec.normalize(LEGACY_PUBLISHED)
    assert out["layout"] == spec.LAYOUT_PUBLISHED
    assert out["presplit"] == []
    assert out["downsample"] == []
    assert out["analyzed"]["n"] == 438
    audit.audit(out)


def test_validate_rejects_expanded_without_presplit():
    out = spec.normalize(copy.deepcopy(LEGACY_EXPANDED))
    out["presplit"] = []
    with pytest.raises(ValueError, match="presplit"):
        spec.validate(out)


def test_validate_rejects_pipeline_on_expanded_layout():
    out = spec.normalize(copy.deepcopy(LEGACY_EXPANDED))
    out["pipeline"] = {"lasso": True}
    with pytest.raises(ValueError, match="pipeline"):
        spec.validate(out)


def test_validate_rejects_wrong_schema_version():
    out = spec.normalize(copy.deepcopy(LEGACY_EXPANDED))
    out["schema_version"] = 1
    with pytest.raises(ValueError, match="schema_version"):
        spec.validate(out)


def test_lxf_style_arithmetic_passes_and_bad_numbers_fail():
    good = spec.normalize(copy.deepcopy(LEGACY_EXPANDED))
    audit.audit(good)

    bad = copy.deepcopy(LEGACY_EXPANDED)
    bad["screened"] = "Eligible patients (n = 999)"
    with pytest.raises(audit.AuditError, match="n-audit"):
        audit.audit(spec.normalize(bad))

    bad = copy.deepcopy(LEGACY_EXPANDED)
    bad["splits"][0]["n"] = 112
    with pytest.raises(audit.AuditError, match="downsample"):
        audit.audit(spec.normalize(bad))


def test_to_json_text_round_trips_and_keeps_layout():
    out = spec.normalize(copy.deepcopy(LEGACY_EXPANDED))
    text = spec.to_json_text(out)
    assert text.endswith("\n")
    assert json.loads(text) == out
    assert json.loads(text)["layout"] == spec.LAYOUT_EXPANDED
