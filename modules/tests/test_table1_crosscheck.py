"""Dev-only Table 1 cross-check against `tableone`.

Measured behaviour (seeded fixture below):
    welch t / mann-whitney / RxC chi-square  -> MATCH at display precision
    2x2 categorical                          -> DIFF, and the gap is fully
                                                explained by tableone's Yates
                                                continuity correction (asserted
                                                by recomputing with Yates here)

Run: python -B -m pytest modules/tests/test_table1_crosscheck.py -q
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from modules.stats.table1_crosscheck import (
    DISPLAY_DIGITS,
    REASON_MW_KW,
    REASON_YATES,
    _fmt,
    _ours_categorical,
    crosscheck,
    main,
    render,
)

N = 200
CATS = ["sex_big", "stage3", "rare"]
CONTS = ["age_normal", "size_skewed"]
COLUMNS = CONTS + CATS


@pytest.fixture(scope="module")
def frame() -> pd.DataFrame:
    rng = np.random.RandomState(20261102)
    g = np.r_[np.ones(N // 2, dtype=int), np.zeros(N - N // 2, dtype=int)]
    df = pd.DataFrame(
        {
            "grp": g,
            "age_normal": np.where(g == 1, rng.normal(55, 10, N), rng.normal(56, 11, N)),
            "size_skewed": np.where(
                g == 1, rng.lognormal(3.2, 0.4, N), rng.lognormal(3.0, 0.45, N)
            ),
            "sex_big": rng.choice([0, 1], N, p=[0.45, 0.55]),
            "stage3": rng.choice([0, 1, 2], N, p=[0.5, 0.3, 0.2]),
        }
    )
    df["rare"] = 0
    df.loc[(g == 1) & (rng.rand(N) < 0.06), "rare"] = 1
    df.loc[(g == 0) & (rng.rand(N) < 0.02), "rare"] = 1
    return df


@pytest.fixture(scope="module")
def report(frame: pd.DataFrame) -> pd.DataFrame:
    return crosscheck(frame, "grp", COLUMNS, CATS, nonnormal=["size_skewed"])


def test_welch_and_mann_whitney_match(report: pd.DataFrame) -> None:
    got = report.set_index("Variable")
    assert got.loc["age_normal", "ours_test"] == "welch t"
    assert got.loc["age_normal", "verdict"] == "MATCH"
    assert got.loc["size_skewed", "ours_test"] == "mann-whitney"
    assert got.loc["size_skewed", "verdict"] == "MATCH"
    assert got.loc["size_skewed", "reason_if_diff"] in ("", REASON_MW_KW)


def test_rxc_chi_square_matches(report: pd.DataFrame) -> None:
    got = report.set_index("Variable")
    assert got.loc["stage3", "verdict"] == "MATCH"


def test_two_by_two_differs_only_by_yates(report: pd.DataFrame) -> None:
    got = report.set_index("Variable")
    for var in ("sex_big", "rare"):
        assert got.loc[var, "verdict"] == "DIFF"
        assert got.loc[var, "reason_if_diff"] == REASON_YATES


def test_yates_recomputation_reproduces_tableone(frame: pd.DataFrame, report: pd.DataFrame) -> None:
    i1 = np.flatnonzero(frame["grp"] == 1)
    i0 = np.flatnonzero(frame["grp"] == 0)
    got = report.set_index("Variable")
    for var in ("sex_big", "rare"):
        p_yates, how = _ours_categorical(frame[var], i1, i0, yates=True)
        assert how == "chi2 yates"
        assert _fmt(p_yates) == got.loc[var, "tableone_display"]


def test_report_has_all_columns(frame: pd.DataFrame, report: pd.DataFrame) -> None:
    assert set(report["Variable"]) == set(COLUMNS)
    text = render(report)
    assert text.count("\n") == len(COLUMNS) + 2
    assert "MATCH" in text and "DIFF" in text


def test_cli_writes_report_and_exit_code(frame: pd.DataFrame, tmp_path) -> None:
    xlsx = tmp_path / "cln.xlsx"
    frame.to_excel(xlsx, sheet_name="cln", index=False)
    out = tmp_path / "crosscheck.md"
    code = main([
        "--xlsx", str(xlsx), "--sheet", "cln", "--group", "grp",
        "--columns", ",".join(COLUMNS), "--categorical", ",".join(CATS),
        "--nonnormal", "size_skewed", "--out", str(out),
    ])
    assert code == 1  # 2x2 variables differ by Yates
    assert "Table 1 cross-check" in out.read_text(encoding="utf-8")

    code_cont = main([
        "--xlsx", str(xlsx), "--sheet", "cln", "--group", "grp",
        "--columns", "age_normal",
    ])
    assert code_cont == 0


def test_fmt_matches_table_one_conventions() -> None:
    assert _fmt(0.0001) == "<0.001"
    assert _fmt(0.195347048) == f"{0.195347048:.{DISPLAY_DIGITS}f}"
    assert _fmt(float("nan")) == "NA"
