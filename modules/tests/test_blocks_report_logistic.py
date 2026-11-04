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


def _logit_child(score_fit: str = "logit"):
    return SimpleNamespace(
        method="logistic", group_name="CTA", score_name="RadScore_CTA",
        features=["f1"], score_fit=score_fit,
        formula_text="<b>RadScore_CTA</b> = 1.0 + 2.0 · <i>f1</i>",
        uni_res=None, mv_df=None, mv_selected=None, firth_uni=None, firth_mv=None,
        ols=None,
    )


def test_formula_hidden_when_no_linear_fit():
    """关掉线性拟合时结果页不出 RadScore Formula（分数照旧用于下游 ROC）。"""
    from modules.stats.report import _radiomics_formula_html

    assert _radiomics_formula_html(_blocks_result([_logit_child("logit")])) == ""
    single = _logit_child("logit")
    assert _radiomics_formula_html(single) == ""


def test_formula_and_ols_table_shown_when_linear_fit_on():
    import statsmodels.api as sm

    from modules.stats.report import _radiomics_formula_html

    df = pd.DataFrame({"const": 1.0, "f1": [0.0, 1.0, 2.0, 3.0]})
    y = pd.Series([0.0, 0.2, 0.8, 1.0])
    fit = sm.OLS(y, df).fit()
    child = _logit_child("ols")
    child.ols = fit
    html = _radiomics_formula_html(_blocks_result([child]))
    assert "RadScore Formula" in html
    assert "RadScore Linear Fit (OLS) — RadScore_CTA" in html
    assert "linear regression (OLS)" in html
