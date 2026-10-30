"""patient_key + CLI dry-run defaults for downsample_train."""
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CLI = REPO / "model-validation" / "downsample_train.py"


def _load():
    spec = importlib.util.spec_from_file_location("downsample_train", CLI)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_patient_key_strips_trailing_space_digit():
    mod = _load()
    assert mod.patient_key("PatA 1") == "pata"
    assert mod.patient_key("PatA 12") == "pata"
    assert mod.patient_key("  PatB  2  ") == "patb"


def test_patient_key_keeps_embedded_digits_without_space():
    mod = _load()
    assert mod.patient_key("patient001") == "patient001"
    assert mod.patient_key("ID-99") == "id-99"
    assert mod.patient_key("A_3") == "a_3"


def test_patient_key_collapses_internal_whitespace():
    mod = _load()
    assert mod.patient_key("Ann  Marie") == "ann marie"


def test_parse_args_default_is_dry_run():
    mod = _load()
    args = mod.parse_args([])
    assert args.execute is False
    args2 = mod.parse_args(["--execute"])
    assert args2.execute is True


def test_clean_data_root_prefers_env(monkeypatch, tmp_path):
    mod = _load()
    monkeypatch.setenv("CLEAN_DATA_ROOT", str(tmp_path))
    monkeypatch.delenv("ODOC_CODE", raising=False)
    assert mod._default_clean_data_root() == tmp_path


def test_require_outcome_labels_accepts_pos_neg_only():
    import pandas as pd
    mod = _load()
    s = pd.Series(["Positive", "Negative", "Positive"])
    mod._require_outcome_labels(s, "Positive", "Negative")


def test_require_outcome_labels_fails_unknown_and_missing():
    import pandas as pd
    import pytest
    mod = _load()
    with pytest.raises(RuntimeError, match="unknown outcome"):
        mod._require_outcome_labels(
            pd.Series(["Positive", "Unknown", "Negative"]),
            "Positive",
            "Negative",
        )
    with pytest.raises(RuntimeError, match="missing outcome"):
        mod._require_outcome_labels(
            pd.Series(["Positive", None, "Negative"]),
            "Positive",
            "Negative",
        )
    with pytest.raises(RuntimeError, match="missing outcome"):
        mod._require_outcome_labels(
            pd.Series(["Positive", float("nan"), "Negative"]),
            "Positive",
            "Negative",
        )


def test_require_patient_ids_fails_before_astype_str():
    import pandas as pd
    import pytest
    mod = _load()
    with pytest.raises(RuntimeError, match="patient ID FAIL"):
        mod._require_patient_ids(pd.Series(["A", None, "B"]), "Patient_ID")
    with pytest.raises(RuntimeError, match="patient ID FAIL"):
        mod._require_patient_ids(pd.Series(["A", float("nan"), "B"]), "Patient_ID")
    with pytest.raises(RuntimeError, match="patient ID FAIL"):
        mod._require_patient_ids(pd.Series(["A", "nan", "B"]), "Patient_ID")
    with pytest.raises(RuntimeError, match="patient ID FAIL"):
        mod._require_patient_ids(pd.Series(["A", "<NA>", "B"]), "Patient_ID")
    with pytest.raises(RuntimeError, match="patient ID FAIL"):
        mod._require_patient_ids(pd.Series(["A", "", "B"]), "Patient_ID")
    out = mod._require_patient_ids(pd.Series([" A ", "B2"]), "Patient_ID")
    assert list(out) == ["A", "B2"]
