"""Parity for the library-backed multiple-comparison adjustments.

Reference implementations below are the previous in-house versions (BH, Holm); the
production functions now delegate to `statsmodels.stats.multitest.multipletests`.
The wrapper must keep NaN positions untouched and never leave the [0, 1] range.

Run: python -B -m pytest modules/tests/test_assoc_adjust_parity.py -q
"""
from __future__ import annotations

import numpy as np
import pytest

from modules.stats.assoc import bh_fdr, holm_fwer

TOL = 1e-12


def _ref_bh(p):
    src = np.asarray(list(p), dtype=float)
    out = np.full(src.shape, np.nan)
    idx = np.flatnonzero(np.isfinite(src))
    m = int(idx.size)
    if m == 0:
        return out
    order = idx[np.argsort(src[idx], kind="mergesort")]
    q = src[order] * m / np.arange(1, m + 1)
    q = np.minimum.accumulate(q[::-1])[::-1]
    out[order] = np.clip(q, 0.0, 1.0)
    return out


def _ref_holm(p):
    src = np.asarray(list(p), dtype=float)
    out = np.full(src.shape, np.nan)
    idx = np.flatnonzero(np.isfinite(src))
    m = int(idx.size)
    if m == 0:
        return out
    order = idx[np.argsort(src[idx], kind="mergesort")]
    adj = src[order] * (m - np.arange(m))
    adj = np.maximum.accumulate(np.clip(adj, 0.0, None))
    out[order] = np.clip(adj, 0.0, 1.0)
    return out


CASES = {
    "four": [0.011, 0.04, 0.03, 0.20],
    "single": [0.037],
    "zeros": [0.0, 0.0, 0.5],
    "all_ones": [1.0, 1.0, 1.0],
    "duplicates": [0.02, 0.02, 0.02, 0.9],
    "unsorted_mixed": [0.9, 0.001, 0.4, 0.0049, 0.5],
    "with_nan": [0.01, float("nan"), 0.04, 0.03, float("inf")],
    "all_nan": [float("nan"), float("nan")],
    "empty": [],
}

ADJUSTERS = {"fdr_bh": (bh_fdr, _ref_bh), "holm": (holm_fwer, _ref_holm)}


@pytest.mark.parametrize("name", sorted(CASES))
@pytest.mark.parametrize("method", sorted(ADJUSTERS))
def test_matches_reference(name: str, method: str) -> None:
    fn, ref = ADJUSTERS[method]
    got = fn(CASES[name])
    want = ref(CASES[name])
    assert got.shape == want.shape
    assert np.array_equal(np.isnan(got), np.isnan(want))
    finite = np.isfinite(want)
    if finite.any():
        assert np.allclose(got[finite], want[finite], rtol=TOL, atol=TOL)


@pytest.mark.parametrize("method", sorted(ADJUSTERS))
def test_never_below_raw_or_above_one(method: str) -> None:
    fn, _ = ADJUSTERS[method]
    raw = np.array([0.0, 0.001, 0.02, 0.04, 0.5, 1.0])
    adj = fn(raw)
    assert np.all(adj >= raw - TOL)
    assert np.all(adj <= 1.0 + TOL)


@pytest.mark.parametrize("method", sorted(ADJUSTERS))
def test_nan_slots_and_accepts_plain_list(method: str) -> None:
    fn, _ = ADJUSTERS[method]
    out = fn([0.01, None if False else float("nan")])
    assert np.isnan(out[1])
    assert out[0] == pytest.approx(0.01, abs=TOL)
