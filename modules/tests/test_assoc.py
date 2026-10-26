"""ANCOVA / Group OLS / partial correlation / Firth / multiple-comparison switches."""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from modules.config.ini_io import INI_SECTIONS
from modules.console.console import SECTION_DEFS, SECTION_OF, build_schema
from modules.stats.assoc import (
    bh_fdr,
    fit_outcomes,
    holm_fwer,
    partial_corr,
    tables_from_fits,
)
from modules.stats.clinical import firth_logit


def test_bh_and_holm_known_values():
    p = [0.01, 0.04, 0.03, 0.20]
    fdr = bh_fdr(p)
    assert fdr[0] == pytest.approx(0.04)
    assert fdr[2] == pytest.approx(0.04 * 4 / 3, rel=1e-6) or fdr[2] == pytest.approx(0.053333, rel=1e-3)
    assert fdr[1] == pytest.approx(fdr[2])
    assert fdr[3] == pytest.approx(0.20)
    holm = holm_fwer(p)
    assert holm[0] == pytest.approx(0.04)
    assert holm[2] == pytest.approx(0.09)
    assert holm[1] == pytest.approx(0.09)
    assert holm[3] == pytest.approx(0.20)
    assert bh_fdr([np.nan, 0.02])[1] == pytest.approx(0.02)


def test_binary_group_beta_matches_shift():
    rng = np.random.default_rng(1)
    n = 80
    group = np.array(["HC"] * 40 + ["SSD"] * 40)
    age = rng.normal(50, 8, n)
    y = 1.5 * (group == "SSD") + 0.02 * age + rng.normal(0, 0.3, n)
    df = pd.DataFrame({"y": y, "Group": group, "age": age})
    fits, notes = fit_outcomes(df, ["y"], "Group", ["age"], "HC")
    assert notes == []
    packed = tables_from_fits(
        fits, ["y"], "Group", ["age"], "HC", "SSD", True, True,
    )
    beta = float(packed["betas"].loc[0, "β"])
    assert beta == pytest.approx(1.5, abs=0.2)
    assert float(packed["betas"].loc[0, "P"]) == pytest.approx(float(packed["means"].loc[0, "P"]), rel=1e-6)
    assert float(packed["means"].loc[0, "FDR P"]) >= float(packed["means"].loc[0, "P"]) - 1e-12
    assert "Adj. mean SSD" in packed["means"].columns
    assert float(packed["means"].loc[0, "Adj. mean SSD"]) > float(packed["means"].loc[0, "Adj. mean HC"])


def test_partial_correlation_removes_shared_factor():
    rng = np.random.default_rng(2)
    z = rng.normal(size=120)
    x = z + rng.normal(scale=0.05, size=120)
    y = z + rng.normal(scale=0.05, size=120)
    df = pd.DataFrame({"x": x, "y": y, "z": z, "sex": np.array(["F", "M"] * 60)})
    raw = float(np.corrcoef(x, y)[0, 1])
    out = partial_corr(df, "x", "y", ["z", "sex"], "pearson")
    assert abs(out["r"]) < abs(raw) / 2
    assert 0 <= out["p"] <= 1


def test_firth_finite_under_separation():
    y = np.array([0, 0, 0, 0, 1, 1, 1, 1], dtype=float)
    x = y.copy()
    X = np.column_stack([np.ones(len(y)), x])
    fit = firth_logit(y, X)
    assert fit is not None and fit["converged"]
    assert np.isfinite(fit["beta"][1])
    assert np.exp(fit["beta"][1]) < 1e6


