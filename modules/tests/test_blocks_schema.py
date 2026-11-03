"""模态块配置：规范化、ini 往返、控制台 hydrate 不吞键。"""
from __future__ import annotations

from pathlib import Path

import pytest

from modules.config.blocks import (
    BlockSpec,
    combined_block_names,
    combined_mode,
    normalize_blocks,
)
from modules.config.ini_io import read_ini, write_ini
from modules.console.schema_ui import hydrate_console_settings


def _two_blocks():
    return [
        {
            "name": "CTA",
            "sheet": "CTA",
            "method": "lasso",
            "prep": {"icc": True, "corr": True, "corr_cutoff": 0.95},
            "lasso": {"nfolds": 10, "prefilter_topk": 25},
        },
        {
            "name": "US",
            "sheet": "US",
            "method": "logistic",
            "logistic": {"uni_p": 0.1, "mv_p": 0.05},
        },
    ]


def test_normalize_two_blocks():
    specs = normalize_blocks(_two_blocks())
    assert [s.name for s in specs] == ["CTA", "US"]
    assert [s.method for s in specs] == ["lasso", "logistic"]
    assert specs[0].score_name == "RadScore_CTA"
    assert specs[1].resolved_sheet == "US"
    assert specs[0].params["lasso"]["prefilter_topk"] == 25
    assert specs[1].params["logistic"]["mv_p"] == 0.05


def test_normalize_empty_means_legacy_path():
    assert normalize_blocks([]) == []
    assert normalize_blocks(None) == [] or all(
        isinstance(s, BlockSpec) for s in normalize_blocks(None)
    )


def test_normalize_rejects_bad_method():
    with pytest.raises(ValueError, match="method"):
        normalize_blocks([{"name": "US", "method": "xgboost"}])


def test_normalize_rejects_duplicate_and_missing_name():
    with pytest.raises(ValueError, match="重复"):
        normalize_blocks([{"name": "US"}, {"name": "us"}])
    with pytest.raises(ValueError, match="name"):
        normalize_blocks([{"method": "lasso"}])


def test_blank_method_is_explicit_skip():
    specs = normalize_blocks([{"name": "US", "method": ""}])
    assert specs[0].method == ""


def test_ini_roundtrip_keeps_method(tmp_path: Path):
    p = tmp_path / "settings.ini"
    write_ini(p, {"MODALITY_BLOCKS": _two_blocks(), "COMBINED_MODE": "joint"})
    text = p.read_text(encoding="utf-8")
    assert "[blocks]" in text
    back = read_ini(p)
    assert [b["name"] for b in back["MODALITY_BLOCKS"]] == ["CTA", "US"]
    assert [b["method"] for b in back["MODALITY_BLOCKS"]] == ["lasso", "logistic"]
    assert back["COMBINED_MODE"] == "joint"


def test_console_hydrate_keeps_method_and_prep():
    out = hydrate_console_settings({"MODALITY_BLOCKS": _two_blocks()})
    blocks = out["MODALITY_BLOCKS"]
    assert [b["method"] for b in blocks] == ["lasso", "logistic"]
    assert blocks[0]["prep"]["corr_cutoff"] == 0.95
    assert blocks[1]["params"]["logistic"]["uni_p"] == 0.1
    assert blocks[0]["score"] == "" or blocks[0]["score"] == "RadScore_CTA"


def test_combined_mode_default_and_invalid():
    assert combined_mode("") == "per_block"
    assert combined_mode("per_block") == "per_block"
    assert combined_mode("joint") == "joint"
    with pytest.raises(ValueError, match="COMBINED_MODE"):
        combined_mode("both")


def test_combined_block_names_respects_exclude():
    scores = ["RadScore_CTA", "RadScore_US"]
    assert combined_block_names(scores) == scores
    import modules.config as cfg

    old = getattr(cfg, "COMBINED_EXCLUDE", None)
    try:
        cfg.COMBINED_EXCLUDE = ["RadScore_US"]
        assert combined_block_names(scores) == ["RadScore_CTA"]
    finally:
        cfg.COMBINED_EXCLUDE = old
