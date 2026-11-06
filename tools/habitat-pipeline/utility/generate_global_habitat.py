#!/usr/bin/env python3
"""
build_global_habitat_model.py
=============================
独立脚本：从已预处理的基线图像生成
  - cohort_pooled_features.npy
  - global_habitat_model.joblib

支持功能（2026-06-29 最终版）：
- preprocessed_roots 支持单个路径或多个路径列表（多来源数据池化）
- habitat.py 可位于其他文件夹（通过 habitat_module_path 配置）
- k_optimization 输出图像为发表级别（英文标注、300 dpi）
- 配置全部置顶，可多次调用
"""

import os
import sys
import logging
from typing import Optional, List, Tuple

import numpy as np
import SimpleITK as sitk
import matplotlib.pyplot as plt
import joblib

# 处理 habitat.py 在其他文件夹的情况
habitat_module_path = r"E:\study\delta_habitat_pipeline"
if os.path.exists(habitat_module_path):
    sys.path.insert(0, os.path.abspath(habitat_module_path))

import habitat
from habitat import compute_voxel_feature_maps, CohortHabitatModel

# ============================================================
# 配置区（只改这里即可）
# ============================================================
CFG = {
    # === 支持多路径（推荐写法）===
    "preprocessed_roots": [
        r"E:\study\results_la\01_preprocessed",
        r"E:\study\results_xa\01_preprocessed",
    ],

    "patient_ids": None,
    "use_cached_voxel_npy": True,
    "force_recompute": False,

    # === habitat.py 位置（关键！）===
    "habitat_module_path": r"E:\study\delta_habitat_pipeline",

    # === 输出 ===
    "output_dir": r"E:\study\0old_files\train",
    "pooled_features_name": "3-7_laxa_cohort_pooled_features.npy",
    "model_name": "3-7_laxa_global_habitat_model.joblib",

    # k_optimization 输出目录（支持 str 或 list[str]）
    "k_optimization_dirs": r"E:\study\0old_files\train\3-7_laxa_k_optimization",

    # === 建模参数 ===
    "k_range": range(3, 7),                 # 评估 K=1,2,3,4,5,6
    "random_state": 666,
    "n_init": 5,
    "consensus_subsample": 0.80,
    "consensus_n_iter": 50,
    "ari_threshold": 0.70,
    "pca_variance": 0.95,

    "window_radius": 3,
    "log_sigmas": [1.0, 2.0, 3.0],
    "use_wavelet": True,
    "wavelet_name": "db2",

    "publication_dpi": 300,
    "log_level": logging.INFO,
}


