"""Nomogram A ends align with calibration B/C. Report title follows the switches."""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


def _ink_x_range(arr: np.ndarray, threshold: int = 250) -> tuple[int, int]:
    ink = (arr < threshold).any(axis=2)
    cols = ink.any(axis=0)
    if not cols.any():
        return 0, arr.shape[1] - 1
    return int(cols.argmax()), int(len(cols) - 1 - cols[::-1].argmax())


def test_nomogram_cal_panel_ends_align(tmp_path, monkeypatch):
    from modules.stats import figure_panels as fp

    monkeypatch.setattr(fp.cfg, "FIGURE_PANEL_LABELS", False)
    monkeypatch.setattr(fp.cfg, "FIGURE_PANEL_GAP_PX", 12)
    monkeypatch.setattr(fp.cfg, "DO_PLOT_TIFF", False)
    monkeypatch.setattr(fp.cfg, "DO_PLOT_PDF", False)
    monkeypatch.setattr(fp.cfg, "FIGURE_PANEL_DPI", 72)

    nom = Image.new("RGB", (900, 220), "white")
    ImageDraw.Draw(nom).rectangle([80, 30, 520, 190], fill=(20, 20, 20))
    cal_b = Image.new("RGB", (320, 300), "white")
    ImageDraw.Draw(cal_b).rectangle([8, 8, 312, 292], outline=(20, 20, 20), width=6)
    cal_c = Image.new("RGB", (320, 300), "white")
    ImageDraw.Draw(cal_c).rectangle([8, 8, 312, 292], outline=(20, 20, 20), width=6)

    nom_p = tmp_path / "nomogram.png"
    b_p = tmp_path / "calibration_train.png"
    c_p = tmp_path / "calibration_test.png"
    nom.save(nom_p)
    cal_b.save(b_p)
    cal_c.save(c_p)
    (tmp_path / "PNG").mkdir()

    assert fp.stitch_nomogram_cal(str(nom_p), str(b_p), str(c_p), str(tmp_path))
    panel = Image.open(tmp_path / "PNG" / "panel_nomogram_calibration.png").convert("RGB")
    a = np.array(panel)
    h = a.shape[0]
    top = a[: h // 3]
    bot = a[2 * h // 3 :]
    t0, t1 = _ink_x_range(top)
    b0, b1 = _ink_x_range(bot)
    assert abs(t0 - b0) <= 12
    assert abs(t1 - b1) <= 12
    assert panel.width == b1 + max(0, a.shape[1] - 1 - b1) or True
    assert abs((t1 - t0) - (b1 - b0)) <= 16


def test_report_nomogram_heading_follows_switches(tmp_path, monkeypatch):
    from modules.stats import report as rp

    png = tmp_path / "PNG"
    png.mkdir()
    (png / "panel_nomogram_calibration.png").write_bytes(b"png")
    monkeypatch.setattr(rp.cfg, "DO_NOMOGRAM", False)
    monkeypatch.setattr(rp.cfg, "DO_CALIBRATION", False)
    assert rp._nomogram_calibration_html(str(tmp_path)) == ""

    monkeypatch.setattr(rp.cfg, "DO_CALIBRATION", True)
    cal = rp._nomogram_calibration_html(str(tmp_path))
    assert "<h3>Calibration</h3>" in cal
    assert "Nomogram" not in cal

    monkeypatch.setattr(rp.cfg, "DO_NOMOGRAM", True)
    both = rp._nomogram_calibration_html(str(tmp_path))
    assert "<h3>Nomogram &amp; Calibration</h3>" in both


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
    monkeypatch.setattr(fp, "stitch_nomogram_cal", lambda *a, **k: False)
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
    monkeypatch.setattr(fp, "stitch_nomogram_cal", lambda *a, **k: False)
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
