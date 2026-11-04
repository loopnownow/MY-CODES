# -*- coding: utf-8 -*-
"""Console flowchart prefill: manuscript list order and docx parsing contract."""
from __future__ import annotations

import docx
import pytest

from modules.console.flow_fill import (
    default_manuscript,
    flow_spec_from_docx,
    scan_manuscripts,
)
from modules.stats.flow_spec_text import AuditError

METHODS_LINES = (
    "Patients",
    "A total of 375 patients with CAC were screened in the picture system.",
    "Finally, a total of 310 patients were enrolled in the study.",
    "Model development",
    "LASSO logistic regression and radiomics features were built.",
)


def _docx(path, lines):
    document = docx.Document()
    for line in lines:
        document.add_paragraph(line)
    document.save(path)
    return path


def test_scan_manuscripts_puts_the_newest_manuscript_first(tmp_path):
    _docx(tmp_path / "Manuscript_demo_V1.docx", ("Title",))
    _docx(tmp_path / "Manuscript_demo_V2.docx", ("Title",))
    _docx(tmp_path / "notes.docx", ("Title",))
    (tmp_path / "data.xlsx").write_bytes(b"")

    files = scan_manuscripts(tmp_path)
    names = [entry["name"] for entry in files]
    assert names == ["Manuscript_demo_V2.docx", "Manuscript_demo_V1.docx", "notes.docx"]
    assert files[0]["is_manuscript"] is True
    assert files[-1]["is_manuscript"] is False
    assert default_manuscript(files) == "Manuscript_demo_V2.docx"


def test_default_manuscript_falls_back_to_first_docx_and_then_empty(tmp_path):
    _docx(tmp_path / "protocol.docx", ("Title",))
    assert default_manuscript(scan_manuscripts(tmp_path)) == "protocol.docx"
    empty = tmp_path / "empty"
    empty.mkdir()
    assert scan_manuscripts(empty) == []
    assert default_manuscript([]) == ""


def test_flow_spec_from_docx_reads_materials_and_methods(tmp_path):
    path = _docx(
        tmp_path / "Manuscript_demo_V1.docx",
        ("Title", "Materials and Methods", *METHODS_LINES, "Results", "Text."),
    )
    out = flow_spec_from_docx(path)
    assert out["file"] == "Manuscript_demo_V1.docx"
    assert out["spec"]["screened_n"] == 375
    assert out["spec"]["included_n"] == 310
    assert "training set n" in out["missing"]


def test_flow_spec_from_docx_needs_a_methods_heading(tmp_path):
    path = _docx(tmp_path / "Manuscript_demo_V1.docx", ("Title", "Introduction", "Text."))
    with pytest.raises(AuditError):
        flow_spec_from_docx(path)


def test_flow_spec_from_docx_rejects_doc(tmp_path):
    path = tmp_path / "Manuscript_old_V1.doc"
    path.write_bytes(b"not a docx")
    with pytest.raises(ValueError, match="docx"):
        flow_spec_from_docx(path)