def _plot_k_optimization_english(k_range, inertias, sil_scores, consensus,
                                optimal_k, output_dir, name):
    """Monkey-patched version for English labels and publication quality."""
    ks = list(k_range)
    # 强制设置字体为无衬线
    plt.rcParams['font.sans-serif'] = ['Arial', 'DejaVu Sans']
    
    fig, axes = plt.subplots(1, 3, figsize=(15, 5), constrained_layout=True)
    fig.suptitle(f"K-Optimization Analysis (Optimal K={optimal_k})", fontsize=16, fontweight='bold')

    # 1. Elbow Method (Inertia)
    axes[0].plot(ks, inertias, 'o-', color='#1f77b4', linewidth=2, markersize=8)
    axes[0].axvline(optimal_k, color='#d62728', linestyle='--', linewidth=2, label=f'Optimal K={optimal_k}')
    axes[0].set_title('Elbow Method (Inertia)', fontsize=14)
    axes[0].set_xlabel('Number of Clusters (K)', fontsize=12)
    axes[0].set_ylabel('Inertia', fontsize=12)
    axes[0].grid(True, linestyle=':', alpha=0.6)
    axes[0].legend()

    # 2. Silhouette Score
    sil_ks = sorted(sil_scores.keys())
    sil_vals = [sil_scores[k] for k in sil_ks]
    axes[1].plot(sil_ks, sil_vals, 's-', color='#2ca02c', linewidth=2, markersize=8)
    axes[1].axvline(optimal_k, color='#d62728', linestyle='--', linewidth=2, label=f'Optimal K={optimal_k}')
    axes[1].set_title('Silhouette Analysis', fontsize=14)
    axes[1].set_xlabel('Number of Clusters (K)', fontsize=12)
    axes[1].set_ylabel('Silhouette Score', fontsize=12)
    axes[1].grid(True, linestyle=':', alpha=0.6)
    axes[1].legend()

    # 3. Consensus ARI (Stability)
    ari_means = [consensus[k]['mean_ari'] for k in ks]
    ari_stds  = [consensus[k]['std_ari']  for k in ks]
    axes[2].errorbar(ks, ari_means, yerr=ari_stds, fmt='D-', color='#ff7f0e', 
                     linewidth=2, markersize=7, capsize=5, ecolor='#7f7f7f')
    
    threshold = habitat.HABITAT.get("ari_threshold", 0.7)
    axes[2].axhline(threshold, color='black', linestyle=':', linewidth=1.5, label=f'Stability Threshold ({threshold})')
    axes[2].axvline(optimal_k, color='#d62728', linestyle='--', linewidth=2, label=f'Optimal K={optimal_k}')
    axes[2].set_title('Consensus Stability', fontsize=14)
    axes[2].set_xlabel('Number of Clusters (K)', fontsize=12)
    axes[2].set_ylabel('Mean ARI Score', fontsize=12)
    axes[2].grid(True, linestyle=':', alpha=0.6)
    axes[2].legend()

    out_path = os.path.join(output_dir, f"{name}_k_optimization.png")
    plt.savefig(out_path, dpi=300, bbox_inches='tight')
    plt.close()


def _ensure_matplotlib_publication_style(dpi: int = 300):
    plt.rcParams.update({
        "figure.dpi": dpi, "savefig.dpi": dpi,
        "figure.facecolor": "white", "axes.facecolor": "white",
        "axes.grid": True, "grid.linestyle": ":", "grid.alpha": 0.6,
        "font.size": 11, "axes.labelsize": 11, "axes.titlesize": 12,
        "legend.fontsize": 9, "xtick.labelsize": 10, "ytick.labelsize": 10,
    })


def _get_k_optimization_dirs(cfg: dict) -> List[str]:
    raw = cfg.get("k_optimization_dirs")
    if raw is None:
        raw = os.path.join(cfg["output_dir"], "k_optimization")
    if isinstance(raw, str):
        raw = [raw]
    return [os.path.abspath(d) for d in raw]


