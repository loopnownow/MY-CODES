"""ROC 图 / ROC 表 / DCA 图例里的组学分数展示名：单特征用特征名，不用块名。"""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")

from types import SimpleNamespace  # noqa: E402

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from modules.stats import curves_dca as dca  # noqa: E402
from modules.stats import curves_roc as roc  # noqa: E402
from modules.stats import radiomics as rad  # noqa: E402


def _res(features, score_name="CTA", method="logistic"):
    return rad.RadiomicsResult(
        features=list(features), coefs=np.array([1.0] * len(features)),
        radscore=np.zeros(3), intercept=0.0, betas=None, ols=None,
        lambda_used=float("nan"), formula_df=None, formula_text=None,
        method=method, score_name=score_name,
    )


def _xy(n=80, seed=0):
    rng = np.random.RandomState(seed)
    x = rng.normal(size=n)
    y = (x + rng.normal(scale=0.4, size=n) > 0).astype(int)
    if y.min() == y.max():
        y[0] = 1 - int(y[0])
    return x, y


def test_single_feature_score_uses_feature_name():
    rad.SCORE_DISPLAY.clear()
    rad.register_score_display(_res(["Eccentricity_index"]))
    assert rad.score_display_name("CTA") == "Eccentricity_index"
    # 非组学模型名不受影响
    assert rad.score_display_name("Clinical") == "Clinical"


def test_multi_feature_score_keeps_score_name():
    rad.SCORE_DISPLAY.clear()
    rad.register_score_display(_res(["f1", "f2"]))
    assert rad.score_display_name("CTA") == "CTA"


def test_grouped_blocks_register_per_block():
    rad.SCORE_DISPLAY.clear()
    res = SimpleNamespace(groups=[
        SimpleNamespace(score_name="RadScore_CTA", features=["Eccentricity_index"]),
        SimpleNamespace(score_name="RadScore_US", features=["Plaque_thickness_mm"]),
        SimpleNamespace(score_name="RadScore_MRI", features=["a", "b"]),
    ])
    rad.register_score_display(res)
    assert rad.score_display_name("RadScore_CTA") == "Eccentricity_index"
    assert rad.score_display_name("RadScore_US") == "Plaque_thickness_mm"
    assert rad.score_display_name("RadScore_MRI") == "RadScore_MRI"


def test_roc_table_uses_feature_name(monkeypatch):
    monkeypatch.setattr(roc.cfg, "DO_ROC", False, raising=False)
    monkeypatch.setattr(roc.cfg, "ROC_PLOT_EXCLUDE", [], raising=False)
    monkeypatch.setattr(roc.cfg, "BOOTSTRAP_N", 20, raising=False)
    rad.SCORE_DISPLAY.clear()
    rad.SCORE_DISPLAY["CTA"] = "Eccentricity_index"
    x, y = _xy()
    roc.ROC_LOCK.clear()
    roc.ROC_METRICS.clear()
    roc.plot_roc({"CTA": x, "LDL": x}, y, dataset_label="train", seed=0, val_mode="refit")
    models = list(roc.ROC_METRICS["roc_train"]["Model"])
    assert "Eccentricity_index" in models
    assert "CTA" not in models


def test_dca_legend_uses_feature_name(monkeypatch):
    monkeypatch.setattr(dca.cfg, "save_plot", lambda *a, **k: False, raising=False)
    rad.SCORE_DISPLAY.clear()
    rad.SCORE_DISPLAY["CTA"] = "Eccentricity_index"
    x, y = _xy()
    prob = 1.0 / (1.0 + np.exp(-x))
    fig = dca.plot_dca({"CTA": prob, "Clinical": prob}, y, dataset_label="train")
    texts = [t.get_text() for t in fig.axes[0].get_legend().get_texts()]
    assert any("Eccentricity_index" in t for t in texts)
    assert "CTA" not in [t.strip() for t in texts]
