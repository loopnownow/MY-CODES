"""Landmark ROC: locked score × each time/status pair."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from modules import config as cfg
from modules.stats.curves_roc import landmark_roc_metrics, orient_auc_above_chance, plot_landmark_roc
from modules.stats.data_io import _slice_landmark, collect_landmark_labels


def _arm(monkeypatch):
    monkeypatch.setattr(cfg, "DO_LANDMARK_ROC", True, raising=False)
    monkeypatch.setattr(cfg, "DO_TIME_DEPENDENT_ROC", False, raising=False)
    monkeypatch.setattr(cfg, "DRY_RUN", False, raising=False)
    monkeypatch.setattr(cfg, "OUTCOME_BINARIZE", {
        "response": {"CR": 1, "PD": 0},
        "lm_status_3m": {"1": 1, "0": 0, 1: 1, 0: 0},
        "lm_status_6m": {"1": 1, "0": 0, 1: 1, 0: 0},
        "lm_status_1y": {"1": 1, "0": 0, 1: 1, 0: 0},
        "lm_status_2y": {"1": 1, "0": 0, 1: 1, 0: 0},
    }, raising=False)
    monkeypatch.setattr(cfg, "READER_VALUE_MAP", {}, raising=False)


def _one_pair(n, status, time_col="FollowUp (months)", status_col="response", label=None):
    return {
        "pairs": [{
            "time_col": time_col,
            "time_label": label if label is not None else time_col,
            "status_col": status_col,
            "time": np.arange(n, dtype=float),
            "status": np.asarray(status, dtype=float),
        }]
    }


def test_landmark_auc_uses_the_status_column():
    rng = np.random.default_rng(0)
    n = 40
    status = np.array([0] * 20 + [1] * 20, dtype=float)
    score = status + rng.normal(0, 0.01, n)
    metrics = landmark_roc_metrics(
        {"RadScore": score},
        _one_pair(n, status),
        n_boot=30,
        seed=1,
    )
    assert len(metrics) == 1
    row = metrics.iloc[0]
    assert row["Time"] == "FollowUp (months)"
    assert row["Status"] == "response"
    assert row["AUC"] == 1.0
    assert int(row["Events"]) == 20
    assert int(row["N"]) == 40


def test_plot_landmark_draws_one_roc_curve(monkeypatch):
    monkeypatch.setattr(cfg, "DRY_RUN", False, raising=False)
    monkeypatch.setattr(cfg, "BOOTSTRAP_N", 20, raising=False)
    saved = []

    def _save(filename, fig, width, height, **_k):
        saved.append(filename)
        import matplotlib.pyplot as plt
        plt.close(fig)
        return True

    monkeypatch.setattr(cfg, "save_plot", _save)
    n = 40
    status = np.array([0] * 20 + [1] * 20, dtype=float)
    fig = plot_landmark_roc(
        {"RadScore": status.astype(float)},
        _one_pair(n, status),
        dataset_label="train",
        seed=1,
    )
    assert fig is not None
    assert saved == ["landmark_roc_FollowUp_months_train"]


def test_reversed_landmark_score_stays_above_the_diagonal(monkeypatch):
    n = 40
    status = np.array([0] * 20 + [1] * 20, dtype=float)
    score = 1.0 - status
    metrics = landmark_roc_metrics(
        {"RadScore": score},
        _one_pair(n, status),
        n_boot=30,
        seed=1,
    )
    assert metrics.iloc[0]["AUC"] == pytest.approx(1.0)
    assert metrics.iloc[0]["AUC_CI_low"] <= metrics.iloc[0]["AUC_CI_high"]
    auc, lo, hi = orient_auc_above_chance(0.20, 0.10, 0.30)
    assert (auc, lo, hi) == pytest.approx((0.80, 0.70, 0.90))

    captured = []

    def _draw(curves, title, fname, color_map=None):
        captured.append(curves)
        return None

    monkeypatch.setattr(cfg, "DRY_RUN", False, raising=False)
    monkeypatch.setattr("modules.stats.curves_roc.draw_roc_curves", _draw)
    plot_landmark_roc(
        {"RadScore": score},
        _one_pair(n, status),
        dataset_label="train",
        seed=1,
    )
    _display, _name, fpr, tpr = captured[0][0]
    area = float(np.sum((fpr[1:] - fpr[:-1]) * (tpr[1:] + tpr[:-1]) / 2.0))
    assert area == pytest.approx(1.0)
    assert "AUC=1.000" in _display


def test_month_dict_is_rejected():
    with pytest.raises(ValueError, match="月份"):
        landmark_roc_metrics({"RadScore": np.zeros(4)}, {"3": np.zeros(4), "6": np.ones(4)})
    with pytest.raises(ValueError, match="月份"):
        _slice_landmark({"3": np.zeros(4)}, [0])


def test_collect_reads_time_and_status_columns(monkeypatch):
    _arm(monkeypatch)
    monkeypatch.setattr(cfg, "TD_ROC_TIMES", "FollowUp (months)", raising=False)
    monkeypatch.setattr(cfg, "TD_ROC_STATUS_COLS", "response", raising=False)
    clin = pd.DataFrame({
        "FollowUp (months)": [3, 6, 12, 24],
        "response": ["CR", "PD", "CR", "PD"],
    })
    got = collect_landmark_labels(clin)
    assert len(got["pairs"]) == 1
    p = got["pairs"][0]
    assert p["time_col"] == "FollowUp (months)"
    assert p["status_col"] == "response"
    assert p["time"].tolist() == [3.0, 6.0, 12.0, 24.0]
    assert p["status"].tolist() == [1.0, 0.0, 1.0, 0.0]
    sliced = _slice_landmark(got, [1, 3])
    assert sliced["pairs"][0]["time"].tolist() == [6.0, 24.0]
    assert sliced["pairs"][0]["status"].tolist() == [0.0, 0.0]
    assert sliced["pairs"][0]["time_col"] == "FollowUp (months)"


def test_collect_multi_pairs_window_months(monkeypatch):
    _arm(monkeypatch)
    monkeypatch.setattr(cfg, "TD_ROC_TIMES", ["", "", "", ""], raising=False)
    monkeypatch.setattr(
        cfg,
        "TD_ROC_STATUS_COLS",
        ["lm_status_3m", "lm_status_6m", "lm_status_1y", "lm_status_2y"],
        raising=False,
    )
    clin = pd.DataFrame({
        "lm_status_3m": [1, 0, 1, 0] * 10,
        "lm_status_6m": [1, 1, 0, 0] * 10,
        "lm_status_1y": [0, 1, 0, 1] * 10,
        "lm_status_2y": [1, 0, 0, 1] * 10,
    })
    got = collect_landmark_labels(clin)
    assert [p["time_label"] for p in got["pairs"]] == ["3", "6", "12", "24"]
    assert [p["status_col"] for p in got["pairs"]] == [
        "lm_status_3m", "lm_status_6m", "lm_status_1y", "lm_status_2y",
    ]
    assert np.allclose(got["pairs"][0]["time"], 3.0)
    assert np.allclose(got["pairs"][2]["time"], 12.0)
    sliced = _slice_landmark(got, [0, 2])
    assert len(sliced["pairs"]) == 4
    assert sliced["pairs"][0]["status"].tolist() == [1.0, 1.0]
    metrics = landmark_roc_metrics(
        {"RadScore": got["pairs"][0]["status"]},
        got,
        n_boot=20,
        seed=1,
    )
    assert sorted(metrics["Time"].astype(str).unique().tolist()) == ["12", "24", "3", "6"]
    assert len(metrics) == 4


def test_collect_errors_when_time_list_is_still_numeric(monkeypatch):
    _arm(monkeypatch)
    monkeypatch.setattr(cfg, "TD_ROC_TIMES", [6, 3], raising=False)
    monkeypatch.setattr(cfg, "TD_ROC_STATUS_COLS", "response", raising=False)
    clin = pd.DataFrame({"response": ["CR", "PD"]})
    with pytest.raises(ValueError, match="临床"):
        collect_landmark_labels(clin)


def test_collect_errors_when_column_missing(monkeypatch):
    _arm(monkeypatch)
    monkeypatch.setattr(cfg, "TD_ROC_TIMES", "FollowUp (months)", raising=False)
    monkeypatch.setattr(cfg, "TD_ROC_STATUS_COLS", "response", raising=False)
    clin = pd.DataFrame({"Age (years)": [60, 70]})
    with pytest.raises(ValueError, match="找不到列"):
        collect_landmark_labels(clin)


def test_collect_off_when_switch_off(monkeypatch):
    monkeypatch.setattr(cfg, "DO_LANDMARK_ROC", False, raising=False)
    monkeypatch.setattr(cfg, "DO_TIME_DEPENDENT_ROC", False, raising=False)
    monkeypatch.setattr(cfg, "TD_ROC_STATUS_COLS", "lm_status_3m", raising=False)
    clin = pd.DataFrame({"lm_status_3m": [1, 0]})
    assert collect_landmark_labels(clin) is None


def test_collect_off_when_only_time_dependent(monkeypatch):
    monkeypatch.setattr(cfg, "DO_LANDMARK_ROC", False, raising=False)
    monkeypatch.setattr(cfg, "DO_TIME_DEPENDENT_ROC", True, raising=False)
    monkeypatch.setattr(cfg, "TD_ROC_STATUS_COLS", "lm_status_3m", raising=False)
    clin = pd.DataFrame({"lm_status_3m": [1, 0]})
    assert collect_landmark_labels(clin) is None


def test_collect_empty_names_mean_not_set(monkeypatch):
    _arm(monkeypatch)
    monkeypatch.setattr(cfg, "TD_ROC_TIMES", "", raising=False)
    monkeypatch.setattr(cfg, "TD_ROC_STATUS_COLS", "", raising=False)
    clin = pd.DataFrame({"Age (years)": [60]})
    assert collect_landmark_labels(clin) is None