def build_global_model(cfg: Optional[dict] = None) -> Tuple[Optional[object], Optional[np.ndarray]]:
    if cfg is None:
        cfg = CFG.copy()

    # 处理 habitat.py 在其他文件夹的情况
    if cfg.get("habitat_module_path"):
        sys.path.insert(0, os.path.abspath(cfg["habitat_module_path"]))

    # 注入猴子补丁 (Monkey Patching)
    # 强制同步配置到 habitat 模块内部
    habitat.HABITAT.update({
        "k_range": cfg["k_range"],
        "random_state": cfg["random_state"],
        "n_init": cfg["n_init"],
        "consensus_subsample": cfg["consensus_subsample"],
        "consensus_n_iter": cfg["consensus_n_iter"],
        "ari_threshold": cfg["ari_threshold"],
    })
    # 替换绘图函数为英文美化版
    habitat._plot_k_optimization = _plot_k_optimization_english

    logging.basicConfig(level=cfg["log_level"],
                    format="%(asctime)s  %(levelname)-8s  %(message)s",
                    datefmt="%H:%M:%S")
    logger = logging.getLogger(__name__)
    _ensure_matplotlib_publication_style(cfg.get("publication_dpi", 300))

    out_dir = cfg["output_dir"]
    os.makedirs(out_dir, exist_ok=True)

    # === 多路径支持核心逻辑 ===
    preprocessed_roots = cfg.get("preprocessed_roots") or cfg.get("preprocessed_root")
    if isinstance(preprocessed_roots, str):
        preprocessed_roots = [preprocessed_roots]
    preprocessed_roots = [os.path.abspath(p) for p in preprocessed_roots if p]

    patient_to_root = {}
    for root in preprocessed_roots:
        if not os.path.isdir(root):
            logger.warning(f"Preprocessed root 不存在，已跳过: {root}")
            continue
        for d in os.listdir(root):
            if d.startswith("patient_") and os.path.isdir(os.path.join(root, d)):
                pid = d.replace("patient_", "")
                if pid in patient_to_root:
                    logger.warning(f"重复 patient_id '{pid}'，使用路径: {root}")
                patient_to_root[pid] = root

    if cfg["patient_ids"] is None:
        patient_ids = sorted(patient_to_root.keys())
    else:
        patient_ids = [str(p) for p in cfg["patient_ids"] if str(p) in patient_to_root]

    logger.info(f"开始构建全局生境模型 | 共 {len(patient_ids)} 个患者（来自 {len(preprocessed_roots)} 个目录）")

    all_voxel_feats = []
    for pid in patient_ids:
        root_for_pid = patient_to_root[pid]
        pid_dir = os.path.join(root_for_pid, f"patient_{pid}")
        cache_path = os.path.join(pid_dir, f"p{pid}_voxel_features.npy")

        if cfg["use_cached_voxel_npy"] and os.path.exists(cache_path) and not cfg["force_recompute"]:
            vfeats = np.load(cache_path)
        else:
            img_path = os.path.join(pid_dir, f"p{pid}_pre_image.nii.gz")
            msk_path = os.path.join(pid_dir, f"p{pid}_pre_mask.nii.gz")
            if not (os.path.exists(img_path) and os.path.exists(msk_path)):
                logger.warning(f"[{pid}] 缺少预处理文件，跳过")
                continue
            img = sitk.ReadImage(img_path)
            msk = sitk.ReadImage(msk_path)
            vfeats, _ = compute_voxel_feature_maps(img, msk)
            np.save(cache_path, vfeats)

        all_voxel_feats.append(vfeats)

    if not all_voxel_feats:
        logger.error("没有收集到任何体素特征，退出。")
        return None, None

    X_pool = np.concatenate(all_voxel_feats, axis=0).astype(np.float32)
    logger.info(f"全队列池化特征矩阵形状: {X_pool.shape}")

    pooled_path = os.path.join(out_dir, cfg["pooled_features_name"])
    np.save(pooled_path, X_pool)
    logger.info(f"已保存池化特征 → {pooled_path}")

    # 拟合模型
    k_opt_dirs = _get_k_optimization_dirs(cfg)
    primary_k_opt_dir = k_opt_dirs[0]
    os.makedirs(primary_k_opt_dir, exist_ok=True)

    model = CohortHabitatModel()
    model.fit(X_pool, primary_k_opt_dir)

    model_path = os.path.join(out_dir, cfg["model_name"])
    model.save(model_path)
    logger.info(f"全局生境模型已保存 → {model_path}（最优 K = {model.optimal_k}）")

    # 多路径复制 K 优化图
    if len(k_opt_dirs) > 1:
        src_png = os.path.join(primary_k_opt_dir, "Cohort_k_optimization.png")
        if os.path.exists(src_png):
            import shutil
            for extra_dir in k_opt_dirs[1:]:
                os.makedirs(extra_dir, exist_ok=True)
                shutil.copy2(src_png, os.path.join(extra_dir, "Cohort_k_optimization.png"))

    return model, X_pool


if __name__ == "__main__":
    build_global_model()
