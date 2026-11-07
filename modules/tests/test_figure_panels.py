"""Nomogram and calibration are two separate panels without A/B labels; report titles follow switches."""
from __future__ import annotations

import os
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from PIL import Image


def test_nomogram_and_calibration_are_separate_panels(tmp_path, monkeypatch):
    from modules.stats import figure_panels as fp

    monkeypatch.setattr(fp.cfg, "DO_PLOT_TIFF", False)
    monkeypatch.setattr(fp.cfg, "DO_PLOT_PDF", False)
    monkeypatch.setattr(fp.cfg, "FIGURE_PANEL_DPI", 72)
    monkeypatch.setattr(fp.cfg, "DO_FIGURE_PANELS", True, raising=False)
    monkeypatch.setattr(fp.cfg, "DO_ROC", False, raising=False)
    monkeypatch.setattr(fp.cfg, "DO_DCA", False, raising=False)
    monkeypatch.setattr(fp.cfg, "DO_SHAP", False, raising=False)
    monkeypatch.setattr(fp.cfg, "DO_LANDMARK_ROC", False, raising=False)
    monkeypatch.setattr(fp.cfg, "DO_TIME_DEPENDENT_ROC", False, raising=False)
    monkeypatch.setattr(fp.cfg, "DO_NOMOGRAM", True, raising=False)
    monkeypatch.setattr(fp.cfg, "DO_CALIBRATION", True, raising=False)
    monkeypatch.setattr(fp.cfg, "SPLIT_COL", "", raising=False)
    monkeypatch.setattr(fp.cfg, "TRAIN_RATIO", 1, raising=False)

    png = tmp_path / "PNG"
    png.mkdir()
    Image.new("RGB", (300, 200), "white").save(png / "nomogram.png")
    Image.new("RGB", (240, 240), "white").save(png / "calibration_train.png")

    fp.build_outcome_panels(str(tmp_path))

    assert (png / "panel_nomogram.png").is_file()
    assert (png / "panel_calibration.png").is_file()
    assert not (png / "panel_nomogram_calibration.png").exists()
    for name in ("panel_nomogram.png", "panel_calibration.png"):
        gray = np.array(Image.open(png / name).convert("L"))
        assert int(gray.min()) > 250  # 全白源图：出现任何字（A/B 角标）就会变暗


def test_report_nomogram_calibration_headings(tmp_path, monkeypatch):
    from modules.stats import report as rp

    png = tmp_path / "PNG"
    png.mkdir()
    (png / "panel_nomogram.png").write_bytes(b"png")
    (png / "panel_calibration.png").write_bytes(b"png")
    monkeypatch.setattr(rp.cfg, "DO_NOMOGRAM", False)
    monkeypatch.setattr(rp.cfg, "DO_CALIBRATION", False)
    assert rp._nomogram_calibration_html(str(tmp_path)) == ""

    monkeypatch.setattr(rp.cfg, "DO_CALIBRATION", True)
    cal = rp._nomogram_calibration_html(str(tmp_path))
    assert "<h3>Calibration</h3>" in cal
    assert "<h3>Nomogram</h3>" not in cal

    monkeypatch.setattr(rp.cfg, "DO_NOMOGRAM", True)
    both = rp._nomogram_calibration_html(str(tmp_path))
    assert both.index("<h3>Nomogram</h3>") < both.index("<h3>Calibration</h3>")
    assert "panel_nomogram_calibration.png" not in both


def test_every_figure_title_goes_through_panel_switch():
    """静态契约：modules/（不含 tests）里 set_title(/suptitle( 都必须经图内标题开关或清空。"""
    root = Path(__file__).resolve().parents[1]
    allowed = ("panel_title", "panel_titles_on")
    empty_reset = re.compile(r"""(?:set_title|suptitle)\(\s*["']{2}""")
    offenders = []
    for path in sorted(root.rglob("*.py")):
        if "tests" in path.parts:
            continue
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1,
        ):
            if "set_title(" not in line and "suptitle(" not in line:
                continue
            if any(tok in line for tok in allowed) or empty_reset.search(line):
                continue
            offenders.append(f"{path.relative_to(root).as_posix()}:{lineno}: {line.strip()}")
    assert not offenders, "图内标题必须经 cfg.panel_title / cfg.panel_titles_on：\n" + "\n".join(offenders)


