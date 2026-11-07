# -*- coding: utf-8 -*-
"""Figure 1 spec v3：旧 spec 映射、未知键保留、幂等、校验与审计。"""
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
    assert out["schema_version"] == spec.SCHEMA_VERSION == 3
    assert out["layout"] == "expanded-split-downsample"
    assert out["screened"] == {"text": "Eligible patients (n = 458)", "n": 458}
    assert [arm["id"] for arm in out["presplit"]] == ["arm-0", "arm-1"]
    assert [entry["arm"] for entry in out["downsample"]] == ["arm-0"]
    assert [cohort["id"] for cohort in out["cohorts"]] == ["cohort-0", "cohort-1"]
    assert out["analyzed"]["n"] == 249
    assert out["included"]["n"] is None
    assert out["external"] == []
    assert len(out["pipeline"]["steps"]) == 6
    assert all(step["enabled"] is False for step in out["pipeline"]["steps"])
    assert out["font_size"] == spec.DEFAULT_FONT_SIZE_PT


def test_normalize_is_idempotent_and_keeps_unknown_keys():
    once = spec.normalize(copy.deepcopy(LEGACY_EXPANDED))
    twice = spec.normalize(copy.deepcopy(once))
    assert twice == once
    assert once["custom_flag"] == {"a": 1}
    assert once["notes"] == "keep me"


def test_normalize_keeps_unknown_entry_keys():
    raw = copy.deepcopy(LEGACY_EXPANDED)
    raw["presplit"]["training"]["flag"] = "keep"
    assert spec.normalize(raw)["presplit"][0]["flag"] == "keep"


def test_layout_is_only_a_source_marker():
    raw = copy.deepcopy(LEGACY_EXPANDED)
    raw["layout"] = "some-future-layout"
    out = spec.normalize(raw)
    assert out["layout"] == "some-future-layout"
    spec.validate(out)


def test_normalize_study_shape_uses_included_and_steps():
    out = spec.normalize(STUDY_SPEC)
    assert out["layout"] == "study"
    assert out["screened"]["n"] == 375
    assert out["included"] == {"text": "", "n": 310, "pp": 100, "non_pp": 210}
    assert [cohort["id"] for cohort in out["cohorts"]] == ["cohort-training", "cohort-test"]
    steps = {step["kind"]: step for step in out["pipeline"]["steps"]}
    assert steps["lasso_logistic"]["enabled"] is True
    assert steps["roc"]["enabled"] is True
    assert steps["nomogram"]["enabled"] is False
    assert steps["models"]["items"] == ["clinical"]
    assert out["period"] == "2019 to 2024"
    audit.audit(out)
    assert spec.normalize(out) == out


def test_normalize_published_shape_and_audit():
    out = spec.normalize(LEGACY_PUBLISHED)
    assert out["layout"] == "published-a"
    assert out["presplit"] == []
    assert out["analyzed"]["n"] == 438
    audit.audit(out)


def test_validate_allows_missing_optional_blocks_but_keeps_content_gate():
    out = spec.normalize(copy.deepcopy(LEGACY_EXPANDED))
    out["presplit"] = []
    out["downsample"] = []
    out["cohorts"] = []
    out["screened"] = {"text": "some screened", "n": 10}
    spec.validate(out)

    empty = spec.normalize({"layout": "x", "screened": {"text": "", "n": None}})
    with pytest.raises(ValueError, match="没有任何可画的节点"):
        spec.validate(empty)


def test_validate_rejects_bad_step_kind_and_wrong_version():
    out = spec.normalize(copy.deepcopy(LEGACY_EXPANDED))
    out["pipeline"]["steps"].append({"kind": "nope", "enabled": True})
    with pytest.raises(ValueError, match="白名单"):
        spec.validate(out)

    out = spec.normalize(copy.deepcopy(LEGACY_EXPANDED))
    out["schema_version"] = 2
    with pytest.raises(ValueError, match="schema_version"):
        spec.validate(out)


def test_lxf_style_arithmetic_passes_and_bad_numbers_fail():
    audit.audit(spec.normalize(copy.deepcopy(LEGACY_EXPANDED)))

    bad = copy.deepcopy(LEGACY_EXPANDED)
    bad["screened"] = "Eligible patients (n = 999)"
    with pytest.raises(audit.AuditError, match="n-audit"):
        audit.audit(spec.normalize(bad))

    bad = copy.deepcopy(LEGACY_EXPANDED)
    bad["splits"][0]["n"] = 112
    with pytest.raises(audit.AuditError, match="n-audit"):
        audit.audit(spec.normalize(bad))


def test_audit_blocks_external_independently():
    raw = copy.deepcopy(LEGACY_PUBLISHED)
    raw["external"] = [{"label": "External", "n": 90, "pp": 40, "non_pp": 40}]
    with pytest.raises(audit.AuditError, match="External"):
        audit.audit(spec.normalize(raw))


def test_to_json_text_round_trips_and_keeps_layout():
    out = spec.normalize(copy.deepcopy(LEGACY_EXPANDED))
    text = spec.to_json_text(out)
    assert text.endswith("\n")
    assert json.loads(text) == out


def test_analyzed_text_parses_pp_pair_for_join_label():
    """analyzed 只有文字时也要解析出 PP/non-PP，供汇合线标签使用。"""
    out = spec.normalize(copy.deepcopy(LEGACY_EXPANDED))
    assert out["analyzed"]["pp"] == 53
    assert out["analyzed"]["non_pp"] == 196


def test_pair_from_text_refuses_mismatched_sum():
    assert spec.pair_from_text("249 patients analyzed (53 vs 196)", 249) == (53, 196)
    assert spec.pair_from_text("249 patients analyzed (53 vs 196)", 248) == (None, None)
    assert spec.pair_from_text("no counts here", 249) == (None, None)
