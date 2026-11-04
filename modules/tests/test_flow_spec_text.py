# -*- coding: utf-8 -*-
"""flow_spec_text: field-by-field prefill that never invents a count."""
from __future__ import annotations

import docx
import pytest

from modules.stats.flow_spec_text import (
    AuditError,
    docx_paragraphs,
    methods_section,
    spec_from_text,
)

METHODS = """Patients
This retrospective study included consecutive patients from June 2018 to September 2021.
A total of 375 patients with CAC confirmed by histology were screened in the picture system.
The inclusion criteria were as follows: (1) histologically confirmed CAC; (2) preoperative MRI; (3) complete clinical records.
Exclusion criteria were as follows: (1) lesion diameter less than 1 cm (n=40); (2) poor image quality (n=15).
Finally, a total of 310 patients were enrolled in the study (positive, n = 80; negative, n = 230).
Among them, 216 patients were assigned to the training cohort, while 94 patients were assigned to the test cohort.
Model development
LASSO logistic regression with radiomics features and a nomogram was built. ROC, calibration, and DCA were reported.
"""


def test_spec_from_text_fills_the_stated_fields():
    out = spec_from_text(METHODS)
    spec = out["spec"]
    assert spec["period"] == "June 2018 to September 2021"
    assert spec["screened_n"] == 375
    assert spec["included_n"] == 310
    assert (spec["included_positive"], spec["included_negative"]) == (80, 230)
    assert spec["training"]["n"] == 216
    assert spec["test"]["n"] == 94
    assert [item["n"] for item in spec["exclusion"]] == [40, 15]
    assert spec["inclusion"][0] == "histologically confirmed CAC"
    assert spec["models"] == ["radiomics", "nomogram"]
    assert spec["metrics"] == ["roc", "calibration", "dca"]
    assert spec["lasso"] is True
    assert out["missing"] == []
    assert "375 patients" in out["evidence"]["screened_n"]


def test_spec_from_text_leaves_unstated_fields_out():
    text = "Patients\nConsecutive records were drawn from the department.\n"
    out = spec_from_text(text)
    assert "screened_n" not in out["spec"]
    assert "included_n" not in out["spec"]
    assert "screened n" in out["missing"]
    assert "included positive and negative counts" in out["missing"]


def test_spec_from_text_ignores_pairs_that_do_not_add_up():
    text = (
        "A total of 310 patients were enrolled in the study. "
        "Images were read by radiologist 2; reviewer 3 resolved disagreements.\n"
    )
    spec = spec_from_text(text)["spec"]
    assert spec["included_n"] == 310
    assert "included_positive" not in spec


def test_spec_from_text_rejects_a_period_without_dates():
    text = "Calibration showed agreement between predicted and observed results.\n"
    out = spec_from_text(text)
    assert "period" not in out["spec"]
    assert "study period" in out["missing"]


def test_spec_from_text_flags_ambiguous_balance():
    text = "SMOTE was applied to the training set; the test set was downsampled.\n"
    out = spec_from_text(text)
    assert "balance" not in out["spec"]
    assert any("SMOTE" in item for item in out["missing"])


def test_methods_section_needs_a_methods_heading():
    with pytest.raises(AuditError):
        methods_section(["Introduction", "Text.", "Results"])


def test_docx_paragraphs_and_methods_section_roundtrip(tmp_path):
    path = tmp_path / "Manuscript_demo_V1.docx"
    document = docx.Document()
    for line in ("Title", "Materials and Methods", *METHODS.splitlines(), "Results", "Text."):
        document.add_paragraph(line)
    document.save(path)

    paragraphs = docx_paragraphs(path)
    assert paragraphs[0] == "Title"
    section = methods_section(paragraphs)
    assert "375 patients" in section
    assert "ROC, calibration" in section
    assert spec_from_text(section)["spec"]["screened_n"] == 375