def test_figure_title_switch_off_and_on(monkeypatch):
    """行为契约：开关关时 axes 标题为空；开时恢复既有标题文字；轴标签不变。"""
    from modules.stats import vif_analysis

    frames = []

    def _cap(filename, fig, *args, **kwargs):
        frames.append(fig)
        return False

    monkeypatch.setattr(vif_analysis.cfg, "save_plot", _cap)
    df = pd.DataFrame({"Variable": ["age", "sex"], "VIF": [1.2, 3.4]})

    monkeypatch.setattr(vif_analysis.cfg, "FIGURE_PANEL_TITLES", False, raising=False)
    vif_analysis.plot_vif_bar(df, dataset_label="train")
    ax = frames[-1].axes[0]
    assert ax.get_title() == ""
    assert ax.get_xlabel() == "VIF"
    assert ax.get_ylabel() == ""

    monkeypatch.setattr(vif_analysis.cfg, "FIGURE_PANEL_TITLES", True, raising=False)
    vif_analysis.plot_vif_bar(df, dataset_label="train")
    ax = frames[-1].axes[0]
    assert ax.get_title() == "Variance Inflation Factor (train)"
    assert ax.get_xlabel() == "VIF"


def test_fig_label_defaults_overrides_and_templates(monkeypatch):
    """fig_label：默认文本 / 覆盖 / 模板格式化 / 未知键与非字符串值 raise。"""
    import modules.config as cfg
    from modules.config import fig_labels as fl

    monkeypatch.setattr(cfg, "FIG_LABELS", {}, raising=False)
    assert fl.fig_label("roc", "xlabel") == "1 − Specificity"
    assert fl.fig_label("roc", "ylabel") == "Sensitivity"
    assert fl.fig_label("km", "xlabel", unit="months") == "Time (months)"
    assert fl.fig_label("vif", "legend", warn=5) == "VIF=5"
    assert fl.fig_label("nomogram", "risk") == "Predicted\nProbability"

    monkeypatch.setattr(cfg, "FIG_LABELS", {"roc": {"xlabel": "1 - FPR"}}, raising=False)
    assert fl.fig_label("roc", "xlabel") == "1 - FPR"
    assert fl.fig_label("roc", "ylabel") == "Sensitivity"

    monkeypatch.setattr(cfg, "FIG_LABELS", {"km": {"xlabel": "t ({unit})"}}, raising=False)
    assert fl.fig_label("km", "xlabel", unit="年") == "t (年)"

    with pytest.raises(ValueError, match="未知图形/键"):
        fl.fig_label("nope", "xlabel")

    monkeypatch.setattr(cfg, "FIG_LABELS", {"roc": {"xlabel": 3}}, raising=False)
    with pytest.raises(ValueError, match="必须是字符串"):
        fl.fig_label("roc", "xlabel")


def test_normalize_fig_labels_contract():
    from modules.config.fig_labels import normalize_fig_labels

    assert normalize_fig_labels(None) == {}
    assert normalize_fig_labels("") == {}
    assert normalize_fig_labels({"roc": {"xlabel": "A"}}) == {"roc": {"xlabel": "A"}}
    # 未知 figure/key 原样保留（向前兼容）。
    assert normalize_fig_labels({"future": {"k": "v"}}) == {"future": {"k": "v"}}
    with pytest.raises(ValueError, match="必须是对象"):
        normalize_fig_labels(["roc"])
    with pytest.raises(ValueError, match="必须是对象"):
        normalize_fig_labels({"roc": []})
    with pytest.raises(ValueError, match="必须是字符串"):
        normalize_fig_labels({"roc": {"xlabel": None}})


