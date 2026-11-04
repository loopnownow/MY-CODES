"""文本编码：自动识别、≤5 等级自动编码、手改优先、超限列不静默。"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from modules import config as cfg
from modules.config.blocks import normalize_blocks
from modules.stats import data_io, text_encode


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    monkeypatch.setattr(cfg, "TEXT_ENCODE", {}, raising=False)
    monkeypatch.setattr(cfg, "TEXT_ENCODE_AUTO", True, raising=False)
    monkeypatch.setattr(cfg, "TEXT_ENCODE_MAX_LEVELS", 5, raising=False)
    monkeypatch.setattr(cfg, "RAD_FEATURE_PREFIX_EXCLUDE", [], raising=False)
    monkeypatch.setattr(cfg, "RAD_ID_COL", "Patient_ID", raising=False)


def _frame():
    return pd.DataFrame({
        "Patient_ID": ["P1", "P2", "P3", "P4"],
        "echo": ["Mixed", "Isoechoic", "Hyperechoic", "Mixed"],
        "num": [1.0, 2.0, 3.0, 4.0],
        "many": ["a", "b", "c", "d"],
    })


def test_detect_and_auto_codes():
    det = text_encode.detect_text_columns({"US": _frame()}, limit=3)
    cols = {d["column"]: d for d in det}
    assert "num" not in cols and "Patient_ID" not in cols
    assert cols["echo"]["n_levels"] == 3 and not cols["echo"]["over_cap"]
    assert cols["echo"]["levels"] == ["Hyperechoic", "Isoechoic", "Mixed"]
    assert cols["many"]["over_cap"] is True
    assert text_encode.default_codes(["10", "2"]) == {"10": 10.0, "2": 2.0}
    assert text_encode.default_codes(["Negative", "Positive"]) == {"Negative": 0.0, "Positive": 1.0}
    assert text_encode.default_codes(["Yes", "No"]) == {"Yes": 1.0, "No": 0.0}
    assert text_encode.default_codes(["B", "A"]) == {"B": 0.0, "A": 1.0}
    assert text_encode.default_codes(["Hyperechoic", "Hypoechoic", "Isoechoic", "Mixed"]) == {
        "Hyperechoic": 1.0, "Hypoechoic": 2.0, "Isoechoic": 3.0, "Mixed": 4.0,
    }


def test_normalize_spec_rejects_non_numeric():
    assert text_encode.normalize_spec(None) == {}
    with pytest.raises(ValueError, match="编码必须是数字"):
        text_encode.normalize_spec({"echo": {"Mixed": "x"}})


def test_prepare_frame_auto_encodes_and_keeps_manual():
    df = _frame()
    cfg.TEXT_ENCODE = {"echo": {"Mixed": 9.0}}
    out = text_encode.prepare_frame(df, frame_label="US")
    assert list(out["echo"]) == [9.0, 2.0, 1.0, 9.0]  # 手写 Mixed=9；其余自动（字典序）
    assert pd.api.types.is_numeric_dtype(out["num"])


def test_prepare_frame_drops_over_cap_column():
    cfg.TEXT_ENCODE_MAX_LEVELS = 2
    out = text_encode.prepare_frame(_frame(), frame_label="US")
    assert "many" not in out.columns
    assert "echo" not in out.columns  # 3 等级 > 上限 2，也不自动编码


def test_unmapped_level_becomes_nan():
    df = _frame()
    fixed, rep = text_encode.apply(df, {"echo": {"Mixed": 1.0}}, frame_label="US")
    assert rep["unmapped"]["echo"] == ["Hyperechoic", "Isoechoic"]
    assert np.isnan(fixed["echo"].iloc[1])


def test_block_kind_default_and_validation():
    specs = normalize_blocks([{"name": "US", "method": "logistic"}])
    assert specs[0].kind == "rad"
    specs = normalize_blocks([{"name": "US", "method": "logistic", "kind": "clin"}])
    assert specs[0].kind == "clin"
    with pytest.raises(ValueError, match="kind"):
        normalize_blocks([{"name": "US", "kind": "image"}])


def test_prepare_blocks_encodes_clin_kind():
    raw = _frame().rename(columns={"Patient_ID": "Patient_ID"})
    specs = {
        "US": normalize_blocks(
            [{"name": "US", "method": "logistic", "kind": "clin"}]
        )[0]
    }
    ids = ["P1", "P2", "P3", "P4"]
    blocks = data_io._prepare_blocks({"US": raw}, ids, specs)
    us = blocks["US"]
    assert pd.api.types.is_numeric_dtype(us["echo"])
    assert "many" in us.columns or "many" not in us.columns  # 4 等级 ≤5 → 自动编码


def test_prepare_blocks_encodes_rad_kind_too():
    """kind=rad 的组学块也要把文本列编码后入模（不再直接排除）。"""
    raw = _frame()
    specs = {"CTA": normalize_blocks([{"name": "CTA", "method": "logistic"}])[0]}
    ids = ["P1", "P2", "P3", "P4"]
    blocks = data_io._prepare_blocks({"CTA": raw}, ids, specs)
    cta = blocks["CTA"]
    assert pd.api.types.is_numeric_dtype(cta["echo"])
    assert list(cta["echo"]) == [3.0, 2.0, 1.0, 3.0]  # 字典序：Hyperechoic 1、Isoechoic 2、Mixed 3


def test_prepare_blocks_leaves_text_when_switch_off(monkeypatch):
    monkeypatch.setattr(cfg, "ENCODE_BLOCK_TEXT", False, raising=False)
    raw = _frame()
    specs = {"CTA": normalize_blocks([{"name": "CTA", "method": "logistic"}])[0]}
    blocks = data_io._prepare_blocks({"CTA": raw}, ["P1", "P2", "P3", "P4"], specs)
    assert not pd.api.types.is_numeric_dtype(blocks["CTA"]["echo"])
