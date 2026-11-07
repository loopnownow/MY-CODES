# -*- coding: utf-8 -*-
"""Console flowchart prefill: manuscript list order and docx parsing contract."""
from __future__ import annotations

import json
from pathlib import Path

import docx
import pytest

from modules.console import flow_figure
from modules.flowfigure import spec as flow_spec
from modules.console.flow_fill import (
    default_manuscript,
    flow_spec_from_docx,
    scan_manuscripts,
)
from modules.stats.flow_spec_text import AuditError
from modules.stats.report import figure1_section_html

METHODS_LINES = (
    "Patients",
    "A total of 375 patients with CAC were screened in the picture system.",
    "Finally, a total of 310 patients were enrolled in the study.",
    "Model development",
    "LASSO logistic regression and radiomics features were built.",
)

# 最小合法 expanded spec：2 arm / 2 cohort，n-audit 全部对得上。
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
BAD_SPEC = {**MIN_SPEC, "screened": "Eligible patients (n = 999)"}


def _write_results_html(proj: Path) -> tuple[Path, Path]:
    res = proj / "Results_demo"
    res.mkdir(parents=True)
    html = res / "Results_demo.html"
    html.write_text(
        '<html><body>\n<section id="baseline"><h2>A. All Data — Baseline</h2>'
        "<p>x</p></section>\n</body></html>",
        encoding="utf-8",
    )
    return res, html


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


def test_scan_manuscripts_orders_decimal_versions(tmp_path):
    """V1.10 must sort above V1.9, and V2.1 above both (major.minor, not first digit)."""
    _docx(tmp_path / "Manuscript_demo_V1.9.docx", ("Title",))
    _docx(tmp_path / "Manuscript_demo_V1.10.docx", ("Title",))
    _docx(tmp_path / "Manuscript_demo_V2.1.docx", ("Title",))

    files = scan_manuscripts(tmp_path)
    assert [entry["name"] for entry in files] == [
        "Manuscript_demo_V2.1.docx",
        "Manuscript_demo_V1.10.docx",
        "Manuscript_demo_V1.9.docx",
    ]
    assert default_manuscript(files) == "Manuscript_demo_V2.1.docx"


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


def test_missing_figure1_spec_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="figure1_strobe.json"):
        flow_figure.load_figure1_spec(tmp_path)


def test_ensure_figure1_section_inserts_once_and_is_idempotent(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    res, html = _write_results_html(proj)

    assert flow_figure.ensure_figure1_section(html) is True
    text = html.read_text(encoding="utf-8")
    assert text.count('id="figure1-flow"') == 1
    assert text.index('id="figure1-flow"') < text.index("A. All Data — Baseline")
    assert figure1_section_html(str(res), force_img=True) in text
    assert flow_figure.ensure_figure1_section(html) is False
    assert html.read_text(encoding="utf-8") == text


def test_ensure_figure1_section_falls_back_to_body(tmp_path):
    html = tmp_path / "Results.html"
    html.write_text("<html><body><p>plain</p></body></html>", encoding="utf-8")
    assert flow_figure.ensure_figure1_section(html) is True
    text = html.read_text(encoding="utf-8")
    assert text.index('id="figure1-flow"') < text.index("</body>")

    no_body = tmp_path / "Results_nobody.html"
    no_body.write_text("<p>fragment</p>", encoding="utf-8")
    with pytest.raises(ValueError, match="插入点"):
        flow_figure.ensure_figure1_section(no_body)


def test_render_figure1_end_to_end(tmp_path):
    """进程内渲染：写 canonical spec、落 PNG+PDF、回填 Results；返回 page_cm/warnings。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    res, html = _write_results_html(proj)

    info = flow_figure.render_figure1(proj, MIN_SPEC)

    spec_file = proj / flow_figure.FIG1_SPEC_REL
    expected = flow_spec.normalize(MIN_SPEC)
    assert json.loads(spec_file.read_text(encoding="utf-8")) == expected
    assert expected["schema_version"] == 2
    png = res / "PNG" / "figure1_flow.png"
    pdf = res / "PNG" / "figure1_flow.pdf"
    assert png.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert pdf.read_bytes().startswith(b"%PDF")
    text = html.read_text(encoding="utf-8")
    assert text.count('id="figure1-flow"') == 1
    assert 'src="PNG/figure1_flow.png"' in text

    assert info["spec_rel"] == flow_figure.FIG1_SPEC_REL
    assert info["png_rel"] == "Results_demo/PNG/figure1_flow.png"
    assert info["pdf_rel"] == "Results_demo/PNG/figure1_flow.pdf"
    assert info["results"] == ["Results_demo"]
    assert info["patched"] == ["Results_demo/Results_demo.html"]
    assert info["page_cm"][0] == pytest.approx(16.0, abs=0.05)
    assert info["warnings"] == []
    state = flow_figure.read_figure1_state(proj)
    assert state["spec"] == expected
    assert state["png_exists"] is True
    assert state["geometry"]["page_in"][0] > 0
    assert {box["id"] for box in state["geometry"]["boxes"]} >= {"screened", "analyzed"}
    assert json.dumps(state)  # GET 响应必须可 JSON 序列化


def test_render_figure1_bad_spec_writes_nothing(tmp_path):
    """n-audit 失败：抛 ValueError，spec 原文、PNG/PDF、Results 全都不动。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    res, html = _write_results_html(proj)
    spec_file = proj / flow_figure.FIG1_SPEC_REL
    spec_file.parent.mkdir(parents=True)
    spec_file.write_text('{"keep": true}\n', encoding="utf-8")
    before_spec = spec_file.read_text(encoding="utf-8")
    before_html = html.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="n-audit"):
        flow_figure.render_figure1(proj, BAD_SPEC)

    assert spec_file.read_text(encoding="utf-8") == before_spec
    assert not (res / "PNG" / "figure1_flow.png").exists()
    assert not (res / "PNG" / "figure1_flow.pdf").exists()
    assert html.read_text(encoding="utf-8") == before_html


