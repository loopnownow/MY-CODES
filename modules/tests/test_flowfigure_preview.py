# -*- coding: utf-8 -*-
"""preview_figure1：临时渲染、不写项目、审计失败零落盘、overrides 生效。"""
from __future__ import annotations

import base64
from io import BytesIO

from PIL import Image

from modules.console import flow_figure

SPEC = {
    "layout": "expanded-split-downsample",
    "screened": "Eligible patients (n = 100)",
    "inclusion": ["Inclusion one", "Inclusion two"],
    "presplit": {"training": {"label": "Training", "n": 60, "pp": 20, "non_pp": 40},
                 "test": {"label": "Test", "n": 40, "pp": 10, "non_pp": 30}},
    "downsample": {"n": 10, "reason": "Downsampled for class imbalance"},
    "analyzed": "90 patients analyzed (30 positive vs 60 negative)",
    "splits": [{"label": "Training Cohort", "n": 50, "pp": 20, "non_pp": 30},
               {"label": "Test Cohort", "n": 40, "pp": 10, "non_pp": 30}],
}


def test_preview_returns_png_and_geometry_without_project_writes(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "sentinel.txt").write_text("keep", encoding="utf-8")

    info = flow_figure.preview_figure1(project, SPEC, None)

    assert info["ok"] is True
    image = Image.open(BytesIO(base64.b64decode(info["png_base64"])))
    assert image.format == "PNG"
    assert image.size[0] == round(info["geometry"]["page_in"][0] * 300) or abs(
        image.size[0] - info["geometry"]["page_in"][0] * 300
    ) <= 1
    assert info["geometry"]["boxes"]
    assert info["warnings"] == []
    assert [path.name for path in project.iterdir()] == ["sentinel.txt"]


def test_preview_audit_failure_returns_problems_and_writes_nothing(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    bad = {**SPEC, "screened": "Eligible patients (n = 999)"}

    info = flow_figure.preview_figure1(project, bad, None)

    assert info["ok"] is False
    assert info["problems"]
    assert "n-audit" in info["error"]
    assert list(project.iterdir()) == []


def test_preview_applies_overrides():
    geometry = flow_figure.preview_figure1(
        ".", SPEC, {"boxes": {"analyzed": {"x": 0.5, "y": 0.5}}},
    )["geometry"]
    box = next(item for item in geometry["boxes"] if item["id"] == "analyzed")
    assert (box["x"], box["y"]) == (0.5, 0.5)


def test_preview_unknown_override_ids_become_warnings():
    info = flow_figure.preview_figure1(".", SPEC, {"boxes": {"ghost": {"x": 1, "y": 1}}})
    assert info["ok"] is True
    assert info["warnings"]