def test_sections_place_new_switches():
    sec = {k: sid for sid, _t, ks in INI_SECTIONS for k in ks}
    assert sec["DO_FDR"] == "switch"
    assert sec["DO_FWER"] == "switch"
    assert sec["DO_FALSE_CLASS"] == "switch"
    assert sec["VIF_THRESHOLD_WARN"] == "switch"
    assert "VIF_THRESHOLD_HIGH" not in sec
    assert "RAD_LOGIT_VIF" not in sec
    assert "其他" not in {t for _s, t, _k in SECTION_DEFS}
    assert "other" not in {s for s, _t, _k in SECTION_DEFS}
    assert sec["BOOTSTRAP_N"] == "switch"
    assert sec["DO_FIRTH"] == "clin"
    assert sec["ENABLE_CORR_FILTER"] == "radprep"
    assert sec["DO_ICC_FILTER"] == "radprep"
    assert sec["LASSO_CACHE"] == "radprep"
    assert sec["DO_LANDMARK_ROC"] == "surv"
    assert sec["DO_TIME_DEPENDENT_ROC"] == "surv"
    assert sec["TD_ROC_TIMES"] == "surv"
    assert sec["TD_ROC_STATUS_COLS"] == "surv"
    assert sec["TD_IPCW_TIME_COL"] == "surv"
    assert sec["TD_IPCW_STATUS_COL"] == "surv"
    assert sec["TD_IPCW_HORIZONS"] == "surv"
    assert sec["DO_SURVIVAL"] == "surv"
    assert sec["DO_ROC"] == "switch"
    assert sec["RAD_MARKER_SIDE"] == "radprep"
    assert sec["RAD_MARKER_MIN_N"] == "radprep"
    assert sec["RAD_FEATURE_PREFIX_EXCLUDE"] == "radprep"
    assert sec["RAD_FEATURE_DISPLAY_STRIP"] == "radprep"
    assert sec["RAD_FEATURE_PREFIX_GROUP"] == "radprep"
    assert sec["RAD_LOGIT_UNI_P"] == "radlog"
    assert sec["DO_RAD_FIRTH"] == "radlog"
    assert sec["ANCOVA_OUTCOMES"] == "ancova"
    assert sec["MLR_OUTCOMES"] == "mlr"
    assert sec["PARTIAL_X"] == "pcorr"
    clin_ini = next(ks for sid, _t, ks in INI_SECTIONS if sid == "clin")
    clinprep_ini = next(ks for sid, _t, ks in INI_SECTIONS if sid == "clinprep")
    assert clinprep_ini == [
        "CLIN_FORCE_CONTINUOUS", "CLIN_FORCE_CATEGORICAL", "SKIP_CLIN_FEATURES",
        "ORDINAL_MAX_LEVELS",
    ]
    assert "SKIP_CLIN_FEATURES" not in clin_ini
    assert "ORDINAL_MAX_LEVELS" not in clin_ini
    assert sec["SKIP_CLIN_FEATURES"] == "clinprep"
    assert sec["CLIN_FORCE_CONTINUOUS"] == "clinprep"
    assert sec["CLIN_FORCE_CATEGORICAL"] == "clinprep"
    assert sec["ORDINAL_MAX_LEVELS"] == "clinprep"
    io_ui = next(ks for sid, _t, ks in SECTION_DEFS if sid == "io")
    clin_ui = next(ks for sid, _t, ks in SECTION_DEFS if sid == "clin")
    clinprep_ui = next(ks for sid, _t, ks in SECTION_DEFS if sid == "clinprep")
    assert "SKIP_CLIN_FEATURES" not in io_ui
    assert "SKIP_CLIN_FEATURES" not in clin_ui
    assert "ORDINAL_MAX_LEVELS" not in clin_ui
    assert "OUTCOME_COLS" in io_ui
    assert clinprep_ui == [
        "CLIN_FORCE_CONTINUOUS", "CLIN_FORCE_CATEGORICAL", "SKIP_CLIN_FEATURES",
        "ORDINAL_MAX_LEVELS",
    ]
    assert SECTION_OF["SKIP_CLIN_FEATURES"][0] == "clinprep"
    assert SECTION_OF["CLIN_FORCE_CONTINUOUS"][0] == "clinprep"
    assert SECTION_OF["CLIN_FORCE_CATEGORICAL"][0] == "clinprep"
    assert SECTION_OF["ORDINAL_MAX_LEVELS"][0] == "clinprep"
    assert SECTION_OF["DO_FDR"][0] == "switch"
    assert SECTION_OF["ENABLE_CORR_FILTER"][0] == "radprep"
    assert SECTION_OF["DO_ICC_FILTER"][0] == "radprep"
    assert SECTION_OF["RAD_LOGIT_UNI_P"][0] == "radlog"
    assert SECTION_OF["DO_RAD_FIRTH"][0] == "radlog"
    assert SECTION_OF["ANCOVA_OUTCOMES"][0] == "ancova"
    schema = build_schema(
        {
            "DO_FDR": False,
            "DO_FWER": False,
            "DO_FIRTH": False,
            "ANCOVA_OUTCOMES": ["relcbf__ACC_bilat"],
            "ANCOVA_GROUP": "Group",
            "ANCOVA_COVARIATES": ["age"],
            "MLR_OUTCOMES": [],
            "MLR_GROUP": "Group",
            "MLR_COVARIATES": [],
            "PARTIAL_X": "",
            "PARTIAL_Y": "",
            "PARTIAL_CONTROLS": [],
            "PARTIAL_METHOD": "spearman",
            "HEATMAP_COLS": "phq15",
        },
        clin_cols=["Group", "age", "sex"],
        measure_cols=["Group", "age", "sex", "relcbf__ACC_bilat"],
        rad_cols=["relcbf__ACC_bilat", "original_firstorder_Mean"],
    )
    by = {f["key"]: f for f in schema}
    assert by["ANCOVA_OUTCOMES"]["widget"] == "addselect"
    assert by["ANCOVA_COVARIATES"]["widget"] == "addselect"
    assert by["MLR_COVARIATES"]["widget"] == "addselect"
    assert by["PARTIAL_CONTROLS"]["widget"] == "addselect"
    assert by["PARTIAL_X"]["widget"] == "addselect"
    assert by["PARTIAL_Y"]["widget"] == "addselect"
    assert by["PARTIAL_X"]["value"] == []
    assert by["PARTIAL_X"]["add_label"] == "+ 选择连续变量"
    assert by["HEATMAP_COLS"]["widget"] == "addselect"
    assert by["HEATMAP_COLS"]["section"] == "corr"
    assert by["HEATMAP_COLS"]["pool_from"] == "both"
    assert by["HEATMAP_COLS"]["option_pool"][:3] == ["Clinical", "RadScore", "Combined"]
    assert "original_firstorder_Mean" in by["HEATMAP_COLS"]["option_pool"]
    assert "relcbf__ACC_bilat" in by["HEATMAP_COLS"]["option_pool"]
    assert by["HEATMAP_COLS"]["value"] == ["phq15"]
    assert by["HEATMAP_COLS"]["add_label"] == "+ 选择热图列"
    assert "age" in by["HEATMAP_COLS"]["add_options"]
    assert "phq15" not in by["HEATMAP_COLS"]["add_options"]
    assert by["SKIP_CLIN_FEATURES"]["pool_from"] == "clin"
    assert by["FORCE_REMOVE_FEATURES"]["pool_from"] == "clin"
    assert "original_firstorder_Mean" not in by["SKIP_CLIN_FEATURES"]["option_pool"]
    assert "Clinical" not in by["SKIP_CLIN_FEATURES"]["option_pool"]
    assert by["FORCE_MODEL_FEATURES"]["pool_from"] == "both"
    assert "original_firstorder_Mean" in by["FORCE_MODEL_FEATURES"]["option_pool"]
    from modules.console.console import hydrate_console_settings
    hyd = hydrate_console_settings({
        "PARTIAL_X": "phq15",
        "PARTIAL_Y": ["a", "a", ""],
        "HEATMAP_COLS": "age",
    })
    assert hyd["PARTIAL_X"] == ["phq15"]
    assert hyd["PARTIAL_Y"] == ["a"]
    assert hyd["HEATMAP_COLS"] == ["age"]
    assert by["ANCOVA_OUTCOMES"]["add_label"] == "+ 选择因变量"
    assert "relcbf__ACC_bilat" in by["ANCOVA_OUTCOMES"]["option_pool"]
    assert "relcbf__ACC_bilat" not in by["ANCOVA_OUTCOMES"]["add_options"]
    assert "age" in by["ANCOVA_OUTCOMES"]["add_options"]
    assert by["DO_FDR"]["section"] == "switch"
    assert by["DO_FIRTH"]["section"] == "clin"
    assert by["PARTIAL_METHOD"]["widget"] == "select"
    assert by["ANCOVA_OUTCOMES"]["section_title"] == "协方差分析"
    assert by["MLR_OUTCOMES"]["section_title"] == "多元线性回归"
    assert by["PARTIAL_X"]["section_title"] == "相关/偏相关分析"
    assert by["DO_ANCOVA"]["section"] == "ancova" and by["DO_ANCOVA"]["widget"] == "bool"
    assert by["DO_ANCOVA"]["value"] is False
    assert by["DO_MLR"]["section"] == "mlr" and by["DO_MLR"]["value"] is False
    assert by["DO_PARTIAL"]["section"] == "pcorr" and by["DO_PARTIAL"]["value"] is False
    assert by["DO_CLIN"]["section"] == "clin" and by["DO_CLIN"]["value"] is True
    assert by["SKIP_CLIN_FEATURES"]["section"] == "clinprep"
    assert by["CLIN_FORCE_CONTINUOUS"]["section"] == "clinprep"
    assert by["CLIN_FORCE_CATEGORICAL"]["section"] == "clinprep"
    assert by["CLIN_FORCE_CATEGORICAL"]["widget"] == "checklist"
    assert by["CLIN_FORCE_CONTINUOUS"]["widget"] == "checklist"
    assert by["SKIP_CLIN_FEATURES"]["section_title"] == "临床 / 预处理"
    assert by["DO_CORR"]["section"] == "pcorr" and by["DO_CORR"]["value"] is True
    assert by["DO_HEATMAP"]["section"] == "corr"
    assert by["CORR_GROUP"]["section"] == "corr"
    assert by["CORR_PREDICTORS"]["section"] == "pcorr"
    assert by["CORR_REFS"]["section"] == "pcorr"
    assert by["DO_HEATMAP"]["section_title"] == "相关热图"
    assert by["CORR_GROUP"]["widget"] == "select"
    assert by["CORR_GROUP"]["options"] == [""]
    assert by["CORR_GROUP"]["option_labels"][""] == "全部"
    assert by["PARTIAL_GROUP"]["section"] == "pcorr"
    assert by["PARTIAL_GROUP"]["widget"] == "select"
    assert by["PARTIAL_GROUP"]["label"] == "分析组别"
    assert sec["CORR_GROUP"] == "corr"
    assert sec["DO_HEATMAP"] == "corr"
    assert sec["HEATMAP_COLS"] == "corr"
    assert sec["DO_CORR"] == "pcorr"
    assert sec["CORR_PREDICTORS"] == "pcorr"
    assert sec["CORR_REFS"] == "pcorr"
    assert sec["PARTIAL_GROUP"] == "pcorr"
    grouped = build_schema({}, group_levels=["SSD", "HC"])
    gby = {f["key"]: f for f in grouped}
    assert gby["CORR_GROUP"]["options"] == ["", "SSD", "HC"]
    assert gby["PARTIAL_GROUP"]["options"] == ["", "SSD", "HC"]
    assert SECTION_OF["HEATMAP_COLS"][0] == "corr"
    assert SECTION_OF["DO_CORR"][0] == "pcorr"
    corr_ui = next(ks for sid, _t, ks in SECTION_DEFS if sid == "corr")
    assert corr_ui.index("DO_HEATMAP") + 1 == corr_ui.index("HEATMAP_COLS")
    assert by["DO_LASSO"]["section"] == "rad" and by["DO_LASSO"]["value"] is True
    assert by["DO_RAD_LOGISTIC"]["section"] == "radlog" and by["DO_RAD_LOGISTIC"]["value"] is False
    assert by["RAD_LOGIT_UNI_P"]["section"] == "radlog"
    assert by["RAD_LOGIT_MV_P"]["section"] == "radlog"
    assert by["BOOTSTRAP_N"]["section"] == "switch" and by["BOOTSTRAP_N"]["value"] == 1000
    assert by["DO_TIME_DEPENDENT_ROC"]["section"] == "surv" and by["DO_TIME_DEPENDENT_ROC"]["value"] is False
    assert by["DO_LANDMARK_ROC"]["section"] == "surv" and by["DO_LANDMARK_ROC"]["value"] is False
    assert by["TD_IPCW_TIME_COL"]["section"] == "surv"
    assert by["TD_ROC_TIMES"]["section"] == "surv" and by["TD_ROC_TIMES"]["widget"] == "growselect"
    assert by["TD_ROC_TIMES"]["more_label"] == "（再选一列）"
    assert by["TD_ROC_TIMES"]["options"] == ["", "Group", "age", "sex"]
    assert 3 not in by["TD_ROC_TIMES"]["options"]
    assert by["TD_ROC_STATUS_COLS"]["section"] == "surv" and by["TD_ROC_STATUS_COLS"]["widget"] == "checklist"
    assert by["TD_ROC_STATUS_COLS"]["add_label"] == "+ 搜索列名"
    assert by["TD_ROC_STATUS_COLS"]["value"] == []
    assert by["TD_ROC_STATUS_COLS"]["option_pool"] == ["Group", "age", "sex"]
    assert by["RAD_MARKER_SIDE"]["section"] == "radmark" and by["RAD_MARKER_SIDE"]["value"] == "both"
    assert by["RAD_MARKER_SIDE"]["widget"] == "select"
    assert by["RAD_MARKER_MIN_N"]["section"] == "radmark" and by["RAD_MARKER_MIN_N"]["value"] == 5
    assert by["DO_RAD_FIRTH"]["section"] == "radlog" and by["DO_RAD_FIRTH"]["value"] is False
    assert by["RAD_FEATURE_DISPLAY_STRIP"]["section"] == "radmark"
    assert by["DO_ICC_FILTER"]["section"] == "radprep"
    assert sec["DO_RAD_LOGISTIC"] == "radlog"
    assert SECTION_OF["DO_RAD_LOGISTIC"][0] == "radlog"
    ids = [sid for sid, _t, _ks in SECTION_DEFS]
    assert ids.index("io") < ids.index("exclude") < ids.index("valmode") < ids.index("clinprep")
    assert ids.index("clinprep") + 1 == ids.index("clin")
    assert ids.index("clin") + 1 == ids.index("roc")
    assert ids.index("roc") + 1 == ids.index("surv")
    assert ids.index("surv") < ids.index("ancova") < ids.index("mlr")
    surv_ui = next(ks for sid, _t, ks in SECTION_DEFS if sid == "surv")
    assert surv_ui.index("DO_COX") < surv_ui.index("DO_TIME_DEPENDENT_ROC") < surv_ui.index("DO_LANDMARK_ROC")
    roc_ui = next(ks for sid, _t, ks in SECTION_DEFS if sid == "roc")
    assert "DO_LANDMARK_ROC" not in roc_ui and "DO_TIME_DEPENDENT_ROC" not in roc_ui
    assert "DO_EXCLUDE_SAMPLES" not in io_ui
    assert "VAL_MODE" not in io_ui
    assert SECTION_OF["DO_EXCLUDE_SAMPLES"][0] == "exclude"
    assert SECTION_OF["VAL_MODE"][0] == "valmode"
    assert SECTION_OF["SUBGROUP_VAL_MODE"][0] == "valmode"
    assert SECTION_OF["EXTERNAL_VAL_MODE"][0] == "valmode"
    assert ids.index("corr") < ids.index("pcorr")
    assert ids.index("radprep") + 1 == ids.index("rad")
    assert ids.index("rad") + 1 == ids.index("radlog")
    assert ids.index("fig") + 1 == ids.index("radmark")
    for _sid, title, _ks in SECTION_DEFS:
        assert "§" not in title
    clin_keys = next(ks for sid, _t, ks in SECTION_DEFS if sid == "clin")
    rad_keys = next(ks for sid, _t, ks in SECTION_DEFS if sid == "rad")
    prep_keys = next(ks for sid, _t, ks in SECTION_DEFS if sid == "radprep")
    mark_keys = next(ks for sid, _t, ks in SECTION_DEFS if sid == "radmark")
    assert "RAD_FEATURE_SOURCE" in rad_keys
    for k in (
        "DO_ICC_FILTER", "ICC_REQUIRE_ROW", "ENABLE_CORR_FILTER", "RAD_COR_CUTOFF",
        "ICC_THRESHOLD", "ICC_FEATURE_COL", "ICC_INTRA_COL", "ICC_INTER_COL",
    ):
        assert k not in clin_keys
        assert k not in rad_keys
        assert k in prep_keys
        assert k not in mark_keys
    assert "LASSO_CACHE" in prep_keys
    assert "LASSO_CACHE" not in rad_keys
    assert "LASSO_CACHE" not in clin_keys
    for k in (
        "RAD_MARKER_SIDE", "RAD_MARKER_MIN_N",
        "RAD_FEATURE_PREFIX_EXCLUDE", "RAD_FEATURE_DISPLAY_STRIP",
        "RAD_FEATURE_PREFIX_GROUP",
    ):
        assert k not in clin_keys
        assert k not in rad_keys
        assert k not in prep_keys
        assert k in mark_keys


