"""model-validation CLIs: the impute wrapper stays faithful to the library.

The library pools outcome-like strata unless `allow_outcome_stratify` is set.
An explicit `--group` from the operator is not a guess, so the CLI must pass
the override and keep the strata apart.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CLI = REPO_ROOT / "model-validation" / "impute_excel.py"


def _load_cli():
    spec = importlib.util.spec_from_file_location("impute_excel_under_test", CLI)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def workbook(tmp_path):
    rng = np.random.default_rng(0)
    rows = []
    for group in ("A", "B"):
        for i in range(20):
            rows.append(
                {
                    "PatientID": f"{group}{i:02d}",
                    "Group": group,
                    "BMI": rng.normal(24, 3),
                    "LDH": rng.normal(200, 40),
                }
            )
    frame = pd.DataFrame(rows)
    frame.loc[frame.sample(4, random_state=1).index, "BMI"] = np.nan
    frame.loc[frame.sample(4, random_state=2).index, "LDH"] = np.nan
    path = tmp_path / "data.xlsx"
    frame.to_excel(path, index=False)
    return path, frame


def test_cli_keeps_explicit_group_strata(workbook):
    path, original = workbook
    cli = _load_cli()
    assert cli.main(["--input", str(path), "--group", "Group", "--method", "median", "--quiet"]) == 0

    result = pd.read_excel(path)
    assert result.shape == original.shape
    assert int(result.isna().sum().sum()) == 0
    # Group is an explicit stratum, not an outcome to strip: both levels survive.
    assert sorted(result["Group"].unique()) == ["A", "B"]


def test_cli_writes_qc_sheet_and_backup(workbook):
    path, original = workbook
    cli = _load_cli()
    assert cli.main(["--input", str(path), "--group", "Group", "--method", "median", "--quiet"]) == 0

    assert (path.parent / "data_pre_impute.xlsx").is_file()
    qc = pd.read_excel(path, sheet_name="impute_qc")
    assert list(qc.columns) == [
        "column",
        "n_miss_before",
        "n_miss_after",
        "n_filled",
        "ndigits_mean_obs",
    ]
    assert set(qc["column"]) == {"BMI", "LDH"}
    assert int(qc["n_miss_after"].sum()) == 0
    assert int(qc["n_filled"].sum()) == int(original.isna().sum().sum())


def test_cli_reports_missing_input(tmp_path, capsys):
    cli = _load_cli()
    assert cli.main(["--input", str(tmp_path / "absent.xlsx"), "--quiet"]) == 1
    assert "ERROR" in capsys.readouterr().err


def test_cli_accepts_every_documented_method(workbook):
    """The skill documents iterative / median / mean / missForest, mice as the alias."""
    cli = _load_cli()
    for method in ("iterative", "mice", "median", "mean"):
        path, _ = workbook
        assert cli.main(
            ["--input", str(path), "--group", "Group", "--method", method, "--quiet", "--no-backup"]
        ) == 0, method
