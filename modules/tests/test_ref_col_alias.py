"""B4: generic ref_col / corr_ref_tables dict, with deprecated aliases."""
from __future__ import annotations

import warnings
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from modules.stats.correlation import check_correlation
from modules.stats.report import generate_results_html
from modules import config as cfg


def _layer():
    age = np.linspace(50, 80, 30)
    score = age + 1
    return SimpleNamespace(
        clin=pd.DataFrame({"Age (years)": age, "Score": score}),
        label=np.zeros(30),
        group=pd.Series(["MCI"] * 30),
        rad=pd.DataFrame(),
    )


def test_ref_col_and_deprecated_egfr_col_alias(monkeypatch):
    monkeypatch.setattr(cfg, "BOOTSTRAP_N", 20, raising=False)
    layer = _layer()
    got = check_correlation(layer, ref_col="Age", targets=["Score"], nboot=20, seed=1)
    assert got.loc[0, "ref"] == "Age"
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DeprecationWarning)
        alias = check_correlation(layer, egfr_col="Age", targets=["Score"], nboot=20, seed=1)
    assert alias.loc[0, "ref"] == "Age"
    assert any(issubclass(w.category, DeprecationWarning) for w in caught)
    assert np.allclose(got["spearman_rho"], alias["spearman_rho"], equal_nan=True)
    with pytest.raises(TypeError, match="differ"):
        check_correlation(layer, ref_col="Age", egfr_col="Score", targets=["Score"], nboot=20, seed=1)


def test_generate_results_html_accepts_corr_ref_tables_dict(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "DRY_RUN", False, raising=False)
    monkeypatch.setattr(cfg, "DO_HEATMAP", False, raising=False)
    monkeypatch.setattr(cfg, "DO_NRI", False, raising=False)
    monkeypatch.setattr(cfg, "DO_CORR", True, raising=False)
    monkeypatch.setattr(cfg, "DO_PARTIAL", False, raising=False)
    monkeypatch.setattr(cfg, "GROUP_KEEP", [], raising=False)
    layer = SimpleNamespace()
    data = SimpleNamespace(all=layer, train=layer, test=layer)
    corr = pd.DataFrame({
        "predictor": ["Score"],
        "ref": ["Age"],
        "spearman_rho": [0.5],
        "p_value": [0.01],
        "ci_lower": [0.2],
        "ci_upper": [0.7],
    })
    html = generate_results_html(
        data,
        {"clin": SimpleNamespace(table1=pd.DataFrame()), "rad": None},
        corr_ref_tables={"Age": corr},
        out_dir=str(tmp_path),
    )
    assert "Correlation with reference variables" in html
    assert "Age" in html
