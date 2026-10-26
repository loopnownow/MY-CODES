# -*- coding: utf-8 -*-
"""Figure 1 study flowchart: Canvas contract and n-audit gate."""
from __future__ import annotations

import pytest

from modules.stats.figure_strobe_flow import AuditError, draw_study_flow


def _spec(**extra):
    spec = {
        "screened_n": 120,
        "included_n": 100,
        "included_positive": 50,
        "included_negative": 50,
        "exclusion": [{"reason": "Missing MRI", "n": 20}],
        "training": {"n": 70, "positive": 35, "negative": 35},
        "test": {"n": 30, "positive": 15, "negative": 15},
        "models": ["clinical", "radiomics", "nomogram"],
        "metrics": ["roc", "calibration", "dca", "nri", "idi"],
        "explain": ["dag", "mediation"],
    }
    spec.update(extra)
    return spec


def test_draw_study_flow_writes_pdf(tmp_path):
    out = draw_study_flow(_spec(), tmp_path / "Figure1_flow.pdf")
    assert out.is_file()
    assert out.stat().st_size > 3_000
    assert out.read_bytes()[:4] == b"%PDF"


def test_draw_study_flow_three_cohorts_png(tmp_path):
    spec = _spec(
        screened_n=170,
        included_n=150,
        included_positive=75,
        included_negative=75,
        training={"n": 70, "positive": 35, "negative": 35},
        test={"n": 40, "positive": 20, "negative": 20},
        validation={"n": 40, "positive": 20, "negative": 20},
        balance="smote",
        lasso=True,
    )
    out = draw_study_flow(spec, tmp_path / "nested" / "Figure1_flow.png")
    assert out.is_file()
    assert out.stat().st_size > 3_000
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_audit_rejects_inconsistent_n(tmp_path):
    spec = _spec(included_n=101)
    with pytest.raises(AuditError):
        draw_study_flow(spec, tmp_path / "bad.pdf")
