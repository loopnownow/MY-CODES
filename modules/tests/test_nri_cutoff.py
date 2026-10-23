"""R-05/06: NRI cutoffs and locked-probability path."""
from __future__ import annotations

import numpy as np

from modules.config.outcome import inherited_nri_old_is_explicit
from modules.pipeline import nri_old_arms
from modules.stats.nri_cir import _normalize_nri_cutoff, nri_from_probs


def test_three_class_cutoff():
    arr = _normalize_nri_cutoff((0.0, 0.3, 0.7, 1.0))
    assert arr.size == 4
    assert int(arr.size - 2) == 2


def test_two_bin_cutoff_still_runs_but_is_not_three_class():
    arr = _normalize_nri_cutoff((0.0, 0.5, 1.0))
    assert int(arr.size - 2) == 1


def test_nri_from_locked_probs():
    rng = np.random.RandomState(3)
    n = 80
    y = rng.binomial(1, 0.4, n)
    p_old = np.clip(0.2 + 0.3 * y + rng.normal(0, 0.05, n), 0.01, 0.99)
    p_new = np.clip(0.15 + 0.5 * y + rng.normal(0, 0.05, n), 0.01, 0.99)
    res = nri_from_probs(
        p_old, p_new, y, cutoff=(0.0, 0.3, 0.7, 1.0), n_boot=50, seed=1,
        apparent=False, layer="test",
    )
    assert res["n_risk_bins"] == 3
    assert res["layer"] == "test"
    assert res["apparent"] is False
    assert res["bootstrap"] == "locked_predictions"
    assert np.isfinite(res["NRI_total"])


def test_reader_pair_splits_and_clinical_columns_stay_one():
    assert nri_old_arms(
        ["Reader_1", "Reader_2"], readers=["Reader_1", "Reader_2"],
    ) == [["Reader_1"], ["Reader_2"]]
    assert nri_old_arms(
        ["lesion_volume_ml", "number_of_lesions"], readers=[],
    ) == [["lesion_volume_ml", "number_of_lesions"]]
    assert nri_old_arms(["clinical"], readers=[]) == [["clinical"]]


def test_explicit_public_nri_old_is_kept_when_outcome_uses_default():
    assert inherited_nri_old_is_explicit(["Reader_1", "Reader_2"]) is True
    assert inherited_nri_old_is_explicit(["clinical"]) is False
    assert inherited_nri_old_is_explicit([]) is False


def test_reader2_section_follows_reader1(monkeypatch):
    from modules import config as cfg
    from modules.stats import report as report_mod

    # Patch the cfg object report actually closes over (survives module reloads).
    monkeypatch.setattr(report_mod, "cfg", cfg, raising=False)
    monkeypatch.setattr(cfg, "DO_NRI", True, raising=False)
    monkeypatch.setattr(cfg, "READER_COMPARE_COL", "Reader_1", raising=False)

    def block(label, total):
        return {
            "old_label": label,
            "chord_file": "",
            "idi_file": "",
            "nri": {
                "NRI_events": total,
                "NRI_nonevents": 0.1,
                "NRI_total": total,
                "NRI_events_CI_low": 0.0,
                "NRI_events_CI_high": 1.0,
                "NRI_nonevents_CI_low": 0.0,
                "NRI_nonevents_CI_high": 1.0,
                "NRI_total_CI_low": 0.0,
                "NRI_total_CI_high": 1.0,
                "layer": "test+refit",
                "apparent": True,
                "cutoff": [0, 0.3, 0.7, 1],
                "group_labels": {0: "Well-differentiated", 1: "Dedifferentiated"},
            },
            "idi": None,
        }

    html = report_mod._nri_idi_section_html(
        {
            "nri_blocks": [block("Reader_1", -0.04), block("Reader_2", 0.2)],
            "group_labels": {},
        },
        ".",
    )
    i1 = html.find("F. NRI / IDI (Reader_1 → Combined)")
    i2 = html.find("F. NRI / IDI (Reader_2 → Combined)")
    assert i1 >= 0 and i2 > i1


def test_roc_extra_names_fall_back_to_rad_sheet():
    from types import SimpleNamespace

    import pandas as pd

    from modules.pipeline import _vectors_for_roc_names

    layer = SimpleNamespace(
        clin=pd.DataFrame({"age": [1.0, 2.0, 3.0]}),
        rad=pd.DataFrame({"Left STG_Vgm": [0.1, 0.4, 0.2]}),
        label=np.array([0, 1, 0]),
    )
    got = _vectors_for_roc_names(layer, ["age", "Left STG_Vgm", "missing"])
    assert list(got) == ["age", "Left STG_Vgm"]
    assert got["Left STG_Vgm"].tolist() == [0.1, 0.4, 0.2]
    short = SimpleNamespace(
        clin=pd.DataFrame({"age": [1.0, 2.0, 3.0]}),
        rad=pd.DataFrame({"Left STG_Vgm": [0.1, 0.4]}),
        label=np.array([0, 1, 0]),
    )
    assert "Left STG_Vgm" not in _vectors_for_roc_names(short, ["Left STG_Vgm"])
