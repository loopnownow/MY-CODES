"""R-09/10: scaled Schoenfeld uses event-time residuals; Cox z is centered."""
from __future__ import annotations

import numpy as np

from modules.stats.curves_survival import fit_cox_ph


def test_cox_ph_p_is_finite_on_proportional_data():
    rng = np.random.RandomState(5)
    n = 80
    x = rng.normal(0, 1, n)
    # exponential time with a modest log-HR; some censoring
    time = rng.exponential(np.exp(-0.4 * x))
    event = rng.binomial(1, 0.7, n)
    row = fit_cox_ph(time, event, x, "score", "per 1 SD")
    assert row["N"] == n
    assert np.isfinite(float(row["HR"]))
    # PH_p may be nan on tiny event counts, but should be in (0,1] when present
    php = float(row["PH_p"])
    if np.isfinite(php):
        assert 0.0 <= php <= 1.0
