"""控制台常量：路径、端口、设置键元数据。

纯数据、无状态，不 import console：console.py 先 bootstrap sys.path，
再 import 本模块并把同名符号复用到自己的命名空间。
"""
from __future__ import annotations

from pathlib import Path

from modules.console import paths as _paths

CONSOLE_DIR = Path(__file__).resolve().parent
MODULES_ROOT = CONSOLE_DIR.parent

STAGE_ROOT_NAMES = _paths.STAGE_ROOT_NAMES

GROK_ROOT, RAD_ROOT = _paths.grok_and_drafting(MODULES_ROOT)
EXTRA_PROJECT_ROOTS = tuple(GROK_ROOT / n for n in STAGE_ROOT_NAMES)
HTML_PATH = CONSOLE_DIR / "console.html"
CSS_PATH = CONSOLE_DIR / "console.css"
JS_ASSETS = {
    "/console_schema.js": CONSOLE_DIR / "console_schema.js",
    "/console_api.js": CONSOLE_DIR / "console_api.js",
    "/console_ui.js": CONSOLE_DIR / "console_ui.js",
    "/console_app.js": CONSOLE_DIR / "console_app.js",  # shim
}
JS_PATH = JS_ASSETS["/console_ui.js"]
STROBE_PATH = CONSOLE_DIR / "strobe" / "index.html"
OVERRIDE_PATH = CONSOLE_DIR / "console.override.json"
DEFAULT_PROJECT = "demo_study"
DEFAULT_PORT = 8765
PORT_LO = 8765
PORT_HI = 8776
CHECK_PORT = 8780
PORT_FILE = CONSOLE_DIR / "console.port.json"
# Live registry lives under the local doc tree (outside the GitHub clone).
# Lab default: <GROK_ROOT>/0doc/console.projects.json
REGISTRY_PATH = GROK_ROOT / "0doc" / "console.projects.json"
HOST = "127.0.0.1"
_TOKEN_SAFE_GET = {
    "/", "/console.html", "/xlm_console.html",
    "/console.css",
    "/console_schema.js", "/console_api.js", "/console_ui.js", "/console_app.js",
    "/api/health", "/api/port",
    # 起始页须用 http://127.0.0.1:<port>/start.html（内嵌 token）；file:// 仅可探测 health
}

