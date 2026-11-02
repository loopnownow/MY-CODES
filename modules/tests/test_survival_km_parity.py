"""Parity for KM steps / log-rank after delegating to statsmodels.survfunc.

The reference helpers are the previous in-house implementations. The public
contract is unchanged: `_km_steps` returns (t_plot, s_plot, uniq_times, n_risk)
on a grid that still includes censored-only times, and `logrank_two_groups`
returns (chi2, p) or (nan, nan) when the comparison is not identified.

Run: python -B -m pytest modules/tests/test_survival_km_parity.py -q
"""
from __future__ import annotations

import numpy as np
import pytest
from scipy import stats

from modules.stats.curves_survival import _km_steps, logrank_two_groups

TOL = 1e-12


def _ref_km_steps(time, event):
    t = np.asarray(time, dtype=float)
    e = np.asarray(event, dtype=int)
    ok = np.isfinite(t) & (t >= 0) & np.isfinite(e)
    t, e = t[ok], e[ok].astype(int)
    if len(t) == 0:
        return np.array([0.0]), np.array([1.0]), np.array([]), np.array([])
    order = np.argsort(t, kind="mergesort")
    t, e = t[order], e[order]
    uniq = np.unique(t)
    n_risk, s, times_s, surv_s = [], 1.0, [0.0], [1.0]
    for ut in uniq:
        at = t == ut
        d = int(e[at].sum())
        r = int((t >= ut).sum())
        n_risk.append(r)
        if r > 0 and d > 0:
            s *= (1.0 - d / r)
        times_s.append(float(ut))
        surv_s.append(float(s))
    return (
        np.asarray(times_s, dtype=float),
        np.asarray(surv_s, dtype=float),
        np.asarray(uniq, dtype=float),
        np.asarray(n_risk, dtype=int),
    )


def _ref_logrank(time, event, group):
    import pandas as pd

    t = np.asarray(time, dtype=float)
    e = np.asarray(event, dtype=int)
    g = np.asarray(group)
    ok = np.isfinite(t) & (t >= 0) & np.isfinite(e) & pd.notna(g)
    t, e, g = t[ok], e[ok].astype(int), g[ok]
    levels = pd.unique(g)
    if len(levels) != 2 or len(t) < 4:
        return float("nan"), float("nan")
    g0, g1 = levels[0], levels[1]
    O1 = E1 = V = 0.0
    for ut in np.unique(t[e == 1]):
        mask_t = t >= ut
        n0 = int(((g == g0) & mask_t).sum())
        n1 = int(((g == g1) & mask_t).sum())
        n = n0 + n1
        if n == 0:
            continue
        d = int(((t == ut) & (e == 1)).sum())
        d1 = int(((t == ut) & (e == 1) & (g == g1)).sum())
        if d == 0 or n <= 1:
            continue
        O1 += d1
        E1 += d * (n1 / n)
        V += (n0 * n1 * d * (n - d)) / (n * n * (n - 1))
    if V <= 1e-12:
        return float("nan"), float("nan")
    chi2 = (O1 - E1) ** 2 / V
    return float(chi2), float(stats.chi2.sf(chi2, df=1))


KM_CASES = {
    "censoring_and_ties": (
        [1, 2, 2, 3, 4, 4, 4, 5, 6, 7],
        [1, 1, 0, 1, 1, 1, 0, 1, 0, 1],
    ),
    "single_event_time": ([5, 5, 5, 5], [1, 0, 1, 0]),
    "all_censored": ([1, 2, 3, 4], [0, 0, 0, 0]),
    "single_subject": ([9], [1]),
    "empty": ([], []),
    "nan_filtered": ([1, 2, float("nan"), 3], [1, 0, 1, 1]),
    "late_censor_only": ([1, 2, 3, 10], [1, 1, 0, 0]),
}


@pytest.mark.parametrize("name", sorted(KM_CASES))
def test_km_steps_match_reference(name: str) -> None:
    t, e = KM_CASES[name]
    got = _km_steps(np.array(t, dtype=float), np.array(e, dtype=int))
    want = _ref_km_steps(np.array(t, dtype=float), np.array(e, dtype=int))
    assert len(got) == 4
    for g_part, w_part in zip(got, want):
        assert g_part.shape == w_part.shape
        assert np.allclose(g_part, w_part, rtol=TOL, atol=TOL)


def test_km_steps_grid_keeps_censored_times() -> None:
    t_plot, s_plot, uniq, n_risk = _km_steps(
        np.array([1.0, 2.0, 3.0]), np.array([1, 0, 1])
    )
    assert t_plot.tolist() == [0.0, 1.0, 2.0, 3.0]
    assert uniq.tolist() == [1.0, 2.0, 3.0]
    assert n_risk.tolist() == [3, 2, 1]
    assert s_plot[1] == pytest.approx(2 / 3, abs=TOL)
    assert s_plot[2] == pytest.approx(2 / 3, abs=TOL)  # censored time keeps the step flat


LOG_CASES = {
    "two_groups": (
        [1, 2, 3, 4, 5, 6, 7, 8],
        [1, 1, 0, 1, 0, 1, 1, 0],
        [0, 0, 0, 0, 1, 1, 1, 1],
    ),
    "events_in_one_group": (
        [1, 2, 3, 4, 5, 6],
        [1, 1, 1, 0, 0, 0],
        [0, 0, 0, 1, 1, 1],
    ),
    "no_events": ([1, 2, 3, 4, 5, 6], [0, 0, 0, 0, 0, 0], [0, 0, 0, 1, 1, 1]),
    "single_group_only": ([1, 2, 3, 4], [1, 1, 0, 0], [1, 1, 1, 1]),
    "too_few": ([1, 2, 3], [1, 1, 0], [0, 0, 1]),
}


@pytest.mark.parametrize("name", sorted(LOG_CASES))
def test_logrank_matches_reference(name: str) -> None:
    t, e, g = LOG_CASES[name]
    got = logrank_two_groups(np.array(t, float), np.array(e, int), np.array(g, int))
    want = _ref_logrank(np.array(t, float), np.array(e, int), np.array(g, int))
    if np.isnan(want[0]):
        assert np.isnan(got[0]) and np.isnan(got[1])
    else:
        assert got[0] == pytest.approx(want[0], rel=TOL, abs=TOL)
        assert got[1] == pytest.approx(want[1], rel=TOL, abs=TOL)
