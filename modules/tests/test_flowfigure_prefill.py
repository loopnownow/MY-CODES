# -*- coding: utf-8 -*-
"""手稿预填：flow_spec_text 输出 -> canonical 草稿（缺项/问题原样带回）。"""
from __future__ import annotations

import pytest

from modules.flowfigure import prefill

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


def test_from_spec_maps_to_canonical_and_validates():
    out = prefill.from_spec(STUDY_SPEC)
    assert out["spec"]["layout"] == "study"
    assert out["spec"]["schema_version"] == 3
    assert out["spec"]["included"]["n"] == 310
    assert out["problems"] == []


def test_from_spec_reports_audit_problem_instead_of_raising():
    bad = {**STUDY_SPEC, "included_n": 999}
    out = prefill.from_spec(bad)
    assert out["problems"]
    assert "n-audit" in out["problems"][0]


def test_from_spec_rejects_non_object():
    with pytest.raises(ValueError, match="对象"):
        prefill.from_spec(["not", "a", "dict"])


def test_from_docx_requires_docx(tmp_path):
    path = tmp_path / "old.doc"
    path.write_bytes(b"not a docx")
    with pytest.raises(ValueError, match="docx"):
        prefill.from_docx(path)


def test_from_docx_maps_methods_and_keeps_missing(tmp_path):
    import docx

    document = docx.Document()
    for line in (
        "Title",
        "Materials and Methods",
        "A total of 375 patients with CAC were screened in the picture system.",
        "Finally, a total of 310 patients were enrolled in the study.",
        "Results",
        "Text.",
    ):
        document.add_paragraph(line)
    path = tmp_path / "Manuscript_demo_V1.docx"
    document.save(path)

    out = prefill.from_docx(path)
    assert out["file"] == "Manuscript_demo_V1.docx"
    assert out["spec"]["layout"] == "study"
    assert out["spec"]["screened"]["n"] == 375
    assert out["spec"]["included"]["n"] == 310
    assert "training set n" in out["missing"]
