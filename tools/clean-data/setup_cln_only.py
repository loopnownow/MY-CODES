# -*- coding: utf-8 -*-
"""Fill empty IDs and write clinical-only settings.ini（默认 wkl_UGIB）。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd

_TIDY = Path(__file__).resolve().parent
# 算法库根（modules 包所在 = MY-CODES 仓根；2026-11-01 迁入 tools/clean-data/）
STATS_ROOT = Path(os.environ.get("STATS_ROOT") or Path(__file__).resolve().parents[2])  # 仓根（本脚本在 tools/<tool>/ 下）
if str(STATS_ROOT) not in sys.path:
    sys.path.insert(0, str(STATS_ROOT))
if str(_TIDY) not in sys.path:
    sys.path.insert(0, str(_TIDY))

# ==================== CONFIG ====================
# 历史落点在 done\；可用 WKL_UGIB_ROOT 覆盖
PROJ = Path(os.environ.get("WKL_UGIB_ROOT") or Path(__file__).resolve().parents[5] / "in-review" / "wkl_UGIB")
XLSX = PROJ / "wkl_UGIB.xlsx"
INI = PROJ / "ref" / "settings.ini"
MODULES_DIR = Path(os.environ.get("MODULES_DIR", str(STATS_ROOT / "modules")))
DRY_RUN = False
# ================================================

from modules.config.ini_io import write_ini  # noqa: E402


def fill_ids() -> int:
    df = pd.read_excel(XLSX, sheet_name="cln")
    col = "Pateint_ID"
    if col not in df.columns:
        raise SystemExit(f"missing {col}")
    n_null = int(df[col].isna().sum())
    if n_null == 0 and df[col].nunique(dropna=True) == len(df):
        print(f"IDs already unique n={len(df)}")
        return 0
    df[col] = [f"UGIB_{i:03d}" for i in range(1, len(df) + 1)]
    with pd.ExcelWriter(XLSX, engine="openpyxl", mode="a", if_sheet_exists="replace") as w:
        df.to_excel(w, sheet_name="cln", index=False)
    print(f"filled {col} n={len(df)} (was null {n_null})")
    return len(df)


def write_settings() -> None:
    INI.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "SEED": 66666,
        "TRAIN_RATIO": 0.7,
        "INPUT": "wkl_UGIB.xlsx",
        "MODULES_DIR": str(MODULES_DIR),
        "RAD_SHEET": "",
        "CLIN_SHEET": "cln",
        "QC_SHEET": "",
        "ICC_SHEET": "",
        "RAD_ID_COL": "Pateint_ID",
        "CLIN_ID_COL": "Pateint_ID",
        "LABEL_COL": "Pateint_ID",
        "CLIN_PATH": "",
        "RAD_PATH": "",
        "OUT_DIR": "",
        "OUTCOME_OUT_SUBDIR": True,
        "EXTERNAL_FILE": "",
        "EXTERNAL_CLIN_SHEET": "",
        "EXTERNAL_RAD_SHEET": "",
        "EXTERNAL_TESTS": [],
        "USE_INTERNAL_SPLIT_WHEN_EXTERNAL": True,
        "OUTCOME_COLS": ["Group"],
        "OUTCOME_OVERRIDES": {
            "Group": {
                "POSITIVE_GROUP_LABEL": "UGIB",
                "GROUP_DISPLAY_POS": "UGIB",
                "GROUP_DISPLAY_NEG": "non-UGIB",
            }
        },
        "PAIRWISE_OVERRIDES": {},
        "SKIP_CLIN_FEATURES": ["Pateint_ID", "Group"],
        "SUBPROJECT_BY_OUTCOME": {},
        "GROUP_COL_KEYWORD": "Group",
        "POSITIVE_GROUP_LABEL": "UGIB",
        "GROUP_DISPLAY_POS": "UGIB",
        "GROUP_DISPLAY_NEG": "non-UGIB",
        "DO_EXCLUDE_SAMPLES": False,
        "EXCLUDE_FILE": "",
        "EXCLUDE_SHEET": "",
        "EXCLUDE_COL": "",
        "EXCLUDE_ROW": "",
        "EXCLUDE_IDS": [],
        "READER_VALUE_MAP": {"Positive": 1, "Negative": 0},
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
        "TABLE1_P_THRESHOLD": 0.1,
        "ORDINAL_MAX_LEVELS": 10,
        "CLIN_FORCE_CONTINUOUS": [],
        "CLIN_FORCE_CATEGORICAL": [],
        "FORCE_REMOVE_FEATURES": [],
        "FORCE_INTER_FEATURES": [],
        "FORCE_MODEL_FEATURES": [],
        "READER_COLS": [],
        "RAD_ID_COLS": ["Pateint_ID"],
        "DO_PLOT": False,
        "DO_FALSE_CLASS": True,
        "ENABLE_CORR_FILTER": True,
        "DO_FIGURE_PANELS": True,
        "DO_QC_REPORT": True,
        "QC_HTML": "qc.html",
        "DO_ICC_FILTER": False,
        "ICC_THRESHOLD": 0.75,
        "ICC_FEATURE_COL": "",
        "ICC_INTRA_COL": "",
        "ICC_INTER_COL": "",
        "ICC_REQUIRE_ROW": False,
        "DO_COMBINED_MODEL": False,
        "DO_NOMOGRAM": True,
        "DO_CALIBRATION": True,
        "ROC_MV_P_THRESHOLD": 0.05,
        "FORCE_CLIN_FOR_ROC": [],
        "ROC_PLOT_EXCLUDE": [],
        "DO_DCA": True,
        "DCA_CURVES": ["Clinical"],
        "DO_NRI": False,
        "NRI_CUTOFF": [0, 0.3, 0.7, 1],
        "NRI_MODEL_OLD": ["clinical"],
        "NRI_MODEL_NEW": ["combined"],
        "CORR_PREDICTORS": ["Clinical"],
        "CORR_REFS": ["Age (y)", "Group"],
        "DO_SURVIVAL": False,
        "SURVIVAL_TIME_COL": "",
        "SURVIVAL_TIME_UNIT": "months",
        "DO_SURVIVAL_BY_RISK": True,
        "DO_COX": False,
        "DO_VIF": True,
        "DO_DAG": False,
        "DO_SHAP": False,
        "DO_MEDIATION": False,
        "VIF_THRESHOLD_WARN": 5,
        "VIF_THRESHOLD_HIGH": 10,
        "SHAP_MAX_SAMPLES": 200,
        "MEDIATION_BOOTSTRAP": 500,
        "DAG_NODES": ["Age (y)", "Group"],
        "DAG_EDGES": [["Age (y)", "Group"]],
        "DAG_MEDIATION_HIGHLIGHT": [],
        "MEDIATION_PATHS": [],
        "RAD_FEATURE_SOURCE": "both",
        "RAD_COR_CUTOFF": 0.95,
        "ALPHA": 1,
        "LASSO_NFOLDS": 10,
        "LASSO_N_LAMBDAS": 40,
        "LASSO_MAX_ITER": 2000,
        "LASSO_N_JOBS": -1,
        "LASSO_C_MAX": 50,
        "LASSO_C_MIN": 0.001,
        "LASSO_TOL": 0.001,
        "LASSO_PREFILTER_TOPK": 25,
        "LASSO_PLOT_PATH": False,
        "RAD_FEATURE_DISPLAY_STRIP": [],
        "FIGURE_PANEL_DPI": 150,
        "FIGURE_PANEL_MEDIATION_NCOLS": 2,
        "FIGURE_PANEL_LABEL_POS": "lower_left",
        "FIGURE_PANEL_LABELS": True,
        "FIGURE_PANEL_GAP_PX": 12,
        "FIG_FONT_SIZE": 11,
        "FIG_PALETTE": "npg",
        "FIG_COLORS_MAIN": ["#D55E00", "#B2182B", "#2166AC", "#762A83", "#117733"],
        "FIG_COLORS_AUX": ["#44AA99", "#DDCC77", "#EE8866", "#AA4499", "#332288"],
    }
    write_ini(INI, data)
    print("wrote", INI)


if __name__ == "__main__":
    print(f"PROJ={PROJ}")
    if DRY_RUN:
        print("DRY_RUN=True — stop before write")
        raise SystemExit(0)
    fill_ids()
    write_settings()
