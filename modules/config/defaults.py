"""Safe defaults for every settings.ini key used by modules.

Import of `modules.config` must succeed without a project settings.ini.
Project ini values overlay these; missing keys keep the defaults below.
"""
from __future__ import annotations

from typing import Any

# Keys listed in ini_io.INI_SECTIONS plus runtime slots used at import/call time.
DEFAULT_SETTINGS: dict[str, Any] = {
    # repro
    "DO_REPRO": True,
    "DRY_RUN": False,
    "SEED": 66666,
    "TRAIN_RATIO": 0.7,
    "SPLIT_COL": "",
    # io
    "DO_IO": True,
    "INPUT": "data_merged.xlsx",
    "MODULES_DIR": "",
    "RAD_SHEET": "rad",
    "CLIN_SHEET": "cln",
    "QC_SHEET": "qc",
    "ICC_SHEET": "icc",
    "RAD_ID_COL": "Patient_ID",
    "CLIN_ID_COL": "Patient_ID",
    "CLIN_PATH": "",
    "RAD_PATH": "",
    "LABEL_COL": "Name",
    "OUT_DIR": "",
    "PNG_DIR": "PNG",
    "OUTCOME_OUT_SUBDIR": True,
    "EXTERNAL_FILE": "",
    "EXTERNAL_CLIN_SHEET": "cln",
    "EXTERNAL_RAD_SHEET": "rad",
    "USE_INTERNAL_SPLIT_WHEN_EXTERNAL": True,
    "OUTCOME_COLS": [],
    "OUTCOME_OVERRIDES": {},
    "PAIRWISE_OVERRIDES": {},
    "SKIP_CLIN_FEATURES": [],
    "SUBPROJECT_BY_OUTCOME": {},
    "GROUP_COL_KEYWORD": "Group",
    "POSITIVE_GROUP_LABEL": "",
    "GROUP_DISPLAY_POS": "",
    # 缺键=开。只决定结果 HTML 是否写出这两节，不改队列、拆分和建模。
    "DO_BASELINE_ALL": True,
    "DO_BASELINE_TRAIN_TEST": True,
    "GROUP_DISPLAY_NEG": "",
    "EXTERNAL_TESTS": [],
    "DO_EXCLUDE_SAMPLES": False,
    # 排除名单 xlsx。空=默认工程根 false_classification.xlsx。一个写成字符串，多个写成列表。
    "EXCLUDE_FILE": "",
    "EXCLUDE_SHEET": "",
    "EXCLUDE_COL": "",
    "EXCLUDE_ROW": "",
    "EXCLUDE_IDS": [],
    "READER_VALUE_MAP": {},
    "OUTCOME_BINARIZE": {},
    "GROUP_KEEP": [],
    "PAIRWISE_GROUPS": [],
    "PAIRWISE_ALL": False,
    "GROUP_FROM_ID": False,
    "GROUP_FROM_ID_SEP": "_",
    "GROUP_LEVEL_DISPLAY": {},
    "SUBGROUP_COL": "",
    "VAL_MODE": "refit",
    "SUBGROUP_VAL_MODE": "",
    "EXTERNAL_VAL_MODE": "refit",
    "GROUP_SOURCE_COL": "",
    # clin
    "DO_CLIN": True,
    # 只关结果里的表。不改入模变量和筛选。缺键=开。
    "DO_UNI_LOGISTIC_TABLE": True,
    "DO_MV_LOGISTIC_TABLE": True,
    "TABLE1_P_THRESHOLD": 0.1,
    # 水平数≤此值→计数；>此值→计量。CLIN_FORCE_CATEGORICAL 强制计数覆盖本阈值。
    "ORDINAL_MAX_LEVELS": 5,
    "CLIN_FORCE_CONTINUOUS": [],
    "CLIN_FORCE_CATEGORICAL": [],
    "FORCE_REMOVE_FEATURES": [],
    "FORCE_INTER_FEATURES": [],
    "FORCE_MODEL_FEATURES": [],
    "READER_COLS": [],
    "RAD_ID_COLS": ["Patient_ID"],
    "DO_FIRTH": False,
    # ancova / mlr / partial — switch off or empty outcomes means the module does not run
    "DO_ANCOVA": False,
    "ANCOVA_OUTCOMES": [],
    "ANCOVA_GROUP": "Group",
    "ANCOVA_COVARIATES": [],
    "DO_MLR": False,
    "MLR_OUTCOMES": [],
    "MLR_GROUP": "Group",
    "MLR_COVARIATES": [],
    "DO_PARTIAL": False,
    "PARTIAL_X": [],
    "PARTIAL_Y": [],
    "PARTIAL_CONTROLS": [],
    "PARTIAL_METHOD": "spearman",
    "PARTIAL_GROUP": "",
    # switch
    "DO_FALSE_CLASS": True,
    "DO_FDR": False,
    "DO_FWER": False,
    "BOOTSTRAP_N": 1000,
    "VIF_THRESHOLD_WARN": 5.0,
    "ENABLE_CORR_FILTER": True,
    "DO_FIGURE_PANELS": True,
    "DO_QC_REPORT": True,
    "QC_HTML": "ref/qc.html",
    "DO_ICC_FILTER": True,
    "LASSO_CACHE": True,
    "ICC_THRESHOLD": 0.75,
    "ICC_FEATURE_COL": "Pictologics_feature",
    "ICC_INTRA_COL": "intra_ICC",
    "ICC_INTER_COL": "inter_ICC",
    "ICC_REQUIRE_ROW": False,
    "DO_COMBINED_MODEL": True,
    "DO_NOMOGRAM": True,
    "DO_CALIBRATION": True,
    "ROC_MV_P_THRESHOLD": 0.05,
    "FORCE_CLIN_FOR_ROC": [],
    "ROC_PLOT_EXCLUDE": [],
    "DO_DCA": True,
    "DCA_CURVES": ["Clinical", "RadScore", "Combined"],
    "DO_NRI": False,
    # Landmark：同一套分数 × 各时间点状态列。与 IPCW 时间依赖性 ROC 分开。
    "DO_LANDMARK_ROC": False,
    # IPCW 累积/动态 AUC。字段用 TD_IPCW_*，与 Landmark 的 TD_ROC_* 分开。
    "DO_TIME_DEPENDENT_ROC": False,
    # Landmark 时间列名，与 TD_ROC_STATUS_COLS 逐行成对。空位=按状态列名取窗口月。
    "TD_ROC_TIMES": "",
    # Landmark 状态列名，一行一个时间点。不再是时间→列的字典。
    "TD_ROC_STATUS_COLS": "",
    "TD_IPCW_TIME_COL": "ipcw_time",
    "TD_IPCW_STATUS_COL": "ipcw_status",
    "TD_IPCW_HORIZONS": [3, 6, 12, 24],
    # two internal cuts → low / intermediate / high
    "NRI_CUTOFF": [0.0, 0.3, 0.7, 1.0],
    "NRI_MODEL_OLD": ["clinical"],
    "NRI_MODEL_NEW": ["combined"],
    "CORR_PREDICTORS": ["RadScore", "Clinical", "Combined"],
    "CORR_REFS": [],
    "CORR_E_REF": "Age",
    "CORR_F_REF": "Group",
    "OUTCOME_READER_COMPARE": {},
    "READER_COMPARE_COL": None,
    "READER_ALL_IN_ROC": False,
    "NRI_OLD_COL": "",
    "NRI_NEW_COL": "",
    # surv
    "DO_SURVIVAL": False,
    "SURVIVAL_TIME_COL": "",
    "SURVIVAL_TIME_UNIT": "months",
    "DO_SURVIVAL_BY_RISK": True,
    "DO_COX": False,
    # advanced
    "DO_ADVANCED": True,
    "DO_DAG": False,
    "DO_SHAP": False,
    "DO_MEDIATION": False,
    "SHAP_MAX_SAMPLES": 200,
    # kept as alias fallback for old overlays / code; console field removed
    "MEDIATION_BOOTSTRAP": 1000,
    "DAG_NODES": [],
    "DAG_EDGES": [],
    "DAG_MEDIATION_HIGHLIGHT": [],
    "MEDIATION_PATHS": [],
    # rad
    "DO_LASSO": True,
    "DO_RAD_LOGISTIC": False,
    "RAD_LOGIT_UNI_P": 0.1,
    "RAD_LOGIT_MV_P": 0.05,
    "DO_RAD_FIRTH": False,
    "RAD_FEATURE_SOURCE": "both",
    "RAD_COR_CUTOFF": 0.95,
    "ALPHA": 1.0,
    "LASSO_NFOLDS": 10,
    "LASSO_N_LAMBDAS": 15,
    "LASSO_MAX_ITER": 500,
    "LASSO_N_JOBS": -1,
    "LASSO_C_MAX": 50.0,
    "LASSO_C_MIN": 1e-3,
    "LASSO_TOL": 1e-3,
    "LASSO_PREFILTER_TOPK": 25,
    "LASSO_PLOT_PATH": False,
    "RAD_FEATURE_DISPLAY_STRIP": [],
    "RAD_FEATURE_PREFIX_EXCLUDE": [],
    # 重新识别标记时看哪一端。prefix | suffix | both。不改变已保存标记的匹配端。
    "RAD_MARKER_SIDE": "both",
    # 同一标记至少这么多列才进入识别列表。
    "RAD_MARKER_MIN_N": 5,
    # names: category order; assign: prefix -> category. Empty = one logistic.
    "RAD_FEATURE_PREFIX_GROUP": {"names": [], "assign": {}},
    # fig
    "DO_PLOT": False,
    "DO_PLOT_TIFF": False,
    "DO_PLOT_PDF": False,
    "DO_ROC": True,
    "DO_CORR": True,
    "CORR_GROUP": "",
    "DO_HEATMAP": True,
    "HEATMAP_COLS": [],
    "FIGURE_PANEL_DPI": 300,
    "FIGURE_PANEL_MEDIATION_NCOLS": 2,
    "FIGURE_PANEL_LABEL_POS": "lower_left",
    "FIGURE_PANEL_LABELS": None,
    "FIGURE_PANEL_GAP_PX": 8,
    "FIG_FONT_SIZE": 11.0,
    "FIG_FONT_FAMILY": "Arial",
    "FIG_PALETTE": "npg",
    "FIG_COLORS_MAIN": ["#D55E00", "#B2182B", "#2166AC", "#762A83", "#117733"],
    "FIG_COLORS_AUX": ["#44AA99", "#DDCC77", "#EE8866", "#AA4499", "#332288"],
}


def merged_settings(overlay: dict[str, Any] | None = None) -> dict[str, Any]:
    """DEFAULT_SETTINGS copied, then overlay (ini / console) applied."""
    out = dict(DEFAULT_SETTINGS)
    if overlay:
        out.update(overlay)
    out_dir = out.get("OUT_DIR") or ""
    out["PNG_DIR"] = (
        __import__("os").path.join(str(out_dir), "PNG") if out_dir else "PNG"
    )
    return out
