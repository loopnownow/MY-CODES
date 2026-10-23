"""Radiomics univariate FDR/FWER adjustment and logistic candidate gating."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from modules.stats.assoc import bh_fdr, holm_fwer
from modules.stats import radiomics as rad


def _uni_df():
    return pd.DataFrame(
        {
            "Feature": ["a", "b", "c", "d"],
            "OR": [2.0, 1.5, 1.2, 1.0],
            "CI_low": [1.1, 0.9, 0.8, 0.5],
            "CI_high": [3.0, 2.5, 1.8, 2.0],
            "p": [0.01, 0.04, 0.03, 0.20],
        }
    )


@pytest.fixture
def frame():
    return pd.DataFrame({c: np.arange(4.0) for c in "abcd"})


def test_attach_rad_uni_adjust_matches_bh_holm(monkeypatch):
    monkeypatch.setattr(rad.cfg, "DO_FDR", True, raising=False)
    monkeypatch.setattr(rad.cfg, "DO_FWER", True, raising=False)
    monkeypatch.setattr(rad.cfg, "RAD_LOGIT_UNI_P", 0.05, raising=False)
    src = _uni_df()
    out = rad._attach_rad_uni_adjust(src)
    p = src["p"].tolist()
    assert list(out["FDR P"]) == pytest.approx(list(bh_fdr(p)))
    assert list(out["FWER P"]) == pytest.approx(list(holm_fwer(p)))
    assert out.loc[0, "Pass FDR"] == "Y"
    assert out.loc[1, "Pass FDR"] == "N"
    assert out.loc[2, "Pass FDR"] == "N"
    assert out.loc[3, "Pass FDR"] == "N"
    assert out.loc[0, "Pass FWER"] == "Y"
    assert out.loc[1, "Pass FWER"] == "N"


def test_attach_skipped_when_switches_off(monkeypatch):
    monkeypatch.setattr(rad.cfg, "DO_FDR", False, raising=False)
    monkeypatch.setattr(rad.cfg, "DO_FWER", False, raising=False)
    out = rad._attach_rad_uni_adjust(_uni_df())
    assert "FDR P" not in out.columns
    assert "FWER P" not in out.columns


def test_candidates_raw_p_when_corrections_off(frame, monkeypatch):
    monkeypatch.setattr(rad.cfg, "DO_FDR", False, raising=False)
    monkeypatch.setattr(rad.cfg, "DO_FWER", False, raising=False)
    got = rad._rad_logit_candidates(_uni_df(), frame, 0.1, set())
    assert got == ["a", "b", "c"]


def test_candidates_fdr_only(frame, monkeypatch):
    monkeypatch.setattr(rad.cfg, "DO_FDR", True, raising=False)
    monkeypatch.setattr(rad.cfg, "DO_FWER", False, raising=False)
    monkeypatch.setattr(rad.cfg, "RAD_LOGIT_UNI_P", 0.1, raising=False)
    adj = rad._attach_rad_uni_adjust(_uni_df())
    got = rad._rad_logit_candidates(adj, frame, 0.1, set())
    # BH q ≈ 0.04, 0.0533, 0.0533, 0.20
    assert got == ["a", "b", "c"]


def test_candidates_and_rule_both_on(frame, monkeypatch):
    monkeypatch.setattr(rad.cfg, "DO_FDR", True, raising=False)
    monkeypatch.setattr(rad.cfg, "DO_FWER", True, raising=False)
    monkeypatch.setattr(rad.cfg, "RAD_LOGIT_UNI_P", 0.05, raising=False)
    adj = rad._attach_rad_uni_adjust(_uni_df())
    got = rad._rad_logit_candidates(adj, frame, 0.05, set())
    assert got == ["a"]


def test_candidates_and_stricter_than_fdr_alone(frame, monkeypatch):
    """α=0.08: FDR passes a,b,c; Holm passes only a (0.04) and maybe b,c (0.09>0.08)."""
    monkeypatch.setattr(rad.cfg, "DO_FDR", True, raising=False)
    monkeypatch.setattr(rad.cfg, "DO_FWER", True, raising=False)
    monkeypatch.setattr(rad.cfg, "RAD_LOGIT_UNI_P", 0.08, raising=False)
    adj = rad._attach_rad_uni_adjust(_uni_df())
    both = rad._rad_logit_candidates(adj, frame, 0.08, set())
    monkeypatch.setattr(rad.cfg, "DO_FWER", False, raising=False)
    adj_f = rad._attach_rad_uni_adjust(_uni_df())
    fdr_only = rad._rad_logit_candidates(adj_f, frame, 0.08, set())
    assert both == ["a"]
    assert fdr_only == ["a", "b", "c"]
    assert set(both).issubset(set(fdr_only))
