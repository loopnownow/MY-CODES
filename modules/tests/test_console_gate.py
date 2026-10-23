"""图总闸、旧 ini 的 Landmark 跟随、退役键写回、Landmark / IPCW 双开关。"""
from __future__ import annotations

from pathlib import Path

import pytest
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from modules import config as cfg
from modules.config.ini_io import read_ini, write_ini
from modules.stats import report


def test_old_ini_lifts_landmark_and_save_writes_both(tmp_path: Path):
    p = tmp_path / "settings.ini"
    p.write_text("[switch]\nDO_TIME_DEPENDENT_ROC = true\n", encoding="utf-8")
    data = read_ini(p)
    assert data["DO_LANDMARK_ROC"] is True
    assert data["DO_TIME_DEPENDENT_ROC"] is True
    write_ini(p, data)
    text = p.read_text(encoding="utf-8")
    assert "DO_LANDMARK_ROC = true" in text
    assert "DO_TIME_DEPENDENT_ROC = true" in text
    assert "DO_PLOT" not in text
    assert "FORCE_REMOVE_PREFIXES" not in text
    assert "FIG_FONT_FAMILY" not in text
    assert "[retired]" not in text


def test_both_keys_present_are_not_rewritten(tmp_path: Path):
    p = tmp_path / "settings.ini"
    p.write_text(
        "[switch]\nDO_LANDMARK_ROC = false\nDO_TIME_DEPENDENT_ROC = true\n",
        encoding="utf-8",
    )
    data = read_ini(p)
    assert data["DO_LANDMARK_ROC"] is False
    assert data["DO_TIME_DEPENDENT_ROC"] is True


def test_write_omits_default_retired_keeps_real_defaults(tmp_path: Path):
    p = tmp_path / "settings.ini"
    write_ini(p, {
        "DO_PLOT": False,
        "FORCE_REMOVE_PREFIXES": [],
        "FIG_FONT_FAMILY": "Arial",
        "DO_LANDMARK_ROC": False,
        "RAD_MARKER_SIDE": "both",
        "RAD_MARKER_MIN_N": 5,
    })
    text = p.read_text(encoding="utf-8")
    assert "DO_PLOT" not in text
    assert "FORCE_REMOVE_PREFIXES" not in text
    assert "FIG_FONT_FAMILY" not in text
    assert "[retired]" not in text
    assert "DO_LANDMARK_ROC = false" in text
    assert 'RAD_MARKER_SIDE = "both"' in text
    assert "RAD_MARKER_MIN_N = 5" in text


def test_write_keeps_nondefault_retired(tmp_path: Path):
    p = tmp_path / "settings.ini"
    write_ini(p, {
        "FORCE_REMOVE_PREFIXES": ["foo_"],
        "DO_PLOT": True,
        "DO_PLOT_TIFF": False,
        "DO_PLOT_PDF": False,
        "DO_LANDMARK_ROC": False,
    })
    text = p.read_text(encoding="utf-8")
    assert "[retired]" in text
    assert 'FORCE_REMOVE_PREFIXES = ["foo_"]' in text
    assert "DO_PLOT = true" in text
    back = read_ini(p)
    assert back["FORCE_REMOVE_PREFIXES"] == ["foo_"]
    assert back["DO_PLOT"] is True
    assert back["DO_LANDMARK_ROC"] is False