def test_figure_specs_shape_and_json():
    import json

    from modules.config.fig_labels import figure_specs

    specs = figure_specs()
    assert isinstance(specs, list) and len(specs) >= 12
    ids = [s["id"] for s in specs]
    for need in ("roc", "roc_time", "dca", "calibration", "nomogram", "km",
                 "shap", "vif", "correlation", "idi", "nri", "lasso", "dag",
                 "mediation", "qc_habitat", "legend"):
        assert need in ids
    for spec in specs:
        assert isinstance(spec["id"], str) and spec["id"]
        assert isinstance(spec["title"], str) and spec["title"]
        assert isinstance(spec["fields"], list) and spec["fields"]
        for field in spec["fields"]:
            assert set(field) == {"key", "label", "default"}
            assert all(isinstance(v, str) and v for v in field.values())
    json.dumps(specs)  # 必须 JSON 可序列化（console 直接收）
    by_id = {s["id"]: s for s in specs}
    assert [f["key"] for f in by_id["legend"]["fields"]] == ["Clinical", "RadScore", "Combined"]
    assert by_id["legend"]["fields"][2]["default"] == "Nomogram"
    assert [f["key"] for f in by_id["dca"]["fields"]] == [
        "xlabel", "ylabel", "treat_all", "treat_none",
    ]


def test_legend_label_override(monkeypatch):
    import modules.config as cfg
    from modules.config.fig_labels import legend_label

    monkeypatch.setattr(cfg, "FIG_LABELS", {}, raising=False)
    assert legend_label("Combined") == "Nomogram"
    assert legend_label("Clinical") == "Clinical"
    assert legend_label("RadScore") == "RadScore"

    monkeypatch.setattr(
        cfg, "FIG_LABELS", {"legend": {"Combined": "联合模型"}}, raising=False,
    )
    assert legend_label("Combined") == "联合模型"
    assert legend_label("RadScore") == "RadScore"


def test_vif_labels_default_and_override(monkeypatch):
    """空 FIG_LABELS 时图内文字与旧硬编码逐字一致；覆盖后按配置出图。"""
    from modules.stats import vif_analysis

    frames = []

    def _cap(filename, fig, *args, **kwargs):
        frames.append(fig)
        return False

    monkeypatch.setattr(vif_analysis.cfg, "save_plot", _cap)
    df = pd.DataFrame({"Variable": ["age", "sex"], "VIF": [1.2, 3.4]})

    monkeypatch.setattr(vif_analysis.cfg, "FIG_LABELS", {}, raising=False)
    vif_analysis.plot_vif_bar(df, dataset_label="train", warn=5)
    ax = frames[-1].axes[0]
    assert ax.get_xlabel() == "VIF"
    assert [t.get_text() for t in ax.get_legend().get_texts()] == ["VIF=5"]

    monkeypatch.setattr(
        vif_analysis.cfg, "FIG_LABELS",
        {"vif": {"xlabel": "方差膨胀因子", "legend": "阈值 {warn:g}"}},
        raising=False,
    )
    vif_analysis.plot_vif_bar(df, dataset_label="train", warn=5)
    ax = frames[-1].axes[0]
    assert ax.get_xlabel() == "方差膨胀因子"
    assert [t.get_text() for t in ax.get_legend().get_texts()] == ["阈值 5"]


