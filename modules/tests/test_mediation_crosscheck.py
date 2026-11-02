"""CROSSCHECK_ONLY: mediation ingredients vs statsmodels.

The main path keeps `modules.stats.mediation` (percentile bootstrap on the
log-odds scale). This module only proves the ingredients are right:

* `a`, `b`, `c`, `c_prime` must equal the coefficients of the same statsmodels
  fits (OLS `m ~ x`; Binomial GLM `y ~ x + m`; Binomial GLM `y ~ x`) to 1e-10;
* `ACME == a*b`, `total == c_prime + a*b`, `prop == ACME/total` to 1e-12;
* the bootstrap interval brackets its point estimate;
* `statsmodels.stats.mediation.Mediation` reports effects on the **probability**
  scale for a binary outcome, so its ACME is not comparable with our log-odds
  ACME. That is why it is not used as the production path.

Run: python -B -m pytest modules/tests/test_mediation_crosscheck.py -q
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
import statsmodels.api as sm
from statsmodels.stats.mediation import Mediation

from modules.stats.mediation import _effects_once, bootstrap_mediation

N = 400
SEED = 20261102
TOL = 1e-10


@pytest.fixture(scope="module")
def sim():
    rng = np.random.RandomState(SEED)
    x = rng.binomial(1, 0.45, N).astype(float)
    m = 1.5 * x + rng.normal(0, 1.0, N)
    lin = -0.8 + 1.2 * x + 0.9 * m
    y = rng.binomial(1, 1 / (1 + np.exp(-lin)), N).astype(float)
    return x, m, y


def test_coefficients_match_statsmodels_fits(sim) -> None:
    x, m, y = sim
    ours = _effects_once(x, m, y)

    a_fit = sm.OLS(m, sm.add_constant(x)).fit()
    assert ours["a"] == pytest.approx(float(a_fit.params[1]), rel=TOL, abs=TOL)

    m_fit = sm.GLM(y, sm.add_constant(np.column_stack([x, m])),
                   family=sm.families.Binomial()).fit()
    assert ours["c_prime"] == pytest.approx(float(m_fit.params[1]), rel=TOL, abs=TOL)
    assert ours["b"] == pytest.approx(float(m_fit.params[2]), rel=TOL, abs=TOL)

    tot_fit = sm.GLM(y, sm.add_constant(x), family=sm.families.Binomial()).fit()
    assert ours["c"] == pytest.approx(float(tot_fit.params[1]), rel=TOL, abs=TOL)


def test_algebraic_identities(sim) -> None:
    x, m, y = sim
    ours = _effects_once(x, m, y)
    acme = ours["a"] * ours["b"]
    assert ours["ACME"] == pytest.approx(acme, rel=1e-12, abs=1e-12)
    assert ours["ADE"] == pytest.approx(ours["c_prime"], rel=1e-12, abs=1e-12)
    assert ours["prop_mediated"] == pytest.approx(acme / ours["total"], rel=1e-12, abs=1e-12)
    assert ours["binary_y"] == 1.0
    # binary outcome + logit link is non-collapsible: total != c' + a*b on the
    # log-odds scale, so that identity is asserted on a continuous outcome below.


def test_linear_outcome_obeys_additivity() -> None:
    rng = np.random.RandomState(4242)
    x = rng.binomial(1, 0.5, 300).astype(float)
    m = 1.2 * x + rng.normal(0, 1.0, 300)
    y = 0.5 + 1.0 * x + 0.8 * m + rng.normal(0, 1.0, 300)
    ours = _effects_once(x, m, y)
    assert ours["binary_y"] == 0.0
    assert ours["total"] == pytest.approx(ours["c_prime"] + ours["ACME"], rel=1e-12, abs=1e-12)


def test_bootstrap_interval_brackets_point_estimate(sim) -> None:
    x, m, y = sim
    res = bootstrap_mediation(x, m, y, n_boot=200, seed=7)
    for key in ("ACME", "ADE", "total"):
        assert res[f"{key}_ci_low"] <= res[key] <= res[f"{key}_ci_high"]
    assert res["n_boot_ok"] > 0


def test_reproducible_with_same_seed(sim) -> None:
    x, m, y = sim
    a = bootstrap_mediation(x, m, y, n_boot=100, seed=11)
    b = bootstrap_mediation(x, m, y, n_boot=100, seed=11)
    assert a["ACME_ci_low"] == b["ACME_ci_low"]
    assert a["ACME_ci_high"] == b["ACME_ci_high"]


def test_library_reports_probability_scale(sim) -> None:
    x, m, y = sim
    ours = _effects_once(x, m, y)
    frame = pd.DataFrame({"x": x, "m": m, "y": y})
    med = Mediation(
        sm.GLM.from_formula("y ~ x + m", data=frame, family=sm.families.Binomial()),
        sm.OLS.from_formula("m ~ x", data=frame),
        exposure="x",
        mediator="m",
    )
    fit = med.fit(method="parametric")
    lib_acme = float(np.ravel(np.asarray(fit.ACME_avg, dtype=float))[0])
    # binary outcome -> probability scale, so |effect| < 1; ours is log-odds
    assert abs(lib_acme) < 1.0
    assert abs(ours["ACME"]) > 1.0
    assert 0.0 < float(np.ravel(np.asarray(fit.prop_med_avg, dtype=float))[0]) < 1.0