def test_column_multi_pool_follows_section():
    from modules.console.console import _column_multi_pool

    clin, models, src = _column_multi_pool("clin", ["age", "Group"], ["original_a"], [])
    assert src == "clin" and models == [] and clin == ["age", "Group"]
    rad, models, src = _column_multi_pool("radlog", ["age"], ["original_a"], [])
    assert src == "rad" and models == [] and rad == ["original_a"]
    both, models, src = _column_multi_pool("corr", ["age"], ["original_a"], ["phq15"])
    assert src == "both"
    assert both[:3] == ["Clinical", "RadScore", "Combined"]
    assert "age" in both and "original_a" in both and "phq15" in both


def test_heatmap_columns_prefer_clin_and_keep_group():
    from modules.stats.correlation import (
        heatmap_wanted,
        resolve_heatmap_columns,
        select_heatmap_frames,
    )

    assert heatmap_wanted("") == []
    assert heatmap_wanted(None) == []
    assert heatmap_wanted("phq15") == ["phq15"]
    clin, rad, missing = resolve_heatmap_columns(
        ["age", "Group", "perfusion_weighted__ACC_L", "nope"],
        ["Group", "age", "sex"],
        ["Group", "perfusion_weighted__ACC_L", "original_firstorder_Mean"],
    )
    assert clin == ["age", "Group"]
    assert rad == ["perfusion_weighted__ACC_L"]
    assert missing == ["nope"]
    assert resolve_heatmap_columns(["group"], ["Age"], ["feat"]) == (["Group"], [], [])

    clin_df = pd.DataFrame({"age": [1, 2], "Group": [0, 1]})
    rad_df = pd.DataFrame({
        "perfusion_weighted__ACC_L": [0.2, 0.4],
        "original_x": [9, 8],
        "age": [7, 8],
    })
    rad_sel, clin_sel, missing = select_heatmap_frames(
        ["age", "perfusion_weighted__ACC_L", "missing_col"],
        clin_df, rad_df, group_labels=pd.Series([1, 0]),
    )
    assert list(clin_sel.columns) == ["age"]
    assert int(clin_sel["age"].iloc[0]) == 1
    assert list(rad_sel.columns) == ["perfusion_weighted__ACC_L"]
    assert missing == ["missing_col"]

    rad_sel, clin_sel, missing = select_heatmap_frames(
        ["Group"],
        pd.DataFrame({"age": [1, 2]}),
        pd.DataFrame({"f": [3, 4]}),
        group_labels=pd.Series([1, 0]),
    )
    assert list(clin_sel.columns) == ["Group"]
    assert list(clin_sel["Group"]) == [1, 0]
    assert rad_sel.shape[1] == 0
    assert missing == []