SKIP_SETTING_KEYS = {
    "os", "copy", "List", "Optional", "Dict", "Any", "annotations",
    "PNG_DIR", "GROUP_SOURCE_COL", "OUT_DIR_BASE",
}
TUPLE_LIST_KEYS = {"DAG_EDGES", "DAG_MEDIATION_HIGHLIGHT"}
SET_OUTCOME_DERIVED = {
    "GROUP_SOURCE_COL", "OUT_DIR", "PNG_DIR",
    "GROUP_DISPLAY_POS", "GROUP_DISPLAY_NEG",
    "SEED", "TRAIN_RATIO",
    # 样本排除为工程级（不进结局覆盖）；勿列入，否则 overlay 写不进 cfg
    "READER_COMPARE_COL", "FORCE_INTER_FEATURES", "FORCE_MODEL_FEATURES",
    "FORCE_REMOVE_FEATURES",
    "SUBGROUP_COL",
    "SKIP_CLIN_FEATURES",
    "GROUP_KEEP", "PAIRWISE_GROUPS", "PAIRWISE_ALL",
    "GROUP_FROM_ID", "GROUP_FROM_ID_SEP", "POSITIVE_GROUP_LABEL",
    "FORCE_CLIN_FOR_ROC", "ROC_PLOT_EXCLUDE",
    "DO_DCA", "DCA_CURVES",
    "DO_NRI", "NRI_CUTOFF", "NRI_MODEL_OLD", "NRI_MODEL_NEW",
    "CORR_PREDICTORS", "CORR_REFS", "CORR_E_REF", "CORR_F_REF",
    "DO_DAG", "DO_SHAP", "DO_MEDIATION",
    "DAG_NODES", "DAG_EDGES", "DAG_MEDIATION_HIGHLIGHT", "MEDIATION_PATHS",
    "FIG_PALETTE", "FIG_COLORS_MAIN", "FIG_COLORS_AUX",
}
SHEET_KEYS = {
    "RAD_SHEET", "CLIN_SHEET", "QC_SHEET", "ICC_SHEET",
}
EXCLUDE_SELECT_KEYS = (
    "EXCLUDE_FILE", "EXCLUDE_SHEET", "EXCLUDE_COL", "EXCLUDE_ROW",
)
EXCLUDE_EMPTY_LABELS = {
    "EXCLUDE_FILE": "（空＝默认工程根 false_classification.xlsx）",
    "EXCLUDE_SHEET": "（空＝默认 exc）",
    "EXCLUDE_COL": "（空＝默认 exc）",
    "EXCLUDE_ROW": "（空＝整列当 ID）",
}
CLIN_COL_SINGLE_KEYS = {
    "CLIN_ID_COL", "LABEL_COL",
    "POSITIVE_GROUP_LABEL", "SUBGROUP_COL",
    "SURVIVAL_TIME_COL", "SPLIT_COL", "NRI_OLD_COL", "NRI_NEW_COL",
}
CONSOLE_DEFAULTS = {
    "DO_SURVIVAL": False,
    "SURVIVAL_TIME_COL": "",
    "SURVIVAL_TIME_UNIT": "months",
    "DO_SURVIVAL_BY_RISK": True,
    "DO_COX": False,
    "GROUP_KEEP": [],
    "PAIRWISE_GROUPS": [],
    "PAIRWISE_ALL": False,
    "GROUP_FROM_ID": False,
    "GROUP_FROM_ID_SEP": "_",
    "DO_PLOT": False,
    "DO_PLOT_TIFF": False,
    "DO_PLOT_PDF": False,
    "DO_LANDMARK_ROC": False,
    "RAD_MARKER_SIDE": "both",
    "RAD_MARKER_MIN_N": 5,
    "DO_ROC": True,
    "DO_HEATMAP": True,
    "FIG_PALETTE": "npg",
    "FIG_COLORS_MAIN": ["#D55E00", "#B2182B", "#2166AC", "#762A83", "#117733"],
    "FIG_COLORS_AUX": ["#44AA99", "#DDCC77", "#EE8866", "#AA4499", "#332288"],
    "VAL_MODE": "lock_threshold",
    "SUBGROUP_VAL_MODE": "",
    "EXTERNAL_VAL_MODE": "lock_threshold",
    "RAD_SHEET": "rad",
    "CLIN_SHEET": "cln",
    "QC_SHEET": "qc",
    "ICC_SHEET": "icc",
    "RAD_ID_COL": "Patient_ID",
    "CLIN_ID_COL": "Patient_ID",
    "LABEL_COL": "",
    "OUTCOME_COLS": [],
    "SKIP_CLIN_FEATURES": [],
    "RAD_ID_COLS": [],
    "GROUP_DISPLAY_POS": "",
    "GROUP_DISPLAY_NEG": "",
    "POSITIVE_GROUP_LABEL": "",
    "EXTERNAL_FILE": "",
    "EXTERNAL_CLIN_SHEET": "cln",
    "EXTERNAL_RAD_SHEET": "rad",
    "EXTERNAL_TESTS": [],
    "USE_INTERNAL_SPLIT_WHEN_EXTERNAL": True,
    "SPLIT_COL": "",
    "CORR_REFS": [],
    "DCA_CURVES": ["Clinical", "RadScore", "Combined"],
    "OUTCOME_OVERRIDES": {},
    "PAIRWISE_OVERRIDES": {},
    "GROUP_LEVEL_DISPLAY": {},
    "SUBGROUP_COL": "",
    "FORCE_REMOVE_FEATURES": [],
    "FORCE_INTER_FEATURES": [],
    "FORCE_MODEL_FEATURES": [],
    "OUTCOME_OUT_SUBDIR": True,
    "DO_EXCLUDE_SAMPLES": False,
    "EXCLUDE_FILE": "",
    "EXCLUDE_SHEET": "",
    "EXCLUDE_COL": "",
    "EXCLUDE_ROW": "",
    "SUBPROJECT_BY_OUTCOME": {},
    "READER_VALUE_MAP": {},
    "OUTCOME_BINARIZE": {},
    "RAD_FEATURE_DISPLAY_STRIP": [],
    "RAD_FEATURE_PREFIX_EXCLUDE": [],
    "RAD_FEATURE_PREFIX_GROUP": {"names": [], "assign": {}},
}
RAD_COL_SINGLE_KEYS = {"RAD_ID_COL", "ICC_FEATURE_COL", "ICC_INTRA_COL", "ICC_INTER_COL"}
LIST_FROM_CLIN = {
    "OUTCOME_COLS", "READER_COLS", "FORCE_REMOVE_FEATURES",
    "FORCE_CLIN_FOR_ROC",
    "CLIN_FORCE_CONTINUOUS", "CLIN_FORCE_CATEGORICAL", "SKIP_CLIN_FEATURES",
    "FORCE_INTER_FEATURES", "FORCE_MODEL_FEATURES",
}
LIST_FROM_RAD_META = {"RAD_ID_COLS"}
_RAD_FEAT_PREFIX = (
    "h0_", "h1_", "h2_", "h3_", "original_", "wavelet_", "log-", "log_",
    "diagnostics_",
)
LIST_FROM_MODELS = {
    "ROC_PLOT_EXCLUDE": ["Clinical", "RadScore", "Combined"],
    "CORR_PREDICTORS": ["RadScore", "Clinical", "Combined"],
    "CORR_REFS": ["Age", "Group"],
    "DCA_CURVES": ["Clinical", "RadScore", "Combined"],
    "NRI_MODEL_OLD": ["clinical", "combined", "radscore"],
    "NRI_MODEL_NEW": ["clinical", "combined", "radscore"],
}
