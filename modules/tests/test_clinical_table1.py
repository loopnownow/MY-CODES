"""R-02/03/04: Table 1 tests, Welch, no silent top-3 fallback."""
from __future__ import annotations

import numpy as np
import pandas as pd

from modules.stats.clinical import (
    _candidates_from_univariate,
    _categorical_p,
    _chi2_monte_carlo,
    _level_label,
    _pool_note,
    select_clinical,
)


def test_2x2_sparse_uses_fisher():
    table = np.array([[8, 1], [1, 0]], dtype=int)
    p = _categorical_p(table)
    assert np.isfinite(p)
    assert 0.0 <= p <= 1.0


def test_expected_counts_not_observed_min():
    # observed min is 4, but expected may be <5 — still a valid table
    table = np.array([[20, 4], [5, 10]], dtype=int)
    p = _categorical_p(table)
    assert np.isfinite(p)


def test_monte_carlo_returns_p():
    table = np.array([[10, 0, 1], [0, 8, 2]], dtype=int)
    p = _chi2_monte_carlo(table, n_resamples=200, seed=1)
    assert np.isfinite(p)
    assert 0.0 < p <= 1.0


def test_pool_note_skips_retired_vif_high():
    note = _pool_note(["Sex"], [], 0.1, mv_p_cut=0.05)
    assert "warn" in note
    assert "high" not in note


def test_no_silent_top3_fallback():
    n = 40
    y = np.array([0] * 20 + [1] * 20)
    clin = pd.DataFrame({
        "noise_a": np.ones(n),
        "noise_b": np.ones(n),
        "noise_c": np.ones(n),
    })
    group = pd.Series(["neg"] * 20 + ["pos"] * 20)
    res = select_clinical(
        clin, y, group, p_threshold=1e-12, dataset_label="unit", mv_p_cut=0.0,
    )
    assert res.features == []
    assert "top" not in (res.mv_note or "").lower() or "fallback" in (res.mv_note or "").lower()


def test_empty_force_model_defaults_to_multivar_p():
    rng = np.random.RandomState(0)
    n = 80
    y = np.array([0] * 40 + [1] * 40)
    clin = pd.DataFrame({
        "sig": np.concatenate([rng.normal(0, 1, 40), rng.normal(2.2, 1, 40)]),
        "noise": rng.normal(0, 1, n),
    })
    group = pd.Series(["neg"] * 40 + ["pos"] * 40)

    auto = select_clinical(
        clin, y, group, p_threshold=0.1, dataset_label="unit", mv_p_cut=0.05,
        force_model_features=[],
    )
    assert "sig" in (auto.candidates or [])
    assert auto.features == ["sig"]
    assert auto.select_source == "mv_p"
    assert auto.mv_selected is not None and len(auto.mv_selected) > 0
    assert auto.mv_df is not None and len(auto.mv_df) > 0

    manual = select_clinical(
        clin, y, group, p_threshold=0.1, dataset_label="unit",
        force_model_features=["noise"],
    )
    assert manual.features == ["noise"]
    assert manual.select_source == "manual"
    assert "sig" in (manual.candidates or [])


def test_multivar_table_has_significance_last():
    from modules.stats.report import _format_mv_df

    mv = pd.DataFrame({
        "Feature": ["FIGO", "noise"],
        "OR": ["1.583", "1.000"],
        "CI_low": ["1.004", "0.900"],
        "CI_high": ["2.496", "1.100"],
        "p": ["0.048", "0.528"],
        "VIF": ["1.680", "1.767"],
    })
    out = _format_mv_df(mv)
    assert list(out.columns) == ["Feature", "OR", "CI", "p", "VIF", "Sig"]
    assert out.loc[0, "Sig"] == "*"
    assert out.loc[1, "Sig"] == ""


def test_candidates_exclude_separation_not_high_vif():
    # VIF 高阈值已退役：只剔完全分离；vif_high 参数忽略。
    uni = pd.DataFrame({
        "Feature": ["ok", "Weight (kg)", "Platinum"],
        "OR": [2.1, 1.025, 7.2e10],
        "CI_low": [1.3, 0.993, 0.0],
        "CI_high": [3.4, 1.058, np.inf],
        "p": [0.001, 0.124, 0.999],
        "VIF": ["2.105", "372.678", "1.224"],
    })
    clin = pd.DataFrame({
        "ok": [0.0],
        "Weight (kg)": [60.0],
        "Platinum": [1.0],
    })
    out = _candidates_from_univariate(uni, clin, 0.1, set(), vif_high=10)
    assert out == ["ok"]

    uni2 = pd.DataFrame({
        "Feature": ["ok", "collinear"],
        "OR": [2.1, 1.4],
        "CI_low": [1.3, 1.1],
        "CI_high": [3.4, 1.8],
        "p": [0.001, 0.02],
        "VIF": ["1.2", "372.0"],
    })
    clin2 = pd.DataFrame({"ok": [0.0], "collinear": [1.0]})
    out2 = _candidates_from_univariate(uni2, clin2, 0.1, set(), vif_high=10)
    assert out2 == ["ok", "collinear"]


def test_figo_roman_tokens_keep_table_labels():
    from modules.stats import data_io as dio

    dio.CLIN_LEVEL_MAPS = {}
    dio.CLIN_VAR_TYPES = {}
    dio.CLIN_ORDINAL_TOKEN_COLS = set()
    clin = pd.DataFrame({"FIGO": ["III", "I", "IV", "II", "I", "III"]})
    out = dio._encode_english_categoricals(clin)
    assert set(pd.to_numeric(out["FIGO"], errors="coerce").dropna().unique()) == {
        1.0, 2.0, 3.0, 4.0,
    }
    m = dio.CLIN_LEVEL_MAPS["FIGO"]
    assert m[1.0] == "I"
    assert m[2.0] == "II"
    assert m[3.0] == "III"
    assert m[4.0] == "IV"
    assert _level_label("FIGO", 1) == "I"
    assert _level_label("FIGO", 3.0) == "III"
