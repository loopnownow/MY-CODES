"""VAL_MODE defaults, fail-loud illegal modes, locked predictions ignore eval y."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
import statsmodels.api as sm

from modules.config.defaults import DEFAULT_SETTINGS
from modules.stats.val_mode import (
    resolve_val_mode,
    refit_clinical_prob,
)


def test_default_val_modes_are_lock_threshold():
    assert DEFAULT_SETTINGS["VAL_MODE"] == "lock_threshold"
    assert DEFAULT_SETTINGS["EXTERNAL_VAL_MODE"] == "lock_threshold"


def test_resolve_val_mode_aliases_and_default():
    assert resolve_val_mode("lock") == "lock_threshold"
    assert resolve_val_mode("apply") == "apply_formula"
    assert resolve_val_mode("refit") == "refit"
    assert resolve_val_mode(None) == "lock_threshold"
    assert resolve_val_mode("") == "lock_threshold"


def test_illegal_val_mode_raises_value_error():
    with pytest.raises(ValueError, match="无效"):
        resolve_val_mode("not_a_mode")
    with pytest.raises(ValueError, match="无效"):
        resolve_val_mode("silent_fallback_please")


def test_lock_threshold_predictions_ignore_mutated_eval_y(monkeypatch):
    """Integration: train lock → predict → mutate eval y → locked probs unchanged.

    Also: missing train lock must not GLM-refit on eval y (unlocked raw path).
    """
    from modules.stats import curves_roc as roc

    monkeypatch.setattr(roc.cfg, "DO_ROC", False, raising=False)
    monkeypatch.setattr(roc.cfg, "ROC_PLOT_EXCLUDE", [], raising=False)
    monkeypatch.setattr(roc.cfg, "BOOTSTRAP_N", 20, raising=False)

    rng = np.random.RandomState(0)
    n_train, n_test = 80, 40
    x_tr = rng.normal(size=n_train)
    y_tr = (x_tr + rng.normal(scale=0.4, size=n_train) > 0).astype(int)
    if y_tr.min() == y_tr.max():
        y_tr[0] = 1 - int(y_tr[0])
    x_te = rng.normal(size=n_test)
    y_te = (x_te + rng.normal(scale=0.4, size=n_test) > 0).astype(int)
    if y_te.min() == y_te.max():
        y_te[0] = 1 - int(y_te[0])

    roc.ROC_LOCK.clear()
    roc.ROC_METRICS.clear()
    roc.plot_roc({"M1": x_tr}, y_tr, dataset_label="train", seed=0, val_mode="lock_threshold")
    assert "train" in roc.ROC_LOCK and "M1" in roc.ROC_LOCK["train"]
    lock = dict(roc.ROC_LOCK["train"])

    roc.plot_roc(
        {"M1": x_te},
        y_te,
        dataset_label="test",
        seed=0,
        val_mode="lock_threshold",
        lock_from=lock,
    )
    m1 = roc.ROC_METRICS["roc_test"].set_index("Model").loc["M1"]
    assert m1["Threshold_source"] == "train"
    thr = float(m1["Threshold"])
    intercept = float(lock["M1"]["intercept"])
    slope = float(lock["M1"]["slope"])
    p_locked = roc._apply_logit_cal(x_te, intercept, slope)

    y_mut = 1 - y_te
    roc.plot_roc(
        {"M1": x_te},
        y_mut,
        dataset_label="test",
        seed=0,
        val_mode="lock_threshold",
        lock_from=lock,
    )
    m2 = roc.ROC_METRICS["roc_test"].set_index("Model").loc["M1"]
    assert m2["Threshold_source"] == "train"
    assert float(m2["Threshold"]) == thr
    p_after = roc._apply_logit_cal(x_te, float(lock["M1"]["intercept"]), float(lock["M1"]["slope"]))
    np.testing.assert_allclose(p_locked, p_after)
    # Mutating eval y must not change locked calibration params used above
    assert lock["M1"] == roc.ROC_LOCK["train"]["M1"]

    # Missing train lock: must NOT refit GLM on eval y; unlocked raw AUC only.
    roc.ROC_LOCK.clear()
    roc.ROC_METRICS.clear()
    roc.plot_roc(
        {"M1": x_te},
        y_te,
        dataset_label="test",
        seed=0,
        val_mode="lock_threshold",
        lock_from={},
    )
    row = roc.ROC_METRICS["roc_test"].set_index("Model").loc["M1"]
    assert row["Threshold_source"] == "unlocked_no_train_lock"
    assert not np.isfinite(float(row["Threshold"]))
    assert not np.isfinite(float(row["ACC"]))
    # Raw continuous AUC is still reported
    assert np.isfinite(float(row["AUC"]))


def test_refit_clinical_prob_changes_when_eval_y_mutates():
    """Contrast: refit uses evaluation-layer y, so scrambled labels change probs."""
    rng = np.random.RandomState(1)
    n = 60
    clin = pd.DataFrame(
        {
            "age": rng.normal(60, 10, size=n),
            "score": rng.normal(0, 1, size=n),
        }
    )
    latent = 0.05 * (clin["age"] - 60) + 0.8 * clin["score"]
    y_true = (latent + rng.normal(scale=0.4, size=n) > 0).astype(int)
    if y_true.min() == y_true.max():
        y_true[0] = 1 - int(y_true[0])
    y_flip = 1 - y_true
    feats = ["age", "score"]
    p_true = refit_clinical_prob(clin, y_true, feats)
    p_flip = refit_clinical_prob(clin, y_flip, feats)
    assert p_true is not None and p_flip is not None
    assert not np.allclose(p_true, p_flip, equal_nan=True)
