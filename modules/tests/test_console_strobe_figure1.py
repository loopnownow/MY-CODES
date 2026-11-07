# -*- coding: utf-8 -*-
"""流程图页 Figure 1 项目源文件面板：静态契约回归，不启浏览器。

契约：Figure 1 单一源是 ``<project>/ref/pipeline/figure1_strobe.json``，
页面用 /flow_figure 读、/api/flow_figure 保存重画；旧的
``ref/Figure1_flow.json`` 只允许作为交互画布的兜底，不得再冒充 Figure 1 来源。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STROBE = ROOT / "modules" / "console" / "strobe" / "index.html"


def _html() -> str:
    return STROBE.read_text(encoding="utf-8")


def test_panel_is_a_collapsible_section_in_the_left_column():
    """面板在左控制栏内、默认隐藏、可折叠；标题写明「项目源文件」。"""
    html = _html()
    panel = html.index('id="fig1-panel"')
    assert panel < html.index("</aside>")
    assert "Figure 1（项目源文件）" in html
    assert 'id="fig1-panel" hidden' in html
    assert "<details" in html[panel:panel + 2000]
    assert 'id="fig1-layout"' in html


def test_panel_fetches_the_project_spec_endpoint():
    """读取走 /flow_figure?project=，并在 ok 时按 spec 渲染面板。"""
    html = _html()
    assert '"/flow_figure?project="' in html
    assert "encodeURIComponent(project)" in html
    assert "ref/pipeline/figure1_strobe.json" in html
    assert "function fig1Load" in html
    assert "fig1.spec = d.spec" in html


def test_panel_only_appears_with_a_project_param():
    """没有 ?project 时面板保持隐藏；只有 fig1Load 在项目分支里被调用。"""
    html = _html()
    load = html[html.index("function fig1Load"):]
    assert "if (!project)" in load
    assert "fig1Els.panel.hidden = true" in load
    assert "fig1Els.panel.hidden = false" in load
    boot = html[html.index("const hadStore"):]
    assert "if (projectName()) fig1Load();" in boot
    assert "fig1Load().then(function (ok)" in boot


def test_editable_fields_cover_the_expanded_schema():
    """screened / inclusion / analyzed / pos_name / notes + presplit / downsample / splits 行。"""
    html = _html()
    for field_id in (
        "fig1-screened",
        "fig1-inclusion",
        "fig1-analyzed",
        "fig1-pos-name",
        "fig1-notes",
        "fig1-presplit-rows",
        "fig1-downsample-rows",
        "fig1-split-rows",
    ):
        assert f'id="{field_id}"' in html
    assert '[["n", "n"], ["pp", "pp"], ["non_pp", "non_pp"]]' in html
    assert '["n", "pp", "non_pp"]' in html
    assert "fig1ItemNum" in html
    assert "fig1ArmEntries" in html
    assert "spec.presplit != null" in html
    assert "spec.splits != null" in html


def test_downsample_row_has_n_reason_and_arm():
    html = _html()
    block = html[html.index("function fig1DownsampleRow"):html.index("function fig1FillDownsample")]
    assert 'setAttribute("data-fig1-field", "n")' in block
    assert 'setAttribute("data-fig1-field", "reason")' in block
    assert 'setAttribute("data-fig1-field", "arm")' in block


def test_preview_img_uses_the_project_asset_route_and_cache_bust():
    html = _html()
    assert 'id="fig1-preview"' in html
    assert '"/files/" + encodeURIComponent(project)' in html
    assert '"?t=" + Date.now()' in html
    assert "payload.png_exists" in html
    assert "payload.png_rel" in html
    assert "fig1Els.previewImg.removeAttribute(\"src\")" in html


def test_save_posts_the_form_spec_with_the_console_token():
    """保存：按钮 id/文案、POST /api/flow_figure、X-Console-Token、body 带 project+spec。"""
    html = _html()
    assert 'id="fig1-save"' in html
    assert "保存并生成 Figure 1" in html
    assert 'id="fig1-reload"' in html
    assert "重新加载" in html
    assert 'fetch("/api/flow_figure", {' in html
    assert '"X-Console-Token": token' in html
    assert "JSON.stringify({ project: project, spec: spec })" in html
    assert 'meta[name="console-token"]' in html
    assert "__CONSOLE_TOKEN__" in html
    assert "window.parent" in html
    assert "function fig1ConsoleToken" in html


def test_save_message_reports_results_html_patching():
    """保存成功后重新读 spec、刷新预览，并明说 Results HTML 是否回填。"""
    html = _html()
    assert "Results HTML 已回填" in html
    assert "Results HTML 未改动" in html
    assert "data.patched" in html
    save = html[html.index("function fig1Save"):]
    assert "return fig1Load().then(function (reloaded)" in save


def test_form_keeps_layout_unknown_keys_and_inclusion_order():
    """spec 从原文件克隆后只改已知字段：layout / 未知键不丢，inclusion 顺序即行顺序。"""
    html = _html()
    assert "function fig1Clone(obj)" in html
    assert "JSON.parse(JSON.stringify(obj))" in html
    assert "const spec = fig1Clone(fig1.spec);" in html
    assert "spec.layout =" not in html
    assert "spec.layout=" not in html
    build = html[html.index("function fig1BuildSpec"):html.index("function fig1Load")]
    assert "fig1Els.inclusion.value.split(/\\r?\\n/)" in build
    assert ".filter(Boolean)" in build
    assert "Math.trunc(n)" in html


def test_missing_spec_is_an_explicit_error_not_a_manuscript_fallback():
    """读不到项目 spec 时面板报原文错误；旧 Figure1_flow.json 只作画布兜底。"""
    html = _html()
    assert "读取 Figure 1 项目源文件失败：" in html
    assert "本面板不回退为手稿数字" in html
    assert "fig1Els.form.hidden = true" in html

    new_src = html.index('"/flow_figure?project="')
    legacy = html.index("ref/Figure1_flow.json")
    assert new_src < legacy
    assert html.count("ref/Figure1_flow.json") == 1
    assert "function bootLegacyFigure1" in html
    assert html.index("function bootLegacyFigure1") < legacy
    boot = html[html.index("const hadStore"):]
    fallback = boot.index("return bootLegacyFigure1();")
    assert boot.index("fig1Load().then(function (ok)") < fallback
    assert "if (hadStore)" in boot[:fallback]
