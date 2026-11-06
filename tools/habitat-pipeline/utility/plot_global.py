#!/usr/bin/env python3
"""
plot_global_habitat_diagnostics.py
==================================
全局生境模型 + 全队列池化体素特征 可视化脚本
版本：v1.0  2026-06

功能
----
一次性生成以下所有图像：
1. PCA 散点图（全队列体素，按预测 Habitat 着色）
2. PCA 诊断图（Scree plot + Cumulative explained variance）
3. 各 Habitat 体素数量分布
4. 关键特征在各 Habitat 的分布（小提琴图）
5. PCA Loadings（前3个主成分的特征贡献）

使用方法
--------
1. 修改下方「配置区」中的路径
2. 直接运行：python plot_global_habitat_diagnostics.py

依赖
----
numpy, matplotlib, joblib, pandas, seaborn, scikit-learn（可选，用于复用函数）
"""

import os
import sys
from pathlib import Path

# 添加项目根目录和必要目录到 sys.path 以解决 ModuleNotFoundError
current_dir = Path(__file__).resolve().parent
project_root = current_dir.parent
sys.path.append(str(project_root))
sys.path.append(str(project_root / "delta_habitat_pipeline"))
sys.path.append(str(project_root / "utility"))

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import joblib
from pathlib import Path

# ============================================================
# 1. 配置区（只改这里）
# ============================================================

MODEL_PATH = r"E:\study\0old_files\train\xa\global_habitat_model.joblib"
POOLED_FEATURES_PATH = r"E:\study\0old_files\train\xa\cohort_pooled_features.npy"
OUTPUT_DIR = r"E:\study\0old_files\train\xa\k_optimization"

# 可视化参数
MAX_VOXELS_FOR_SCATTER = 8000      # PCA散点图最大采样数（防内存爆炸）
MAX_VOXELS_FOR_VIOLIN  = 4000      # 小提琴图最大采样数
DPI = 300
RANDOM_STATE = 66

# 选择要在小提琴图中展示的特征通道索引（根据 habitat.py 的 compute_voxel_feature_maps 顺序）
# 0=原始强度, 1=局部均值, 2=局部方差, 3=局部熵, 4+=LoG响应, 后面可能是小波
KEY_CHANNEL_INDICES = [0, 1, 2, 4, 5]  
CHANNEL_NAMES = {
    0: "Original Intensity",
    1: "Local Mean",
    2: "Local Variance", 
    3: "Local Entropy",
    4: "LoG sigma=1mm",
    5: "LoG sigma=2mm",
    # 如有小波特征可继续添加
}

# ============================================================
# 2. 辅助函数
# ============================================================

def ensure_dir(path):
    Path(path).mkdir(parents=True, exist_ok=True)

def load_model_and_features():
    print("[加载] 全局模型...")
    model = joblib.load(MODEL_PATH)
    print(f"       K = {model.optimal_k}, PCA components = {model.pca.n_components_ if model.pca else 'None'}")
    
    print("[加载] 全队列池化特征...")
    X = np.load(POOLED_FEATURES_PATH)
    print(f"       Shape: {X.shape} (体素数 × 特征通道数)")
    return model, X

def predict_labels(model, X):
    print("[预测] 使用全局模型预测 Habitat 标签...")
    labels = model.predict(X)
    print(f"       预测完成，共 {len(np.unique(labels))} 个 Habitat")
    return labels

# ============================================================
# 3. 可视化函数
# ============================================================

def plot_pca_scatter(model, X, labels, output_dir):
    """复用 visualize_pipeline.py 中的函数生成 PCA 散点图"""
    print("[生成] PCA 散点图（全队列）...")
    
    if True:
        print("       [强制] 使用内置 PCA 绘图以生成 fallback 图像...")
        # 备用方案
        from sklearn.decomposition import PCA
        pca = PCA(n_components=2, random_state=RANDOM_STATE)
        X_pca = pca.fit_transform(X[:MAX_VOXELS_FOR_SCATTER] if len(X) > MAX_VOXELS_FOR_SCATTER else X)
        lab = labels[:MAX_VOXELS_FOR_SCATTER] if len(X) > MAX_VOXELS_FOR_SCATTER else labels
        
        plt.figure(figsize=(8, 6), dpi=DPI)
        for h in np.unique(lab):
            mask = lab == h
            plt.scatter(X_pca[mask, 0], X_pca[mask, 1], s=3, alpha=0.5, label=f"H{h}")
        plt.title("Global Habitat Model - PCA Projection (Pooled Voxels)", fontsize=13, fontweight='bold')
        plt.xlabel("PC1")
        plt.ylabel("PC2")
        plt.legend(markerscale=3)
        plt.tight_layout()
        plt.savefig(os.path.join(output_dir, "pcohort_global_habitat_pca_fallback.png"), dpi=DPI)
        plt.close()

def plot_pca_diagnostics(model, output_dir):
    """绘制 Scree plot + Cumulative explained variance"""
    print("[生成] PCA 诊断图...")
    if model.pca is None:
        print("       [跳过] 模型未使用 PCA")
        return

    evr = model.pca.explained_variance_ratio_
    cum_var = np.cumsum(evr)
    n_comp = len(evr)

    fig, axes = plt.subplots(1, 2, figsize=(13, 5), dpi=DPI)

    # Scree plot
    axes[0].plot(range(1, n_comp + 1), evr, 'o-', color='#0173B2', markersize=5)
    axes[0].set_title("Scree Plot (Explained Variance per PC)", fontweight='bold', fontsize=12)
    axes[0].set_xlabel("Principal Component")
    axes[0].set_ylabel("Explained Variance Ratio")
    axes[0].grid(True, alpha=0.3)

    # Cumulative
    axes[1].plot(range(1, n_comp + 1), cum_var, 'o-', color='#029E73', markersize=5)
    axes[1].axhline(y=0.95, color='#DE8F05', linestyle='--', linewidth=1.5, label="95% threshold")
    axes[1].set_title("Cumulative Explained Variance", fontweight='bold', fontsize=12)
    axes[1].set_xlabel("Principal Component")
    axes[1].set_ylabel("Cumulative Variance")
    axes[1].legend()
    axes[1].grid(True, alpha=0.3)

    plt.tight_layout()
    save_path = os.path.join(output_dir, "global_pca_diagnostics.png")
    plt.savefig(save_path, dpi=DPI, bbox_inches='tight')
    plt.close()
    print(f"       [Done] 已保存: {save_path}")

