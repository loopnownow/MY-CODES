# -*- coding: utf-8 -*-
"""把 <工程>/settings.ini 读成模块级常量。

与旧 habitat_pipeline/config.py 同名同义（旧文件每个工程一份，只有 3 行路径不同）。
路径与全部参数现在只存在工程根的 settings.ini。

派生项（不写进 ini，改 OUTPUT_ROOT 自动跟随）：
  DIRS、HABITAT["global_model_path"]、HABITAT["pooled_features_path"]

运行: from . import config;  config.DIRS / config.HABITAT ...
"""
from __future__ import annotations

import os

from .ini_config import find_ini, read_ini, require

INI_PATH = find_ini()
_CFG = read_ini(INI_PATH)

PROJECT = str(_CFG.get("PROJECT") or "")
CORE_ROOT = str(_CFG.get("CORE_ROOT") or "")
DATA_TABLE_PATH = require(_CFG, "DATA_TABLE_PATH")
LABELS_TABLE_PATH = _CFG.get("LABELS_TABLE_PATH") or DATA_TABLE_PATH
OUTPUT_ROOT = require(_CFG, "OUTPUT_ROOT")
# 归档根：结果 04-06 与 QC 落这里；未配置则与 OUTPUT_ROOT 同根（单根模式）
ARCHIVE_ROOT = str(_CFG.get("ARCHIVE_ROOT") or OUTPUT_ROOT)
QC_DIR = _CFG.get("QC_DIR") or os.path.join(os.path.dirname(str(OUTPUT_ROOT)), "QC")

# 各阶段输出子目录（由 pipeline.py 的 _setup_dirs() 自动创建）
#   01-03：处理后中间数据，随 OUTPUT_ROOT 留在工作盘
#   04-06：结果，随 ARCHIVE_ROOT 归档到 D:
DIRS = {
    "preprocessed":   os.path.join(OUTPUT_ROOT, "01_preprocessed"),
    "registration":   os.path.join(OUTPUT_ROOT, "02_registration"),
    "habitat_masks":  os.path.join(OUTPUT_ROOT, "03_habitat_masks"),
    "features":       os.path.join(ARCHIVE_ROOT, "results", "04_features"),
    "selected":       os.path.join(ARCHIVE_ROOT, "results", "05_selected"),
    "qc_reports":     os.path.join(ARCHIVE_ROOT, "results", "06_qc"),
}

PREPROC = require(_CFG, "PREPROC")
REGISTRATION = require(_CFG, "REGISTRATION")
PARAMETRIC_MAPS = require(_CFG, "PARAMETRIC_MAPS")
PARAMETRIC_MAP_EPSILON = float(require(_CFG, "PARAMETRIC_MAP_EPSILON"))
VOXEL_FEATURES = require(_CFG, "VOXEL_FEATURES")
RADIOMICS = require(_CFG, "RADIOMICS")
FEATURE_SELECTION = require(_CFG, "FEATURE_SELECTION")
# [DELTA] 段：delta 流程用；缺段时回落到默认值，不中断既有工程
DELTA = dict(_CFG.get("DELTA") or {
    "compute_absolute": True,
    "compute_relative": True,
    "epsilon": 1e-6,
})
MEMORY = require(_CFG, "MEMORY")

HABITAT = dict(require(_CFG, "HABITAT"))
HABITAT.setdefault(
    "global_model_path", os.path.join(str(OUTPUT_ROOT), "global_habitat_model.joblib")
)
HABITAT.setdefault(
    "pooled_features_path", os.path.join(str(OUTPUT_ROOT), "cohort_pooled_features.npy")
)
