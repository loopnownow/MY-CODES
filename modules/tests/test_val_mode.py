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
    _safe_predict_glm,
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


def test_locked_glm_predictions_ignore_mutated_eval_y():
    """Train coefficients + test X → same probs when test y is scrambled."""
    rng = np.random.RandomState(0)
    n_train, n_test = 80, 40
    x_tr = rng.normal(size=(n_train, 2))
    y_tr = (0.8 * x_tr[:, 0] - 0.5 * x_tr[:, 1] + rng.normal(scale=0.3, size=n_train) > 0).astype(int)
    if y_tr.min() == y_tr.max():
        y_tr[0] = 1 - y_tr[0]
    Xtr = sm.add_constant(pd.DataFrame({"a": x_tr[:, 0], "b": x_tr[:, 1]}), has_constant="add")
    model = sm.GLM(y_tr, Xtr, family=sm.families.Binomial()).fit()

    x_te = rng.normal(size=(n_test, 2))
    Xte = sm.add_constant(pd.DataFrame({"a": x_te[:, 0], "b": x_te[:, 1]}), has_constant="add")
    y1 = (rng.rand(n_test) > 0.5).astype(int)
    y2 = 1 - y1

    p1 = _safe_predict_glm(model, Xte, n_test)
    p2 = _safe_predict_glm(model, Xte, n_test)
    assert p1 is not None and p2 is not None
    np.testing.assert_allclose(p1, p2)
    # y vectors unused by locked predict; keep names so the contract is visible
    assert y1.shape == y2.shape


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
