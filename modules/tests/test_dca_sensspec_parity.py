"""DCA 的 sens/spec 换 sklearn.roc_curve 之后的对拍（与逐点计数参考实现比，1e-12）。

参考实现就是原来的逐点循环，按 test_mc_chi2_parity.py 的惯例留在测试里；
实测最大差 1.11e-16（含并列/常数/越界三种边界），DCA 数字不变。

Run: python -B -m pytest modules/tests/test_dca_sensspec_parity.py -q
"""
from __future__ import annotations

import numpy as np
import pytest

from modules.stats.curves_dca import (
    DCA_N_POINTS,
    DCA_THRESHOLD_MAX,
    DCA_THRESHOLD_MIN,
    _sens_spec_at_thresholds,
)

TOL = 1e-12
PTS = np.linspace(DCA_THRESHOLD_MIN, DCA_THRESHOLD_MAX, DCA_N_POINTS)


def _ref(y: np.ndarray, pred: np.ndarray, pts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    sens = np.empty_like(pts)
    spec = np.empty_like(pts)
    y_pos, y_neg = y == 1, y == 0
    n_pos, n_neg = y_pos.sum(), y_neg.sum()
    for i, pt in enumerate(pts):
        pred_pos = pred >= pt
        sens[i] = np.sum(pred_pos & y_pos) / n_pos if n_pos > 0 else np.nan
        spec[i] = np.sum(~pred_pos & y_neg) / n_neg if n_neg > 0 else np.nan
    return sens, spec


def _cases(n: int = 200, seed: int = 11) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    rng = np.random.RandomState(seed)
    y = rng.randint(0, 2, n)
    return {
        "distinct": (y, rng.rand(n)),
        "ties": (y, np.round(rng.rand(n), 1)),
        "binary_scores": (y, rng.randint(0, 2, n).astype(float)),
        "constant_scores": (y, np.full(n, 0.42)),
        "perfect": (y, y.astype(float)),
        "below_all_thresholds": (y, np.round(rng.rand(n), 2) - 100.0),
    }


CASES = _cases()


@pytest.mark.parametrize("name", sorted(CASES))
def test_matches_pointwise_reference(name: str) -> None:
    y, pred = CASES[name]
    got_sens, got_spec = _sens_spec_at_thresholds(y, pred, PTS)
    want_sens, want_spec = _ref(y, pred, PTS)
    assert np.allclose(got_sens, want_sens, rtol=0, atol=TOL)
    assert np.allclose(got_spec, want_spec, rtol=0, atol=TOL)


def test_single_class_returns_nan() -> None:
    y = np.zeros(20, dtype=int)
    sens, spec = _sens_spec_at_thresholds(y, np.linspace(0, 1, 20), PTS)
    assert np.isnan(sens).all() and np.isnan(spec).all()


def test_non_finite_scores_raise() -> None:
    y = np.r_[np.ones(10), np.zeros(10)].astype(int)
    pred = np.linspace(0, 1, 20)
    pred[3] = np.nan
    with pytest.raises(ValueError, match="非有限值"):
        _sens_spec_at_thresholds(y, pred, PTS)
