"""Parity for the vectorised Monte Carlo chi-square (fixed-margin RxC permutation).

The reference below is the previous double-Python-loop implementation. The p value
must be **bit-identical** because the RNG consumption is unchanged (one
`rng.shuffle(col_ids)` per resample) and only the inner tally/chi-square moved to
numpy. Speed is the reason for the rewrite; the numbers are not allowed to move.

Run: python -B -m pytest modules/tests/test_mc_chi2_parity.py -q
"""
from __future__ import annotations

import numpy as np
import pytest
from scipy import stats

from modules.stats.clinical import _chi2_monte_carlo

SEED = 66666


def _ref_mc(table, n_resamples=2000, seed=SEED):
    table = np.asarray(table, dtype=int)
    obs = float(stats.chi2_contingency(table, correction=False)[0])
    rs = table.sum(axis=1)
    cs = table.sum(axis=0)
    row_ids = np.repeat(np.arange(len(rs)), rs)
    col_ids = np.repeat(np.arange(len(cs)), cs)
    rng = np.random.RandomState(seed)
    ge = n_ok = 0
    for _ in range(int(n_resamples)):
        shuffled = col_ids.copy()
        rng.shuffle(shuffled)
        sim = np.zeros_like(table)
        for r, c in zip(row_ids, shuffled):
            sim[r, c] += 1
        try:
            chi = float(stats.chi2_contingency(sim, correction=False)[0])
        except Exception:
            continue
        n_ok += 1
        if chi >= obs:
            ge += 1
    if n_ok < 20:
        return float("nan")
    return float((ge + 1) / (n_ok + 1))


TABLES = {
    "2x2": [[10, 5], [3, 12]],
    "2x3": [[5, 10, 15], [8, 3, 2]],
    "3x3": [[10, 20, 30], [5, 15, 25], [2, 8, 18]],
    "2x4_sparse": [[1, 1, 1, 9], [8, 1, 1, 2]],
    "3x2_skewed": [[1, 5], [6, 7], [9, 4]],
    "2x2_tiny": [[1, 1], [1, 1]],
}


@pytest.mark.parametrize("name", sorted(TABLES))
def test_p_value_is_bit_identical(name: str) -> None:
    table = np.asarray(TABLES[name], dtype=int)
    got = _chi2_monte_carlo(table, n_resamples=500, seed=SEED)
    want = _ref_mc(table, n_resamples=500, seed=SEED)
    if np.isnan(want):
        assert np.isnan(got)
    else:
        assert got == want


def test_default_resample_count_still_matches() -> None:
    table = np.asarray(TABLES["2x2"], dtype=int)
    assert _chi2_monte_carlo(table) == _ref_mc(table)


def test_seed_changes_result_but_stays_in_range() -> None:
    table = np.asarray(TABLES["3x3"], dtype=int)
    a = _chi2_monte_carlo(table, n_resamples=200, seed=1)
    b = _chi2_monte_carlo(table, n_resamples=200, seed=2)
    assert a != b
    assert 0.0 < a < 1.0 and 0.0 < b < 1.0


def test_zero_margin_returns_nan_instead_of_raising() -> None:
    assert np.isnan(_chi2_monte_carlo(np.asarray([[0, 0], [3, 4]]), n_resamples=50))
