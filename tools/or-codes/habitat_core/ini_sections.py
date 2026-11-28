# -*- coding: utf-8 -*-
"""settings.ini 的段与键元数据（写回顺序 + 配置页标签共用一份）。

改键名只改这里，config_server.py 与各工程 settings.html 都跟着变。
"""
from __future__ import annotations

# (段 id, 段标题, 键列表)；写回 ini 时按此顺序
SECTIONS: list[tuple[str, str, list[str]]] = [
    ("paths", "数据与路径", ["PROJECT", "CORE_ROOT", "OUTPUT_ROOT", "ARCHIVE_ROOT",
                             "DATA_TABLE_PATH", "LABELS_TABLE_PATH", "QC_DIR",
                             "IMG_ROOT", "ROI_ROOT", "BACKUP_DIR"]),
    ("preproc", "预处理（按模态）", ["PREPROC"]),
    ("registration", "序列间配准（ANTsPy）", ["REGISTRATION"]),
    ("parametric", "参数图派生规则", ["PARAMETRIC_MAPS", "PARAMETRIC_MAP_EPSILON"]),
    ("voxel", "体素级融合特征", ["VOXEL_FEATURES"]),
    ("habitat", "生境聚类", ["HABITAT"]),
    ("radiomics", "影像组学特征提取", ["RADIOMICS"]),
    ("delta", "Delta 特征（pre/post）", ["DELTA"]),
    ("feature_selection", "特征筛选", ["FEATURE_SELECTION"]),
    ("memory", "内存管理", ["MEMORY"]),
    ("qc_log", "日志解析（tools/log_reader.py）", [
        "LOG_INPUT_PATHS", "LOG_SUMMARY_CSV", "LOG_ERR_CSV", "GENERATE_ERR_CSV",
        "LOG_ENCODING", "TIME_FMT", "RECURSIVE", "MERGE_STRATEGY"]),
    ("datasets", "多数据集（本项目自有）", ["DATASETS"]),
    ("tools", "加工脚本路径（OR-CODES\\tools）", [
        "ADC_ROOT_DIR", "CLEANUP_TARGET_DIR", "CLEANUP_ERR_CSV",
        "FIT_DKI_ROOT_DIR", "FIT_IVIM_ROOT_DIR", "MATCH_CONFIG",
        "PLOT_MODEL_PATH", "PLOT_POOLED_PATH", "PLOT_OUTPUT_DIR",
        "ROI_DEFAULT_PATH", "ROI_LOG_DIR", "ROI_CHECKPOINT_DIR"]),
    ("console", "配置页", ["CONFIG_PAGE", "SERVER_AUTOSTART"]),
]

# 键 -> 中文说明（配置页显示）
KEY_LABELS: dict[str, str] = {
    "PROJECT": "工程名（lowercase_UPPERCASE）",
    "CORE_ROOT": "共享代码根（OR-CODES）",
    "ARCHIVE_ROOT": "结果/QC/数据归档根（D:）；留空=与 OUTPUT_ROOT 同根",
    "BACKUP_DIR": "配置备份目录（改 ini 时原件存这里）",
    "DATA_TABLE_PATH": "数据表（长表 CSV）",
    "LABELS_TABLE_PATH": "标签表（默认同数据表）",
    "OUTPUT_ROOT": "结果根目录",
    "QC_DIR": "QC 汇总目录",
    "IMG_ROOT": "影像根（NAS 只读）",
    "ROI_ROOT": "ROI 根（NAS 只读）",
    "PREPROC": "预处理：目标体素间距 / 模态策略 / 序列覆盖",
    "REGISTRATION": "SyN 迭代 / 平滑 / 下采样 / 相似度测度",
    "PARAMETRIC_MAPS": "参数图派生规则（列表）",
    "PARAMETRIC_MAP_EPSILON": "参数图除零保护",
    "VOXEL_FEATURES": "体素级融合特征开关",
    "HABITAT": "k 范围 / 共识聚类 / Superpixel",
    "RADIOMICS": "标准化配置名 / 序列 / 逐生境保存",
    "DELTA": "Delta 计算开关（绝对/相对/变化率）与除零保护",
    "FEATURE_SELECTION": "近零方差阈值 / 相关冗余阈值",
    "MEMORY": "懒加载 / 回收频率",
    "LOG_INPUT_PATHS": "日志所在目录（可多个）",
    "LOG_SUMMARY_CSV": "患者主表输出",
    "LOG_ERR_CSV": "失败患者表输出",
    "GENERATE_ERR_CSV": "是否生成失败表",
    "LOG_ENCODING": "日志编码",
    "TIME_FMT": "日志时间格式",
    "RECURSIVE": "是否递归子目录",
    "MERGE_STRATEGY": "多次运行合并策略",
    "DATASETS": "多数据集根与数据表",
    "CONFIG_PAGE": "本工程配置页文件名",
    "SERVER_AUTOSTART": "打开配置页时自动拉服务（true/false）",
    "ADC_ROOT_DIR": "ADC 拟合根目录",
    "CLEANUP_TARGET_DIR": "清理目标目录（checkpoints）",
    "CLEANUP_ERR_CSV": "清理依据的失败患者表",
    "FIT_DKI_ROOT_DIR": "DKI 拟合根目录",
    "FIT_IVIM_ROOT_DIR": "IVIM 拟合根目录",
    "MATCH_CONFIG": "影像↔ROI 配对配置（tasks / output_dir）",
    "PLOT_MODEL_PATH": "生境全局模型 joblib",
    "PLOT_POOLED_PATH": "队列池化特征 npy",
    "PLOT_OUTPUT_DIR": "绘图输出目录",
    "ROI_DEFAULT_PATH": "ROI 外扩默认根",
    "ROI_LOG_DIR": "ROI 外扩日志目录",
    "ROI_CHECKPOINT_DIR": "ROI 外扩断点目录",
}

# 只读键：配置页不给改（防止把代码根/工程名改坏）
READONLY_KEYS = {"PROJECT", "CORE_ROOT", "CONFIG_PAGE"}

# 写回时按 JSON 保存的复合类型键（其余按标量）
COMPOSITE_KEYS = {k for _sid, _t, keys in SECTIONS for k in keys if k.isupper() and (
    k in {"PREPROC", "REGISTRATION", "PARAMETRIC_MAPS", "VOXEL_FEATURES", "HABITAT",
          "RADIOMICS", "FEATURE_SELECTION", "MEMORY", "DATASETS", "LOG_INPUT_PATHS"})}
