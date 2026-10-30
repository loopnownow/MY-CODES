"""Unit tests for model-validation/split_leakage_check.py."""
from __future__ import annotations

import csv
import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CLI = REPO / "model-validation" / "split_leakage_check.py"


def _load():
    spec = importlib.util.spec_from_file_location("split_leakage_check", CLI)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _rows(pairs):
    return [{"patient_id": p, "split": s} for p, s in pairs]


def test_empty_patient_id_fails():
    mod = _load()
    code, report = mod.check_rows(
        _rows([("", "train"), ("A", "train"), ("B", "test")]),
        patient_col="patient_id",
        split_col="split",
    )
    assert code == 2
    assert "Empty patient ID rows: 1" in report
    assert "FAIL" in report


def test_empty_split_fails():
    mod = _load()
    code, report = mod.check_rows(
        _rows([("A", ""), ("B", "test")]),
        patient_col="patient_id",
        split_col="split",
    )
    assert code == 2
    assert "Empty split rows: 1" in report
    assert "FAIL" in report


def test_cross_split_leak_detected_hashed_by_default():
    mod = _load()
    code, report = mod.check_rows(
        _rows([("P1", "train"), ("P1", "test"), ("P2", "train")]),
        patient_col="patient_id",
        split_col="split",
        show_raw_ids=False,
    )
    assert code == 1
    assert "Patients in multiple splits: 1" in report
    assert "| P1 |" not in report
    assert "id_" in report
    assert "test" in report and "train" in report


def test_cross_split_leak_show_raw_ids():
    mod = _load()
    code, report = mod.check_rows(
        _rows([("SECRET_ID", "train"), ("SECRET_ID", "val")]),
        patient_col="patient_id",
        split_col="split",
        show_raw_ids=True,
    )
    assert code == 1
    assert "SECRET_ID" in report


def test_duplicate_id_same_split_ok():
    mod = _load()
    code, report = mod.check_rows(
        _rows([("A", "train"), ("A", "train"), ("B", "test")]),
        patient_col="patient_id",
        split_col="split",
    )
    assert code == 0
    assert "No patient-level split leakage detected." in report


def test_cli_writes_report(tmp_path):
    mod = _load()
    path = tmp_path / "splits.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["patient_id", "split"])
        w.writeheader()
        w.writerow({"patient_id": "", "split": "train"})
        w.writerow({"patient_id": "A", "split": "train"})
    assert mod.main([str(path)]) == 2