def test_nri_sector_labels_default_and_override(monkeypatch):
    """NRI 重分类弦图的扇区标签走 fig_label("nri")：默认 PC/SC + 分组名，可手写覆盖。"""
    import modules.config as cfg
    from modules.stats import nri_cir

    frames = []

    def _cap(filename, fig, *args, **kwargs):
        frames.append(fig)
        return False

    monkeypatch.setattr(nri_cir.cfg, "save_plot", _cap)
    data = [10, 2, 3, 5, 4, 6, 1, 2]

    def _texts():
        out = []
        for ax in frames[-1].axes:
            out.extend(t.get_text() for t in ax.texts)
        return out

    monkeypatch.setattr(cfg, "FIG_LABELS", {}, raising=False)
    assert nri_cir.chord_sector_labels("Neg", "Pos") == (
        "PC Neg", "PC Pos", "SC Neg", "SC Pos",
    )
    nri_cir.cir_plot(data, a="PC Neg", b="PC Pos", c="SC Neg", d="SC Pos")
    texts = _texts()
    assert "PC Neg" in texts and "PC Pos" in texts
    assert "SC Neg" in texts and "SC Pos" in texts

    monkeypatch.setattr(
        cfg, "FIG_LABELS",
        {"nri": {"prev_label": "旧 {group}", "new_label": "新 {group}"}},
        raising=False,
    )
    assert nri_cir.chord_sector_labels("Neg", "Pos") == (
        "旧 Neg", "旧 Pos", "新 Neg", "新 Pos",
    )
    nri_cir.cir_plot(data, a="旧 Neg", b="旧 Pos", c="新 Neg", d="新 Pos")
    texts = _texts()
    assert "旧 Neg" in texts and "旧 Pos" in texts
    assert "新 Neg" in texts and "新 Pos" in texts


def test_partial_pairs_cross_two_lists():
    from modules.stats.assoc import _as_name_list, _partial_pairs

    assert _as_name_list("phq15") == ["phq15"]
    assert _as_name_list("") == []
    assert _partial_pairs(["a", "b"], ["c"]) == [("a", "c"), ("b", "c")]
    assert _partial_pairs(["a"], ["a"]) == []
    assert _partial_pairs(["a", "b"], ["b", "a"]) == [("a", "b")]


def test_partial_html_lists_each_pair(monkeypatch):
    from modules.stats import report as rp

    monkeypatch.setattr(rp.cfg, "DO_PARTIAL", True, raising=False)
    html = rp._partial_html({"partial": [
        {"x": "a", "y": "b", "controls": ["age"], "method": "spearman",
         "r": 0.1, "lo": 0.0, "hi": 0.2, "p": 0.4, "n": 20, "df": 16},
        {"x": "a", "y": "c", "controls": ["age"], "method": "spearman",
         "r": 0.2, "lo": 0.0, "hi": 0.3, "p": 0.2, "n": 20, "df": 16},
    ]})
    assert html.count("<tr>") >= 3
    assert ">b<" in html and ">c<" in html


def test_report_hides_roc_when_switch_off(tmp_path, monkeypatch):
    from modules.stats import report as rp

    png = tmp_path / "PNG"
    png.mkdir()
    (png / "panel_roc.png").write_bytes(b"png")
    monkeypatch.setattr(rp.cfg, "DO_ROC", False, raising=False)
    monkeypatch.setattr(rp.cfg, "DO_NOMOGRAM", False, raising=False)
    monkeypatch.setattr(rp.cfg, "DO_CALIBRATION", False, raising=False)
    hidden = rp._figures_section_html(str(tmp_path))
    assert "ROC" not in hidden
    monkeypatch.setattr(rp.cfg, "DO_ROC", True, raising=False)
    shown = rp._figures_section_html(str(tmp_path))
    assert "ROC (train | test)" in shown


def test_report_hides_combined_heading_when_off(monkeypatch):
    import pandas as pd
    from modules.stats import report as rp

    class _Model:
        params = pd.Series({"const": 0.1, "age": 0.2})
        pvalues = pd.Series({"const": 0.4, "age": 0.01})

    class _Nomo:
        model = _Model()

    info = {"nomo": _Nomo()}
    monkeypatch.setattr(rp.cfg, "DO_COMBINED_MODEL", False, raising=False)
    hidden = "\n".join(rp._combined_block_html("train", info, False))
    assert "Combined Model" not in hidden

    monkeypatch.setattr(rp.cfg, "DO_COMBINED_MODEL", True, raising=False)
    shown = "\n".join(rp._combined_block_html("train", info, False))
    assert "<h3>Combined Model (Clinical + RadScore)</h3>" in shown
    assert "Combined Model" not in "\n".join(
        rp._combined_block_html("train", {"nomo": None}, False)
    )