def test_analysis_group_keeps_named_rows():
    from modules.stats.assoc import subset_frame_by_group
    from modules.stats.correlation import group_row_mask, take_group_rows

    assert group_row_mask(["SSD", "HC", "SSD"], "") is None
    mask = group_row_mask(["SSD", "HC", "SSD"], "HC", 3)
    assert list(mask) == [False, True, False]
    frame = pd.DataFrame({"Group": ["SSD", "HC", "SSD"], "x": [1, 2, 3]})
    sub = subset_frame_by_group(frame, "SSD")
    assert list(sub["x"]) == [1, 3]
    assert list(take_group_rows(frame, mask)["x"]) == [2]
    assert subset_frame_by_group(frame, "") is frame
    with pytest.raises(ValueError):
        group_row_mask(["SSD", "HC"], "NC", 2)


def test_heatmap_expands_screened_clinical_and_radscore():
    from modules.console.console import HINTS
    from modules.stats.correlation import expand_heatmap_columns, select_heatmap_frames

    names, empty = expand_heatmap_columns(
        ["Clinical", "phq15", "RadScore", "Combined"],
        clin_selected=["age", "sex", "age"],
        rad_selected=["feat_a", "age"],
    )
    assert names == ["Clinical", "age", "sex", "phq15", "RadScore", "feat_a", "Combined"]
    assert empty == []

    names, empty = expand_heatmap_columns(
        ["clinical", "rad_score"],
        clin_selected=[],
        rad_selected=["feat_a"],
    )
    assert names == ["Clinical", "RadScore", "feat_a"]
    assert empty == []

    assert expand_heatmap_columns([], ["age"], ["feat_a"]) == ([], [])
    assert expand_heatmap_columns(None, ["age"], ["feat_a"]) == ([], [])
    assert expand_heatmap_columns("RadScore", rad_selected=["feat_a"]) == (
        ["RadScore", "feat_a"], [],
    )

    clin_df = pd.DataFrame({
        "age": [1, 2], "sex": [0, 1], "Group": [0, 1], "phq15": [3, 4],
        "Clinical": [0.2, 0.8], "RadScore": [0.1, 0.9],
    })
    rad_df = pd.DataFrame({"feat_a": [0.1, 0.2], "feat_b": [3, 4], "other": [9, 8]})
    names, empty = expand_heatmap_columns(
        ["Clinical", "RadScore", "phq15"],
        ["age", "sex"],
        ["feat_a"],
    )
    assert empty == []
    rad_sel, clin_sel, missing = select_heatmap_frames(
        names, clin_df, rad_df, group_labels=pd.Series([1, 0]),
    )
    assert list(clin_sel.columns) == ["Clinical", "age", "sex", "RadScore", "phq15"]
    assert "Group" not in list(clin_sel.columns)
    assert list(rad_sel.columns) == ["feat_a"]
    assert missing == []

    names, _ = expand_heatmap_columns(["Combined"], ["age"], ["feat_a"])
    assert names == ["Combined"]
    _, _, missing = select_heatmap_frames(names, clin_df, rad_df)
    assert missing == ["Combined"]

    assert "临床评分" in HINTS["DO_HEATMAP"]
    assert "组学评分" in HINTS["DO_HEATMAP"]
    assert "入选成分特征" in HINTS["HEATMAP_COLS"]
    assert "热图由「相关热图」决定" in HINTS["DO_CORR"]
    assert "也不画热图" not in HINTS["DO_CORR"]
    assert "MEDIATION_BOOTSTRAP" not in HINTS
    from modules.console.console import LABELS
    assert "MEDIATION_BOOTSTRAP" not in LABELS


