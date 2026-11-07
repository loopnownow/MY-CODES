# -*- coding: utf-8 -*-
"""Figure 1 渲染：16 cm 目标宽、内容占宽 ≥85%、300 dpi PNG + 矢量 PDF、坏 spec 零落盘。"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

TOOLS = Path(__file__).resolve().parents[2] / "tools" / "draw-flowchart"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import draw_strobe_flow as published  # noqa: E402
import draw_strobe_flow_expanded as expanded  # noqa: E402

from modules.flowfigure import spec as spec_mod  # noqa: E402

LXF = {
    "layout": "expanded-split-downsample",
    "screened": "Eligible patients after inclusion/exclusion (n = 458). "
                "Study period ending December 2024.",
    "inclusion": [
        "Histologically confirmed adenocarcinoma or squamous-cell carcinoma",
        "Measurable tumor at baseline and after treatment",
        "At least two cycles of chemoimmunotherapy",
        "Progression monitored for 4-6 weeks to classify pseudoprogression",
    ],
    "presplit": {
        "training": {
            "label": "Training set (7:3 split; model development)",
            "n": 320, "pp": 37, "non_pp": 283,
        },
        "test": {
            "label": "Test set (7:3 split; locked-model evaluation)",
            "n": 138, "pp": 16, "non_pp": 122,
        },
    },
    "downsample": {
        "n": 209,
        "reason": "Training-set downsampling of 209 non-pseudoprogression "
                  "(class imbalance; not a clinical exclusion)",
    },
    "analyzed": "249 patients analyzed (53 pseudoprogression vs 196 non-pseudoprogression)",
    "pos_name": "pseudoprogression",
    "splits": [
        {"label": "Training Cohort", "n": 111, "pp": 37, "non_pp": 74},
        {"label": "Test Cohort", "n": 138, "pp": 16, "non_pp": 122},
    ],
}

MIN_SPEC = {
    "layout": "expanded-split-downsample",
    "screened": "Eligible patients (n = 100)",
    "inclusion": ["Inclusion one", "Inclusion two"],
    "presplit": {
        "training": {"label": "Training", "n": 60, "pp": 20, "non_pp": 40},
        "test": {"label": "Test", "n": 40, "pp": 10, "non_pp": 30},
    },
    "downsample": {"n": 10, "reason": "Downsampled for class imbalance"},
    "analyzed": "90 patients analyzed (30 positive vs 60 negative)",
    "pos_name": "positive",
    "splits": [
        {"label": "Training Cohort", "n": 50, "pp": 20, "non_pp": 30},
        {"label": "Test Cohort", "n": 40, "pp": 10, "non_pp": 30},
    ],
}

PUBLISHED = {
    "screened": "458 patients screened (January 2020 to December 2024)",
    "inclusion": ["Histologically confirmed carcinoma", "Paired CT scans available"],
    "exclusion": [{"reason": "Incomplete imaging", "n": 20}],
    "analyzed": "438 patients analyzed (53 positive vs 385 negative)",
    "splits": [{"label": "Training Cohort", "n": 300}, {"label": "Validation Cohort", "n": 138}],
}


def _measure(png: Path) -> tuple[Image.Image, float, float]:
    image = Image.open(png)
    gray = np.asarray(image.convert("L"))
    mask = gray < 240
    cols = np.where(mask.any(axis=0))[0]
    width = image.size[0]
    content_fraction = (cols[-1] - cols[0] + 1) / width
    right_blank = (width - cols[-1] - 1) / width
    return image, content_fraction, right_blank


def test_expanded_render_hits_target_width_and_fills_page(tmp_path):
    out = tmp_path / "figure1_flow.png"
    expanded.draw_expanded_strobe(LXF, out, audit_n=True)

    image, content_fraction, right_blank = _measure(out)
    assert image.format == "PNG"
    assert image.info["dpi"][0] == pytest.approx(300, abs=1)
    width_cm = image.size[0] / 300 * 2.54
    assert spec_mod.TARGET_WIDTH_CM - 0.05 <= width_cm <= spec_mod.MAX_WIDTH_CM + 1e-6
    assert content_fraction >= 0.85
    assert right_blank <= 0.10


def test_expanded_min_spec_stretches_to_16cm(tmp_path):
    out = tmp_path / "min.png"
    expanded.draw_expanded_strobe(MIN_SPEC, out, audit_n=True)
    image, content_fraction, _ = _measure(out)
    width_cm = image.size[0] / 300 * 2.54
    assert width_cm == pytest.approx(spec_mod.TARGET_WIDTH_CM, abs=0.05)
    assert content_fraction >= 0.85


def test_expanded_pdf_is_vector(tmp_path):
    out = tmp_path / "figure1_flow.pdf"
    expanded.draw_expanded_strobe(LXF, out, audit_n=True)
    head = out.read_bytes()[:8]
    assert head.startswith(b"%PDF")


def test_bad_spec_raises_and_writes_nothing(tmp_path):
    bad = {**MIN_SPEC, "screened": "Eligible patients (n = 999)"}
    out = tmp_path / "bad.png"
    with pytest.raises(published.AuditError, match="n-audit"):
        expanded.draw_expanded_strobe(bad, out, audit_n=True)
    assert not out.exists()


def test_published_a_render_hits_target_width(tmp_path):
    out = tmp_path / "a.png"
    published.draw_strobe_flow(PUBLISHED, out, audit=True)
    image, content_fraction, right_blank = _measure(out)
    width_cm = image.size[0] / 300 * 2.54
    assert width_cm == pytest.approx(spec_mod.TARGET_WIDTH_CM, abs=0.05)
    assert content_fraction >= 0.85
    assert right_blank <= 0.10


def test_legacy_and_canonical_render_are_identical(tmp_path):
    """旧 spec 经 normalize 后渲染与直接渲染逐像素一致（C2 验收）。"""
    legacy_png = tmp_path / "legacy.png"
    canonical_png = tmp_path / "canonical.png"
    expanded.draw_expanded_strobe(LXF, legacy_png, audit_n=True)
    expanded.draw_expanded_strobe(spec_mod.normalize(LXF), canonical_png, audit_n=True)
    legacy = np.asarray(Image.open(legacy_png).convert("L"))
    canonical = np.asarray(Image.open(canonical_png).convert("L"))
    assert legacy.shape == canonical.shape
    assert (legacy == canonical).all()


def test_tool_script_cli_keeps_compat_flags(tmp_path):
    """旧脚本 CLI（--json/--out/--no-audit）仍可用，返回 0 且出图。"""
    import json

    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(MIN_SPEC), encoding="utf-8")
    out = tmp_path / "cli.png"
    assert expanded.main(["--json", str(spec_path), "--out", str(out)]) == 0
    assert out.exists()
    image = Image.open(out)
    assert image.size[0] / 300 * 2.54 == pytest.approx(spec_mod.TARGET_WIDTH_CM, abs=0.05)


def test_cli_returns_nonzero_and_writes_nothing_on_audit_failure(tmp_path):
    import json

    bad = {**MIN_SPEC, "screened": "Eligible patients (n = 999)"}
    spec_path = tmp_path / "bad.json"
    spec_path.write_text(json.dumps(bad), encoding="utf-8")
    out = tmp_path / "bad.png"
    assert expanded.main(["--json", str(spec_path), "--out", str(out)]) == 1
    assert not out.exists()