def test_report_hides_dca_and_shap_when_off(tmp_path, monkeypatch):
    from modules.stats import report as rp

    png = tmp_path / "PNG"
    png.mkdir()
    (png / "panel_dca.png").write_bytes(b"png")
    (png / "panel_shap.png").write_bytes(b"png")
    monkeypatch.setattr(rp.cfg, "DO_ROC", False, raising=False)
    monkeypatch.setattr(rp.cfg, "DO_NOMOGRAM", False, raising=False)
    monkeypatch.setattr(rp.cfg, "DO_CALIBRATION", False, raising=False)
    monkeypatch.setattr(rp.cfg, "DO_DCA", False, raising=False)
    monkeypatch.setattr(rp.cfg, "DO_SHAP", False, raising=False)
    monkeypatch.setattr(rp.cfg, "SPLIT_COL", "", raising=False)
    hidden = rp._figures_section_html(str(tmp_path))
    assert "DCA" not in hidden
    assert "SHAP" not in hidden

    monkeypatch.setattr(rp.cfg, "DO_DCA", True, raising=False)
    monkeypatch.setattr(rp.cfg, "TRAIN_RATIO", 1, raising=False)
    full_train = rp._figures_section_html(str(tmp_path))
    assert "<h3>DCA</h3>" in full_train
    assert "train | test" not in full_train
    assert "SHAP" not in full_train

    monkeypatch.setattr(rp.cfg, "TRAIN_RATIO", 0.7, raising=False)
    split = rp._figures_section_html(str(tmp_path))
    assert "DCA (train | test)" in split


def test_td_roc_panels_place_train_left_of_test(tmp_path, monkeypatch):
    from modules.stats import figure_panels as fp

    captured = []

    def _cap(paths, stem, out_dir, labels=None):
        captured.append((stem, list(paths)))
        return True

    png = tmp_path / "PNG"
    png.mkdir()
    for name in (
        "landmark_roc_6_train.png",
        "landmark_roc_6_test.png",
        "landmark_roc_12_train.png",
        "ipcw_roc_24_test.png",
        "ipcw_roc_24_train.png",
    ):
        (png / name).write_bytes(b"png")
    monkeypatch.setattr(fp, "Image", object())
    monkeypatch.setattr(fp, "stitch_row", _cap)
    monkeypatch.setattr(fp, "stitch_subgroup_train_test", lambda *a, **k: None)
    monkeypatch.setattr(fp, "stitch_external_panels", lambda *a, **k: None)
    monkeypatch.setattr(fp, "_mediation_paths", lambda png_dir: [])
    monkeypatch.setattr(fp.cfg, "DO_ROC", False, raising=False)
    monkeypatch.setattr(fp.cfg, "DO_NOMOGRAM", False, raising=False)
    monkeypatch.setattr(fp.cfg, "DO_CALIBRATION", False, raising=False)
    monkeypatch.setattr(fp.cfg, "DO_DCA", False, raising=False)
    monkeypatch.setattr(fp.cfg, "DO_SHAP", False, raising=False)
    monkeypatch.setattr(fp.cfg, "DO_LANDMARK_ROC", True, raising=False)
    monkeypatch.setattr(fp.cfg, "DO_TIME_DEPENDENT_ROC", True, raising=False)
    fp.build_outcome_panels(str(tmp_path))
    got = {stem: paths for stem, paths in captured}
    assert [os.path.basename(p) for p in got["panel_landmark_roc_6"]] == [
        "landmark_roc_6_train.png", "landmark_roc_6_test.png",
    ]
    assert [os.path.basename(p) for p in got["panel_landmark_roc_12"]] == [
        "landmark_roc_12_train.png", "landmark_roc_12_test.png",
    ]
    assert [os.path.basename(p) for p in got["panel_ipcw_roc_24"]] == [
        "ipcw_roc_24_train.png", "ipcw_roc_24_test.png",
    ]


