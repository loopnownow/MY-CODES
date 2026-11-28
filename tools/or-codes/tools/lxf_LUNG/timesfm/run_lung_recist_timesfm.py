#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_lung_recist_timesfm.py
==========================
肺癌时序影像 TimesFM RECIST 预测 · 主运行脚本

功能模块：
  A. 模型加载与编译（TimesFM 2.5-200M PyTorch）
  B. Leave-one-out 预测（point forecast + quantile 不确定性）
  C. 多靶病灶支持（SLD 序列 → 聚合 RECIST 分类）
  D. RECIST 1.1 分类（CR / PR / SD / PD）
  E. 统计验证（vs persistence baseline + linear baseline）
     - Accuracy、Weighted Kappa、混淆矩阵
     - Bootstrap 95% CI
     - 分位数不确定性分析
  F. 可视化输出（轨迹图、混淆矩阵热力图、Bland-Altman）
  G. 自动生成 Markdown 摘要报告

用法：
  python run_lung_recist_timesfm.py --config config.yaml
  python run_lung_recist_timesfm.py --config config.yaml --features artifacts/lung_features_long.csv

依赖：
  pip install timesfm pandas numpy matplotlib seaborn scikit-learn pyyaml

作者：Dr. Ying Li, Jinshan Hospital, Fudan University
版本：1.0.0（2026-06）
"""

import argparse
import json
import logging
import os
import random
import sys
import warnings
from pathlib import Path
from typing import List, Optional, Tuple

import matplotlib
matplotlib.use("Agg")  # 无头服务器模式，无需 display
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd
import seaborn as sns
import yaml
from sklearn.metrics import (
    accuracy_score,
    cohen_kappa_score,
    confusion_matrix,
    classification_report,
)

# ── 忽略 sklearn 版本警告 ──────────────────────────────────────────────────
warnings.filterwarnings("ignore", category=FutureWarning)


# =============================================================================
# 工具函数：日志 / 配置 / 随机种子
# =============================================================================
def setup_logging(verbose: bool = False) -> logging.Logger:
    """初始化日志器（verbose=True → DEBUG；否则 INFO）"""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    return logging.getLogger(__name__)


def load_config(config_path: str) -> dict:
    """从 YAML 文件加载全局配置"""
    p = Path(config_path)
    if not p.exists():
        raise FileNotFoundError(f"配置文件不存在：{p}")
    with open(p, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def set_global_seed(seed: int) -> None:
    """固定所有随机种子，保证结果可复现"""
    random.seed(seed)
    np.random.seed(seed)
    os.environ["PYTHONHASHSEED"] = str(seed)


# =============================================================================
# 模块 A：加载 TimesFM 模型
# =============================================================================
def load_timesfm_model(cfg: dict, logger: logging.Logger):
    """
    加载并编译 TimesFM 2.5-200M PyTorch 模型。

    流程：
    1. from_pretrained() 从 HuggingFace Hub 下载权重（首次约 1GB）
    2. model.compile() 绑定 ForecastConfig，构建推理图
    3. 返回编译完成的 model 对象

    参数
    ----
    cfg    : 全局 config.yaml 加载后的字典
    logger : 日志器

    返回
    ----
    model  : 已编译的 TimesFM 模型实例
    """
    try:
        import torch
        import timesfm
    except ImportError as e:
        logger.error(
            f"缺少依赖：{e}。请执行："
            f"\n  pip install torch --index-url https://download.pytorch.org/whl/cu121"
            f"\n  pip install -e 'path/to/timesfm[torch]'"
        )
        sys.exit(1)

    model_cfg = cfg.get("model", {})
    fc_cfg = model_cfg.get("forecast_config", {})

    # ── 设置 PyTorch matmul 精度（A100 上显著提升吞吐）────────────────────
    matmul_precision = model_cfg.get("matmul_precision", "high")
    torch.set_float32_matmul_precision(matmul_precision)
    logger.info(f"torch matmul precision = {matmul_precision}")

    # ── 加载预训练权重 ───────────────────────────────────────────────────
    pretrained_id = model_cfg.get("pretrained_id", "google/timesfm-2.5-200m-pytorch")
    use_compile = model_cfg.get("torch_compile", True)
    logger.info(f"加载 TimesFM 模型：{pretrained_id}（torch_compile={use_compile}）")

    model = timesfm.TimesFM_2p5_200M_torch.from_pretrained(
        pretrained_id,
        torch_compile=use_compile,   # 控制 torch.compile() 的推理图优化
    )

    # ── 编译模型（绑定 ForecastConfig）──────────────────────────────────
    #    ForecastConfig 决定：最大 context 长度、预测步长上界、归一化策略
    #    compile() 必须在 from_pretrained() 之后、forecast() 之前调用
    model.compile(
        timesfm.ForecastConfig(
            max_context=fc_cfg.get("max_context", 512),
            max_horizon=fc_cfg.get("max_horizon", 64),
            normalize_inputs=fc_cfg.get("normalize_inputs", True),
            use_continuous_quantile_head=fc_cfg.get("use_continuous_quantile_head", True),
            fix_quantile_crossing=fc_cfg.get("fix_quantile_crossing", True),
            force_flip_invariance=fc_cfg.get("force_flip_invariance", False),
            infer_is_positive=fc_cfg.get("infer_is_positive", True),
        )
    )
    logger.info("TimesFM 模型编译完成，准备推理")
    return model


# =============================================================================
# 模块 B：RECIST 1.1 分类函数
# =============================================================================
def recist_category(
    pct_change: float,
    pred_value: Optional[float] = None,
    pr_threshold: float = -30.0,
    pd_threshold: float = 20.0,
    cr_volume_threshold_ml: float = 0.1,
    cr_diameter_threshold_mm: float = 5.0,
    feature_col: str = "volume_ml",
) -> str:
    """
    根据百分比变化（相对基线）分类 RECIST 1.1 响应。

    RECIST 1.1 标准：
    - CR（Complete Response）：所有靶灶消失
      → 此处用 volume < 0.1 ml 或 diameter < 5 mm 作为 proxy
    - PR（Partial Response）：SLD 较基线缩小 ≥ 30%
    - PD（Progressive Disease）：SLD 较基线增大 ≥ 20%（或出现新病灶）
    - SD（Stable Disease）：介于 PR 与 PD 之间

    参数
    ----
    pct_change              : 变化百分比（负=缩小，正=增大）
    pred_value              : 预测的绝对值（ml 或 mm），用于 CR 判断
    pr_threshold            : PR 阈值（默认 -30.0，来自 config）
    pd_threshold            : PD 阈值（默认 +20.0，来自 config）
    cr_volume_threshold_ml  : CR 体积阈值（ml，默认 0.1）
    cr_diameter_threshold_mm: CR 直径阈值（mm，默认 5.0）
    feature_col             : 当前使用的特征列（决定 CR 阈值类型）

    返回
    ----
    str：'CR' / 'PR' / 'SD' / 'PD'
    """
    # ── CR 判断（优先于百分比规则）──────────────────────────────────────
    if pred_value is not None:
        if feature_col == "volume_ml" and pred_value < cr_volume_threshold_ml:
            return "CR"
        if feature_col == "max_diam_mm" and pred_value < cr_diameter_threshold_mm:
            return "CR"

    # ── 百分比变化规则（RECIST 1.1 标准顺序：先 PD，再 PR，最后 SD）──
    if pct_change >= pd_threshold:
        return "PD"
    elif pct_change <= pr_threshold:
        return "PR"
    else:
        return "SD"


# =============================================================================
# 模块 C：Baseline 预测函数（用于对比验证）
# =============================================================================
def predict_persistence(context: np.ndarray) -> float:
    """
    Persistence Baseline（零阶外推）：预测值 = context 最后一个观测值。
    最简单的 baseline，TimesFM 在短序列上的表现不应劣于此。
    """
    return float(context[-1])


def predict_linear(context: np.ndarray) -> float:
    """
    Linear Baseline（一阶线性外推）：对 context 拟合线性趋势，外推一步。
    context 长度 ≥ 2 时有效；否则退化为 persistence。
    """
    if len(context) < 2:
        return predict_persistence(context)
    # 用序列索引作为 x（等间隔假设），context 值作为 y
    x = np.arange(len(context), dtype=float)
    # np.polyfit(x, y, deg=1) 返回 [slope, intercept]
    slope, intercept = np.polyfit(x, context, 1)
    # 预测下一步（x = len(context)）
    pred = slope * len(context) + intercept
    return float(pred)


# =============================================================================
# 模块 D：Bootstrap 置信区间
# =============================================================================
def bootstrap_metric(
    y_true: List[str],
    y_pred: List[str],
    metric_fn,
    n_bootstrap: int = 1000,
    seed: int = 42,
) -> Tuple[float, float, float]:
    """
    用 Bootstrap 重采样估计分类指标的 95% 置信区间。

    参数
    ----
    y_true      : 真实类别列表
    y_pred      : 预测类别列表
    metric_fn   : 计算指标的函数（接受 y_true, y_pred，返回 float）
    n_bootstrap : Bootstrap 重采样次数（默认 1000）
    seed        : 随机种子

    返回
    ----
    (点估计, CI_lower, CI_upper)，均为 float
    """
    rng = np.random.RandomState(seed)
    n = len(y_true)
    y_true_arr = np.array(y_true)
    y_pred_arr = np.array(y_pred)

    # 点估计（全样本）
    point_est = metric_fn(y_true_arr, y_pred_arr)

    # Bootstrap 循环
    boot_vals = []
    for _ in range(n_bootstrap):
        # 有放回重采样
        idx = rng.randint(0, n, size=n)
        bt = y_true_arr[idx]
        bp = y_pred_arr[idx]
        # 跳过只有一个类别的退化样本（Kappa 无法计算）
        if len(np.unique(bt)) < 2:
            continue
        try:
            boot_vals.append(metric_fn(bt, bp))
        except Exception:
            continue

    if not boot_vals:
        return point_est, float("nan"), float("nan")

    ci_lo = float(np.percentile(boot_vals, 2.5))
    ci_hi = float(np.percentile(boot_vals, 97.5))
    return point_est, ci_lo, ci_hi


# =============================================================================
# 模块 E：可视化函数
# =============================================================================
def plot_patient_trajectory(
    pid: str,
    g: pd.DataFrame,
    pred_vol: float,
    feature_col: str,
    out_dir: Path,
    dpi: int = 300,
) -> None:
    """
    绘制单患者肿瘤体积/直径时间轨迹图。

    图示：
    - 蓝色实线 + 圆点：历史 context 数据（前 n-1 个时间点）
    - 红色星形：Leave-one-out 真实值（第 n 个时间点）
    - 绿色菱形：TimesFM 预测值（第 n 个时间点）
    """
    fig, ax = plt.subplots(figsize=(7, 4))

    days = g["days_from_tx_start"].values
    vals = g[feature_col].values
    unit = "ml" if feature_col == "volume_ml" else "mm"

    # 历史轨迹（context = 前 n-1 点）
    ax.plot(
        days[:-1], vals[:-1],
        "o-", color="#2563eb", linewidth=1.8, markersize=6,
        label="历史（context）",
    )
    # 真实最后一个时间点
    ax.plot(
        days[-1], vals[-1],
        "*", color="#dc2626", markersize=14,
        label=f"实测（day {int(days[-1])}）",
    )
    # TimesFM 预测值
    ax.plot(
        days[-1], pred_vol,
        "D", color="#16a34a", markersize=10,
        label=f"TimesFM 预测",
    )

    ax.set_xlabel("治疗后天数（days from tx start）")
    ax.set_ylabel(f"{feature_col} ({unit})")
    ax.set_title(f"患者 {pid} · 肿瘤轨迹")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)
    sns.despine(ax=ax)

    fig.tight_layout()
    fig.savefig(out_dir / f"{pid}_trajectory.png", dpi=dpi, bbox_inches="tight")
    plt.close(fig)


def plot_confusion_matrix(
    y_true: List[str],
    y_pred: List[str],
    title: str,
    out_path: Path,
    dpi: int = 300,
) -> None:
    """
    绘制标准化混淆矩阵热力图（行归一化，显示比例 + 原始计数）。
    类别顺序固定为 CR / PR / SD / PD，与 RECIST 临床顺序一致。
    """
    labels = ["CR", "PR", "SD", "PD"]
    # 只保留实际出现的类别（避免空行/列）
    present = sorted(set(y_true) | set(y_pred), key=lambda x: labels.index(x) if x in labels else 99)

    cm = confusion_matrix(y_true, y_pred, labels=present)
    # 行归一化（每行总和 = 1，反映召回率）
    cm_norm = cm.astype(float) / (cm.sum(axis=1, keepdims=True) + 1e-9)

    fig, ax = plt.subplots(figsize=(6, 5))
    sns.heatmap(
        cm_norm,
        annot=False,  # 手动添加注释（同时显示比例和原始计数）
        fmt="",
        cmap="Blues",
        xticklabels=present,
        yticklabels=present,
        vmin=0, vmax=1,
        linewidths=0.5,
        ax=ax,
    )
    # 在每个格子中同时显示归一化比例（大字）和原始计数（小字）
    for i in range(len(present)):
        for j in range(len(present)):
            text_color = "white" if cm_norm[i, j] > 0.6 else "black"
            ax.text(
                j + 0.5, i + 0.4,
                f"{cm_norm[i, j]:.2f}",
                ha="center", va="center",
                fontsize=13, fontweight="bold", color=text_color,
            )
            ax.text(
                j + 0.5, i + 0.65,
                f"n={cm[i, j]}",
                ha="center", va="center",
                fontsize=8, color=text_color,
            )

    ax.set_xlabel("预测 RECIST")
    ax.set_ylabel("实测 RECIST")
    ax.set_title(title)
    fig.tight_layout()
    fig.savefig(out_path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)


def plot_bland_altman(
    y_true_vals: np.ndarray,
    y_pred_vals: np.ndarray,
    feature_col: str,
    out_path: Path,
    dpi: int = 300,
) -> None:
    """
    Bland-Altman 图：评估预测值与实测值的一致性。
    X 轴：(实测 + 预测) / 2（均值）
    Y 轴：预测 - 实测（差值）
    参考线：mean ± 1.96 SD（95% LoA）
    """
    diff = y_pred_vals - y_true_vals
    mean_val = (y_pred_vals + y_true_vals) / 2
    mean_diff = np.mean(diff)
    std_diff = np.std(diff, ddof=1)
    loa_upper = mean_diff + 1.96 * std_diff
    loa_lower = mean_diff - 1.96 * std_diff
    unit = "ml" if feature_col == "volume_ml" else "mm"

    fig, ax = plt.subplots(figsize=(7, 5))
    ax.scatter(mean_val, diff, alpha=0.6, color="#2563eb", s=40, edgecolors="white", linewidths=0.5)
    ax.axhline(mean_diff, color="#16a34a", linewidth=1.8, linestyle="-", label=f"均值差 = {mean_diff:.3f}")
    ax.axhline(loa_upper, color="#dc2626", linewidth=1.2, linestyle="--", label=f"+1.96 SD = {loa_upper:.3f}")
    ax.axhline(loa_lower, color="#dc2626", linewidth=1.2, linestyle="--", label=f"-1.96 SD = {loa_lower:.3f}")
    ax.fill_between(ax.get_xlim(), loa_lower, loa_upper, alpha=0.05, color="#dc2626")

    ax.set_xlabel(f"(实测 + 预测) / 2 ({unit})")
    ax.set_ylabel(f"预测 - 实测 ({unit})")
    ax.set_title("Bland-Altman 一致性分析（TimesFM 预测 vs 实测）")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)
    sns.despine(ax=ax)
    fig.tight_layout()
    fig.savefig(out_path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)


def plot_recist_comparison(
    results_df: pd.DataFrame,
    out_path: Path,
    dpi: int = 300,
) -> None:
    """
    横向条形图：比较 TimesFM vs Persistence vs Linear 三种方法的 RECIST 分类准确率。
    """
    methods = []
    accuracies = []

    # 计算各方法准确率（仅统计 obs_RECIST 非空的行）
    df_valid = results_df.dropna(subset=["obs_RECIST"])
    if df_valid.empty:
        return

    for col, name in [
        ("pred_RECIST", "TimesFM"),
        ("persistence_RECIST", "Persistence"),
        ("linear_RECIST", "Linear"),
    ]:
        if col in df_valid.columns:
            acc = accuracy_score(df_valid["obs_RECIST"], df_valid[col])
            methods.append(name)
            accuracies.append(acc)

    fig, ax = plt.subplots(figsize=(6, 3.5))
    colors = ["#2563eb", "#ca8a04", "#9333ea"][:len(methods)]
    bars = ax.barh(methods, accuracies, color=colors, height=0.5, edgecolor="white")

    # 在条形右侧标注数值
    for bar, acc in zip(bars, accuracies):
        ax.text(
            bar.get_width() + 0.01, bar.get_y() + bar.get_height() / 2,
            f"{acc:.3f}",
            va="center", fontsize=11, fontweight="bold",
        )

    ax.set_xlim(0, 1.15)
    ax.set_xlabel("RECIST 分类准确率")
    ax.set_title("各方法 RECIST 分类准确率对比")
    ax.xaxis.set_major_formatter(mticker.PercentFormatter(xmax=1.0))
    ax.grid(axis="x", alpha=0.3)
    sns.despine(ax=ax)
    fig.tight_layout()
    fig.savefig(out_path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)


def plot_quantile_coverage(
    results_df: pd.DataFrame,
    feature_col: str,
    out_path: Path,
    dpi: int = 300,
) -> None:
    """
    分位数覆盖率图：验证 q10-q90 区间对实测值的覆盖率（应接近 80%）。
    同时绘制 scatter：预测中位数（q50）vs 实测值。
    """
    df = results_df.dropna(subset=["obs_value", "q50"])
    if df.empty:
        return

    fig, axes = plt.subplots(1, 2, figsize=(12, 5))

    # ── 左图：q50 vs 实测（散点 + 对角线）────────────────────────────────
    ax = axes[0]
    ax.scatter(df["obs_value"], df["q50"], alpha=0.6, color="#2563eb", s=40)
    lim = [
        min(df["obs_value"].min(), df["q50"].min()) * 0.95,
        max(df["obs_value"].max(), df["q50"].max()) * 1.05,
    ]
    ax.plot(lim, lim, "k--", linewidth=1, alpha=0.5, label="完美预测线")
    ax.set_xlabel(f"实测值 ({feature_col})")
    ax.set_ylabel("TimesFM 预测中位数 (q50)")
    ax.set_title("预测中位数 vs 实测值")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)
    sns.despine(ax=ax)

    # ── 右图：q10-q90 覆盖率（按患者展示）────────────────────────────────
    ax = axes[1]
    df_sorted = df.sort_values("obs_value").reset_index(drop=True)
    x_idx = np.arange(len(df_sorted))

    # 误差条（q10 到 q90）
    ax.errorbar(
        x_idx,
        df_sorted["q50"],
        yerr=[
            df_sorted["q50"] - df_sorted["q10"],
            df_sorted["q90"] - df_sorted["q50"],
        ],
        fmt="none", color="#93c5fd", alpha=0.5, capsize=2,
    )
    ax.scatter(x_idx, df_sorted["obs_value"], color="#dc2626", s=20, zorder=5, label="实测值")
    ax.scatter(x_idx, df_sorted["q50"], color="#2563eb", s=20, zorder=4, label="预测中位数")

    # 计算覆盖率
    covered = ((df_sorted["obs_value"] >= df_sorted["q10"]) &
               (df_sorted["obs_value"] <= df_sorted["q90"])).mean()
    ax.set_title(f"q10-q90 区间覆盖率 = {covered:.1%}（理论值 80%）")
    ax.set_xlabel("患者（按实测值排序）")
    ax.set_ylabel(f"{feature_col}")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)
    sns.despine(ax=ax)

    fig.tight_layout()
    fig.savefig(out_path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)


# =============================================================================
# 模块 F：Markdown 摘要报告
# =============================================================================
def generate_markdown_report(
    results_df: pd.DataFrame,
    stats_dict: dict,
    cfg: dict,
    out_path: Path,
) -> None:
    """
    生成 Markdown 格式的统计摘要报告。
    包含：样本统计、各方法 RECIST 准确率、Kappa、Bootstrap CI、类别分布。
    """
    df_valid = results_df.dropna(subset=["obs_RECIST"])
    n_total = len(results_df)
    n_valid = len(df_valid)

    lines = [
        "# 肺癌 TimesFM RECIST 预测 · 统计摘要报告",
        "",
        f"> 生成时间：{pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}  ",
        f"> 配置文件：`{cfg.get('_config_path', 'config.yaml')}`",
        "",
        "## 1. 样本概况",
        "",
        f"| 指标 | 值 |",
        f"|------|-----|",
        f"| 总患者数 | {n_total} |",
        f"| 有效验证患者数（含 obs_RECIST） | {n_valid} |",
        f"| 使用特征列 | `{cfg.get('forecast', {}).get('feature_col', 'volume_ml')}` |",
        f"| leave-one-out 模式 | {cfg.get('forecast', {}).get('leave_one_out', True)} |",
        f"| 多靶病灶聚合 | {cfg.get('forecast', {}).get('multi_lesion_agg', 'sum_diameter')} |",
        "",
        "## 2. RECIST 分类准确率（各方法对比）",
        "",
        "| 方法 | 准确率 | 加权 Kappa | Kappa 95% CI |",
        "|------|--------|------------|--------------|",
    ]

    # 填入各方法统计
    for method_key in ["timesfm", "persistence", "linear"]:
        s = stats_dict.get(method_key, {})
        if not s:
            continue
        name = {"timesfm": "TimesFM", "persistence": "Persistence Baseline", "linear": "Linear Baseline"}[method_key]
        acc = f"{s.get('accuracy', float('nan')):.4f}"
        kappa = f"{s.get('kappa', float('nan')):.4f}"
        ci_lo = s.get('kappa_ci_lo', float('nan'))
        ci_hi = s.get('kappa_ci_hi', float('nan'))
        ci_str = f"[{ci_lo:.4f}, {ci_hi:.4f}]" if not (np.isnan(ci_lo) or np.isnan(ci_hi)) else "N/A"
        lines.append(f"| {name} | {acc} | {kappa} | {ci_str} |")

    lines += [
        "",
        "## 3. TimesFM RECIST 分类报告",
        "",
        "```",
        stats_dict.get("timesfm_classification_report", "（无数据）"),
        "```",
        "",
        "## 4. 预测类别分布",
        "",
        "| 类别 | TimesFM 预测 | 实测 |",
        "|------|------------|------|",
    ]

    # 类别分布
    for cat in ["CR", "PR", "SD", "PD"]:
        pred_n = (results_df["pred_RECIST"] == cat).sum()
        obs_n = (df_valid["obs_RECIST"] == cat).sum() if n_valid > 0 else "—"
        lines.append(f"| {cat} | {pred_n} | {obs_n} |")

    lines += [
        "",
        "## 5. 预测误差统计（量化）",
        "",
        f"| 指标 | 值 |",
        f"|------|-----|",
    ]

    quant_stats = stats_dict.get("quantitative", {})
    for k, v in quant_stats.items():
        lines.append(f"| {k} | {v:.4f} |")

    lines += [
        "",
        "## 6. 分位数覆盖率",
        "",
        f"| 区间 | 理论覆盖率 | 实测覆盖率 |",
        f"|------|-----------|-----------|",
        f"| q10-q90 | 80.0% | {stats_dict.get('q10_q90_coverage', float('nan')):.1%} |",
        f"| q20-q80 | 60.0% | {stats_dict.get('q20_q80_coverage', float('nan')):.1%} |",
        "",
        "---",
        "*本报告由 run_lung_recist_timesfm.py 自动生成。*",
    ]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# =============================================================================
# 主函数
# =============================================================================
def main():
    # ── 解析命令行参数 ─────────────────────────────────────────────────────
    parser = argparse.ArgumentParser(
        description="TimesFM × 肺癌 RECIST 预测 + 统计验证",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--config", type=str, default="config.yaml",
        help="YAML 配置文件路径（默认：config.yaml）",
    )
    parser.add_argument(
        "--features", type=str, default=None,
        help="覆盖 config 中的特征 CSV 路径（可选）",
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="覆盖 config 中的预测输出路径（可选）",
    )
    parser.add_argument(
        "--no-plots", action="store_true",
        help="跳过可视化输出（加速批量运行）",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="DEBUG 级别日志",
    )
    args = parser.parse_args()

    # ── 加载配置 ──────────────────────────────────────────────────────────
    cfg = load_config(args.config)
    cfg["_config_path"] = args.config  # 供报告引用

    verbose = args.verbose or cfg.get("runtime", {}).get("verbose", False)
    logger = setup_logging(verbose)

    seed = cfg.get("runtime", {}).get("seed", 42)
    set_global_seed(seed)
    logger.info(f"随机种子 = {seed}")

    # ── 解析路径配置 ───────────────────────────────────────────────────────
    features_path = args.features or cfg["paths"]["features_out"]
    pred_out_path = args.output or cfg["paths"]["predictions_out"]
    report_dir = Path(cfg["paths"]["report_dir"])
    fig_dir = Path(cfg["paths"]["figures_dir"])
    fig_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    # ── 解析预测配置 ───────────────────────────────────────────────────────
    fc_cfg = cfg.get("forecast", {})
    feature_col = fc_cfg.get("feature_col", "volume_ml")  # 时间序列特征列
    horizon = fc_cfg.get("horizon", 1)                     # 预测步长
    min_tp = fc_cfg.get("min_timepoints", 2)               # 最少时间点
    loo = fc_cfg.get("leave_one_out", True)                # 是否 leave-one-out

    # ── 解析 RECIST 阈值 ───────────────────────────────────────────────────
    recist_cfg = cfg.get("recist", {})
    pr_thr = recist_cfg.get("pr_threshold", -30.0)
    pd_thr = recist_cfg.get("pd_threshold", 20.0)
    cr_vol_thr = recist_cfg.get("cr_volume_threshold_ml", 0.1)
    cr_dia_thr = recist_cfg.get("cr_diameter_threshold_mm", 5.0)

    # ── 解析统计配置 ───────────────────────────────────────────────────────
    stats_cfg = cfg.get("stats", {})
    kappa_weights = stats_cfg.get("kappa_weights", "linear")
    do_bootstrap = stats_cfg.get("bootstrap_ci", True)
    boot_n = stats_cfg.get("bootstrap_n", 1000)
    boot_seed = stats_cfg.get("bootstrap_seed", 42)
    fig_dpi = stats_cfg.get("figure_dpi", 300)
    q_idx = stats_cfg.get("quantile_indices", {"low": 1, "mid": 5, "high": 9})
    baseline_methods = stats_cfg.get("baseline_methods", ["persistence", "linear"])
    dec = cfg.get("runtime", {}).get("decimal_places", 4)

    # ── 读取特征 CSV ───────────────────────────────────────────────────────
    feat_path = Path(features_path)
    if not feat_path.exists():
        logger.error(f"特征文件不存在：{feat_path}，请先运行 extract_lung_features.py")
        sys.exit(1)

    feat_df = pd.read_csv(feat_path)
    logger.info(f"加载特征文件：{feat_path}，共 {len(feat_df)} 行")

    if feature_col not in feat_df.columns:
        logger.error(
            f"特征列 '{feature_col}' 不在 CSV 中，可用列：{list(feat_df.columns)}"
        )
        sys.exit(1)

    # ── 加载 TimesFM 模型 ─────────────────────────────────────────────────
    model = load_timesfm_model(cfg, logger)

    # ── 主循环：逐患者预测 ────────────────────────────────────────────────
    results = []    # 收集每位患者的预测结果
    traj_count = 0  # 轨迹图计数（每患者生成一张，数量多时可限制）

    # 按患者分组
    grouped = feat_df.groupby("patient_id")
    n_patients = feat_df["patient_id"].nunique()
    logger.info(f"开始预测：共 {n_patients} 例患者，feature={feature_col}，horizon={horizon}")

    for pid, g in grouped:
        # 按时间排序（关键：确保序列有序）
        g = g.sort_values("days_from_tx_start").reset_index(drop=True)

        # 时间点不足则跳过
        if len(g) < min_tp:
            logger.warning(f"患者 {pid} 仅有 {len(g)} 个时间点，少于最低要求 {min_tp}，跳过")
            continue

        # ── 构建输入 context ─────────────────────────────────────────────
        all_vals = g[feature_col].values.astype(float)

        if loo:
            # Leave-one-out：用前 n-1 个点预测第 n 点（可计算真实误差）
            context = all_vals[:-1]       # shape: (n-1,)
            actual_last = float(all_vals[-1])  # 待预测的真实值
        else:
            # 预测真正的未来点（无真实值可验证）
            context = all_vals            # shape: (n,)
            actual_last = None

        baseline_vol = float(all_vals[0])  # 基线测量值（第一个时间点）

        logger.debug(
            f"患者 {pid}：n_tp={len(g)}，context_len={len(context)}，"
            f"baseline={baseline_vol:.3f}"
        )

        # ── TimesFM 预测 ─────────────────────────────────────────────────
        #    model.forecast() 接受：
        #      horizon : int，预测步长
        #      inputs  : list of np.ndarray，每个元素是一个时间序列
        #    返回：
        #      point_forecast   : np.ndarray, shape (n_series, horizon)
        #      quantile_forecast: np.ndarray, shape (n_series, horizon, 10)
        #                         [mean, q10, q20, q30, q40, q50, q60, q70, q80, q90]
        point_forecast, quantile_forecast = model.forecast(
            horizon=horizon,
            inputs=[context],  # 注意：必须是 list，即使只有一个序列
        )

        # 取第一个系列（索引 0）的第一步预测（索引 0）
        pred_val = float(point_forecast[0, 0])

        # 提取分位数（用于不确定性评估）
        q10 = float(quantile_forecast[0, 0, q_idx["low"]])   # 10th 分位
        q50 = float(quantile_forecast[0, 0, q_idx["mid"]])   # 50th 分位（中位数）
        q90 = float(quantile_forecast[0, 0, q_idx["high"]])  # 90th 分位

        # 提取 q20/q80（用于 60% 覆盖率计算）
        q20 = float(quantile_forecast[0, 0, 2])
        q80 = float(quantile_forecast[0, 0, 8])

        # ── Baseline 预测 ────────────────────────────────────────────────
        persistence_val = predict_persistence(context) if "persistence" in baseline_methods else None
        linear_val = predict_linear(context) if "linear" in baseline_methods else None

        # ── 计算百分比变化并分类 RECIST ──────────────────────────────────
        #    pct_change = (预测值 - 基线值) / 基线值 × 100
        #    注意：避免基线为 0 导致除零
        eps = 1e-9  # 防止零除

        pct_pred = (pred_val - baseline_vol) / (baseline_vol + eps) * 100
        pred_recist = recist_category(
            pct_pred, pred_value=pred_val,
            pr_threshold=pr_thr, pd_threshold=pd_thr,
            cr_volume_threshold_ml=cr_vol_thr,
            cr_diameter_threshold_mm=cr_dia_thr,
            feature_col=feature_col,
        )

        # Persistence baseline RECIST
        pers_recist = None
        if persistence_val is not None:
            pct_pers = (persistence_val - baseline_vol) / (baseline_vol + eps) * 100
            pers_recist = recist_category(
                pct_pers, pred_value=persistence_val,
                pr_threshold=pr_thr, pd_threshold=pd_thr,
                cr_volume_threshold_ml=cr_vol_thr,
                cr_diameter_threshold_mm=cr_dia_thr,
                feature_col=feature_col,
            )

        # Linear baseline RECIST
        lin_recist = None
        if linear_val is not None:
            pct_lin = (linear_val - baseline_vol) / (baseline_vol + eps) * 100
            lin_recist = recist_category(
                pct_lin, pred_value=linear_val,
                pr_threshold=pr_thr, pd_threshold=pd_thr,
                cr_volume_threshold_ml=cr_vol_thr,
                cr_diameter_threshold_mm=cr_dia_thr,
                feature_col=feature_col,
            )

        # Observed RECIST（仅 leave-one-out 模式下有真实值）
        obs_recist = None
        if actual_last is not None:
            pct_obs = (actual_last - baseline_vol) / (baseline_vol + eps) * 100
            obs_recist = recist_category(
                pct_obs, pred_value=actual_last,
                pr_threshold=pr_thr, pd_threshold=pd_thr,
                cr_volume_threshold_ml=cr_vol_thr,
                cr_diameter_threshold_mm=cr_dia_thr,
                feature_col=feature_col,
            )

        # ── 汇总结果 ─────────────────────────────────────────────────────
        row = {
            "patient_id": pid,
            "n_timepoints": len(g),
            "context_length": len(context),
            "days_last": int(g.iloc[-1]["days_from_tx_start"]),
            "baseline_value": round(baseline_vol, dec),
            "pred_value": round(pred_val, dec),
            "obs_value": round(actual_last, dec) if actual_last is not None else None,
            "pct_change_pred": round(pct_pred, 2),
            "pct_change_obs": round(pct_obs, 2) if actual_last is not None else None,
            "pred_RECIST": pred_recist,
            "obs_RECIST": obs_recist,
            "persistence_value": round(persistence_val, dec) if persistence_val is not None else None,
            "persistence_RECIST": pers_recist,
            "linear_value": round(linear_val, dec) if linear_val is not None else None,
            "linear_RECIST": lin_recist,
            "abs_error_ml": round(abs(pred_val - actual_last), dec) if actual_last is not None else None,
            "q10": round(q10, dec),
            "q20": round(q20, dec),
            "q50": round(q50, dec),
            "q80": round(q80, dec),
            "q90": round(q90, dec),
        }
        results.append(row)

        logger.info(
            f"患者 {pid}：pred={pred_val:.3f}，"
            f"obs={actual_last:.3f if actual_last else 'N/A'}，"
            f"pred_RECIST={pred_recist}，obs_RECIST={obs_recist}"
        )

        # ── 生成轨迹图（可关闭）─────────────────────────────────────────
        if not args.no_plots and actual_last is not None:
            plot_patient_trajectory(
                pid=str(pid), g=g,
                pred_vol=pred_val,
                feature_col=feature_col,
                out_dir=fig_dir / "trajectories",
                dpi=fig_dpi,
            )
            (fig_dir / "trajectories").mkdir(parents=True, exist_ok=True)
            traj_count += 1

    # ── 输出预测 CSV ──────────────────────────────────────────────────────
    results_df = pd.DataFrame(results)
    Path(pred_out_path).parent.mkdir(parents=True, exist_ok=True)
    results_df.to_csv(pred_out_path, index=False)
    logger.info(f"预测结果已保存：{pred_out_path}（{len(results_df)} 行）")

    # ── 统计验证 ──────────────────────────────────────────────────────────
    logger.info("=== 开始统计验证 ===")
    df_valid = results_df.dropna(subset=["obs_RECIST"])
    stats_dict = {}
    quantitative_stats = {}

    if df_valid.empty:
        logger.warning("无有效的 obs_RECIST 数据，跳过统计验证")
    else:
        # ── 量化误差指标 ─────────────────────────────────────────────────
        errors = df_valid["abs_error_ml"].dropna().values
        if len(errors) > 0:
            quantitative_stats["MAE"] = float(np.mean(errors))
            quantitative_stats["RMSE"] = float(np.sqrt(np.mean(errors**2)))
            quantitative_stats["Median_AE"] = float(np.median(errors))
            quantitative_stats["Max_AE"] = float(np.max(errors))
        stats_dict["quantitative"] = quantitative_stats

        # ── 分位数覆盖率 ─────────────────────────────────────────────────
        df_q = df_valid.dropna(subset=["obs_value", "q10", "q90"])
        if not df_q.empty:
            cov_80 = ((df_q["obs_value"] >= df_q["q10"]) & (df_q["obs_value"] <= df_q["q90"])).mean()
            cov_60 = ((df_q["obs_value"] >= df_q["q20"]) & (df_q["obs_value"] <= df_q["q80"])).mean()
            stats_dict["q10_q90_coverage"] = float(cov_80)
            stats_dict["q20_q80_coverage"] = float(cov_60)
            logger.info(f"q10-q90 覆盖率：{cov_80:.1%}（理论 80%）")
            logger.info(f"q20-q80 覆盖率：{cov_60:.1%}（理论 60%）")

        # ── 各方法 RECIST 分类指标 ────────────────────────────────────────
        for method_key, pred_col in [
            ("timesfm", "pred_RECIST"),
            ("persistence", "persistence_RECIST"),
            ("linear", "linear_RECIST"),
        ]:
            if pred_col not in df_valid.columns:
                continue
            df_m = df_valid.dropna(subset=[pred_col, "obs_RECIST"])
            if df_m.empty:
                continue

            yt = df_m["obs_RECIST"].tolist()
            yp = df_m[pred_col].tolist()

            # 准确率
            acc = accuracy_score(yt, yp)

            # 加权 Kappa（需要至少 2 个类别）
            try:
                kappa = cohen_kappa_score(yt, yp, weights=kappa_weights)
            except Exception:
                kappa = float("nan")

            # Bootstrap CI（可选）
            kappa_ci_lo, kappa_ci_hi = float("nan"), float("nan")
            if do_bootstrap and not np.isnan(kappa):
                _, kappa_ci_lo, kappa_ci_hi = bootstrap_metric(
                    yt, yp,
                    lambda a, b: cohen_kappa_score(a, b, weights=kappa_weights),
                    n_bootstrap=boot_n,
                    seed=boot_seed,
                )

            stats_dict[method_key] = {
                "accuracy": acc,
                "kappa": kappa,
                "kappa_ci_lo": kappa_ci_lo,
                "kappa_ci_hi": kappa_ci_hi,
            }

            if method_key == "timesfm":
                stats_dict["timesfm_classification_report"] = classification_report(
                    yt, yp, zero_division=0
                )

            logger.info(
                f"[{method_key.upper()}] Accuracy={acc:.4f}，"
                f"Weighted Kappa={kappa:.4f} 95%CI=[{kappa_ci_lo:.4f}, {kappa_ci_hi:.4f}]"
            )

        # ── 打印分类报告 ─────────────────────────────────────────────────
        print("\n" + "=" * 60)
        print("TimesFM RECIST 分类报告")
        print("=" * 60)
        print(stats_dict.get("timesfm_classification_report", "无数据"))

    # ── 可视化 ────────────────────────────────────────────────────────────
    if not args.no_plots:
        logger.info("生成可视化图像...")

        # 混淆矩阵（TimesFM）
        if not df_valid.empty:
            plot_confusion_matrix(
                y_true=df_valid["obs_RECIST"].tolist(),
                y_pred=df_valid["pred_RECIST"].tolist(),
                title="TimesFM RECIST 混淆矩阵",
                out_path=fig_dir / "confusion_matrix_timesfm.png",
                dpi=fig_dpi,
            )

            # 各方法准确率对比
            plot_recist_comparison(
                results_df=df_valid,
                out_path=fig_dir / "recist_accuracy_comparison.png",
                dpi=fig_dpi,
            )

            # Bland-Altman
            df_ba = df_valid.dropna(subset=["obs_value", "pred_value"])
            if len(df_ba) >= 3:
                plot_bland_altman(
                    y_true_vals=df_ba["obs_value"].values,
                    y_pred_vals=df_ba["pred_value"].values,
                    feature_col=feature_col,
                    out_path=fig_dir / "bland_altman.png",
                    dpi=fig_dpi,
                )

            # 分位数覆盖率
            plot_quantile_coverage(
                results_df=df_valid,
                feature_col=feature_col,
                out_path=fig_dir / "quantile_coverage.png",
                dpi=fig_dpi,
            )

        logger.info(f"可视化图像已保存至：{fig_dir}")

    # ── 生成 Markdown 报告 ────────────────────────────────────────────────
    report_path = report_dir / "summary_report.md"
    generate_markdown_report(
        results_df=results_df,
        stats_dict=stats_dict,
        cfg=cfg,
        out_path=report_path,
    )
    logger.info(f"Markdown 摘要报告：{report_path}")

    # ── 保存统计 JSON（供后续 R/Python 读入）─────────────────────────────
    stats_json_path = report_dir / "stats_summary.json"
    # 将 nan 替换为 None 以兼容 JSON 格式
    def nan_to_none(d):
        if isinstance(d, dict):
            return {k: nan_to_none(v) for k, v in d.items()}
        if isinstance(d, float) and np.isnan(d):
            return None
        return d

    with open(stats_json_path, "w", encoding="utf-8") as f:
        json.dump(nan_to_none(stats_dict), f, ensure_ascii=False, indent=2)
    logger.info(f"统计 JSON：{stats_json_path}")

    # ── 最终摘要打印 ──────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("运行完成 · 输出文件")
    print("=" * 60)
    print(f"  预测 CSV      : {pred_out_path}")
    print(f"  可视化图像    : {fig_dir}/")
    print(f"  Markdown 报告 : {report_path}")
    print(f"  统计 JSON     : {stats_json_path}")
    print(f"  轨迹图数量    : {traj_count}")
    print("=" * 60)


# =============================================================================
# 入口
# =============================================================================
if __name__ == "__main__":
    main()
