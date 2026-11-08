#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
line_ROC.py
===========
对筛选后的 Delta 特征进行多变量逻辑回归（线性拟合）建模，计算并绘制 ROC 曲线，
输出完整的模型评估指标（AUC、95% CI、敏感度、特异度、准确率、PPV、NPV 等），
并将结果图像和指标数据保存至 E:\\lxf\\results_all\\06_selected\\ 目录下。
"""

import os
import sys
import logging
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')  # 强制无 GUI 后端
import matplotlib.pyplot as plt

from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_curve, auc, accuracy_score, confusion_matrix

# ═══════════════════════════════════════════════════════════════════
#  置顶配置区
# ═══════════════════════════════════════════════════════════════════
CFG = {
    # ── 输入路径 ──────────────────────────────────────────────────
    "feature_csv": os.environ.get("ROC_FEATURE_CSV", "data/features/delta_features_selected.csv"),
    "label_csv": os.environ.get("ROC_LABEL_CSV", "data/QC_train.csv"),

    # ── 输出路径 ──────────────────────────────────────────────────
    "output_dir": os.environ.get("ROC_OUTPUT_DIR", "results/roc"),
    "output_roc_png": "roc_curve.png",
    "output_metrics_csv": "roc_metrics.csv",
    "output_metrics_txt": "roc_metrics_report.txt",

    # ── 模型参数 ──────────────────────────────────────────────────
    "random_state": 66,
    "max_iter": 1000,
    "C": 1.0,  # 正则化强度倒数

    # ── 自助法置信区间参数 ─────────────────────────────────────────
    "bootstrap_rounds": 1000,
    "confidence_level": 0.95,
}

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("line_ROC")


def calculate_bootstrap_auc_ci(y_true, y_pred_prob, rounds=1000, ci_level=0.95):
    """
    使用 Bootstrapping（自助法）计算 AUC 的 95% 置信区间。
    """
    rng = np.random.default_rng(CFG["random_state"])
    bootstrapped_aucs = []
    n_samples = len(y_true)

    # 将其转为 numpy 数组便于索引
    y_true_arr = np.array(y_true)
    y_pred_prob_arr = np.array(y_pred_prob)

    for i in range(rounds):
        # 有放回抽样
        indices = rng.choice(n_samples, size=n_samples, replace=True)
        # 确保抽样中同时包含 0 和 1 两个类别
        if len(np.unique(y_true_arr[indices])) < 2:
            continue
        
        fpr_b, tpr_b, _ = roc_curve(y_true_arr[indices], y_pred_prob_arr[indices])
        bootstrapped_aucs.append(auc(fpr_b, tpr_b))

    bootstrapped_aucs = np.sort(np.array(bootstrapped_aucs))
    
    # 计算分位数
    lower_pct = (1.0 - ci_level) / 2.0
    upper_pct = 1.0 - lower_pct
    
    lower_idx = int(np.floor(lower_pct * len(bootstrapped_aucs)))
    upper_idx = int(np.ceil(upper_pct * len(bootstrapped_aucs))) - 1
    
    # 防止越界
    lower_idx = max(0, min(lower_idx, len(bootstrapped_aucs) - 1))
    upper_idx = max(0, min(upper_idx, len(bootstrapped_aucs) - 1))

    auc_lower = bootstrapped_aucs[lower_idx]
    auc_upper = bootstrapped_aucs[upper_idx]
    
    return auc_lower, auc_upper


def main():
    logger.info("开始执行线性拟合与 ROC 分析流程...")

    # 1. 检测并创建输出目录
    os.makedirs(CFG["output_dir"], exist_ok=True)
    logger.info(f"输出目录已确认: {CFG['output_dir']}")

    # 2. 读取特征数据
    if not os.path.exists(CFG["feature_csv"]):
        logger.error(f"未找到特征文件: {CFG['feature_csv']}")
        sys.exit(1)
    
    try:
        df_feat = pd.read_csv(CFG["feature_csv"], encoding='utf-8')
    except UnicodeDecodeError:
        df_feat = pd.read_csv(CFG["feature_csv"], encoding='gbk')

    # 3. 读取标签数据
    if not os.path.exists(CFG["label_csv"]):
        logger.error(f"未找到标签文件: {CFG['label_csv']}")
        sys.exit(1)

    try:
        df_label = pd.read_csv(CFG["label_csv"], encoding='utf-8')
    except UnicodeDecodeError:
        df_label = pd.read_csv(CFG["label_csv"], encoding='gbk')

    # 4. 预处理标签与对齐
    if 'label' not in df_label.columns or 'patient_id' not in df_label.columns:
        logger.error("标签 CSV 必须包含 'patient_id' 和 'label' 列")
        sys.exit(1)

    # 剔除 label 为空的样本
    df_label_clean = df_label.dropna(subset=['label']).copy()
    df_label_clean['label'] = pd.to_numeric(df_label_clean['label'], errors='coerce')
    df_label_clean = df_label_clean.dropna(subset=['label'])
    df_label_clean['label'] = df_label_clean['label'].astype(int)

    # 对齐样本 (Inner Join via patient_id)
    # 特征表可能也包含 patient_id 列
    if 'patient_id' not in df_feat.columns:
        logger.error("特征 CSV 必须包含 'patient_id' 列作为第一列或主键")
        sys.exit(1)

    merged_df = pd.merge(df_feat, df_label_clean[['patient_id', 'label']], on='patient_id', how='inner')
    logger.info(f"样本对齐完成。特征表行数: {len(df_feat)}, 标签表有效行数: {len(df_label_clean)}, 对齐后样本数: {len(merged_df)}")

    if len(merged_df) == 0:
        logger.error("对齐后样本数为 0，请检查 patient_id 是否匹配")
        sys.exit(1)

    # 提取特征 X 和标签 y
    feature_cols = [col for col in merged_df.columns if col not in ['patient_id', 'label']]
    X = merged_df[feature_cols].copy()
    y = merged_df['label'].copy()

    # 填充缺失值（防御性容错，前面已经过筛选通常无 NaN）
    X = X.fillna(0.0)

    logger.info(f"参与拟合的特征数: {len(feature_cols)}, 样本数: {len(X)}")
    logger.info(f"正负样本分布: {np.bincount(y)}")

    # 5. 线性拟合 (使用 Logistic Regression)
    # 标准化特征（使系数大小有可比性，防止量纲影响）
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    # 拟合 Logistic Regression
    clf = LogisticRegression(
        penalty='l2',
        C=CFG["C"],
        max_iter=CFG["max_iter"],
        random_state=CFG["random_state"]
    )
    clf.fit(X_scaled, y)

    # 获取预测概率 (预测为 1 的概率)
    y_pred_prob = clf.predict_proba(X_scaled)[:, 1]

    # 6. 计算 ROC 曲线与 AUC
    fpr, tpr, thresholds = roc_curve(y, y_pred_prob)
    roc_auc = auc(fpr, tpr)
    logger.info(f"逻辑回归拟合 AUC = {roc_auc:.4f}")

    # 计算 95% 置信区间
    auc_lower, auc_upper = calculate_bootstrap_auc_ci(
        y, y_pred_prob, 
        rounds=CFG["bootstrap_rounds"], 
        ci_level=CFG["confidence_level"]
    )
    logger.info(f"AUC 95% 置信区间 (Bootstrap {CFG['bootstrap_rounds']} 次): [{auc_lower:.4f}, {auc_upper:.4f}]")

    # 7. 寻找最佳截断值 (Youden 指数最大化: Sensitivity + Specificity - 1)
    youden_indices = tpr - fpr
    best_idx = np.argmax(youden_indices)
    best_threshold = thresholds[best_idx]
    best_sens = tpr[best_idx]
    best_spec = 1 - fpr[best_idx]
    logger.info(f"最佳截断值 (Youden Index): {best_threshold:.4f} (敏感度: {best_sens:.4f}, 特异度: {best_spec:.4f})")

    # 根据最佳截断值生成二分类预测
    y_pred_class = (y_pred_prob >= best_threshold).astype(int)

    # 计算详细指标
    acc = accuracy_score(y, y_pred_class)
    tn, fp, fn, tp = confusion_matrix(y, y_pred_class).ravel()
    
    sens = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    spec = tn / (tn + fp) if (tn + fp) > 0 else 0.0
    ppv = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    npv = tn / (tn + fn) if (tn + fn) > 0 else 0.0

    logger.info(f"在最佳截断值下的性能指标:")
    logger.info(f"  准确率 (ACC) : {acc:.4f}")
    logger.info(f"  敏感度 (SEN) : {sens:.4f}")
    logger.info(f"  特异度 (SPE) : {spec:.4f}")
    logger.info(f"  阳性预测值 (PPV) : {ppv:.4f}")
    logger.info(f"  阴性预测值 (NPV) : {npv:.4f}")

    # 8. 保存指标数据
    metrics_data = {
        "Metric": ["AUC", "AUC_95%_CI_Lower", "AUC_95%_CI_Upper", "Best_Cutoff", "Accuracy", "Sensitivity", "Specificity", "PPV", "NPV", "True_Positives", "False_Positives", "True_Negatives", "False_Negatives"],
        "Value": [roc_auc, auc_lower, auc_upper, best_threshold, acc, sens, spec, ppv, npv, tp, fp, tn, fn]
    }
    df_metrics = pd.DataFrame(metrics_data)
    metrics_csv_path = os.path.join(CFG["output_dir"], CFG["output_metrics_csv"])
    df_metrics.to_csv(metrics_csv_path, index=False, encoding='utf-8-sig')
    logger.info(f"性能指标 CSV 已保存至: {metrics_csv_path}")

    # 保存可读的文本文档报告
    metrics_txt_path = os.path.join(CFG["output_dir"], CFG["output_metrics_txt"])
    with open(metrics_txt_path, "w", encoding="utf-8") as f:
        f.write("==================================================\n")
        f.write("      Multivariate Logistic Regression ROC Report \n")
        f.write("==================================================\n")
        f.write(f"特征数量: {len(feature_cols)}\n")
        f.write(f"样本总数: {len(X)} (正样本: {sum(y == 1)}, 负样本: {sum(y == 0)})\n\n")
        f.write(f"AUC: {roc_auc:.4f}\n")
        f.write(f"AUC 95% 置信区间: [{auc_lower:.4f}, {auc_upper:.4f}]\n")
        f.write(f"最佳截断值 (Youden): {best_threshold:.6f}\n\n")
        f.write("最佳截断值下的二分类性能:\n")
        f.write(f"  准确率 (Accuracy): {acc:.4%}\n")
        f.write(f"  敏感度 (Sensitivity): {sens:.4%}\n")
        f.write(f"  特异度 (Specificity): {spec:.4%}\n")
        f.write(f"  阳性预测值 (PPV): {ppv:.4%}\n")
        f.write(f"  阴性预测值 (NPV): {npv:.4%}\n\n")
        f.write("混淆矩阵:\n")
        f.write(f"  True Positives (TP): {tp}\n")
        f.write(f"  False Positives (FP): {fp}\n")
        f.write(f"  True Negatives (TN): {tn}\n")
        f.write(f"  False Negatives (FN): {fn}\n")
        f.write("==================================================\n")
    logger.info(f"可读文本报告已保存至: {metrics_txt_path}")

    # 9. 绘制美观且符合学术出版标准的 ROC 曲线图
    plt.figure(figsize=(6, 5.5), dpi=300)
    
    # 颜色选用 colorblind-safe 的 positive 青蓝色
    plt.plot(
        fpr, tpr, 
        color='#0173B2', 
        lw=2.5, 
        label=f'Linear Signature (AUC = {roc_auc:.4f})\n95% CI: [{auc_lower:.3f}, {auc_upper:.3f}]'
    )
    
    # 对角参考线
    plt.plot([0, 1], [0, 1], color='#DE8F05', lw=1.5, linestyle='--', label='Random Guess (AUC = 0.50)')
    
    # 标记最佳截断值点
    plt.scatter(
        1 - best_spec, best_sens, 
        color='#DE8F05', 
        s=50, 
        zorder=5, 
        label=f'Best Cutoff = {best_threshold:.3f}\n(Sens: {best_sens:.3f}, Spec: {best_spec:.3f})'
    )

    plt.xlim([-0.02, 1.02])
    plt.ylim([-0.02, 1.02])
    plt.xlabel('False Positive Rate (1 - Specificity)', fontsize=11, fontweight='bold', labelpad=8)
    plt.ylabel('True Positive Rate (Sensitivity)', fontsize=11, fontweight='bold', labelpad=8)
    plt.title('ROC Curve of Combined Linear Signature', fontsize=12, fontweight='bold', pad=15)
    plt.grid(True, linestyle=':', alpha=0.6)
    plt.legend(loc="lower right", fontsize=9.5, frameon=True, shadow=False)
    plt.tight_layout()

    roc_png_path = os.path.join(CFG["output_dir"], CFG["output_roc_png"])
    plt.savefig(roc_png_path, dpi=300)
    plt.close()
    logger.info(f"ROC 图像已保存至: {roc_png_path}")
    logger.info("分析流程圆满结束！")


if __name__ == "__main__":
    main()