def test_prefix_exclude_drops_matching_rad_features(monkeypatch):
    from modules.console.console import LABELS
    from modules.stats import data_io

    fresh = {f["key"]: f for f in build_schema({})}
    assert fresh["RAD_FEATURE_PREFIX_EXCLUDE"]["widget"] == "json"
    assert fresh["RAD_FEATURE_PREFIX_EXCLUDE"]["section"] == "radmark"
    assert fresh["RAD_FEATURE_PREFIX_EXCLUDE"]["label"] == "前缀 /筛选/ 剥离"
    assert fresh["RAD_FEATURE_PREFIX_EXCLUDE"]["value"] == []
    assert fresh["RAD_FEATURE_DISPLAY_STRIP"]["value"] == []
    assert fresh["RAD_FEATURE_PREFIX_GROUP"]["widget"] == "json"
    assert fresh["RAD_FEATURE_PREFIX_GROUP"]["section"] == "radmark"
    assert fresh["RAD_FEATURE_PREFIX_GROUP"]["label"] == "前缀分类"
    assert fresh["RAD_FEATURE_PREFIX_GROUP"]["value"] == {"names": [], "assign": {}}
    assert LABELS["RAD_FEATURE_PREFIX_EXCLUDE"] == "前缀 /筛选/ 剥离"

    clin = ["age", "Patient_ID", "relcbf__ACC_L"]
    rad = [
        "Patient_ID", "relcbf__ACC_L", "relcbf__Insula_L",
        "perfusion_weighted__ACC_L",
    ]
    fields = build_schema(
        {"RAD_FEATURE_PREFIX_EXCLUDE": "relcbf__"},
        clin_cols=clin,
        rad_cols=rad,
        rad_id_cols=["Patient_ID"],
    )
    by = {f["key"]: f for f in fields}
    assert by["RAD_FEATURE_PREFIX_EXCLUDE"]["value"] == ["relcbf__"]
    heat = by["HEATMAP_COLS"]["option_pool"]
    assert heat[:3] == ["Clinical", "RadScore", "Combined"]
    assert "perfusion_weighted__ACC_L" in heat
    assert "age" in heat
    assert "Patient_ID" in heat
    assert "relcbf__ACC_L" in heat
    assert "relcbf__Insula_L" not in heat
    partial = by["PARTIAL_X"]["option_pool"]
    assert "relcbf__Insula_L" not in partial
    assert "perfusion_weighted__ACC_L" in partial

    frame = pd.DataFrame({
        "Patient_ID": ["a", "b"],
        "Group": ["SSD", "HC"],
        "relcbf__ACC_L": [1.0, 2.0],
        "perfusion_weighted__ACC_L": [3.0, 4.0],
    })
    monkeypatch.setattr(data_io.cfg, "RAD_ID_COL", "Patient_ID", raising=False)
    monkeypatch.setattr(data_io.cfg, "RAD_ID_COLS", ["Patient_ID"], raising=False)
    monkeypatch.setattr(data_io.cfg, "RAD_FEATURE_PREFIX_EXCLUDE", ["relcbf__"], raising=False)
    prepared = data_io._prepare_rad_frame(frame)
    assert list(prepared.columns) == ["Name", "perfusion_weighted__ACC_L"]

    monkeypatch.setattr(data_io.cfg, "RAD_FEATURE_PREFIX_EXCLUDE", [], raising=False)
    kept = data_io._prepare_rad_frame(frame)
    assert list(kept.columns) == ["Name", "relcbf__ACC_L", "perfusion_weighted__ACC_L"]

    untouched = data_io.drop_excluded_rad_columns(frame, prefixes=["", "  "])
    assert list(untouched.columns) == list(frame.columns)
    mid = data_io.drop_excluded_rad_columns(frame, prefixes=["ACC_L"])
    assert "relcbf__ACC_L" in mid.columns
    kept_id = data_io.drop_excluded_rad_columns(
        pd.DataFrame({"relcbf__id": ["a"], "relcbf__ACC_L": [1.0]}),
        prefixes=["relcbf__"],
        id_cols=["relcbf__id"],
    )
    assert list(kept_id.columns) == ["relcbf__id"]


