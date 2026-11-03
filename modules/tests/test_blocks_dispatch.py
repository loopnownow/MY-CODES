"""模态块派发：每块自选 lasso/logistic、参数覆盖生效并恢复、非法值报错。"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from modules import config as cfg
from modules.config.blocks import normalize_blocks
from modules.stats import radiomics, selectors


def _y(n: int = 120, seed: int = 0):
    return np.random.default_rng(seed).integers(0, 2, n).astype(int)


def _frame(y, seed: int = 1, signal: float = 1.6,
           noise: int = 3, prefix: str = "a"):
    rng = np.random.default_rng(seed)
    n = len(y)
    df = pd.DataFrame({"Name": [f"P{i:03d}" for i in range(n)]})
    df[f"{prefix}_signal"] = y * signal + rng.normal(0, 1, n)
    for k in range(noise):
        df[f"{prefix}_noise{k}"] = rng.normal(0, 1, n)
    return df


@pytest.fixture(autouse=True)
def _no_lasso_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "LASSO_CACHE", False, raising=False)
    monkeypatch.setattr(cfg, "DO_ICC_FILTER", False, raising=False)
    monkeypatch.setattr(cfg, "RAD_FEATURE_SOURCE", "both", raising=False)
    monkeypatch.setattr(cfg, "OUT_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(cfg, "PNG_DIR", str(tmp_path / "PNG"), raising=False)
    monkeypatch.setattr(cfg, "DO_FIGURE_PANELS", False, raising=False)
    monkeypatch.setattr(cfg, "LASSO_PLOT_PATH", False, raising=False)


def test_run_blocks_scores_and_table2():
    y = _y()
    a = _frame(y, seed=1, prefix="a")
    b = _frame(y, seed=2, signal=1.2, prefix="b")
    specs = normalize_blocks([
        {"name": "A", "method": "lasso",
         "lasso": {"nfolds": 5, "n_lambdas": 10, "prefilter_topk": 4}},
        {"name": "B", "method": "logistic",
         "logistic": {"uni_p": 0.2, "mv_p": 0.05}},
    ])
    res = selectors.run_blocks({"A": a, "B": b}, y, specs, dataset_label="t1")
    scores = radiomics.score_map(res)
    assert set(scores) == {"RadScore_A", "RadScore_B"}
    assert [g.method for g in res.groups] == ["lasso", "logistic"]
    assert res.method == "blocks"
    lasso_g = [g for g in res.groups if g.method == "lasso"][0]
    assert lasso_g.preprocess and lasso_g.preprocess.get("keep_cols")
    assert {"Block", "Method"}.issubset(set(res.table2.columns))


def test_block_param_override_restored():
    y = _y()
    a = _frame(y, seed=3, prefix="c")
    old = cfg.RAD_LOGIT_UNI_P
    specs = normalize_blocks([
        {"name": "C", "method": "logistic", "logistic": {"uni_p": 0.9}},
    ])
    selectors.run_block("C", a, y, specs[0], dataset_label="t2")
    assert cfg.RAD_LOGIT_UNI_P == old


def test_unknown_method_raises():
    with pytest.raises(ValueError, match="method"):
        normalize_blocks([{"name": "C", "method": "svm"}])


def test_unknown_param_raises():
    y = _y()
    a = _frame(y, seed=4, prefix="d")
    specs = normalize_blocks([
        {"name": "D", "method": "logistic", "logistic": {"bogus": 1}},
    ])
    with pytest.raises(ValueError, match="未知参数"):
        selectors.run_block("D", a, y, specs[0], dataset_label="t3")


def test_external_specs_carry_block_sheets(monkeypatch):
    """外验 spec 的 sheets 覆盖：未写的块回落到本工程块的工作表名。"""
    from modules.pipeline import _resolve_external_specs

    monkeypatch.setattr(cfg, "MODALITY_BLOCKS", [
        {"name": "A", "sheet": "A", "method": "lasso"},
        {"name": "B", "method": "logistic"},
    ], raising=False)
    monkeypatch.setattr(cfg, "EXTERNAL_TESTS", [
        {"name": "ext", "file": "x.xlsx", "clin_sheet": "cln", "sheets": {"B": "B_ext"}},
    ], raising=False)
    specs = _resolve_external_specs("main.xlsx")
    assert len(specs) == 1
    assert specs[0]["sheets"] == {"A": "A", "B": "B_ext"}
    assert specs[0]["rad_sheet"] == "A"


def test_missing_block_data_raises():
    y = _y()
    a = _frame(y, seed=5, prefix="e")
    specs = normalize_blocks([{"name": "E", "method": "logistic"}])
    with pytest.raises(ValueError, match="没有数据"):
        selectors.run_blocks({"X": a}, y, specs, dataset_label="t4")
