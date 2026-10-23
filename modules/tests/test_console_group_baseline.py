"""总表多组列、VS 门控、文案、悬停说明、强制计数手写框。"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from modules import config as cfg
from modules.config.defaults import DEFAULT_SETTINGS, merged_settings
from modules.console.console import LABELS, HINTS, build_schema
from modules.stats import data_io as dio
from modules.stats.clinical import (
    _anova_lsd,
    _baseline_display_names,
    _build_table1_by_groups,
    baseline_include_vs,
)
from modules.stats.data_io import _baseline_split_ids, _collapse_extra_groups_for_model

ROOT = Path(__file__).resolve().parents[2]
UI = (ROOT / "modules" / "console" / "console_ui.js").read_text(encoding="utf-8")
HTML = (ROOT / "modules" / "console" / "console.html").read_text(encoding="utf-8")
CSS = (ROOT / "modules" / "console" / "console.css").read_text(encoding="utf-8")


def test_labels_are_zongbiao_and_table1():
    assert LABELS["DO_BASELINE_ALL"] == "总表"
    assert LABELS["DO_BASELINE_TRAIN_TEST"] == "Table 1"
    assert "总表" in HTML
    assert "> Table 1<" in HTML or ">Table 1</label>" in HTML
    assert "Baseline — All" not in HTML
    assert "Train vs Test</label>" not in HTML


def test_pair_mode_zongbiao_keeps_all_levels_and_lsd(monkeypatch):
    monkeypatch.setattr(cfg, "GROUP_KEEP", ["MCI", "CN-ESRD"], raising=False)
    monkeypatch.setattr(cfg, "PAIRWISE_ALL", False, raising=False)
    monkeypatch.setattr(cfg, "POSITIVE_GROUP_LABEL", "MCI", raising=False)
    monkeypatch.setattr(cfg, "GROUP_DISPLAY_POS", "MCI", raising=False)
    monkeypatch.setattr(cfg, "GROUP_DISPLAY_NEG", "CN", raising=False)
    monkeypatch.setattr(
        cfg, "GROUP_LEVEL_DISPLAY", {"HC": "CN", "CN-ESRD": "CN"}, raising=False,
    )
    assert baseline_include_vs() is False
    # 展示名冲突时总表用原始水平名，不把 HC 并进 CN
    assert _baseline_display_names(["MCI", "CN-ESRD", "HC"]) == ["MCI", "CN-ESRD", "HC"]

    rng = np.random.RandomState(0)
    n = 30
    groups = (["MCI"] * 10) + (["CN-ESRD"] * 10) + (["HC"] * 10)
    clin = pd.DataFrame({
        "Age": np.concatenate([
            rng.normal(70, 5, 10), rng.normal(60, 5, 10), rng.normal(55, 5, 10),
        ]),
        "Sex": ([0, 1] * 5) + ([0, 1] * 5) + ([0, 1] * 5),
    })
    dio.CLIN_VAR_TYPES = {"Age": "continuous", "Sex": "categorical"}
    dio.CLIN_LEVEL_MAPS = {"Sex": {0.0: "F", 1.0: "M"}}
    tbl = _build_table1_by_groups(clin, pd.Series(groups), include_vs=False)
    cols = [str(c) for c in tbl.columns]
    assert any(c.startswith("MCI=") for c in cols)
    assert any(c.startswith("CN-ESRD=") for c in cols)
    assert any(c.startswith("HC=") for c in cols)
    # 选两组也不关掉两两列
    assert "MCI vs CN-ESRD" in cols
    assert "MCI vs HC" in cols
    assert "CN-ESRD vs HC" in cols
    # 缺水平不造列（无 HC 行时不得出现 HC=）
    tbl2 = _build_table1_by_groups(
        clin.iloc[:20], pd.Series(groups[:20]), include_vs=False,
    )
    cols2 = [str(c) for c in tbl2.columns]
    assert any(c.startswith("MCI=") for c in cols2)
    # 仅两水平且无冲突时 CN-ESRD 可显示为映射名 CN
    assert any(c.startswith("CN-ESRD=") or c.startswith("CN=") for c in cols2)
    assert not any(c.startswith("HC=") for c in cols2)
    assert not any(" vs " in c for c in cols2)


def test_binary_codes_use_display_names(monkeypatch):
    monkeypatch.setattr(cfg, "GROUP_KEEP", [], raising=False)
    monkeypatch.setattr(cfg, "PAIRWISE_ALL", False, raising=False)
    monkeypatch.setattr(cfg, "POSITIVE_GROUP_LABEL", "Positive", raising=False)
    monkeypatch.setattr(cfg, "GROUP_DISPLAY_POS", "Growth", raising=False)
    monkeypatch.setattr(cfg, "GROUP_DISPLAY_NEG", "Non-growth", raising=False)
    monkeypatch.setattr(
        cfg, "GROUP_LEVEL_DISPLAY",
        {"Positive": "Growth", "Negative": "Non-growth"},
        raising=False,
    )
    assert _baseline_display_names(["0.0", "1.0"]) == ["Non-growth", "Growth"]
    clin = pd.DataFrame({"Age": [50, 51, 52, 60, 61, 62]})
    dio.CLIN_VAR_TYPES = {"Age": "continuous"}
    dio.CLIN_LEVEL_MAPS = {}
    tbl = _build_table1_by_groups(
        clin, pd.Series(["0.0", "0.0", "0.0", "1.0", "1.0", "1.0"]),
    )
    cols = [str(c) for c in tbl.columns]
    assert any(c.startswith("Growth=") for c in cols)
    assert any(c.startswith("Non-growth=") for c in cols)
    assert not any(c.startswith("0.0=") or c.startswith("1.0=") for c in cols)
    growth_i = next(i for i, c in enumerate(cols) if c.startswith("Growth="))
    nongrowth_i = next(i for i, c in enumerate(cols) if c.startswith("Non-growth="))
    assert growth_i < nongrowth_i


def test_all_mode_zongbiao_has_vs(monkeypatch):
    monkeypatch.setattr(cfg, "GROUP_KEEP", [], raising=False)
    monkeypatch.setattr(cfg, "PAIRWISE_ALL", False, raising=False)
    monkeypatch.setattr(cfg, "POSITIVE_GROUP_LABEL", "MCI", raising=False)
    monkeypatch.setattr(cfg, "GROUP_DISPLAY_POS", "", raising=False)
    monkeypatch.setattr(cfg, "GROUP_DISPLAY_NEG", "", raising=False)
    monkeypatch.setattr(cfg, "GROUP_LEVEL_DISPLAY", {}, raising=False)
    assert baseline_include_vs() is True
    groups = (["MCI"] * 8) + (["CN-ESRD"] * 8) + (["HC"] * 8)
    clin = pd.DataFrame({"Age": list(range(24))})
    dio.CLIN_VAR_TYPES = {"Age": "continuous"}
    dio.CLIN_LEVEL_MAPS = {}
    tbl = _build_table1_by_groups(clin, pd.Series(groups), include_vs=True)
    cols = [str(c) for c in tbl.columns]
    assert any(" vs " in c for c in cols)
    assert "MCI vs CN-ESRD" in cols or any(c == "MCI vs CN-ESRD" for c in cols)


def test_allpairs_mode_no_vs_flag(monkeypatch):
    monkeypatch.setattr(cfg, "PAIRWISE_ALL", True, raising=False)
    monkeypatch.setattr(cfg, "GROUP_KEEP", ["MCI", "HC", "CN-ESRD"], raising=False)
    monkeypatch.setattr(cfg, "POSITIVE_GROUP_LABEL", "", raising=False)
    monkeypatch.setattr(cfg, "GROUP_LEVEL_DISPLAY", {}, raising=False)
    monkeypatch.setattr(cfg, "GROUP_DISPLAY_POS", "", raising=False)
    monkeypatch.setattr(cfg, "GROUP_DISPLAY_NEG", "", raising=False)
    # 模式标记仍表示「不是全部组」，但表不再看这个标记。
    assert baseline_include_vs() is False
    groups = (["MCI"] * 6) + (["CN-ESRD"] * 6) + (["HC"] * 6)
    clin = pd.DataFrame({"Age": list(range(18))})
    dio.CLIN_VAR_TYPES = {"Age": "continuous"}
    dio.CLIN_LEVEL_MAPS = {}
    tbl = _build_table1_by_groups(clin, pd.Series(groups), include_vs=False)
    cols = [str(c) for c in tbl.columns]
    assert "MCI vs HC" in cols
    assert "±" in str(tbl.loc[tbl["Variable"].eq("Age"), "MCI=6"].iloc[0])


def test_hover_hints_not_resident_body():
    assert "forceCountTitleHtml" not in UI
    assert 'data-tip="' in UI
    assert "sec-body-hint" not in UI or UI.count("sec-body-hint") == 0
    assert "display:none" in CSS
    assert "#grpCard > p.hint#grpHint{display:none}" in CSS.replace(" ", "") or (
        "#grpCard > p.hint#grpHint" in CSS and "display:none" in CSS
    )
    assert 'titleKeys = ["DO_ROC"' in UI or 'titleKeys = ["DO_ROC", "DO_LANDMARK_ROC"' in UI
    assert '["DO_UNI_LOGISTIC_TABLE", "DO_MV_LOGISTIC_TABLE", "DO_FIRTH"]' in UI
    assert 'titleKeys = ["DO_CORR", "DO_PARTIAL"]' in UI
    assert 'titleKeys = ["DO_DAG", "DO_SHAP", "DO_MEDIATION"]' in UI
    assert 'titleKeys = ["DO_FALSE_CLASS", "DO_FDR", "DO_FWER"]' in UI
    assert 'titleKeys = ["DO_ICC_FILTER", "ICC_REQUIRE_ROW", "ENABLE_CORR_FILTER", "LASSO_CACHE"]' in UI
    assert 'titleKeys = ["LASSO_PLOT_PATH"]' in UI
    assert 'titleKeys = ["DO_PLOT_TIFF", "DO_PLOT_PDF"]' in UI
    assert 'titleKeys = ["DO_RAD_FIRTH"]' in UI
    assert "btnCopyLog" in HTML
    assert "复制运行日志" in HTML
    api_js = (ROOT / "modules" / "console" / "console_api.js").read_text(encoding="utf-8")
    assert "data-open-folder" not in api_js
    assert 'name="resultPick"' in api_js
    assert "title-gap" in CSS


def test_force_count_textbox_overrides_default(monkeypatch):
    assert DEFAULT_SETTINGS["ORDINAL_MAX_LEVELS"] == 5
    got = merged_settings({})
    assert got["CLIN_FORCE_CATEGORICAL"] == []
    fields = build_schema({}, clin_cols=["Age", "Score", "Flag"])
    by = {f["key"]: f for f in fields}
    assert by["CLIN_FORCE_CATEGORICAL"]["widget"] == "checklist"
    assert by["CLIN_FORCE_CONTINUOUS"]["widget"] == "checklist"
    assert set(by["CLIN_FORCE_CATEGORICAL"]["option_pool"]) >= {"Age", "Score", "Flag"}
    assert set(by["CLIN_FORCE_CONTINUOUS"]["option_pool"]) >= {"Age", "Score", "Flag"}
    assert by["CLIN_FORCE_CATEGORICAL"]["section"] == "clinprep"
    assert by["ORDINAL_MAX_LEVELS"]["section"] == "clinprep"
    assert by["ORDINAL_MAX_LEVELS"]["widget"] == "number"
    assert by["ORDINAL_MAX_LEVELS"]["placeholder"] == "5"
    assert LABELS["CLIN_FORCE_CATEGORICAL"] == "强制计数"
    assert LABELS["ORDINAL_MAX_LEVELS"] == "计数/计量等级"
    assert "CLIN_FORCE_CATEGORICAL" in HINTS["CLIN_FORCE_CATEGORICAL"] or "强制" in HINTS["CLIN_FORCE_CATEGORICAL"]
    assert "ORDINAL_MAX_LEVELS" in UI
    assert "num-step" not in UI
    assert "data-numstep-key" not in UI
    assert "num-step" not in CSS
    assert "forceCountTitleHtml" not in UI
    assert 'id === "clinprep" || id === "clin"' not in UI
    assert "clinprep-body" in UI
    assert "clinprep-body" in CSS
    assert "repeat(4,minmax(0,1fr))" in CSS
    assert '[data-sec="clinprep"] .sec-body.clinprep-body{grid-template-columns:1fr}' not in CSS.replace(" ", "")

    monkeypatch.setattr(cfg, "ORDINAL_MAX_LEVELS", 5, raising=False)
    monkeypatch.setattr(cfg, "CLIN_FORCE_CATEGORICAL", [], raising=False)
    monkeypatch.setattr(cfg, "CLIN_FORCE_CONTINUOUS", [], raising=False)
    dio.CLIN_LEVEL_MAPS = {}
    dio.CLIN_ORDINAL_TOKEN_COLS = set()
    s6 = pd.Series([1, 2, 3, 4, 5, 6, 1, 2])
    assert dio.classify_clin_series("Score", s6) == "continuous"
    s5 = pd.Series([1, 2, 3, 4, 5, 1, 2])
    assert dio.classify_clin_series("Grade", s5) == "categorical"
    s3 = pd.Series([0, 1, 2, 0, 1, 2])
    assert dio.classify_clin_series("Flag", s3) == "categorical"
    monkeypatch.setattr(cfg, "ORDINAL_MAX_LEVELS", 6, raising=False)
    assert dio.classify_clin_series("Score", s6) == "categorical"
    monkeypatch.setattr(cfg, "ORDINAL_MAX_LEVELS", 5, raising=False)
    monkeypatch.setattr(cfg, "CLIN_FORCE_CATEGORICAL", ["Score"], raising=False)
    assert dio.classify_clin_series("Score", s6) == "categorical"
    monkeypatch.setattr(cfg, "CLIN_FORCE_CATEGORICAL", [], raising=False)
    monkeypatch.setattr(cfg, "CLIN_FORCE_CONTINUOUS", ["Flag"], raising=False)
    assert dio.classify_clin_series("Flag", s3) == "continuous"


def test_hydrate_rejects_numeric_force_categorical(monkeypatch):
    from modules.console.console import hydrate_console_settings

    with pytest.raises(ValueError, match="CLIN_FORCE_CATEGORICAL"):
        hydrate_console_settings({"CLIN_FORCE_CATEGORICAL": ["5"]})
    got = hydrate_console_settings({"ORDINAL_MAX_LEVELS": "", "CLIN_FORCE_CATEGORICAL": []})
    assert got["ORDINAL_MAX_LEVELS"] == 5
    with pytest.raises(ValueError, match="ORDINAL_MAX_LEVELS"):
        hydrate_console_settings({"ORDINAL_MAX_LEVELS": 0})
    monkeypatch.setattr(cfg, "ORDINAL_MAX_LEVELS", "x", raising=False)
    with pytest.raises((ValueError, TypeError)):
        dio._ordinal_max_levels()
    monkeypatch.setattr(cfg, "ORDINAL_MAX_LEVELS", "", raising=False)
    assert dio._ordinal_max_levels() == 5


def test_three_groups_anova_and_lsd_ignore_mode(monkeypatch):
    monkeypatch.setattr(cfg, "GROUP_KEEP", ["MCI", "CN-ESRD"], raising=False)
    monkeypatch.setattr(cfg, "PAIRWISE_ALL", False, raising=False)
    monkeypatch.setattr(cfg, "POSITIVE_GROUP_LABEL", "MCI", raising=False)
    monkeypatch.setattr(cfg, "GROUP_DISPLAY_POS", "MCI", raising=False)
    monkeypatch.setattr(cfg, "GROUP_DISPLAY_NEG", "CN", raising=False)
    monkeypatch.setattr(cfg, "GROUP_LEVEL_DISPLAY", {}, raising=False)
    age = np.array([1, 2, 3, 2, 3, 4, 5, 6, 7], dtype=float)
    groups = ["MCI", "MCI", "MCI", "CN-ESRD", "CN-ESRD", "CN-ESRD", "HC", "HC", "HC"]
    dio.CLIN_VAR_TYPES = {"Age": "continuous", "Sex": "categorical"}
    dio.CLIN_LEVEL_MAPS = {"Sex": {0.0: "F", 1.0: "M"}}
    clin = pd.DataFrame({
        "Age": age,
        "Sex": [0, 1, 0, 1, 0, 1, 0, 1, 0],
    })
    tbl = _build_table1_by_groups(clin, pd.Series(groups), include_vs=False)
    age_row = tbl.loc[tbl["Variable"].eq("Age")].iloc[0]
    chunks = [np.array([1, 2, 3], float), np.array([2, 3, 4], float), np.array([5, 6, 7], float)]
    p_anova, lsd = _anova_lsd(chunks)
    assert age_row["P value"] == ("<0.001" if p_anova < 0.001 else f"{p_anova:.3f}")
    assert "±" in str(age_row["MCI=3"])
    assert "[" not in str(age_row["MCI=3"])
    # 顺序：阳性 MCI，然后出现顺序 CN-ESRD、HC → 下标 (0,2) 是 MCI vs HC
    mci_hc = lsd[(0, 2)]
    assert age_row["MCI vs HC"] == ("<0.001" if mci_hc < 0.001 else f"{mci_hc:.3f}")
    sex_row = tbl.loc[tbl["Variable"].eq("Sex")].iloc[0]
    assert str(sex_row["MCI vs HC"]).strip() not in ("", "nan", "None")


def test_collapse_and_baseline_split_ids(monkeypatch):
    monkeypatch.setattr(cfg, "POSITIVE_GROUP_LABEL", "MCI", raising=False)
    monkeypatch.setattr(cfg, "GROUP_DISPLAY_NEG", "CN", raising=False)
    raw = pd.DataFrame({"Group": ["MCI", "HC", "CN-ESRD", "MCI"], "Age": [1, 2, 3, 4]})
    out = _collapse_extra_groups_for_model(raw)
    assert raw["Group"].tolist() == ["MCI", "HC", "CN-ESRD", "MCI"]
    assert out["Group"].tolist() == ["MCI", "CN", "CN", "MCI"]
    monkeypatch.setattr(cfg, "GROUP_DISPLAY_NEG", "HC", raising=False)
    out2 = _collapse_extra_groups_for_model(raw)
    assert set(out2["Group"]) == {"MCI", "non_MCI"}
    train_ids, test_ids = _baseline_split_ids(
        [10, 11, 12, 13], [0, 1], [2, 3], [4, 5, 6], 0.7, 1, True,
    )
    assert train_ids & test_ids == set()
    assert {10, 11} <= train_ids
    assert {12, 13} <= test_ids
    assert (train_ids | test_ids) == {10, 11, 12, 13, 4, 5, 6}
    assert len([i for i in (4, 5, 6) if i in train_ids]) == 2
    train_only, test_none = _baseline_split_ids(
        [0, 1], [0, 1], [], [2], 0.7, 1, False,
    )
    assert 2 in train_only and not test_none