def test_clear_selected_heatmap_removes_stale_png(tmp_path, monkeypatch):
    from modules.stats import correlation as corr

    png_dir = tmp_path / "PNG"
    png_dir.mkdir()
    png = png_dir / "correlation_heatmap_selected.png"
    tiff = tmp_path / "correlation_heatmap_selected.tiff"
    png.write_bytes(b"png")
    tiff.write_bytes(b"tiff")
    monkeypatch.setattr(corr.cfg, "OUT_DIR", str(tmp_path))
    corr.clear_selected_heatmap()
    assert not png.exists()
    assert not tiff.exists()


def test_project_roi_ancova_runs_when_workbook_present():
    # Set RAD_BOOK_XLSX to any workbook with cln/rad sheets to enable this test.
    raw = os.environ.get("RAD_BOOK_XLSX", "").strip()
    if not raw:
        pytest.skip("RAD_BOOK_XLSX not set")
    book = Path(raw)
    if not book.is_file():
        pytest.skip(f"RAD_BOOK_XLSX not found: {book}")
    clin = pd.read_excel(book, sheet_name="cln")
    rad = pd.read_excel(book, sheet_name="rad")
    df = clin.merge(rad, on="Patient_ID", suffixes=("", "_rad"))
    # Current cln/rad headers: Age, Sex, Education years, SSS, relcbf_<ROI>.
    outcomes = [
        "relcbf_Frontal_bilat",
        "relcbf_basal_ganglia_bilat",
        "relcbf_hippocampus_bilat",
    ]
    covars = ["Age", "Sex", "Education years"]
    fits, notes = fit_outcomes(df, outcomes, "Group", covars, "HC")
    assert notes == []
    packed = tables_from_fits(
        fits, outcomes, "Group", covars, "HC", "SSD", True, False,
    )
    assert list(packed["betas"]["Outcome"]) == outcomes
    assert packed["betas"]["Contrast"].eq("SSD vs HC").all()
    assert packed["betas"]["β"].map(np.isfinite).all()
    assert packed["means"]["F"].map(np.isfinite).all()
    part = partial_corr(df, "relcbf_Frontal_bilat", "SSS", covars, "spearman")
    assert -1 <= part["r"] <= 1
    assert part["n"] > 50


