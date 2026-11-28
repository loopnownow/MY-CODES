# -*- coding: utf-8 -*-
"""Delta（pre/post 变化量）特征计算与队列汇总（并入自 delta_habitat_pipeline）。

与 core 的分工
--------------
radiomics.py 负责"单时间点、按序列"提特征；本模块只做两件纯 DataFrame 的事：
1. 逐 Habitat 计算 Δf / Δf% / Δf_rate；
2. 把逐患者结果展平为队列矩阵（列名 h{H}_{feature}）。

刻意不依赖 config
------------------
参数全部由调用方传入（默认值与 config.DELTA 一致）；这样单测和批处理都不需要
settings.ini，也不会因为缺 [DELTA] 段而中断既有工程。

数据泄露
--------
Z-score 标准化不在本模块做：必须在全队列汇总后、仅用训练集统计量完成。
"""
from __future__ import annotations

import logging
import os

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def compute_delta_features(features_pre: pd.DataFrame,
                           features_post: pd.DataFrame,
                           patient_id: str,
                           output_dir: str,
                           delta_t_days: float = None,
                           compute_absolute: bool = True,
                           compute_relative: bool = True,
                           epsilon: float = 1e-6) -> pd.DataFrame:
    """逐 Habitat、逐特征计算 Delta。

    Δf      = f_post - f_pre
    Δf%     = (f_post - f_pre) / (|f_pre| + epsilon) * 100
    Δf_rate = (f_post - f_pre) / delta_t_days      （仅 delta_t_days > 0 时）

    Parameters
    ----------
    features_pre / features_post : index=Habitat 编号，列=特征名（两侧须同名）
    output_dir : 逐患者 CSV 落点（p<patient_id>_delta_features.csv）
    delta_t_days : 两次扫描间隔天数，用于变化率

    Returns
    -------
    pd.DataFrame — 行=Habitat，列=delta_abs_ / delta_pct_ / delta_rate_
    """
    logger.info("[Delta 计算] 患者 %s", patient_id)
    os.makedirs(output_dir, exist_ok=True)

    common_cols = features_pre.columns.intersection(features_post.columns)
    if len(common_cols) == 0:
        raise ValueError(
            "患者 %s：pre 与 post 特征列名无交集，"
            "请确认两个时间点使用了完全相同的 Pictologics 配置。" % patient_id
        )

    common_idx = features_pre.index.intersection(features_post.index)
    if len(common_idx) == 0:
        raise ValueError(
            "患者 %s：pre 与 post Habitat 索引无交集，"
            "请检查 Mask Propagation 是否正确执行。" % patient_id
        )
    if len(common_idx) < len(features_pre.index):
        logger.warning("  Habitat 索引部分不一致：pre=%s，post=%s，仅算共有 %s",
                       list(features_pre.index), list(features_post.index),
                       list(common_idx))

    f_pre = features_pre.loc[common_idx, common_cols]
    f_post = features_post.loc[common_idx, common_cols]

    delta_records = {}
    for h in common_idx:
        v_pre = f_pre.loc[h].values.astype(np.float64)
        v_post = f_post.loc[h].values.astype(np.float64)
        diff = v_post - v_pre
        row = {}

        if compute_absolute:
            for name, val in zip(common_cols, diff):
                row["delta_abs_%s" % name] = float(val)

        if compute_relative:
            with np.errstate(divide="ignore", invalid="ignore"):
                pct = diff / (np.abs(v_pre) + epsilon) * 100.0
            for name, val in zip(common_cols, pct):
                row["delta_pct_%s" % name] = float(val)

        if delta_t_days is not None and delta_t_days > 0:
            rate = diff / delta_t_days
            for name, val in zip(common_cols, rate):
                row["delta_rate_%s" % name] = float(val)

        delta_records[h] = row

    delta_df = pd.DataFrame(delta_records).T
    delta_df.index.name = "habitat"

    save_path = os.path.join(output_dir, "p%s_delta_features.csv" % patient_id)
    delta_df.to_csv(save_path)
    logger.info("  Delta 特征：%d Habitat x %d 列 -> %s",
                delta_df.shape[0], delta_df.shape[1], save_path)
    return delta_df


def aggregate_delta_features(patient_delta_dict: dict) -> pd.DataFrame:
    """逐患者 Delta 展平为队列矩阵：行=patient_id，列=h{H}_{delta_feature}。

    列名带 Habitat 前缀，避免 K>1 时同名特征互相覆盖。
    """
    patient_rows = {}
    for patient_id, delta_df in patient_delta_dict.items():
        flat_row = {}
        for h in delta_df.index:
            for col in delta_df.columns:
                flat_row["h%s_%s" % (h, col)] = delta_df.loc[h, col]
        patient_rows[patient_id] = flat_row

    result_df = pd.DataFrame(patient_rows).T
    result_df.index.name = "patient_id"
    logger.info("[汇总] 队列 Delta 矩阵：%d 患者 x %d 特征",
                result_df.shape[0], result_df.shape[1])
    return result_df