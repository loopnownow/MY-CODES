"""OUTCOME_BINARIZE 与 GROUP_KEEP 同用时不硬报错（多组只比两组、HC 只出表）。"""
from __future__ import annotations

import pandas as pd
import pytest

from modules import config as cfg
from modules.stats.data_io import _apply_outcome_binarize


def _arm(monkeypatch, keep):
    monkeypatch.setattr(cfg, "GROUP_SOURCE_COL", "Group", raising=False)
    monkeypatch.setattr(
        cfg, "OUTCOME_BINARIZE", {"Group": {"MCI": 1, "CN-ESRD": 0}}, raising=False
    )
    monkeypatch.setattr(cfg, "READER_VALUE_MAP", {}, raising=False)
    monkeypatch.setattr(cfg, "GROUP_KEEP", keep, raising=False)


def _clin() -> pd.DataFrame:
    """177 例形态：61 MCI / 56 CN-ESRD / 60 HC（HC 无实验室数据）。"""
    return pd.DataFrame({"Group": ["MCI"] * 61 + ["CN-ESRD"] * 56 + ["HC"] * 60})


def test_group_keep_keeps_labels_for_dropped_level(monkeypatch):
    _arm(monkeypatch, ["MCI", "CN-ESRD"])
    out = _apply_outcome_binarize(_clin())
    assert out["Group"].tolist() == _clin()["Group"].tolist()


def test_group_keep_keeps_labels_even_when_map_would_match(monkeypatch):
    """GROUP_KEEP 靠标签筛行；预二值化会把标签换成 0/1，白名单就筛不到行。"""
    _arm(monkeypatch, ["MCI", "CN-ESRD"])
    out = _apply_outcome_binarize(pd.DataFrame({"Group": ["MCI", "CN-ESRD", "MCI"]}))
    assert out["Group"].tolist() == ["MCI", "CN-ESRD", "MCI"]


def test_without_group_keep_binarizes(monkeypatch):
    _arm(monkeypatch, [])
    out = _apply_outcome_binarize(pd.DataFrame({"Group": ["MCI", "CN-ESRD", "MCI"]}))
    assert out["Group"].tolist() == [1.0, 0.0, 1.0]


def test_without_group_keep_partial_map_raises(monkeypatch):
    _arm(monkeypatch, [])
    with pytest.raises(ValueError, match="无法按映射编码"):
        _apply_outcome_binarize(_clin())


def test_without_user_map_stays_untouched(monkeypatch):
    _arm(monkeypatch, [])
    monkeypatch.setattr(cfg, "OUTCOME_BINARIZE", {}, raising=False)
    out = _apply_outcome_binarize(_clin())
    assert out["Group"].tolist() == _clin()["Group"].tolist()