def test_prefix_exclude_double_underscore_covers_single(monkeypatch):
    """perfusion_weighted__ also drops perfusion_weighted_Frontal_*."""
    from modules.stats import data_io
    frame = pd.DataFrame({
        "Patient_ID": ["a"],
        "perfusion_weighted_Frontal_L": [1.0],
        "perfusion_weighted__basal_ganglia_L": [2.0],
        "relcbf_Frontal_L": [3.0],
    })
    monkeypatch.setattr(data_io.cfg, "RAD_FEATURE_PREFIX_EXCLUDE", ["perfusion_weighted__"], raising=False)
    monkeypatch.setattr(data_io.cfg, "RAD_ID_COL", "Patient_ID", raising=False)
    monkeypatch.setattr(data_io.cfg, "RAD_ID_COLS", ["Patient_ID"], raising=False)
    out = data_io.drop_excluded_rad_columns(frame)
    assert "perfusion_weighted_Frontal_L" not in out.columns
    assert "perfusion_weighted__basal_ganglia_L" not in out.columns
    assert "relcbf_Frontal_L" in out.columns
    assert "Patient_ID" in out.columns


def test_suffix_marker_matches_only_when_tagged():
    """结尾标记必须带 suffix:。裸的 _Vgm 不因首字符被当成后缀。"""
    from modules.stats import data_io
    from modules.stats.radiomics import display_rad_feature, prefix_group_plan

    name = "Left Lateral Ventricle_Vgm"
    assert data_io._column_matches_prefix(name, "suffix:_Vgm")
    assert not data_io._column_matches_prefix(name, "_Vgm")
    assert not data_io._column_matches_prefix(name, "suffix:_Vcsf")
    assert not data_io._column_matches_prefix("Keep_Vgm_Inside_Name", "suffix:_Vgm")
    assert data_io._column_matches_prefix("relcbf__ACC_L", "relcbf__")
    assert not data_io._column_matches_prefix("relcbf__ACC_L", "ACC_L")

    frame = pd.DataFrame({
        "Patient_ID": ["a"],
        "Left Lateral Ventricle_Vgm": [1.0],
        "Left Lateral Ventricle_Vcsf": [2.0],
        "Brain Stem_Vwm": [3.0],
    })
    dropped = data_io.drop_excluded_rad_columns(frame, prefixes=["suffix:_Vcsf", "suffix:_Vwm"])
    assert list(dropped.columns) == ["Patient_ID", "Left Lateral Ventricle_Vgm"]
    untouched = data_io.drop_excluded_rad_columns(frame, prefixes=["_Vcsf", "_Vwm"])
    assert "Left Lateral Ventricle_Vcsf" in untouched.columns

    import modules.config as cfg
    old_strip = getattr(cfg, "RAD_FEATURE_DISPLAY_STRIP", [])
    old_group = getattr(cfg, "RAD_FEATURE_PREFIX_GROUP", None)
    try:
        cfg.RAD_FEATURE_DISPLAY_STRIP = ["suffix:_Vgm", "suffix:_Vcsf", "suffix:_Vwm"]
        assert display_rad_feature(name) == "Left Lateral Ventricle"
        assert display_rad_feature("CSF_Vcsf") == "CSF"
        cfg.RAD_FEATURE_DISPLAY_STRIP = ["_Vgm"]
        assert display_rad_feature(name) == name
        cfg.RAD_FEATURE_DISPLAY_STRIP = ["original_"]
        assert display_rad_feature("original_firstorder_Mean") == "firstorder_Mean"
        cfg.RAD_FEATURE_PREFIX_GROUP = {
            "names": ["csf", "vgm", "vwm", "seq"],
            "assign": {
                "suffix:_Vcsf": "csf",
                "suffix:_Vgm": "vgm",
                "suffix:_Vwm": "vwm",
                "perfusion_weighted_": "seq",
            },
        }
        plan = dict(prefix_group_plan([
            "Patient_ID",
            "Left Lateral Ventricle_Vgm",
            "Left Lateral Ventricle_Vcsf",
            "Brain Stem_Vwm",
            "perfusion_weighted_Frontal_L",
        ]))
    finally:
        cfg.RAD_FEATURE_DISPLAY_STRIP = old_strip
        cfg.RAD_FEATURE_PREFIX_GROUP = old_group
    assert plan["vgm"] == ["Left Lateral Ventricle_Vgm"]
    assert plan["csf"] == ["Left Lateral Ventricle_Vcsf"]
    assert plan["vwm"] == ["Brain Stem_Vwm"]
    assert plan["seq"] == ["perfusion_weighted_Frontal_L"]


def test_expand_corr_predictors_keeps_radscore_and_components():
    from modules.stats.correlation import expand_corr_predictors

    names = expand_corr_predictors(
        ["RadScore", "PHQ15"],
        rad_selected=["feat_a", "feat_b", "feat_a"],
        clin_selected=["age"],
    )
    assert names == ["RadScore", "feat_a", "feat_b", "PHQ15"]

    names = expand_corr_predictors(
        ["Clinical", "RadScore"],
        rad_selected=["feat_a"],
        clin_selected=["age", "sex"],
    )
    assert names == ["Clinical", "age", "sex", "RadScore", "feat_a"]

    assert expand_corr_predictors(["Combined"], rad_selected=["feat_a"]) == ["Combined"]
    assert expand_corr_predictors([], rad_selected=["feat_a"]) == []


