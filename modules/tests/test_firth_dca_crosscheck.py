"""CROSSCHECK_ONLY: Firth logistic and DCA net benefit.

`firthlogist` is BLOCKED on this machine (every release pins Python < 3.11, we run
3.13.9) and `dcurves` could not be installed (network timeout), so both checks use
an independent implementation instead of the package:

* Firth: minimise the Jeffreys-penalised log-likelihood with scipy's BFGS using an
  analytically derived score. Our production code uses IRLS with the equivalent
  (but differently derived) `h*(0.5-mu)` form, so agreement is not tautological.
* DCA: the production path turns sens/spec into net benefit on the rate scale;
  the cross-check recomputes the same thresholds on the count scale
  `(TP - FP*pt/(1-pt))/n`, i.e. Vickers' definition.

Neither production module changes.

Run: python -B -m pytest modules/tests/test_firth_dca_crosscheck.py -q
"""
from __future__ import annotations

import numpy as np
import pytest
from scipy.optimize import minimize
from scipy.special import expit

from modules.stats.clinical import firth_logit
from modules.stats.curves_dca import _sens_spec_at_thresholds

TOL_BETA = 1e-6
TOL_NB = 1e-12


def _penalized_firth(y: np.ndarray, X: np.ndarray):
    """Jeffreys-penalised likelihood fitted with an independent optimiser."""
    n, p = X.shape

    def neg_loglik(beta):
        eta = X @ beta
        mu = expit(eta)
        ll = float(np.sum(y * eta - np.logaddexp(0.0, eta)))
        w = mu * (1.0 - mu)
        fisher = X.T @ (X * w[:, None])
        _sign, logdet = np.linalg.slogdet(fisher)
        return -(ll + 0.5 * logdet)

    def grad(beta):
        eta = X @ beta
        mu = expit(eta)
        w = mu * (1.0 - mu)
        fisher = X.T @ (X * w[:, None])
        finv = np.linalg.inv(fisher)
        h = np.einsum("ij,jk,ik->i", X, finv, X)
        dw = mu * (1.0 - mu) * (1.0 - 2.0 * mu)
        return -(X.T @ (y - mu) + 0.5 * (X.T @ (dw * h)))

    res = minimize(
        neg_loglik, np.zeros(p), jac=grad, method="BFGS",
        options={"gtol": 1e-10, "maxiter": 5000},
    )
    beta = np.asarray(res.x, dtype=float)
    mu = expit(X @ beta)
    w = mu * (1.0 - mu)
    se = np.sqrt(np.clip(np.diag(np.linalg.inv(X.T @ (X * w[:, None]))), 0.0, None))
    return beta, se, float(np.max(np.abs(grad(beta))))


def _separation_data():
    """Deliberately separation-prone so the penalised fit differs from plain MLE."""
    rng = np.random.RandomState(20261102)
    n = 80
    x1 = rng.normal(0, 1, n)
    x2 = rng.binomial(1, 0.4, n).astype(float)
    eta = -1.0 + 2.5 * x1 + 3.0 * x2
    y = (eta + rng.normal(0, 0.4, n) > 0).astype(float)
    X = np.column_stack([np.ones(n), x1, x2])
    return y, X


def test_firth_matches_independent_optimiser() -> None:
    y, X = _separation_data()
    ours = firth_logit(y, X)
    assert ours is not None
    beta_ref, se_ref, grad_norm = _penalized_firth(y, X)
    # BFGS reports success=False at gtol=1e-10 through precision loss, so judge
    # convergence on the score itself (measured here ~1e-9).
    assert grad_norm < 1e-6, f"reference optimiser did not converge (grad={grad_norm:.2e})"
    assert np.allclose(ours["beta"], beta_ref, rtol=0, atol=TOL_BETA)
    assert np.allclose(ours["se"], se_ref, rtol=0, atol=TOL_BETA)


def test_firth_refuses_degenerate_input() -> None:
    y = np.ones(10)
    X = np.column_stack([np.ones(10), np.linspace(-1, 1, 10)])
    assert firth_logit(y, X) is None


def _nb_rate(sens, spec, prev, pt):
    return sens * prev - (1.0 - spec) * (1.0 - prev) * pt / (1.0 - pt)


def _nb_count(y, pred, pt):
    n = y.size
    pred_pos = pred >= pt
    tp = int(np.sum(pred_pos & (y == 1)))
    fp = int(np.sum(pred_pos & (y == 0)))
    return (tp - fp * pt / (1.0 - pt)) / n


def test_dca_net_benefit_matches_count_definition() -> None:
    rng = np.random.RandomState(7)
    n = 300
    y = rng.binomial(1, 0.35, n).astype(int)
    pred = np.clip(0.35 + 0.3 * (y - 0.35) + rng.normal(0, 0.2, n), 0.001, 0.999)
    pts = np.linspace(0.05, 0.9, 12)
    prev = float(np.mean(y == 1))

    sens, spec = _sens_spec_at_thresholds(y, pred, pts)
    for pt, se, sp in zip(pts, sens, spec):
        rate = _nb_rate(se, sp, prev, pt)
        count = _nb_count(y, pred, pt)
        assert rate == pytest.approx(count, rel=0, abs=TOL_NB)


def test_dca_treat_all_and_none_identities() -> None:
    rng = np.random.RandomState(11)
    n = 200
    y = rng.binomial(1, 0.4, n).astype(int)
    pred = np.clip(0.4 + rng.normal(0, 0.25, n), 0.001, 0.999)
    prev = float(np.mean(y == 1))
    for pt in (0.1, 0.3, 0.5):
        treat_all = prev - (1.0 - prev) * pt / (1.0 - pt)
        assert _nb_count(y, np.ones(n), pt) == pytest.approx(treat_all, abs=TOL_NB)
        assert _nb_count(y, np.zeros(n), pt) == pytest.approx(0.0, abs=TOL_NB)
        assert _nb_rate(1.0, 0.0, prev, pt) == pytest.approx(treat_all, abs=TOL_NB)
