"""模态块结果也要出组学单/多因素 Logistic 表（method=blocks 不是 logistic）。"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from modules.stats import radiomics as rad
from modules.stats.report import _radiomics_logistic_html

ROOT = Path(__file__).resolve().parents[2]
REPORT_SRC = (ROOT / "modules" / "stats" / "report.py").read_text(encoding="utf-8")


def _frame(seed: int = 11, n: int = 90):
    rng = np.random.default_rng(seed)
    x1 = rng.normal(size=n)
    x2 = rng.normal(size=n)
    y = (rng.random(n) < 1 / (1 + np.exp(-(1.2 * x1 + 0.9 * x2)))).astype(int)
    return pd.DataFrame({"Name": [f"p{i}" for i in range(n)], "f1": x1, "f2": x2}), y


@pytest.fixture
def base_cfg(monkeypatch):
    monkeypatch.setattr(rad.cfg, "RAD_FEATURE_SOURCE", "both", raising=False)
    monkeypatch.setattr(rad.cfg, "DO_ICC_FILTER", False, raising=False)
    monkeypatch.setattr(rad.cfg, "ENABLE_CORR_FILTER", False, raising=False)
    monkeypatch.setattr(rad.cfg, "RAD_LOGIT_UNI_P", 0.2, raising=False)
    monkeypatch.setattr(rad.cfg, "RAD_LOGIT_MV_P", 0.2, raising=False)
    monkeypatch.setattr(rad.cfg, "DO_FDR", False, raising=False)
    monkeypatch.setattr(rad.cfg, "DO_FWER", False, raising=False)
    monkeypatch.setattr(rad.cfg, "DO_RAD_FIRTH", False, raising=False)
    monkeypatch.setattr(rad.cfg, "RAD_SCORE_LINEAR", False, raising=False)


def _blocks_result(groups):
    return rad.RadiomicsResult(
        features=[], coefs=np.array([]), radscore=None, intercept=None,
        betas=None, ols=None, lambda_used=float("nan"), formula_df=None,
        formula_text=None, method="blocks", groups=groups, select_source="blocks",
    )


def test_blocks_logistic_emits_uni_and_multi_tables(base_cfg):
    df, y = _frame()
    child = rad.run_radiomics_logistic(
        df, y, score_name="RadScore_CTA", group_name="CTA",
    )
    html = _radiomics_logistic_html(_blocks_result([child]), "train")
    assert "Radiomics logistic — CTA" in html
    assert "Radiomics Univariate Logistic" in html
    assert "Radiomics Multivariate Logistic (Train — candidate pool)" in html


def test_blocks_lasso_children_stay_silent(base_cfg):
    lasso_child = SimpleNamespace(
        method="lasso", group_name="US", score_name="RadScore_US",
        features=["f1"], uni_res=None, mv_df=None, mv_selected=None,
        firth_uni=None, firth_mv=None, formula_text=None,
    )
    assert _radiomics_logistic_html(_blocks_result([lasso_child]), "train") == ""


def test_panel_gate_accepts_block_results():
    """_build_panel 的闸门必须放行 method=blocks 的结果，否则整段组学 Logistic 会消失。"""
    assert 'str(getattr(rad_res, "method", "") or "") == "logistic" or rad_groups' in REPORT_SRC
    assert "_radiomics_logistic_html(rad_res, panel_label)" in REPORT_SRC