def test_run_assoc_expands_ancova_mlr_radscore_measures():
    from types import SimpleNamespace
    from modules.stats.assoc import run_assoc
    import modules.config as cfg

    clin = pd.DataFrame({
        "Age": [50.0, 60.0, 55.0, 45.0, 70.0, 40.0, 65.0, 48.0],
        "Sex": ["M", "F", "M", "F", "M", "F", "M", "F"],
        "PHQ15": [2.0, 8.0, 3.0, 9.0, 1.0, 10.0, 4.0, 7.0],
    })
    rad = pd.DataFrame({
        "feat_a": [0.1, 0.9, 0.2, 0.8, 0.15, 0.85, 0.25, 0.75],
        "Name": [f"p{i}" for i in range(8)],
    })
    group = ["SSD", "SSD", "SSD", "SSD", "HC", "HC", "HC", "HC"]
    layer = SimpleNamespace(clin=clin, rad=rad, group=group)
    rs = np.linspace(0.0, 1.0, 8)
    prev = {
        "DO_PARTIAL": getattr(cfg, "DO_PARTIAL", False),
        "DO_ANCOVA": getattr(cfg, "DO_ANCOVA", False),
        "DO_MLR": getattr(cfg, "DO_MLR", False),
        "ANCOVA_OUTCOMES": getattr(cfg, "ANCOVA_OUTCOMES", []),
        "ANCOVA_GROUP": getattr(cfg, "ANCOVA_GROUP", "Group"),
        "ANCOVA_COVARIATES": getattr(cfg, "ANCOVA_COVARIATES", []),
        "MLR_OUTCOMES": getattr(cfg, "MLR_OUTCOMES", []),
        "MLR_GROUP": getattr(cfg, "MLR_GROUP", "Group"),
        "MLR_COVARIATES": getattr(cfg, "MLR_COVARIATES", []),
        "DO_FDR": getattr(cfg, "DO_FDR", False),
        "DO_FWER": getattr(cfg, "DO_FWER", False),
        "POSITIVE_GROUP_LABEL": getattr(cfg, "POSITIVE_GROUP_LABEL", ""),
        "GROUP_DISPLAY_NEG": getattr(cfg, "GROUP_DISPLAY_NEG", ""),
    }
    try:
        cfg.DO_PARTIAL = False
        cfg.DO_ANCOVA = True
        cfg.DO_MLR = True
        cfg.DO_FDR = False
        cfg.DO_FWER = False
        cfg.ANCOVA_OUTCOMES = ["RadScore"]
        cfg.ANCOVA_GROUP = "Group"
        cfg.ANCOVA_COVARIATES = ["Age"]
        cfg.MLR_OUTCOMES = ["RadScore"]
        cfg.MLR_GROUP = "Group"
        cfg.MLR_COVARIATES = ["Age"]
        cfg.POSITIVE_GROUP_LABEL = "SSD"
        cfg.GROUP_DISPLAY_NEG = "HC"
        out = run_assoc(
            layer, extras={"RadScore": rs},
            rad_selected=["feat_a"], clin_selected=["Age"],
        )
        assert out is not None
        assert out.get("ancova")
        assert out.get("mlr")
        means = out["ancova"].get("means")
        if means is not None and hasattr(means, "columns") and "outcome" in means.columns:
            outs = set(means["outcome"].astype(str))
            assert "RadScore" in outs
            assert "feat_a" in outs
        warns = " | ".join(out.get("warnings") or [])
        assert "缺少列 RadScore" not in warns
    finally:
        for k, v in prev.items():
            setattr(cfg, k, v)


def test_analysis_frame_merges_radscore_extras():
    from types import SimpleNamespace
    from modules.stats.assoc import analysis_frame, partial_corr, run_assoc
    import modules.config as cfg

    clin = pd.DataFrame({
        "Age": [50.0, 60.0, 55.0, 45.0, 70.0, 40.0, 65.0, 48.0],
        "Sex": ["M", "F", "M", "F", "M", "F", "M", "F"],
        "PHQ15": [2.0, 8.0, 3.0, 9.0, 1.0, 10.0, 4.0, 7.0],
        "Education years": [12, 9, 16, 11, 14, 10, 13, 15],
    })
    rad = pd.DataFrame({
        "feat_a": [0.1, 0.9, 0.2, 0.8, 0.15, 0.85, 0.25, 0.75],
        "Name": [f"p{i}" for i in range(8)],
    })
    group = ["SSD", "SSD", "SSD", "SSD", "HC", "HC", "HC", "HC"]
    layer = SimpleNamespace(clin=clin, rad=rad, group=group)
    rs = np.linspace(0.0, 1.0, 8)
    frame = analysis_frame(layer, extras={"RadScore": rs})
    assert "RadScore" in frame.columns
    assert "feat_a" in frame.columns
    assert np.allclose(frame["RadScore"].to_numpy(), rs)

    row = partial_corr(frame, "RadScore", "PHQ15", ["Age", "Sex"], "spearman")
    assert row["n"] >= 5
    assert np.isfinite(row["r"])

    # run_assoc with DO_PARTIAL and extras — no missing-column warning
    prev = {
        "DO_PARTIAL": getattr(cfg, "DO_PARTIAL", False),
        "DO_ANCOVA": getattr(cfg, "DO_ANCOVA", False),
        "DO_MLR": getattr(cfg, "DO_MLR", False),
        "PARTIAL_X": getattr(cfg, "PARTIAL_X", []),
        "PARTIAL_Y": getattr(cfg, "PARTIAL_Y", []),
        "PARTIAL_CONTROLS": getattr(cfg, "PARTIAL_CONTROLS", []),
        "PARTIAL_METHOD": getattr(cfg, "PARTIAL_METHOD", "spearman"),
        "PARTIAL_GROUP": getattr(cfg, "PARTIAL_GROUP", ""),
    }
    try:
        cfg.DO_PARTIAL = True
        cfg.DO_ANCOVA = False
        cfg.DO_MLR = False
        cfg.PARTIAL_X = ["RadScore"]
        cfg.PARTIAL_Y = ["PHQ15"]
        cfg.PARTIAL_CONTROLS = ["Age", "Sex"]
        cfg.PARTIAL_METHOD = "spearman"
        cfg.PARTIAL_GROUP = ""
        out = run_assoc(layer, extras={"RadScore": rs}, rad_selected=["feat_a"])
        assert out is not None
        assert out.get("partial")
        assert len(out["partial"]) >= 2  # RadScore + feat_a vs PHQ15
        warns = " | ".join(out.get("warnings") or [])
        assert "缺少列 RadScore" not in warns
    finally:
        for k, v in prev.items():
            setattr(cfg, k, v)