def test_state_geometry_matches_rendered_png_page(tmp_path):
    """GET 的 geometry.page_in 与磁盘 PNG 像素尺寸一致（同一次渲染）。"""
    from PIL import Image

    proj = tmp_path / "proj"
    proj.mkdir()
    res, _html = _write_results_html(proj)
    flow_figure.render_figure1(proj, MIN_SPEC)

    state = flow_figure.read_figure1_state(proj)
    png = proj / state["png_rel"]
    image = Image.open(png)
    page_w, page_h = state["geometry"]["page_in"]
    assert abs(image.size[0] - page_w * 300) <= 1
    assert abs(image.size[1] - page_h * 300) <= 1


def test_render_figure1_applies_spec_overrides_and_keeps_them(tmp_path):
    """overrides 随 spec 落盘，GET 返回的 geometry 已应用平移。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    res, _html = _write_results_html(proj)
    spec = json.loads(json.dumps(MIN_SPEC))
    spec["overrides"] = {"boxes": {"analyzed": {"x": 0.5, "y": 0.5}}, "edges": {}, "hidden": []}

    flow_figure.render_figure1(proj, spec)

    saved = json.loads((proj / flow_figure.FIG1_SPEC_REL).read_text(encoding="utf-8"))
    assert saved["overrides"]["boxes"]["analyzed"] == {"x": 0.5, "y": 0.5}
    state = flow_figure.read_figure1_state(proj)
    analyzed = next(box for box in state["geometry"]["boxes"] if box["id"] == "analyzed")
    assert analyzed["x"] == pytest.approx(0.5)
    assert analyzed["y"] == pytest.approx(0.5)
    assert state["warnings"] == []


def test_read_state_reports_unknown_override_id_as_warning(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    _res, _html = _write_results_html(proj)
    spec = json.loads(json.dumps(MIN_SPEC))
    spec["overrides"] = {"boxes": {"ghost": {"x": 1, "y": 1}}, "edges": {}, "hidden": []}
    from modules.flowfigure import spec as flow_spec
    from modules.console import flow_figure as figure

    target = proj / figure.FIG1_SPEC_REL
    target.parent.mkdir(parents=True)
    target.write_text(flow_spec.to_json_text(flow_spec.normalize(spec)), encoding="utf-8")

    state = figure.read_figure1_state(proj)
    assert state["warnings"]
    assert "ghost" in state["warnings"][0]


def test_legacy_shape_bridge_round_trips_to_canonical():
    """legacy.html 读的旧 shape 经 normalize 能回到同一份 canonical（含 overrides）。"""
    canonical = flow_spec.normalize(MIN_SPEC)
    canonical["overrides"] = {
        "boxes": {"analyzed": {"x": 1.0, "y": 2.0}}, "edges": {}, "hidden": [],
    }
    legacy = flow_figure.to_legacy_shape(canonical)
    assert legacy["screened"] == canonical["screened"]["text"]
    assert "arm_0" in legacy["presplit"]
    assert legacy["overrides"]["boxes"]["analyzed"] == {"x": 1.0, "y": 2.0}

    back = flow_spec.normalize(legacy)
    assert back["schema_version"] == 2
    assert back["layout"] == canonical["layout"]
    assert back["presplit"][0]["n"] == canonical["presplit"][0]["n"]
    assert back["downsample"][0]["n"] == canonical["downsample"][0]["n"]
    assert back["cohorts"][0]["n"] == canonical["cohorts"][0]["n"]
    assert back["overrides"]["boxes"]["analyzed"] == {"x": 1.0, "y": 2.0}
