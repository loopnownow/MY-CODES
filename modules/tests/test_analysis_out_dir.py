"""Analysis OUT_DIR must land at project endpoint, never ref or project root."""
from __future__ import annotations

import pytest

from modules.config.ini_io import migrate_retired_settings
from modules.config.outcome import map_outcome_out_dir, normalize_analysis_out_dir


def _slash(p: str) -> str:
    return str(p).replace(chr(92), "/")


def test_normalize_strips_ref_pipeline_to_endpoint():
    assert normalize_analysis_out_dir("ref/pipeline/OV", fallback="OV") == "OV"
    assert normalize_analysis_out_dir("ref/results", fallback="Group") == "Group"
    assert normalize_analysis_out_dir("0result/ASL", fallback="ASL") == "ASL"


def test_normalize_keeps_healthy_endpoint():
    assert normalize_analysis_out_dir("ASL") == "ASL"
    assert normalize_analysis_out_dir("Group") == "Group"
    assert _slash(normalize_analysis_out_dir("Response/pos_vs_neg")) == "Response/pos_vs_neg"


def test_normalize_rejects_root_without_fallback():
    with pytest.raises(ValueError):
        normalize_analysis_out_dir("")
    with pytest.raises(ValueError):
        normalize_analysis_out_dir(".")
    with pytest.raises(ValueError):
        normalize_analysis_out_dir("ref/pipeline")


def test_map_outcome_never_returns_empty_or_ref():
    assert map_outcome_out_dir("Group", "", {}, True) == "Group"
    assert map_outcome_out_dir("Group", "", {"Group": "test_T"}, True) == "test_T"
    assert map_outcome_out_dir("OV", "", {"OV": "ref/pipeline/OV"}, True) == "OV"
    assert map_outcome_out_dir("Group", "", {"Group": "ref/results"}, True) == "Group"


def test_migrate_rewrites_ref_subproject_and_out_dir():
    data = migrate_retired_settings(
        {
            "OUT_DIR": "ref/pipeline",
            "SUBPROJECT_BY_OUTCOME": {"OV": "ref/pipeline/OV"},
            "OUTCOME_COLS": ["OV"],
        }
    )
    assert data["OUT_DIR"] == ""
    assert data["SUBPROJECT_BY_OUTCOME"]["OV"] == "OV"