def test_save_plot_writes_nothing_when_figure_gate_off(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(cfg, "DO_FIGURE_PANELS", False, raising=False)
    monkeypatch.setattr(cfg, "DO_PLOT_TIFF", True, raising=False)
    monkeypatch.setattr(cfg, "DO_PLOT_PDF", True, raising=False)
    fig, ax = plt.subplots()
    ax.plot([0, 1], [0, 1])
    ok = cfg.save_plot("gate_off", fig, width=2, height=2, out_dir=str(tmp_path))
    assert ok is False
    assert list(tmp_path.rglob("*.png")) == []
    assert list(tmp_path.rglob("*.tiff")) == []
    assert list(tmp_path.rglob("*.pdf")) == []


def test_img_tag_empty_when_figure_gate_off(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(cfg, "DO_FIGURE_PANELS", False, raising=False)
    png = tmp_path / "PNG"
    png.mkdir()
    (png / "a.png").write_bytes(b"png")
    html = report._img_tag("a.png", "a", str(tmp_path))
    assert html == ""
    assert "<img" not in html


def test_img_tag_embeds_when_figure_gate_on(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(cfg, "DO_FIGURE_PANELS", True, raising=False)
    png = tmp_path / "PNG"
    png.mkdir()
    (png / "a.png").write_bytes(b"png")
    html = report._img_tag("a.png", "a", str(tmp_path))
    assert "<img" in html
    assert "PNG/a.png" in html


def test_ipcw_on_skips_landmark_when_landmark_off(monkeypatch):
    import modules.pipeline as pl
    monkeypatch.setattr(cfg, "DO_LANDMARK_ROC", False, raising=False)
    monkeypatch.setattr(cfg, "DO_TIME_DEPENDENT_ROC", True, raising=False)
    hits = {"lm": 0, "ipcw": 0}

    monkeypatch.setattr(
        "modules.stats.curves_roc.plot_landmark_roc",
        lambda *_a, **_k: hits.__setitem__("lm", hits["lm"] + 1),
    )
    monkeypatch.setattr(
        "modules.stats.curves_ipcw.plot_ipcw_roc",
        lambda *_a, **_k: hits.__setitem__("ipcw", hits["ipcw"] + 1),
    )

    class Layer:
        landmark = {"pairs": []}
        ipcw = {
            "time_col": "ipcw_time",
            "status_col": "ipcw_status",
            "horizons": [6],
            "time": [1.0, 2.0],
            "status": [0.0, 1.0],
        }

    pl._maybe_td_rocs("train", {"RadScore": [0.1, 0.9]}, Layer())
    assert hits["lm"] == 0
    assert hits["ipcw"] == 1


def test_both_roc_keys_run_landmark_and_ipcw(monkeypatch):
    import modules.pipeline as pl
    monkeypatch.setattr(cfg, "DO_LANDMARK_ROC", True, raising=False)
    monkeypatch.setattr(cfg, "DO_TIME_DEPENDENT_ROC", True, raising=False)
    hits = {"lm": 0, "ipcw": 0}

    monkeypatch.setattr(
        "modules.stats.curves_roc.plot_landmark_roc",
        lambda *_a, **_k: hits.__setitem__("lm", hits["lm"] + 1),
    )
    monkeypatch.setattr(
        "modules.stats.curves_ipcw.plot_ipcw_roc",
        lambda *_a, **_k: hits.__setitem__("ipcw", hits["ipcw"] + 1),
    )

    class Layer:
        landmark = {
            "pairs": [{
                "time_col": "",
                "time_label": "6",
                "status_col": "lm_status_6m",
                "time": [6.0, 6.0],
                "status": [0.0, 1.0],
            }]
        }
        ipcw = {
            "time_col": "ipcw_time",
            "status_col": "ipcw_status",
            "horizons": [3, 6],
            "time": [1.0, 8.0],
            "status": [1.0, 0.0],
        }

    pl._maybe_td_rocs("train", {"RadScore": [0.1, 0.9]}, Layer())
    assert hits["lm"] == 1
    assert hits["ipcw"] == 1


def test_external_missing_ipcw_skips_and_keeps_landmark(monkeypatch):
    import modules.pipeline as pl
    monkeypatch.setattr(cfg, "DO_LANDMARK_ROC", True, raising=False)
    monkeypatch.setattr(cfg, "DO_TIME_DEPENDENT_ROC", True, raising=False)
    hits = {"lm": 0}

    monkeypatch.setattr(
        "modules.stats.curves_roc.plot_landmark_roc",
        lambda *_a, **_k: hits.__setitem__("lm", hits["lm"] + 1),
    )

    class Layer:
        landmark = {
            "pairs": [{
                "time_col": "",
                "time_label": "6",
                "status_col": "response_6m",
                "time": [6.0, 6.0],
                "status": [0.0, 1.0],
            }]
        }
        ipcw = None

    pl._maybe_td_rocs("ext_ext", {"RadScore": [0.1, 0.9]}, Layer(), ipcw_required=False)
    assert hits["lm"] == 1


def test_external_section_shows_missing_ipcw_note(monkeypatch):
    monkeypatch.setattr(cfg, "DO_ROC", True, raising=False)
    html = report._external_section_html(
        [{
            "display": "ext",
            "level": "ext",
            "token": "ext",
            "val_mode": "refit",
            "splits": {"ext": {
                "n": 37,
                "n1": 29,
                "n0": 8,
                "auc": 0.8,
                "ipcw_note": (
                    "Time-dependent ROC (IPCW) was not run on this external set: "
                    "the sheet has no ipcw_time or ipcw_status."
                ),
            }},
        }],
        ".",
    )
    assert "External validation" in html
    assert "ipcw_time" in html
    assert "0.800" in html


def test_train_missing_ipcw_still_raises(monkeypatch):
    import modules.pipeline as pl
    monkeypatch.setattr(cfg, "DO_LANDMARK_ROC", False, raising=False)
    monkeypatch.setattr(cfg, "DO_TIME_DEPENDENT_ROC", True, raising=False)

    class Layer:
        landmark = None
        ipcw = None

    with pytest.raises(ValueError, match="没有 IPCW"):
        pl._maybe_td_rocs("train", {"RadScore": [0.1, 0.9]}, Layer())
