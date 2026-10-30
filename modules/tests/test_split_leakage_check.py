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


def test_empty_sentinels_fail():
    """N7: None/nan/NA/"None"/"nan" and kin count as empty, not valid IDs."""
    mod = _load()
    for sentinel in [None, float("nan"), "None", "nan", "NA", "n/a", "<NA>", "null", "  "]:
        code, report = mod.check_rows(
            [{"patient_id": sentinel, "split": "train"}, {"patient_id": "A", "split": "test"}],
            patient_col="patient_id",
            split_col="split",
        )
        assert code == 2, repr(sentinel)
        assert "Empty patient ID rows: 1" in report
        assert "FAIL" in report
    for sentinel in [None, "None", "nan", "NA", ""]:
        code, report = mod.check_rows(
            [{"patient_id": "A", "split": sentinel}, {"patient_id": "B", "split": "test"}],
            patient_col="patient_id",
            split_col="split",
        )
        assert code == 2, repr(sentinel)
        assert "Empty split rows: 1" in report


def test_is_empty_field_helpers():
    mod = _load()
    assert mod.is_empty_field(None) is True
    assert mod.is_empty_field("") is True
    assert mod.is_empty_field("  ") is True
    assert mod.is_empty_field("None") is True
    assert mod.is_empty_field("nan") is True
    assert mod.is_empty_field("NA") is True
    assert mod.is_empty_field(float("nan")) is True
    assert mod.is_empty_field("P001") is False


def test_pseudonymized_wording_not_anonymized():
    """N13: report says pseudonymized; notes HMAC-with-key for external share."""
    mod = _load()
    code, report = mod.check_rows(
        _rows([("P1", "train"), ("P1", "test")]),
        patient_col="patient_id",
        split_col="split",
        show_raw_ids=False,
    )
    assert code == 1
    assert "pseudonymized" in report.lower()
    assert "anonymized" in report.lower()  # clarification "not anonymized"
    assert "HMAC-with-key" in report
    assert "Patient (hashed)" not in report
    assert "Patient (pseudonymized)" in report
    # alias still works
    assert mod.anonymize_id("P1") == mod.pseudonymize_id("P1")