def plot_voxel_counts(labels, output_dir):
    """各 Habitat 体素数量柱状图"""
    print("[生成] 体素数量分布图...")
    counts = pd.Series(labels).value_counts().sort_index()

    plt.figure(figsize=(7, 4.5), dpi=DPI)
    sns.barplot(x=counts.index, y=counts.values, palette="tab10", edgecolor="black")
    plt.title(f"Global Habitat Voxel Distribution\n(Total voxels = {len(labels):,})", 
              fontsize=13, fontweight='bold')
    plt.xlabel("Habitat ID", fontsize=11)
    plt.ylabel("Number of voxels", fontsize=11)
    for i, v in enumerate(counts.values):
        plt.text(i, v + max(counts.values)*0.01, f"{v:,}", ha='center', fontsize=9)
    plt.tight_layout()
    save_path = os.path.join(output_dir, "global_habitat_voxel_counts.png")
    plt.savefig(save_path, dpi=DPI)
    plt.close()
    print(f"       [Done] 已保存: {save_path}")

def plot_feature_violins(X, labels, output_dir):
    """关键特征在各 Habitat 的分布（小提琴图）"""
    print("[生成] 特征分布小提琴图...")
    
    n_samples = min(MAX_VOXELS_FOR_VIOLIN, len(X))
    idx = np.random.RandomState(RANDOM_STATE).choice(len(X), n_samples, replace=False)
    X_sub = X[idx]
    labels_sub = labels[idx]

    n_channels = len(KEY_CHANNEL_INDICES)
    fig, axes = plt.subplots(1, n_channels, figsize=(4 * n_channels, 5), dpi=DPI, sharey=True)

    if n_channels == 1:
        axes = [axes]

    for ax, ch_idx in zip(axes, KEY_CHANNEL_INDICES):
        ch_name = CHANNEL_NAMES.get(ch_idx, f"Channel {ch_idx}")
        data = pd.DataFrame({
            "Habitat": [f"H{h}" for h in labels_sub],
            "Value": X_sub[:, ch_idx]
        })
        sns.violinplot(data=data, x="Habitat", y="Value", ax=ax, palette="tab10", inner="quartile")
        ax.set_title(ch_name, fontsize=11, fontweight='bold')
        ax.set_xlabel("")
        if ax == axes[0]:
            ax.set_ylabel("Normalized Value", fontsize=10)

    plt.suptitle("Feature Distribution per Global Habitat (Pooled Voxels)", 
                 fontsize=13, fontweight='bold', y=1.02)
    plt.tight_layout()
    save_path = os.path.join(output_dir, "global_habitat_feature_violins.png")
    plt.savefig(save_path, dpi=DPI, bbox_inches='tight')
    plt.close()
    print(f"       [Done] 已保存: {save_path}")

def plot_pca_loadings(model, output_dir):
    """PCA 前3个主成分的特征 loadings"""
    print("[生成] PCA Loadings 图...")
    if model.pca is None:
        print("       [跳过] 模型未使用 PCA")
        return

    loadings = model.pca.components_[:3]  # 前3个PC
    n_features = loadings.shape[1]
    feature_idx = np.arange(n_features)

    fig, axes = plt.subplots(3, 1, figsize=(12, 8), dpi=DPI, sharex=True)

    for i, (ax, comp) in enumerate(zip(axes, loadings)):
        colors = ['#0173B2' if v > 0 else '#DE8F05' for v in comp]
        ax.bar(feature_idx, comp, color=colors, edgecolor='black', linewidth=0.3)
        ax.set_ylabel(f"PC{i+1} Loading", fontsize=10)
        ax.axhline(0, color='gray', linewidth=0.8)
        ax.set_title(f"Principal Component {i+1}", fontweight='bold', fontsize=11)

    axes[-1].set_xlabel("Feature Channel Index", fontsize=10)
    plt.suptitle("PCA Loadings of Global Habitat Model (Top 3 Components)", 
                 fontsize=13, fontweight='bold', y=1.0)
    plt.tight_layout()
    save_path = os.path.join(output_dir, "global_pca_loadings.png")
    plt.savefig(save_path, dpi=DPI, bbox_inches='tight')
    plt.close()
    print(f"       [Done] 已保存: {save_path}")

# ============================================================
# 4. 主函数
# ============================================================

def main():
    print("=" * 60)
    print("全局生境模型可视化脚本启动")
    print("=" * 60)

    ensure_dir(OUTPUT_DIR)

    model, X = load_model_and_features()
    labels = predict_labels(model, X)

    # 生成所有图像
    plot_pca_scatter(model, X, labels, OUTPUT_DIR)
    plot_pca_diagnostics(model, OUTPUT_DIR)
    plot_voxel_counts(labels, OUTPUT_DIR)
    plot_feature_violins(X, labels, OUTPUT_DIR)
    plot_pca_loadings(model, OUTPUT_DIR)

    print("\n" + "=" * 60)
    print("所有图像生成完成！")
    print(f"输出目录: {OUTPUT_DIR}")
    print("=" * 60)

if __name__ == "__main__":
    main()