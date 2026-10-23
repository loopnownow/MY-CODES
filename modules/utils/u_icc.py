"""
u_icc.py — ICC 组内相关系数特征筛选
═══════════════════════════════════════════════════════════════════
功能：通过重复测量数据评估影像组学特征的一致性，排除 ICC 低于阈值
      的低可靠性特征。

数据格式：配对设计 — 每个特征有两次测量列
    列1/列2 → 特征1 (首次测量/重复测量)
    列3/列4 → 特征2 (首次测量/重复测量)
    ...以此类推

ICC 模型：绝对一致性双向随机效应单测量 ICC (A,1)
    ICC(A,1) = (BMS - EMS) / (BMS + (k-1)*EMS + k/n*(JMS-EMS))
其中 k=测量次数(=2)，n=受试者数，BMS/EMS/JMS 分别为受试者间/误差/
测量者间均方（双因素方差分析）。

阈值：ICC ≥ 0.75 为良好一致性 (Cicchetti, 1994)。
═══════════════════════════════════════════════════════════════════
"""

from __future__ import annotations
import re
from dataclasses import dataclass
from typing import List

import numpy as np
import pandas as pd

# ═════════════════════════════════════════════════════════════════
# §1  顶层参数（可覆盖）
# ═════════════════════════════════════════════════════════════════
ICC_MODEL: str = "twoway"       # 双向模型（假定测量者和受试者均为随机效应）
ICC_TYPE: str = "agreement"     # 绝对一致（比 consistency 更严格）
ICC_UNIT: str = "single"        # 单个评分者 ICC
ICC_THRESHOLD: float = 0.75     # 一致性阈值（≥0.75=良好, ≥0.90=极好）
ICC_MIN_N: int = 10             # 每对测量所需的最小有效样本量


@dataclass
class ICCFilterResult:
    results: pd.DataFrame     # 每个特征的 ICC 值
    features: List[str]       # 通过阈值的特征名
    threshold: float


def _icc_twoway_agreement_single(r1: np.ndarray, r2: np.ndarray) -> float:
    """
    ICC(A,1) — 双向随机效应、绝对一致、单测量 ICC。
    """
    n = len(r1)
    k = 2  # 两次测量
    data = np.column_stack([r1, r2])
    grand_mean = data.mean()
    subj_means = data.mean(axis=1)
    rater_means = data.mean(axis=0)

    # 双因素方差分析（受试者 × 测量者）
    ss_subj = k * np.sum((subj_means - grand_mean) ** 2)
    ss_rater = n * np.sum((rater_means - grand_mean) ** 2)
    ss_total = np.sum((data - grand_mean) ** 2)
    ss_error = ss_total - ss_subj - ss_rater

    df_subj = n - 1
    df_rater = k - 1
    df_error = (n - 1) * (k - 1)

    bms = ss_subj / df_subj if df_subj > 0 else np.nan   # 受试者间均方
    jms = ss_rater / df_rater if df_rater > 0 else np.nan  # 测量者间均方
    ems = ss_error / df_error if df_error > 0 else np.nan  # 误差均方

    denom = bms + (k - 1) * ems + (k / n) * (jms - ems)
    if denom == 0 or np.isnan(denom):
        return np.nan
    return float((bms - ems) / denom)


def icc_filter(data: pd.DataFrame, model: str = ICC_MODEL, type_: str = ICC_TYPE,
               unit: str = ICC_UNIT, threshold: float = ICC_THRESHOLD) -> ICCFilterResult:
    """遍历每对相邻列，计算 ICC 值，输出通过/不通过的汇总结果。"""
    print("\n═══ ICC FILTER ═══")
    print(f"  模型={model}  类型={type_}  单位={unit}  阈值={threshold:.2f}")

    if model != "twoway" or type_ != "agreement" or unit != "single":
        print("  ⚠ 当前仅实现 twoway/agreement/single ICC(A,1)；"
              "其余组合请自行扩展 _icc_twoway_agreement_single()。")

    n_cols = data.shape[1]
    if n_cols % 2 != 0:
        raise ValueError("ICC data must have an even number of columns (paired measurements)")

    n_features = n_cols // 2
    rows = []
    for i in range(n_features):
        col_r1, col_r2 = data.columns[2 * i], data.columns[2 * i + 1]
        pair = data[[col_r1, col_r2]].dropna()
        feat_name = re.sub(r"(_r1|_rep1|\.r1|\.rep1)$", "", str(col_r1))

        if len(pair) < ICC_MIN_N:
            rows.append({"Feature": feat_name, "ICC": np.nan})
            print(f"  {feat_name:<40s} 样本不足 (<{ICC_MIN_N})")
            continue

        try:
            icc_val = _icc_twoway_agreement_single(pair[col_r1].to_numpy(),
                                                     pair[col_r2].to_numpy())
        except Exception:
            icc_val = np.nan

        if np.isnan(icc_val):
            rows.append({"Feature": feat_name, "ICC": np.nan})
            print(f"  {feat_name:<40s}  计算失败")
            continue

        rows.append({"Feature": feat_name, "ICC": round(icc_val, 4)})
        mark = "✓" if icc_val >= threshold else "✗"
        print(f"  {feat_name:<40s}  ICC={icc_val:.4f}  {mark}")

    icc_results = pd.DataFrame(rows)
    passed_features = icc_results.loc[icc_results["ICC"] >= threshold, "Feature"].tolist()
    print(f"  通过 ICC≥{threshold:.2f}: {len(passed_features)} / {n_features}")

    return ICCFilterResult(results=icc_results, features=passed_features, threshold=threshold)