def test_report_shows_stitched_td_roc_panels(tmp_path, monkeypatch):
    from modules.stats import report as rp

    png = tmp_path / "PNG"
    png.mkdir()
    for name in (
        "panel_landmark_roc_12.png",
        "panel_landmark_roc_6.png",
        "panel_ipcw_roc_24.png",
        "landmark_roc_6_train.png",
        "ipcw_roc_24_test.png",
    ):
        (png / name).write_bytes(b"png")
    monkeypatch.setattr(rp.cfg, "DO_FIGURE_PANELS", True, raising=False)
    monkeypatch.setattr(rp.cfg, "DO_ROC", False, raising=False)
    monkeypatch.setattr(rp.cfg, "DO_NOMOGRAM", False, raising=False)
    monkeypatch.setattr(rp.cfg, "DO_CALIBRATION", False, raising=False)
    monkeypatch.setattr(rp.cfg, "DO_DCA", False, raising=False)
    monkeypatch.setattr(rp.cfg, "DO_SHAP", False, raising=False)
    monkeypatch.setattr(rp.cfg, "DO_LANDMARK_ROC", True, raising=False)
    monkeypatch.setattr(rp.cfg, "DO_TIME_DEPENDENT_ROC", True, raising=False)
    html = rp._figures_section_html(str(tmp_path))
    assert "Landmark ROC (train | test)" in html
    assert "Time-dependent ROC (train | test)" in html
    assert html.index("panel_landmark_roc_6.png") < html.index("panel_landmark_roc_12.png")
    assert "panel_ipcw_roc_24.png" in html
    assert "landmark_roc_6_train.png" not in html
    assert "ipcw_roc_24_test.png" not in html

    monkeypatch.setattr(rp.cfg, "DO_LANDMARK_ROC", False, raising=False)
    monkeypatch.setattr(rp.cfg, "DO_TIME_DEPENDENT_ROC", False, raising=False)
    hidden = rp._figures_section_html(str(tmp_path))
    assert "Landmark ROC" not in hidden
    assert "Time-dependent ROC" not in hidden


def test_panels_skip_dca_shap_when_off(tmp_path, monkeypatch):
    from modules.stats import figure_panels as fp

    captured = []

    def _cap(paths, stem, out_dir, labels=None):
        captured.append((stem, list(paths)))
        return False

    (tmp_path / "PNG").mkdir()
    monkeypatch.setattr(fp, "Image", object())
    monkeypatch.setattr(fp, "stitch_row", _cap)
    monkeypatch.setattr(fp, "stitch_subgroup_train_test", lambda *a, **k: None)
    monkeypatch.setattr(fp, "stitch_external_panels", lambda *a, **k: None)
    monkeypatch.setattr(fp, "_mediation_paths", lambda png: [])
    monkeypatch.setattr(fp.cfg, "DO_ROC", False, raising=False)
    monkeypatch.setattr(fp.cfg, "DO_NOMOGRAM", False, raising=False)
    monkeypatch.setattr(fp.cfg, "DO_CALIBRATION", False, raising=False)
    monkeypatch.setattr(fp.cfg, "DO_DCA", False, raising=False)
    monkeypatch.setattr(fp.cfg, "DO_SHAP", False, raising=False)
    fp.build_outcome_panels(str(tmp_path))
    assert "panel_dca" not in [stem for stem, _ in captured]
    assert "panel_shap" not in [stem for stem, _ in captured]

    captured.clear()
    monkeypatch.setattr(fp.cfg, "DO_DCA", True, raising=False)
    monkeypatch.setattr(fp.cfg, "TRAIN_RATIO", 1, raising=False)
    monkeypatch.setattr(fp.cfg, "SPLIT_COL", "", raising=False)
    fp.build_outcome_panels(str(tmp_path))
    dca = [paths for stem, paths in captured if stem == "panel_dca"]
    assert len(dca) == 1
    assert any(p.endswith("dca_train.png") for p in dca[0])
    assert all(not p.endswith("dca_test.png") for p in dca[0])
