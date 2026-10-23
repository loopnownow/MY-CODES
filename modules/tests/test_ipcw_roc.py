"""IPCW cumulative/dynamic AUC."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from modules import config as cfg
from modules.console.console import _td_roc_dry_lines
from modules.stats.curves_ipcw import (
    ipcw_auc_at, ipcw_roc_curve, ipcw_roc_metrics, plot_ipcw_roc, weighted_auc, weighted_roc_curve,
)
from modules.stats.data_io import collect_ipcw_labels, external_ipcw_labels


def _arm(monkeypatch):
    monkeypatch.setattr(cfg, "DO_TIME_DEPENDENT_ROC", True, raising=False)
    monkeypatch.setattr(cfg, "DO_LANDMARK_ROC", False, raising=False)
    monkeypatch.setattr(cfg, "DRY_RUN", False, raising=False)
    monkeypatch.setattr(cfg, "TD_IPCW_TIME_COL", "ipcw_time", raising=False)
    monkeypatch.setattr(cfg, "TD_IPCW_STATUS_COL", "ipcw_status", raising=False)
    monkeypatch.setattr(cfg, "TD_IPCW_HORIZONS", [3, 6, 12], raising=False)
    monkeypatch.setattr(cfg, "OUTCOME_BINARIZE", {}, raising=False)
    monkeypatch.setattr(cfg, "READER_VALUE_MAP", {}, raising=False)
    monkeypatch.setattr(cfg, "BOOTSTRAP_N", 20, raising=False)
    monkeypatch.setattr(cfg, "SEED", 1, raising=False)


def test_weighted_roc_area_matches_auc():
    rng = np.random.default_rng(0)
    case = rng.normal(1.0, 1.0, 30)
    ctrl = rng.normal(0.0, 1.0, 40)
    case_w = rng.uniform(0.4, 1.6, 30)
    ctrl_w = rng.uniform(0.4, 1.6, 40)
    fpr, tpr = weighted_roc_curve(case, case_w, ctrl, ctrl_w)
    area = float(np.sum((fpr[1:] - fpr[:-1]) * (tpr[1:] + tpr[:-1]) / 2.0))
    assert abs(area - weighted_auc(case, case_w, ctrl, ctrl_w)) < 1e-6


def test_ipcw_roc_curve_uses_the_same_weights():
    time = np.array([1.0, 2.0, 4.0, 8.0, 10.0, 12.0])
    status = np.array([1, 1, 0, 1, 0, 0], dtype=float)
    marker = np.array([0.9, 0.8, 0.2, 0.7, 0.1, 0.05])
    fpr, tpr = ipcw_roc_curve(time, status, marker, 6.0)
    area = float(np.sum((fpr[1:] - fpr[:-1]) * (tpr[1:] + tpr[:-1]) / 2.0))
    assert abs(area - ipcw_auc_at(time, status, marker, 6.0)["auc"]) < 1e-6


def test_ipcw_auc_separates_cases_and_controls():
    time = np.array([1.0, 2.0, 4.0, 8.0, 10.0, 12.0])
    status = np.array([1, 1, 0, 1, 0, 0], dtype=float)
    marker = np.array([0.9, 0.8, 0.2, 0.7, 0.1, 0.05])
    got = ipcw_auc_at(time, status, marker, 6.0)
    assert got["cases"] == 2
    assert got["controls"] == 3
    assert got["estimable"] == 1
    assert np.isfinite(got["auc"])
    assert got["auc"] > 0.5


def test_ipcw_metrics_one_row_per_horizon_and_model():
    n = 40
    rng = np.random.default_rng(2)
    time = rng.uniform(1, 24, n)
    status = (rng.random(n) < 0.4).astype(float)
    marker = status + rng.normal(0, 0.2, n)
    metrics = ipcw_roc_metrics(
        {"RadScore": marker, "Clinical": rng.random(n)},
        {
            "time_col": "ipcw_time",
            "status_col": "ipcw_status",
            "horizons": [6, 12],
            "time": time,
            "status": status,
        },
        n_boot=0,
        seed=1,
    )
    assert len(metrics) == 4
    assert sorted(metrics["Time"].unique().tolist()) == [6.0, 12.0]
    assert set(metrics["Model"]) == {"RadScore", "Clinical"}


def test_collect_ipcw_labels(monkeypatch):
    _arm(monkeypatch)
    clin = pd.DataFrame({
        "ipcw_time": [1, 3, 6, 12, 18],
        "ipcw_status": [1, 0, 1, 0, 0],
    })
    got = collect_ipcw_labels(clin)
    assert got["time_col"] == "ipcw_time"
    assert got["status_col"] == "ipcw_status"
    assert got["horizons"] == [3.0, 6.0, 12.0]
    assert got["time"].tolist() == [1.0, 3.0, 6.0, 12.0, 18.0]
    assert got["status"].tolist() == [1.0, 0.0, 1.0, 0.0, 0.0]


def test_collect_ipcw_missing_column(monkeypatch):
    _arm(monkeypatch)
    clin = pd.DataFrame({"ipcw_time": [1, 2]})
    with pytest.raises(ValueError, match="IPCW 找不到列"):
        collect_ipcw_labels(clin)


def test_external_ipcw_missing_column_skips(monkeypatch):
    _arm(monkeypatch)
    clin = pd.DataFrame({"response_6m": ["PD", "PR"]})
    assert external_ipcw_labels(clin, "ext") is None


def test_external_ipcw_bad_status_still_raises(monkeypatch):
    _arm(monkeypatch)
    clin = pd.DataFrame({"ipcw_time": [1, 2], "ipcw_status": [1, 3]})
    with pytest.raises(ValueError, match="ipcw_status"):
        external_ipcw_labels(clin, "ext")


def test_reversed_ipcw_marker_stays_above_the_diagonal(monkeypatch):
    time = np.array([1.0, 2.0, 4.0, 8.0, 10.0, 12.0])
    status = np.array([1, 1, 0, 1, 0, 0], dtype=float)
    marker = np.array([0.1, 0.05, 0.4, 0.2, 0.8, 0.9])
    raw = ipcw_auc_at(time, status, marker, 6.0)["auc"]
    assert raw < 0.5
    metrics = ipcw_roc_metrics(
        {"RadScore": marker},
        {
            "time_col": "ipcw_time",
            "status_col": "ipcw_status",
            "horizons": [6],
            "time": time,
            "status": status,
        },
        n_boot=0,
        seed=1,
    )
    assert metrics.iloc[0]["AUC"] == pytest.approx(1.0 - raw)

    captured = []

    def _draw(curves, title, fname, color_map=None):
        captured.append(curves)
        return None

    monkeypatch.setattr(cfg, "DRY_RUN", False, raising=False)
    monkeypatch.setattr("modules.stats.curves_ipcw.draw_roc_curves", _draw)
    plot_ipcw_roc(
        {"RadScore": marker},
        {
            "time_col": "ipcw_time",
            "status_col": "ipcw_status",
            "horizons": [6],
            "time": time,
            "status": status,
        },
        dataset_label="train",
        seed=1,
    )
    _display, _name, fpr, tpr = captured[0][0]
    area = float(np.sum((fpr[1:] - fpr[:-1]) * (tpr[1:] + tpr[:-1]) / 2.0))
    assert area == pytest.approx(1.0 - raw)
    assert area > 0.5


def test_plot_ipcw_draws_one_roc_per_horizon(monkeypatch):
    monkeypatch.setattr(cfg, "DRY_RUN", False, raising=False)
    monkeypatch.setattr(cfg, "BOOTSTRAP_N", 20, raising=False)
    saved = []

    def _save(filename, fig, width, height, **_k):
        saved.append(filename)
        import matplotlib.pyplot as plt
        plt.close(fig)
        return True

    monkeypatch.setattr(cfg, "save_plot", _save)
    time = np.array([1.0, 2.0, 4.0, 8.0, 10.0, 12.0])
    status = np.array([1, 1, 0, 1, 0, 0], dtype=float)
    marker = np.array([0.9, 0.8, 0.2, 0.7, 0.1, 0.05])
    fig = plot_ipcw_roc(
        {"RadScore": marker},
        {
            "time_col": "ipcw_time",
            "status_col": "ipcw_status",
            "horizons": [6, 12],
            "time": time,
            "status": status,
        },
        dataset_label="train",
        seed=1,
    )
    assert fig is not None
    assert saved == ["ipcw_roc_6_train"]


def test_plot_ipcw_dry_plan(monkeypatch, capsys):
    monkeypatch.setattr(cfg, "DRY_RUN", True, raising=False)
    fig = plot_ipcw_roc(
        {"RadScore": np.array([0.1, 0.9])},
        {
            "time_col": "ipcw_time",
            "status_col": "ipcw_status",
            "horizons": [6, 12],
            "time": np.array([1.0, 10.0]),
            "status": np.array([1.0, 0.0]),
        },
        dataset_label="train",
    )
    assert fig is None
    out = capsys.readouterr().out
    assert "ipcw_train" in out
    assert "ipcw_roc_" in out and "_train" in out


def test_dry_lines_split_landmark_and_ipcw_missing_cols():
    lines = _td_roc_dry_lines(
        {
            "DO_LANDMARK_ROC": True,
            "DO_TIME_DEPENDENT_ROC": True,
            "TD_ROC_TIMES": ["", ""],
            "TD_ROC_STATUS_COLS": ["lm_status_3m", "lm_status_6m"],
            "TD_IPCW_TIME_COL": "ipcw_time",
            "TD_IPCW_STATUS_COL": "ipcw_status",
            "TD_IPCW_HORIZONS": [3, 6],
        },
        ["Age (years)", "lm_status_3m"],
    )
    text = "\n".join(lines)
    assert "Landmark ROC: on" in text
    assert "lm_status_6m" in text and "缺列" in text
    assert "时间依赖性 ROC（IPCW）: on" in text
    assert "ipcw_time" in text and "缺列" in text


def test_dry_lines_off_when_switches_off():
    lines = _td_roc_dry_lines(
        {"DO_LANDMARK_ROC": False, "DO_TIME_DEPENDENT_ROC": False},
        ["ipcw_time"],
    )
    assert lines == ["Landmark ROC: off", "时间依赖性 ROC（IPCW）: off"]
