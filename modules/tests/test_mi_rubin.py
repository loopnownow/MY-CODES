"""Multiple imputation + Rubin pooling.

Two things are pinned here:

1. `rubin_pool` against hand-computed values (Rubin 1987) and against a
   between-imputation variance of zero, where pooling must collapse to the
   single-imputation answer.
2. `z_impute_mi` actually draws — the imputed cells differ between datasets, the
   observed cells do not. A sampler that ignores `sample_posterior` would make
   every draw identical and silently reduce MI to single imputation.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from modules.utils.u_impute import z_impute
from modules.utils.u_mi import MI_DRAWS, rubin_pool, rubin_pool_frame, z_impute_mi


# ── Rubin's rules ──────────────────────────────────────────────────────────
def test_pool_matches_hand_calculation():
    q = [1.0, 2.0, 3.0, 4.0]
    u = [0.04] * 4
    pooled = rubin_pool(q, u)

    between = float(np.var(q, ddof=1))
    total = 0.04 + (1 + 1 / 4) * between
    r = (1 + 1 / 4) * between / 0.04

    assert pooled.estimate == pytest.approx(2.5)
    assert pooled.within_var == pytest.approx(0.04)
    assert pooled.between_var == pytest.approx(between)
    assert pooled.se**2 == pytest.approx(total)
    assert pooled.riv == pytest.approx(r)
    assert pooled.fmi == pytest.approx((1 + 1 / 4) * between / total)
    assert pooled.df == pytest.approx((4 - 1) * (1 + 1 / r) ** 2)


def test_no_between_variance_reduces_to_single_imputation():
    pooled = rubin_pool([2.0, 2.0, 2.0], [0.09, 0.09, 0.09])
    assert pooled.estimate == pytest.approx(2.0)
    assert pooled.se == pytest.approx(0.3)
    assert pooled.between_var == 0.0
    assert pooled.riv == 0.0
    assert pooled.fmi == 0.0


def test_total_variance_is_not_smaller_than_within():
    """The defect single imputation has: an SE that ignores missingness."""
    pooled = rubin_pool([1.0, 2.0, 3.0, 4.0], [0.04] * 4)
    assert pooled.se**2 > pooled.within_var


def test_barnard_rubin_blends_and_shrinks_df():
    """ν = ν_old·ν_obs/(ν_old+ν_obs) is a harmonic blend, so ν ≤ ν_old always.

    With heavy missingness (FMI ~0.98 here) ν stays near m-1 whatever n is; with
    light missingness ν_obs dominates and the df rises well above m-1.
    """
    heavy = rubin_pool([1.0, 2.0, 3.0, 4.0], [0.04] * 4)
    heavy_with_n = rubin_pool([1.0, 2.0, 3.0, 4.0], [0.04] * 4, n_complete=500)
    assert heavy.fmi > 0.9
    assert heavy_with_n.df <= heavy.df + 1e-9

    # Light missingness: tiny between-imputation variance, large n -> df approaches n.
    light = rubin_pool([2.00, 2.01, 1.99, 2.00], [0.04] * 4, n_complete=500)
    assert light.fmi < 0.1
    assert light.df > 100


def test_rejects_degenerate_input():
    with pytest.raises(ValueError):
        rubin_pool([1.0], [0.1])                      # m < 2
    with pytest.raises(ValueError):
        rubin_pool([1.0, 2.0], [0.1])                 # length mismatch
    with pytest.raises(ValueError):
        rubin_pool([1.0, 2.0], [0.1, -0.1])           # negative variance


def test_pool_frame_one_row_per_term():
    table = pd.DataFrame(
        {
            "term": ["Age"] * 3 + ["Score"] * 3,
            "estimate": [0.1, 0.2, 0.3, 0.5, 0.6, 0.7],
            "variance": [0.01] * 6,
        }
    )
    pooled = rubin_pool_frame(table, n_complete=100)
    assert list(pooled["term"]) == ["Age", "Score"]
    assert (pooled["m"] == 3).all()
    assert pooled.loc[pooled.term == "Age", "estimate"].iloc[0] == pytest.approx(0.2)


# ── the sampler ───────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def missing_frame():
    rng = np.random.default_rng(5)
    n = 120
    x1 = rng.normal(50, 10, n)
    x2 = 0.6 * x1 + rng.normal(0, 5, n)
    frame = pd.DataFrame({"Group": np.repeat(["A", "B"], n // 2), "x1": x1, "x2": x2})
    for column in ("x1", "x2"):
        frame.loc[rng.choice(n, 30, replace=False), column] = np.nan
    return frame


def test_draws_differ_between_imputations(missing_frame):
    draws = z_impute_mi(missing_frame, m=5, group_col="Group", seed=1, verbose=False)
    assert len(draws) == 5
    masked = missing_frame["x1"].isna()
    first = draws[0].loc[masked, "x1"].to_numpy()
    second = draws[1].loc[masked, "x1"].to_numpy()
    assert not np.allclose(first, second), "draws are identical - the sampler is not sampling"


def test_observed_cells_never_move(missing_frame):
    draws = z_impute_mi(missing_frame, m=3, group_col="Group", seed=2, verbose=False)
    observed = missing_frame["x1"].notna()
    for draw in draws:
        assert np.allclose(
            draw.loc[observed, "x1"].to_numpy(),
            missing_frame.loc[observed, "x1"].to_numpy(),
        )


def test_no_missing_left(missing_frame):
    draws = z_impute_mi(missing_frame, m=2, group_col="Group", seed=3, verbose=False)
    for draw in draws:
        assert int(draw[["x1", "x2"]].isna().sum().sum()) == 0


def test_requires_two_draws(missing_frame):
    with pytest.raises(ValueError):
        z_impute_mi(missing_frame, m=1, group_col="Group", verbose=False)


def test_single_imputation_is_not_a_draw(missing_frame):
    """z_impute is deterministic; that is the whole reason u_mi exists."""
    a = z_impute(missing_frame.copy(), group_col="Group", method="iterative", verbose=False)
    b = z_impute(missing_frame.copy(), group_col="Group", method="iterative", verbose=False)
    masked = missing_frame["x1"].isna()
    assert np.allclose(a.loc[masked, "x1"], b.loc[masked, "x1"])

    draws = z_impute_mi(missing_frame, m=2, group_col="Group", seed=4, verbose=False)
    assert not np.allclose(
        draws[0].loc[masked, "x1"].to_numpy(), draws[1].loc[masked, "x1"].to_numpy()
    )


def test_default_draw_count_is_documented():
    assert MI_DRAWS >= 20


# ── parity with z_impute ──────────────────────────────────────────────────
def _stratum_frame():
    rng = np.random.default_rng(21)
    frame = pd.DataFrame({
        "Group": np.repeat(["HC", "SSD"], 25),
        "Score": np.concatenate([rng.normal(10, 1, 25), rng.normal(30, 1, 25)]),
    })
    frame.loc[[0, 1, 25, 26], "Score"] = np.nan
    return frame


def test_mi_and_single_agree_on_implicit_pooling():
    """Both entry points must read an omitted group_col the same way.

    They disagreed once: z_impute took a sentinel default, z_impute_mi still took
    the string "Group", so a bare MI call stratified on an outcome-like column
    while a bare single call pooled it.
    """
    frame = _stratum_frame()
    mi = z_impute_mi(frame, m=2, seed=7, verbose=False)[0]
    single = z_impute(frame.copy(), method="median", verbose=False)

    def pooled(draw):
        return float(draw.loc[0, "Score"]) == float(draw.loc[25, "Score"])

    assert pooled(mi), "bare MI call stratified - it must pool like z_impute"
    assert pooled(single), "bare single call stratified - it must pool"


def test_mi_and_single_agree_on_explicit_stratum():
    frame = _stratum_frame()
    mi = z_impute_mi(frame, m=2, group_col="Group", seed=8, verbose=False)[0]
    assert float(mi.loc[0, "Score"]) < 15
    assert float(mi.loc[25, "Score"]) > 25


# ── CLI error handling ────────────────────────────────────────────────────
def _load_cli():
    import importlib.util

    path = REPO_ROOT / "model-validation" / "mi_impute.py"
    spec = importlib.util.spec_from_file_location("mi_impute_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]


def test_pool_cli_rejects_single_imputation(tmp_path, capsys):
    """One row per term cannot be pooled; say so instead of raising a traceback."""
    table = tmp_path / "one.csv"
    table.write_text("term,estimate,variance\nAge,0.1,0.01\n", encoding="utf-8")
    assert _load_cli().main(["pool", "--table", str(table)]) == 1
    assert "at least 2 imputations" in capsys.readouterr().err


def test_pool_cli_rejects_missing_columns(tmp_path, capsys):
    table = tmp_path / "bad.csv"
    table.write_text("a,b\n1,2\n", encoding="utf-8")
    assert _load_cli().main(["pool", "--table", str(table)]) == 1
    assert "missing column" in capsys.readouterr().err
