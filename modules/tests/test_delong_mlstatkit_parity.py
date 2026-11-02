"""Parity for the DeLong test after delegating to MLstatkit.

The reference below is the previous in-house implementation (Sun & Xu 2014
mid-ranks). Two behaviours are intentionally kept by the wrapper: insufficient
data -> nan, identical predictions -> 1.0. One behaviour intentionally changes:
when the DeLong variance degenerates, MLstatkit would silently switch to a
bootstrap, so the wrapper raises RuntimeError instead of reporting p = 0.0.

Run: python -B -m pytest modules/tests/test_delong_mlstatkit_parity.py -q
"""
from __future__ import annotations

import numpy as np
import pytest
from scipy.stats import norm

from modules.stats.curves_roc import delong_roc_test

TOL = 1e-12


def _midrank(x):
    x = np.asarray(x, dtype=float)
    order = np.argsort(x)
    z = x[order]
    n = len(x)
    t = np.empty(n, dtype=float)
    i = 0
    while i < n:
        j = i
        while j < n and z[j] == z[i]:
            j += 1
        t[i:j] = 0.5 * (i + j - 1) + 1.0
        i = j
    out = np.empty(n, dtype=float)
    out[order] = t
    return out


def _ref_delong(y, p1, p2):
    y = np.asarray(y).astype(int)
    a = np.asarray(p1, dtype=float)
    b = np.asarray(p2, dtype=float)
    ok = np.isfinite(a) & np.isfinite(b) & np.isfinite(y)
    y, a, b = y[ok], a[ok], b[ok]
    if y.size < 4:
        return float("nan")
    m = int((y == 1).sum())
    n = int((y == 0).sum())
    if m < 2 or n < 2:
        return float("nan")
    if np.allclose(a, b, equal_nan=False):
        return 1.0
    order = np.argsort(-y)
    preds = np.vstack((a, b))[:, order]
    k = preds.shape[0]
    tx = np.empty((k, m))
    ty = np.empty((k, n))
    tz = np.empty((k, m + n))
    for r in range(k):
        tx[r, :] = _midrank(preds[r, :m])
        ty[r, :] = _midrank(preds[r, m:])
        tz[r, :] = _midrank(preds[r, :])
    aucs = tz[:, :m].sum(axis=1) / m / n - (m + 1.0) / 2.0 / n
    v01 = (tz[:, :m] - tx) / n
    v10 = 1.0 - (tz[:, m:] - ty) / m
    sigma = np.cov(v01, ddof=1) / m + np.cov(v10, ddof=1) / n
    var = float(sigma[0, 0] + sigma[1, 1] - 2.0 * sigma[0, 1])
    diff = float(aucs[0] - aucs[1])
    if not np.isfinite(var) or var <= 0:
        return 1.0 if abs(diff) < 1e-12 else 0.0
    return float(2.0 * norm.sf(abs(diff) / np.sqrt(var)))


CASES = {
    "moderate": (
        [1, 1, 1, 1, 0, 0, 0, 0],
        [0.9, 0.7, 0.6, 0.55, 0.5, 0.4, 0.3, 0.2],
        [0.8, 0.75, 0.5, 0.45, 0.6, 0.35, 0.4, 0.25],
    ),
    "ties": (
        [1, 1, 1, 0, 0, 0],
        [0.5, 0.5, 0.6, 0.5, 0.5, 0.4],
        [0.6, 0.55, 0.45, 0.4, 0.35, 0.3],
    ),
    "identical": ([1, 1, 0, 0], [0.7, 0.6, 0.4, 0.3], [0.7, 0.6, 0.4, 0.3]),
    "too_few": ([1, 0, 0], [0.9, 0.4, 0.2], [0.8, 0.5, 0.3]),
    "one_class": ([1, 1, 1, 1], [0.9, 0.7, 0.6, 0.5], [0.8, 0.75, 0.5, 0.45]),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_matches_reference(name: str) -> None:
    y, a, b = CASES[name]
    got = delong_roc_test(np.array(y), np.array(a, float), np.array(b, float))
    want = _ref_delong(np.array(y), np.array(a, float), np.array(b, float))
    if np.isnan(want):
        assert np.isnan(got)
    else:
        assert got == pytest.approx(want, rel=TOL, abs=TOL)


@pytest.mark.parametrize("seed", [0, 1, 2, 3, 4])
def test_matches_reference_on_random_data(seed: int) -> None:
    rng = np.random.RandomState(seed)
    n = 60
    y = np.r_[np.ones(n // 2, dtype=int), np.zeros(n - n // 2, dtype=int)]
    a = np.clip(rng.beta(2.5, 1.8, n), 0.001, 0.999)
    b = np.clip(a + rng.normal(0, 0.12, n), 0.001, 0.999)
    got = delong_roc_test(y, a, b)
    want = _ref_delong(y, a, b)
    assert got == pytest.approx(want, rel=1e-10, abs=1e-10)


def test_identical_predictions_return_one() -> None:
    y = np.array([1, 1, 0, 0])
    p = np.array([0.6, 0.4, 0.4, 0.2])
    assert delong_roc_test(y, p, p.copy()) == 1.0


def test_equal_auc_monotone_transform_returns_one() -> None:
    # The real fixture hits this: Combined is a logistic transform of RadScore, so
    # AUC is identical, DeLong variance is 0, and the answer is p = 1.
    y = np.array([1, 1, 1, 0, 0, 0])
    rad = np.array([0.8, 0.6, 0.3, 0.55, 0.2, 0.05])
    sig = 1.0 / (1.0 + np.exp(-(2.0 * rad - 1.0)))
    assert not np.allclose(rad, sig)
    assert delong_roc_test(y, rad, sig) == 1.0


def test_degenerate_variance_raises_instead_of_bootstrapping(monkeypatch) -> None:
    # Simulate the library's degenerate path deterministically: whenever it reports
    # method='bootstrap' the wrapper must refuse rather than pass a bootstrap p off
    # as DeLong.
    from modules.stats import curves_roc

    def fake_delong(true, prob_a, prob_b, **kwargs):
        return (0.0, 0.5, (0.0, 1.0), (0.0, 1.0), 0.7, 0.6, {"method": "bootstrap"})

    monkeypatch.setattr(curves_roc, "Delong_test", fake_delong)
    y, a, b = CASES["moderate"]
    with pytest.raises(RuntimeError, match="bootstrap"):
        curves_roc.delong_roc_test(np.array(y), np.array(a, float), np.array(b, float))


def test_perfect_separation_of_both_models_returns_one() -> None:
    # Both models rank every positive above every negative: equal AUC, so p = 1.
    y = np.array([1, 1, 0, 0])
    a = np.array([0.9, 0.8, 0.2, 0.1])
    b = np.array([0.6, 0.55, 0.45, 0.4])
    assert delong_roc_test(y, a, b) == 1.0
