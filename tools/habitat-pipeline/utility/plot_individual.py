#!/usr/bin/env python3
"""
visualize_pipeline.py
=====================
Delta 生境影像组学流程可视化脚本
版本：v2.0  2026-06

修复内容（相对 v1.0）
--------------------
- [BUG-1] plot_preproc_qc：mask contour 切片逻辑错误（三维数组传入 contour()）已修复
- [BUG-2] plot_registration_qc：Row-1 第三列重复显示 Fixed 图，改为差异图（Fixed−Reg）
- [BUG-3] 所有函数：sagittal / coronal 背景图始终使用 axial 切片，现已分别取正确正交切片
- 新增：plot_habitat_intensity_distribution — 逐 Habitat 强度小提琴图
- 新增：plot_habitat_delta           — pre/post 并排 Delta 对比图
- 新增：plot_cohort_habitat_consistency — 全队列 N×K 一致性面板
- 新增：plot_habitat_feature_space   — t-SNE 特征空间散点图
- 新增：registration Row-2 warped mask 缺失时显示文字提示
- 自动患者发现改为 sorted() 保证顺序可复现

设计原则
--------
- 配置全部置顶（PATIENT_IDS / STAGES / VIZ_CONFIG）
- 所有可调参数软编码（路径 / dpi / alpha / colormap / 切片策略等）
- 核心函数均支持 override_config，可独立 / 批量 / notebook 中灵活调用
- 文件缺失自动跳过 + logger 提示，不中断整体流程

使用方式
--------
1. 修改顶部配置区后直接运行：
       python visualize_pipeline.py

2. 在 notebook 或其他脚本中 import 后调用：
       from visualize_pipeline import run_visualization, plot_habitat_overlay

3. 集成到 pipeline.py debug 模式：
       if debug:
           plot_preproc_qc(pid, "pre")
           plot_registration_qc(pid)
           plot_habitat_overlay(pid)

依赖
----
SimpleITK, numpy, matplotlib, scikit-learn（t-SNE，仅 plot_habitat_feature_space 需要）
"""

import os
import sys
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

import numpy as np
import SimpleITK as sitk
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.colors import ListedColormap

# delta_habitat_pipeline 目录加入 sys.path，供 .nii 彩色导出中懒加载
# registration.py（真实形变传播需复用其 ANTs 变换逻辑）
_PIPELINE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "delta_habitat_pipeline",
)
if _PIPELINE_DIR not in sys.path:
    sys.path.insert(0, _PIPELINE_DIR)

# ============================================================
# 1. 配置区（置顶修改 — 只需改本区域）
# ============================================================

# 所有中间结果 & 最终结果的根目录（可改为任意本地路径）
OUTPUT_ROOT = r"E:\lxf\results_xa"


# 各阶段输出子目录（由 pipeline.py 的 _setup_dirs() 自动创建）
# 键名即阶段标识，与断点标志文件名对应
DIRS = {
    "preprocessed"  : os.path.join(OUTPUT_ROOT, "01_preprocessed"),
    "registration"  : os.path.join(OUTPUT_ROOT, "02_registration"),
    "habitat_masks" : os.path.join(OUTPUT_ROOT, "03_habitat_masks"),
    "features"      : os.path.join(OUTPUT_ROOT, "04_features"),
    "delta"         : os.path.join(OUTPUT_ROOT, "05_delta"),
    "selected"      : os.path.join(OUTPUT_ROOT, "06_selected"),
    "qc_reports"    : os.path.join(OUTPUT_ROOT, "07_qc"),
}

# --- 患者与阶段选择 ---
PATIENT_IDS: Optional[List[str]] = ["guo_qing_wen"]
# None = 自动从 preprocessed 目录发现；或手动指定 ["001", "002"]

STAGES: List[str] = ["preproc", "registration", "habitat"]
# 可选子集：["preproc"] / ["registration", "habitat"] 等

# --- 可视化全局参数 ---
VIZ_CONFIG: Dict[str, Any] = {

    # 输出根目录
    "output_root": os.path.join(OUTPUT_ROOT, "qc_reports", "viz"),

    # 图像显示
    "dpi": 200,
    "robust_percentiles": (2, 98),          # 抗 outlier 显示范围

    # Mask / Contour
    "mask_contour_color": "red",
    "mask_contour_linewidth": 1.8,
    "mask_contour_alpha": 0.85,

    # Habitat overlay
    "colormap_habitat": "tab10",            # matplotlib colormap 名称
    "habitat_overlay_alpha": 0.55,

    # 切片策略（"mid" = 中位数切片；"percent" = 百分位切片）
    "slice_strategy": "mid",
    "slice_percent": 0.5,                   # 仅 strategy="percent" 时生效

    # 输出格式
    "save_format": "png",                   # "png" 或 "pdf"
    "filename_template": "p{patient_id}_{stage}_{suffix}.{ext}",

    # 可选显示元素
    "show_colorbar": True,
    "show_legend": True,

    # t-SNE 参数（plot_habitat_feature_space）
    "tsne_n_components": 2,
    "tsne_perplexity": 30,
    "tsne_random_state": 66,               # 随机种子（保证可复现）
    "tsne_max_voxels": 5000,               # 随机下采样上限（避免内存溢出）

    # 全队列一致性面板（plot_cohort_habitat_consistency）
    "cohort_panel_cell_size": (3, 3),       # 单格 figsize (w, h)

    # 强度分布图（plot_habitat_intensity_distribution）
    "violin_figsize": (8, 4),
}

# --- .nii 彩色图像导出配置（患者范围与 PATIENT_IDS 共用，全体 / 单个样本均可）---
NII_EXPORT_CONFIG: Dict[str, Any] = {
    # 总开关：False 时 run_nii_export() 直接跳过，不生成任何 .nii 彩色文件
    "enabled": True,

    # 处理范围与 PNG 可视化完全一致，统一由顶层 PATIENT_IDS 决定：
    #   PATIENT_IDS = None          → 自动发现全部患者
    #   PATIENT_IDS = ["xxx", ...]  → 仅处理指定患者（可含单个或多个）
    # 无需在此单独指定 mode / single_patient_id。

    # ── 各子项独立开关（可单独关闭不需要的输出）──────────────

    "export_diff_nii": True,                        # Difference (Fixed-Reg)
    "export_fixed_warped_mask_nii": True,            # Fixed + Warped Mask
    "export_registered_warped_mask_nii": True,       # Registered + Warped Mask
    "export_fixed_warped_mask_enlarged_nii": True,   # Fixed + Warped Mask (enlarged)
    "export_habitat_t1_baseline_combined_nii": True, # 全部 t1 baseline habitat 合并 mask（multi-label，1..K，仅 mask 不含图像）
    "export_habitat_warped_direct_nii": True,        # Warped Habitat mask（配准后 t2 空间，直接复用 t1 baseline habitat，仅 mask 不含图像）
    "export_habitat_warped_propagated_nii": True,    # Warped Habitat mask（habitat mask 经真实 ANTs 形变传播后，仅 mask 不含图像）

    # ── 彩色渲染参数 ──────────────────────────────────────
    "diff_colormap": "RdBu_r",             # 差异图 colormap
    "mask_overlay_color": (255, 0, 0),     # mask 叠加颜色（RGB，0-255）
    "mask_overlay_alpha": 0.5,             # mask 叠加透明度


    # ── Enlarged 裁剪 / 放大参数 ──────────────────────────
    "enlarge_margin_ratio": 0.3,   # bbox 各方向外扩比例（相对 bbox 自身尺寸）
    "enlarge_zoom_factor": 2.0,    # 上采样倍数（体素分辨率放大倍数）

    # 输出子目录：{VIZ_CONFIG['output_root']}/{output_subdir}/patient_{pid}/
    "output_subdir": "nii_exports",
}

# --- 日志 ---

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ============================================================
# 2. 辅助函数
# ============================================================

def _cfg(override: Optional[Dict] = None) -> Dict:
    """合并全局配置与临时覆盖参数"""
    return {**VIZ_CONFIG, **(override or {})}


def get_robust_vmin_vmax(
    arr: np.ndarray,
    percentiles: Optional[Tuple[float, float]] = None,
    cfg: Optional[Dict] = None,
) -> Tuple[float, float]:
    """Percentile-based 显示范围（抗 outlier）"""
    if percentiles is None:
        percentiles = (cfg or VIZ_CONFIG)["robust_percentiles"]
    return float(np.percentile(arr, percentiles[0])), float(np.percentile(arr, percentiles[1]))


def get_slice_indices(
    shape: Tuple[int, ...],
    strategy: Optional[str] = None,
    percent: Optional[float] = None,
    cfg: Optional[Dict] = None,
) -> Tuple[int, int, int]:
    """返回 (z, y, x) 三个正交切片索引"""
    c = cfg or VIZ_CONFIG
    if strategy is None:
        strategy = c["slice_strategy"]
    if percent is None:
        percent = c["slice_percent"]

    if strategy == "mid":
        return shape[0] // 2, shape[1] // 2, shape[2] // 2
    else:
        return (
            int(shape[0] * percent),
            int(shape[1] * percent),
            int(shape[2] * percent),
        )


def ensure_dir(path: str) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)


def build_patient_paths(patient_id: str) -> Dict[str, str]:
    """集中构造各阶段路径"""
    return {
        "preproc":      os.path.join(DIRS["preprocessed"], f"patient_{patient_id}"),
        "registration": os.path.join(DIRS["registration"],  f"patient_{patient_id}"),
        "habitat":      os.path.join(DIRS["habitat_masks"], f"patient_{patient_id}"),
    }


def _orthogonal_slices(arr: np.ndarray, z: int, y: int, x: int) -> List[np.ndarray]:
    """从 (D, H, W) 数组提取三个正交二维切片，顺序：Axial / Sagittal / Coronal"""
    return [
        arr[z, :, :],    # Axial   → H×W
        arr[:, y, :],    # Sagittal→ D×W
        arr[:, :, x],    # Coronal → D×H
    ]


def _save_fig(fig: plt.Figure, out_dir: str, filename: str, cfg: Dict) -> str:
    """保存 figure 并关闭"""
    ensure_dir(out_dir)
    out_path = os.path.join(out_dir, filename)
    fig.savefig(out_path, dpi=cfg["dpi"], bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out_path


def _get_viz_output_path(
    patient_id: str, stage: str, suffix: str, cfg: Optional[Dict] = None
) -> str:
    """Return full path to expected visualization output file (for skip/resume check)."""
    c = cfg or VIZ_CONFIG
    out_dir = os.path.join(c["output_root"], f"{stage}_viz")
    filename = c["filename_template"].format(
        patient_id=patient_id, stage=stage, suffix=suffix, ext=c["save_format"]
    )
    return os.path.join(out_dir, filename)


def _skip_existing(out_path: str, log_prefix: str = "") -> Optional[str]:
    """If output file already exists, log skip message and return path; else return None to proceed with generation.
    Enables breakpoint resume (断点续跑) without re-generating unchanged visualizations.
    """
    if os.path.exists(out_path):
        logger.info(f"[Skip] {log_prefix} already exists → {out_path}")
        return out_path
    return None


# ── .nii 彩色导出：RGB 渲染通用工具函数 ─────────────────────

def _nii_cfg(override: Optional[Dict] = None) -> Dict:
    """合并 NII_EXPORT_CONFIG 与临时覆盖参数"""
    return {**NII_EXPORT_CONFIG, **(override or {})}


def _get_nii_export_dir(patient_id: str, cfg: Dict) -> str:
    """返回该患者 .nii 彩色导出的输出目录（自动创建）"""
    base_root = VIZ_CONFIG["output_root"]
    d = os.path.join(base_root, cfg.get("output_subdir", "nii_exports"), f"patient_{patient_id}")
    ensure_dir(d)
    return d


def _nii_skip_existing(out_path: str, log_prefix: str = "") -> Optional[str]:
    """.nii 导出专用的断点续跑跳过检查（与 _skip_existing 逻辑一致，独立命名便于区分日志来源）"""
    if os.path.exists(out_path):
        logger.info(f"[NII-Skip] {log_prefix} already exists → {out_path}")
        return out_path
    return None


def _normalize_to_uint8(
    arr: np.ndarray, vmin: Optional[float] = None, vmax: Optional[float] = None
) -> np.ndarray:
    """将浮点数组按 [vmin, vmax] 归一化并映射至 uint8 [0, 255]（未提供范围时用鲁棒百分位数）"""
    if vmin is None or vmax is None:
        vmin, vmax = get_robust_vmin_vmax(arr)
    arr_c = np.clip(arr, vmin, vmax)
    if vmax > vmin:
        arr_n = (arr_c - vmin) / (vmax - vmin)
    else:
        arr_n = np.zeros_like(arr_c, dtype=np.float32)
    return (arr_n * 255).astype(np.uint8)


def _gray_to_rgb_uint8(
    arr: np.ndarray, vmin: Optional[float] = None, vmax: Optional[float] = None
) -> np.ndarray:
    """灰度体积 (Z,Y,X) float → RGB uint8 体积 (Z,Y,X,3)（R=G=B）"""
    gray_u8 = _normalize_to_uint8(arr, vmin, vmax)
    return np.stack([gray_u8, gray_u8, gray_u8], axis=-1)


def _blend_mask_overlay(
    rgb_arr: np.ndarray,
    mask_arr: np.ndarray,
    color: Tuple[int, int, int],
    alpha: float,
) -> np.ndarray:
    """在 rgb_arr (Z,Y,X,3) 上，对 mask_arr 为 True 的体素做纯色半透明叠加（填充式，非轮廓线）"""
    out = rgb_arr.astype(np.float32).copy()
    color_arr = np.array(color, dtype=np.float32)
    m = mask_arr.astype(bool)
    out[m] = out[m] * (1 - alpha) + color_arr * alpha
    return np.clip(out, 0, 255).astype(np.uint8)


def _apply_colormap_scalar(
    scalar_arr: np.ndarray, cmap_name: str, vmin: float, vmax: float
) -> np.ndarray:
    """标量体积 (Z,Y,X) → RGB uint8 体积 (Z,Y,X,3)，按 matplotlib colormap 着色"""
    cmap = plt.get_cmap(cmap_name)
    if vmax > vmin:
        norm = np.clip((scalar_arr - vmin) / (vmax - vmin), 0, 1)
    else:
        norm = np.zeros_like(scalar_arr, dtype=np.float32)
    rgba = cmap(norm)                      # (Z,Y,X,4) float [0,1]
    return (rgba[..., :3] * 255).astype(np.uint8)


def _blend_colormap_overlay(
    rgb_arr: np.ndarray,
    overlay_rgb: np.ndarray,
    mask_arr: np.ndarray,
    alpha: float,
) -> np.ndarray:
    """在 rgb_arr (Z,Y,X,3) 上，对 mask_arr 为 True 的体素叠加逐体素 RGB 颜色（用于多标签 habitat 着色）"""
    out = rgb_arr.astype(np.float32).copy()
    m = mask_arr.astype(bool)
    out[m] = out[m] * (1 - alpha) + overlay_rgb[m].astype(np.float32) * alpha
    return np.clip(out, 0, 255).astype(np.uint8)


def _build_combined_habitat_mask(
    mask_arrs: List[np.ndarray], reference: sitk.Image
) -> sitk.Image:
    """
    将 K 个二值 habitat mask（各自 (Z,Y,X) bool）合并为单一 multi-label 标量 NIfTI，
    标签值 = habitat 序号（1..K，与其余 habitat 可视化函数的编号规则一致）。
    重叠体素以后者（索引较大的 habitat）覆盖前者（habitat 划分本身应互斥，一般不会重叠）。
    输出为纯 mask（不含任何图像灰度信息），空间信息复制自 reference。
    """
    combined = np.zeros(mask_arrs[0].shape, dtype=np.uint8)
    for h, m_arr in enumerate(mask_arrs):
        combined[m_arr] = h + 1
    combined_img = sitk.GetImageFromArray(combined)
    combined_img.CopyInformation(reference)
    return combined_img


def _rgb_array_to_sitk(rgb_arr: np.ndarray, reference: sitk.Image) -> sitk.Image:
    """(Z,Y,X,3) uint8 numpy 数组 → sitk VectorUInt8 图像，空间信息复制自 reference（scalar sitk.Image）"""

    channels = []
    for c in range(3):
        ch = sitk.GetImageFromArray(np.ascontiguousarray(rgb_arr[..., c]))
        ch.CopyInformation(reference)
        channels.append(ch)
    rgb_img = sitk.Compose(channels)
    rgb_img.CopyInformation(reference)
    return rgb_img


def _find_ants_transform_files(reg_dir: str, patient_id: str) -> Optional[List[str]]:
    """
    在配准结果目录中查找 ANTs 正向变换文件，并按正确顺序排列（[Warp, Affine]）。
    复用 delta_habitat_pipeline/pipeline.py 中 _load_transform_from_disk 的核心逻辑
    （不直接导入 pipeline.py，避免引入其重量级依赖链；此处独立实现同等逻辑）。
    """
    prefix = f"p{patient_id}_"
    if not os.path.isdir(reg_dir):
        return None
    all_files = [
        os.path.join(reg_dir, f) for f in os.listdir(reg_dir) if f.startswith(prefix)
    ]
    warp_files   = [f for f in all_files if f.endswith('Warp.nii.gz') and 'Inverse' not in f]
    affine_files = [f for f in all_files if f.endswith('.mat')]
    transform_files = sorted(warp_files) + sorted(affine_files)
    return transform_files if transform_files else None


def _compute_bbox_with_margin(
    mask_arr: np.ndarray, margin_ratio: float
) -> Tuple[int, int, int, int, int, int]:
    """
    计算 mask 的包围盒（体素索引），并按 margin_ratio（相对 bbox 自身尺寸）外扩。
    mask 为空时回退为整个体积范围。

    Returns
    -------
    (z0, z1, y0, y1, x0, x1) — 半开区间 [z0, z1) 等
    """
    coords = np.argwhere(mask_arr)
    shape = mask_arr.shape
    if coords.size == 0:
        return 0, shape[0], 0, shape[1], 0, shape[2]

    zmin, ymin, xmin = coords.min(axis=0)
    zmax, ymax, xmax = coords.max(axis=0) + 1  # 转为半开区间上界

    def _expand(lo: int, hi: int, dim_size: int) -> Tuple[int, int]:
        margin = int(round((hi - lo) * margin_ratio))
        return max(0, lo - margin), min(dim_size, hi + margin)

    z0, z1 = _expand(int(zmin), int(zmax), shape[0])
    y0, y1 = _expand(int(ymin), int(ymax), shape[1])
    x0, x1 = _expand(int(xmin), int(xmax), shape[2])
    return z0, z1, y0, y1, x0, x1


def _resample_zoom(
    image: sitk.Image, zoom_factor: float, interpolator=sitk.sitkLinear
) -> sitk.Image:
    """
    对 sitk 图像（标量或向量）按 zoom_factor 倍上采样（体素分辨率放大，物理范围不变）。
    spacing 按比例缩小，size 按比例放大，origin/direction 保持不变。
    """
    orig_spacing = image.GetSpacing()
    orig_size    = image.GetSize()
    new_spacing  = [s / zoom_factor for s in orig_spacing]
    new_size     = [int(round(sz * zoom_factor)) for sz in orig_size]

    resampler = sitk.ResampleImageFilter()
    resampler.SetOutputSpacing(new_spacing)
    resampler.SetSize(new_size)
    resampler.SetOutputOrigin(image.GetOrigin())
    resampler.SetOutputDirection(image.GetDirection())
    resampler.SetInterpolator(interpolator)
    resampler.SetDefaultPixelValue(0)
    return resampler.Execute(image)


# ============================================================
# 3. 核心可视化函数
# ============================================================


# ── 3.1 预处理 QC ─────────────────────────────────────────

def plot_preproc_qc(
    patient_id: str,
    timepoint: str = "pre",
    override_config: Optional[Dict] = None,
) -> Optional[str]:
    """
    预处理结果可视化：3 正交切片 + mask contour overlay

    修复 BUG-1：mask contour 切片现与对应视图严格对齐
    修复 BUG-3：sagittal / coronal 背景图现取正确正交切片（非 axial）

    Parameters
    ----------
    patient_id      : 患者编号（字符串）
    timepoint       : "pre" 或 "post"
    override_config : 临时覆盖 VIZ_CONFIG 中任意参数的字典
    """
    cfg   = _cfg(override_config)

    # Breakpoint resume: skip if output already generated
    out_path = _get_viz_output_path(patient_id, "preproc", timepoint, cfg)
    skipped = _skip_existing(out_path, f"Preproc {timepoint} P{patient_id}")
    if skipped:
        return skipped

    paths = build_patient_paths(patient_id)

    img_path = os.path.join(paths["preproc"], f"p{patient_id}_{timepoint}_image.nii.gz")
    msk_path = os.path.join(paths["preproc"], f"p{patient_id}_{timepoint}_mask.nii.gz")

    if not os.path.exists(img_path):
        logger.warning(f"[Preproc] 跳过 {patient_id} {timepoint}：图像不存在 → {img_path}")
        return None

    img = sitk.ReadImage(img_path)
    arr = sitk.GetArrayFromImage(img).astype(np.float32)   # (D, H, W)
    vmin, vmax = get_robust_vmin_vmax(arr, cfg=cfg)
    z, y, x = get_slice_indices(arr.shape, cfg=cfg)

    # 图像正交切片
    img_slices = _orthogonal_slices(arr, z, y, x)

    # [BUG-1 修复] mask 正交切片与图像切片严格对齐
    msk_slices = None
    if os.path.exists(msk_path):
        m_arr = sitk.GetArrayFromImage(sitk.ReadImage(msk_path)).astype(bool)
        msk_slices = _orthogonal_slices(m_arr, z, y, x)

    view_titles = [f"Axial z={z}", f"Sagittal y={y}", f"Coronal x={x}"]

    fig, axes = plt.subplots(1, 3, figsize=(18, 5.5))
    fig.suptitle(
        f"Preprocessed QC  |  P{patient_id}  {timepoint.upper()}"
        f"  |  Shape={arr.shape}"
        f"  |  Spacing={tuple(round(s, 3) for s in img.GetSpacing())}",
        fontsize=12,
    )

    for idx, (ax, img_sl, title) in enumerate(zip(axes, img_slices, view_titles)):
        im = ax.imshow(img_sl, cmap="gray", vmin=vmin, vmax=vmax, origin="lower")
        if msk_slices is not None:
            ax.contour(
                msk_slices[idx],
                levels=[0.5],
                colors=cfg["mask_contour_color"],
                linewidths=cfg["mask_contour_linewidth"],
                alpha=cfg["mask_contour_alpha"],
            )
        ax.set_title(title, fontsize=10)
        ax.axis("off")
        if cfg["show_colorbar"]:
            plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04, label="Intensity")

    plt.tight_layout(rect=[0, 0, 1, 0.96])

    out_dir = os.path.join(cfg["output_root"], "preproc_viz")
    filename = cfg["filename_template"].format(
        patient_id=patient_id, stage="preproc", suffix=timepoint, ext=cfg["save_format"]
    )
    out_path = _save_fig(fig, out_dir, filename, cfg)
    logger.info(f"[Preproc] Saved → {out_path}")
    return out_path


# ── 3.2 配准 QC ───────────────────────────────────────────

def plot_registration_qc(
    patient_id: str,
    override_config: Optional[Dict] = None,
) -> Optional[str]:
    """
    配准结果可视化（2 行 × 3 列）

    Row 1：Fixed | Registered | Difference (Fixed−Reg)   ← [BUG-2 修复：第三列改为差异图]
    Row 2：Fixed+Warped Mask | Registered+Warped Mask | Overlay （mask 缺失时显示文字提示）

    修复 BUG-3：所有视图均取同一 axial 切片（z），保持一致性
    若需正交视图，可将 override_config["show_orthogonal"] = True 扩展。
    """
    cfg   = _cfg(override_config)

    # Breakpoint resume: skip if output already generated
    out_path = _get_viz_output_path(patient_id, "registration", "qc", cfg)
    skipped = _skip_existing(out_path, f"Registration QC P{patient_id}")
    if skipped:
        return skipped

    paths = build_patient_paths(patient_id)

    fixed_path       = os.path.join(paths["preproc"],      f"p{patient_id}_pre_image.nii.gz")
    reg_path         = os.path.join(paths["registration"],  f"p{patient_id}_t2_registered.nii.gz")
    warped_mask_path = os.path.join(paths["registration"],  "qc", f"p{patient_id}_t2_mask_warped.nii.gz")

    if not os.path.exists(reg_path):
        logger.warning(f"[Registration] 跳过 {patient_id}：配准图像不存在 → {reg_path}")
        return None
    if not os.path.exists(fixed_path):
        logger.warning(f"[Registration] 跳过 {patient_id}：Fixed 图像不存在 → {fixed_path}")
        return None

    arr_f = sitk.GetArrayFromImage(sitk.ReadImage(fixed_path)).astype(np.float32)
    arr_r = sitk.GetArrayFromImage(sitk.ReadImage(reg_path)).astype(np.float32)

    # 统一显示范围（基于 fixed）
    vmin, vmax = get_robust_vmin_vmax(arr_f, cfg=cfg)
    z, _, _ = get_slice_indices(arr_f.shape, cfg=cfg)

    # [BUG-2 修复] 第三列为差异图
    diff = arr_f[z, :, :] - arr_r[z, :, :]
    diff_vmax = float(np.percentile(np.abs(diff), 98))

    fig, axes = plt.subplots(2, 3, figsize=(18, 10))
    fig.suptitle(
        f"Registration QC  |  P{patient_id}  |  Fixed vs Registered  |  z={z}",
        fontsize=13,
    )

    # Row 1：图像对比 + 差异图
    row1_data = [
        (arr_f[z, :, :], "gray",    vmin,     vmax,     f"Fixed (t1) z={z}"),
        (arr_r[z, :, :], "gray",    vmin,     vmax,     f"Registered (t2) z={z}"),
        (diff,            "RdBu_r", -diff_vmax, diff_vmax, "Difference (Fixed − Reg)"),
    ]
    for ax, (sl, cmap, lo, hi, title) in zip(axes[0], row1_data):
        im = ax.imshow(sl, cmap=cmap, vmin=lo, vmax=hi, origin="lower")
        ax.set_title(title, fontsize=10)
        ax.axis("off")
        if cfg["show_colorbar"]:
            plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)

    # Row 2：Warped Mask overlay
    if os.path.exists(warped_mask_path):
        m_arr = sitk.GetArrayFromImage(sitk.ReadImage(warped_mask_path)).astype(bool)
        m_sl  = m_arr[z, :, :]

        row2_data = [
            (arr_f[z, :, :], "Fixed + Warped Mask"),
            (arr_r[z, :, :], "Registered + Warped Mask"),
            (arr_f[z, :, :], "Fixed + Warped Mask (enlarged)"),
        ]
        for ax, (base_sl, title) in zip(axes[1], row2_data):
            ax.imshow(base_sl, cmap="gray", vmin=vmin, vmax=vmax, origin="lower")
            ax.contour(
                m_sl,
                levels=[0.5],
                colors=cfg["mask_contour_color"],
                linewidths=cfg["mask_contour_linewidth"],
                alpha=cfg["mask_contour_alpha"],
            )
            ax.set_title(title, fontsize=10)
            ax.axis("off")
    else:
        logger.warning(f"[Registration] Warped mask 不存在，Row-2 显示提示 → {warped_mask_path}")
        for ax in axes[1]:
            ax.text(
                0.5, 0.5,
                "Warped mask not found\n(skip or rerun registration with save_qc=True)",
                ha="center", va="center", transform=ax.transAxes,
                fontsize=10, color="gray", style="italic",
            )
            ax.axis("off")

    plt.tight_layout(rect=[0, 0, 1, 0.96])

    out_dir = os.path.join(cfg["output_root"], "registration_viz")
    filename = cfg["filename_template"].format(
        patient_id=patient_id, stage="registration", suffix="qc", ext=cfg["save_format"]
    )
    out_path = _save_fig(fig, out_dir, filename, cfg)
    logger.info(f"[Registration] Saved → {out_path}")
    return out_path


# ── 3.3 Habitat Overlay ────────────────────────────────────

def plot_habitat_overlay(
    patient_id: str,
    override_config: Optional[Dict] = None,
) -> Optional[str]:
    """
    生境划分可视化：基线图像 + 彩色多 Habitat overlay（3 正交视图）

    修复 BUG-3：sagittal / coronal 背景图现取正确正交切片
    新增：图例标注每个 Habitat 的体积（mL）与占比（%）
    """
    cfg   = _cfg(override_config)

    # Breakpoint resume + early exit if already done
    out_path = _get_viz_output_path(patient_id, "habitat", "overlay", cfg)
    skipped = _skip_existing(out_path, f"Habitat overlay P{patient_id}")
    if skipped:
        return skipped

    paths = build_patient_paths(patient_id)

    baseline_path = os.path.join(paths["preproc"], f"p{patient_id}_pre_image.nii.gz")
    if not os.path.exists(baseline_path):
        logger.warning(f"[Habitat] 跳过 {patient_id}：基线图像不存在 → {baseline_path}")
        return None

    img = sitk.ReadImage(baseline_path)
    arr = sitk.GetArrayFromImage(img).astype(np.float32)
    vmin, vmax = get_robust_vmin_vmax(arr, cfg=cfg)

    habitat_files = sorted([
        f for f in os.listdir(paths["habitat"])
        if f"p{patient_id}_habitat" in f and "_t1_baseline" in f
    ])
    if not habitat_files:
        logger.warning(f"[Habitat] 未找到 {patient_id} 的 Habitat masks → {paths['habitat']}")
        return None

    masks = [sitk.ReadImage(os.path.join(paths["habitat"], f)) for f in habitat_files]
    k     = len(masks)

    spacing    = img.GetSpacing()           # (x, y, z) in mm
    vox_vol_ml = spacing[0] * spacing[1] * spacing[2] / 1000.0

    # 构建 combined label map
    combined = np.zeros_like(arr, dtype=np.uint8)
    vox_counts: List[int] = []
    for h, m in enumerate(masks):
        m_arr = sitk.GetArrayFromImage(m).astype(bool)
        combined[m_arr] = h + 1
        vox_counts.append(int(m_arr.sum()))

    total_vox  = sum(vox_counts) or 1
    # Fix: make label=0 (background / non-ROI) fully transparent so habitat colors
    # only appear inside ROI voxels (addresses "habitat显示应仅基于roi,不针对全局图像")
    colors = plt.get_cmap(cfg["colormap_habitat"])(np.linspace(0, 1, k))
    cmap_colors = np.zeros((k + 1, 4), dtype=np.float32)
    cmap_colors[1:] = colors
    cmap_colors[0, 3] = 0.0  # transparent for bg label=0
    cmap_hab = ListedColormap(cmap_colors)

    z, y, x = get_slice_indices(arr.shape, cfg=cfg)

    # [BUG-3 修复] 背景图与 label 图均取正确正交切片
    img_slices  = _orthogonal_slices(arr,      z, y, x)
    lab_slices  = _orthogonal_slices(combined, z, y, x)
    view_titles = [f"Axial z={z}", f"Sagittal y={y}", f"Coronal x={x}"]

    fig, axes = plt.subplots(1, 3, figsize=(18, 6))
    fig.suptitle(f"Habitat Overlay  |  P{patient_id}  |  K={k}", fontsize=13)

    for ax, img_sl, lab_sl, title in zip(axes, img_slices, lab_slices, view_titles):
        ax.imshow(img_sl,  cmap="gray",   vmin=vmin, vmax=vmax,
                  origin="lower", alpha=0.75)
        ax.imshow(lab_sl,  cmap=cmap_hab, vmin=0,    vmax=k,
                  origin="lower", alpha=cfg["habitat_overlay_alpha"])
        ax.set_title(title, fontsize=10)
        ax.axis("off")

    if cfg["show_legend"]:
        handles = [
            plt.Line2D(
                [0], [0], marker="o", color="w",
                markerfacecolor=colors[i], markersize=9,
                label=(
                    f"H{i+1}  "
                    f"{vox_counts[i] * vox_vol_ml:.1f} mL  "
                    f"({vox_counts[i] / total_vox * 100:.1f}%)"
                ),
            )
            for i in range(k)
        ]
        fig.legend(handles=handles, loc="upper right", fontsize=9, framealpha=0.9)

    plt.tight_layout(rect=[0, 0, 0.88 if cfg["show_legend"] else 1, 0.96])

    out_dir  = os.path.join(cfg["output_root"], "habitat_viz")
    filename = cfg["filename_template"].format(
        patient_id=patient_id, stage="habitat", suffix="overlay", ext=cfg["save_format"]
    )
    out_path = _save_fig(fig, out_dir, filename, cfg)
    logger.info(f"[Habitat] Overlay saved → {out_path}  (K={k})")
    return out_path


# ── 3.4 Habitat 强度分布图（新增）────────────────────────

def plot_habitat_intensity_distribution(
    patient_id: str,
    override_config: Optional[Dict] = None,
) -> Optional[str]:
    """
    逐 Habitat 强度小提琴图（+ 箱线图内嵌）

    验证聚类是否捕捉了真实强度异质性；
    是论文 Supplementary 中最常用的聚类质量展示图之一。
    """
    cfg   = _cfg(override_config)

    # Breakpoint resume
    out_path = _get_viz_output_path(patient_id, "habitat", "intensity_dist", cfg)
    skipped = _skip_existing(out_path, f"Habitat intensity_dist P{patient_id}")
    if skipped:
        return skipped

    paths = build_patient_paths(patient_id)

    baseline_path = os.path.join(paths["preproc"], f"p{patient_id}_pre_image.nii.gz")
    if not os.path.exists(baseline_path):
        logger.warning(f"[Habitat-Dist] 跳过 {patient_id}：基线图像不存在")
        return None

    img = sitk.ReadImage(baseline_path)
    arr = sitk.GetArrayFromImage(img).astype(np.float32)

    habitat_files = sorted([
        f for f in os.listdir(paths["habitat"])
        if f"p{patient_id}_habitat" in f and "_t1_baseline" in f
    ])
    if not habitat_files:
        logger.warning(f"[Habitat-Dist] 未找到 {patient_id} 的 Habitat masks")
        return None

    masks = [sitk.ReadImage(os.path.join(paths["habitat"], f)) for f in habitat_files]
    k     = len(masks)
    colors = plt.get_cmap(cfg["colormap_habitat"])(np.linspace(0, 1, k))

    # 过滤空 Habitat，避免 violinplot 崩溃
    intensity_data = []
    valid_positions = []
    valid_colors = []
    for i, m in enumerate(masks):
        data = arr[sitk.GetArrayFromImage(m).astype(bool)].tolist()
        if len(data) > 0:
            intensity_data.append(data)
            valid_positions.append(i + 1)
            valid_colors.append(colors[i])

    if not intensity_data:
        logger.warning(f"[Habitat-Dist] 跳过 {patient_id}：所有 Habitat 均为空")
        return None

    fig, ax = plt.subplots(figsize=cfg["violin_figsize"])
    parts = ax.violinplot(
        intensity_data,
        positions=valid_positions,
        showmedians=True,
        showextrema=True,
    )

    # 按 Habitat 着色（仅限有效的）
    for patch, color in zip(parts["bodies"], valid_colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.75)
    parts["cmedians"].set_color("black")
    parts["cmedians"].set_linewidth(2)

    ax.set_xticks(range(1, k + 1))
    ax.set_xticklabels([f"H{i+1}" for i in range(k)], fontsize=11)
    ax.set_xlabel("Habitat", fontsize=12)
    ax.set_ylabel("Voxel Intensity (normalized)", fontsize=12)
    ax.set_title(
        f"Intensity Distribution per Habitat  |  P{patient_id}  |  K={k}",
        fontsize=12,
    )
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    plt.tight_layout()

    out_dir  = os.path.join(cfg["output_root"], "habitat_viz")
    filename = cfg["filename_template"].format(
        patient_id=patient_id, stage="habitat", suffix="intensity_dist", ext=cfg["save_format"]
    )
    out_path = _save_fig(fig, out_dir, filename, cfg)
    logger.info(f"[Habitat-Dist] Saved → {out_path}")
    return out_path


# ── 3.5 Delta pre/post 并排对比图（新增）─────────────────

def plot_habitat_delta(
    patient_id: str,
    override_config: Optional[Dict] = None,
) -> Optional[str]:
    """
    Delta 核心图：pre / post 治疗 Habitat overlay 并排对比（axial 视图）

    布局（1 行 × 3 列）：
      左：Pre-treatment Habitat overlay
      中：Post-treatment Habitat overlay（配准后，与 pre 对齐）
      右：标签变化图（pre label ≠ post label 的体素高亮）

    依赖：post-treatment habitat masks 命名含 "_t2_" 或 "_post_"
    """
    cfg   = _cfg(override_config)

    # Breakpoint resume
    out_path = _get_viz_output_path(patient_id, "habitat", "delta", cfg)
    skipped = _skip_existing(out_path, f"Habitat delta P{patient_id}")
    if skipped:
        return skipped

    paths = build_patient_paths(patient_id)

    pre_img_path  = os.path.join(paths["preproc"], f"p{patient_id}_pre_image.nii.gz")
    post_img_path = os.path.join(paths["preproc"], f"p{patient_id}_post_image.nii.gz")

    if not os.path.exists(pre_img_path) or not os.path.exists(post_img_path):
        logger.warning(f"[Habitat-Delta] 跳过 {patient_id}：pre/post 图像不完整")
        return None

    arr_pre  = sitk.GetArrayFromImage(sitk.ReadImage(pre_img_path)).astype(np.float32)
    arr_post = sitk.GetArrayFromImage(sitk.ReadImage(post_img_path)).astype(np.float32)

    # pre habitat masks
    pre_files = sorted([
        f for f in os.listdir(paths["habitat"])
        if f"p{patient_id}_habitat" in f and "_t1_baseline" in f
    ])
    # post habitat masks（支持 _t2_ 或 _post_ 命名）
    post_files = sorted([
        f for f in os.listdir(paths["habitat"])
        if f"p{patient_id}_habitat" in f and ("_t2_" in f or "_post_" in f)
    ])

    if not pre_files:
        logger.warning(f"[Habitat-Delta] 未找到 {patient_id} pre-treatment habitat masks")
        return None
    if not post_files:
        logger.warning(f"[Habitat-Delta] 未找到 {patient_id} post-treatment habitat masks，跳过 Delta 图")
        return None

    k      = len(pre_files)
    # Same ROI-transparent fix as overlay: habitat colors only inside lesion ROI
    colors = plt.get_cmap(cfg["colormap_habitat"])(np.linspace(0, 1, k))
    cmap_colors = np.zeros((k + 1, 4), dtype=np.float32)
    cmap_colors[1:] = colors
    cmap_colors[0, 3] = 0.0
    cmap_h = ListedColormap(cmap_colors)

    def build_combined(mask_files: List[str], ref_shape: Tuple) -> np.ndarray:
        combined = np.zeros(ref_shape, dtype=np.uint8)
        for h, f in enumerate(mask_files):
            m = sitk.GetArrayFromImage(
                sitk.ReadImage(os.path.join(paths["habitat"], f))
            ).astype(bool)
            if m.shape == ref_shape:
                combined[m] = h + 1
        return combined

    combined_pre  = build_combined(pre_files,  arr_pre.shape)
    combined_post = build_combined(post_files, arr_post.shape)

    z, y, x = get_slice_indices(arr_pre.shape, cfg=cfg)
    vmin, vmax = get_robust_vmin_vmax(arr_pre, cfg=cfg)

    # 变化图：pre 与 post label 不一致的区域
    change_map = (combined_pre != combined_post).astype(np.float32)
    change_map[combined_pre == 0] = np.nan   # 掩掉背景

    # Load pre ROI mask to overlay contour (adds explicit ROI visualization to pre/post comparison)
    roi_sl = None
    mask_path = os.path.join(paths["preproc"], f"p{patient_id}_pre_mask.nii.gz")
    if os.path.exists(mask_path):
        try:
            m_arr = sitk.GetArrayFromImage(sitk.ReadImage(mask_path)).astype(bool)
            if m_arr.shape == arr_pre.shape:
                roi_sl = m_arr[z, :, :]
        except Exception:
            pass  # skip silently (no error on bad/missing file, per requirements)

    fig, axes = plt.subplots(1, 3, figsize=(18, 6))
    fig.suptitle(
        f"Habitat Delta  |  P{patient_id}  |  K={k}  |  z={z}",
        fontsize=13,
    )

    panels = [
        (arr_pre[z, :, :],   combined_pre[z, :, :],  cmap_h, "Pre-treatment Habitat"),
        (arr_post[z, :, :],  combined_post[z, :, :], cmap_h, "Post-treatment Habitat"),
        (arr_pre[z, :, :],   change_map[z, :, :],    "Reds",  "Habitat Change Map"),
    ]

    for ax, (bg, overlay, cmap_name, title) in zip(axes, panels):
        ax.imshow(bg, cmap="gray", vmin=vmin, vmax=vmax, origin="lower", alpha=0.75)
        ax.imshow(overlay, cmap=cmap_name,
                  vmin=0, vmax=k if cmap_name is cmap_h else 1,
                  origin="lower", alpha=cfg["habitat_overlay_alpha"])
        if roi_sl is not None:
            # Draw ROI contour (yellow) to highlight whole lesion boundary vs sub-habitat fills
            ax.contour(
                roi_sl, levels=[0.5],
                colors="yellow", linewidths=1.5, alpha=0.85
            )
        ax.set_title(title, fontsize=10)
        ax.axis("off")

    if cfg["show_legend"]:
        handles = [
            plt.Line2D([0], [0], marker="o", color="w",
                       markerfacecolor=colors[i], markersize=8, label=f"H{i+1}")
            for i in range(k)
        ]
        fig.legend(handles=handles, loc="upper right", fontsize=9, framealpha=0.9)

    plt.tight_layout(rect=[0, 0, 0.92, 0.96])

    out_dir  = os.path.join(cfg["output_root"], "habitat_viz")
    filename = cfg["filename_template"].format(
        patient_id=patient_id, stage="habitat", suffix="delta", ext=cfg["save_format"]
    )
    out_path = _save_fig(fig, out_dir, filename, cfg)
    logger.info(f"[Habitat-Delta] Saved → {out_path}")
    return out_path


# ── 3.6 全队列一致性面板（新增）──────────────────────────

def plot_cohort_habitat_consistency(
    all_patient_ids: List[str],
    k: int,
    override_config: Optional[Dict] = None,
) -> Optional[str]:
    """
    全队列 N×K 一致性面板

    每格 = 第 i 个患者第 j 个 Habitat 在轴位中心切片上的 overlay；
    相同列 = 相同 Habitat 编号，便于肉眼确认跨患者语义一致性。
    这是 cohort_mode=True 方法学验证的标准图。

    Parameters
    ----------
    all_patient_ids : 队列中全部患者 ID 列表
    k               : Habitat 数量（固定值）
    override_config : 临时覆盖参数
    """
    cfg    = _cfg(override_config)

    # Breakpoint resume for cohort panel
    out_path = _get_viz_output_path("cohort", "habitat", "consistency", cfg)
    skipped = _skip_existing(out_path, f"Cohort habitat consistency N={len(all_patient_ids)} K={k}")
    if skipped:
        return skipped

    cw, ch = cfg["cohort_panel_cell_size"]
    n      = len(all_patient_ids)
    colors = plt.get_cmap(cfg["colormap_habitat"])(np.linspace(0, 1, k))
    cmap_h = ListedColormap(colors)

    fig, axes = plt.subplots(n, k, figsize=(cw * k, ch * n))
    # 确保 axes 始终为 2D 数组
    if n == 1:
        axes = axes[np.newaxis, :]
    if k == 1:
        axes = axes[:, np.newaxis]

    fig.suptitle(
        f"Cohort Habitat Consistency  |  N={n}  K={k}",
        fontsize=max(11, int(cw * k * 0.6)),
    )

    for i, pid in enumerate(all_patient_ids):
        paths         = build_patient_paths(pid)
        baseline_path = os.path.join(paths["preproc"], f"p{pid}_pre_image.nii.gz")
        habitat_dir   = paths["habitat"]

        if not os.path.exists(baseline_path):
            logger.warning(f"[Cohort] 患者 {pid} 基线图像不存在，跳过")
            for j in range(k):
                axes[i][j].text(0.5, 0.5, f"P{pid}\nNo image",
                                ha="center", va="center", fontsize=8, color="gray")
                axes[i][j].axis("off")
            continue

        arr = sitk.GetArrayFromImage(sitk.ReadImage(baseline_path)).astype(np.float32)
        vmin, vmax = get_robust_vmin_vmax(arr, cfg=cfg)
        z, _, _    = get_slice_indices(arr.shape, cfg=cfg)

        for j in range(k):
            ax    = axes[i][j]
            h_idx = j + 1

            # 尝试加载第 h_idx 个 Habitat mask
            pattern = f"p{pid}_habitat{h_idx}_t1_baseline"
            matches = [f for f in os.listdir(habitat_dir) if pattern in f]

            ax.imshow(arr[z, :, :], cmap="gray", vmin=vmin, vmax=vmax, origin="lower")

            if matches:
                m_arr = sitk.GetArrayFromImage(
                    sitk.ReadImage(os.path.join(habitat_dir, matches[0]))
                ).astype(bool)
                overlay = np.where(m_arr[z, :, :], float(h_idx), np.nan)
                ax.imshow(overlay, cmap=cmap_h, vmin=0, vmax=k,
                          origin="lower", alpha=cfg["habitat_overlay_alpha"])
            else:
                ax.text(0.5, 0.5, "mask\nnot found",
                        ha="center", va="center", transform=ax.transAxes,
                        fontsize=7, color="red")

            # 行标（患者 ID）/ 列标（Habitat 编号）
            if j == 0:
                ax.set_ylabel(f"P{pid}", fontsize=8, rotation=0, labelpad=28, va="center")
            if i == 0:
                ax.set_title(f"H{h_idx}", fontsize=9, color=colors[j].tolist())
            ax.axis("off")

    plt.tight_layout(rect=[0, 0, 1, 0.97])

    out_dir  = os.path.join(cfg["output_root"], "habitat_viz")
    filename = cfg["filename_template"].format(
        patient_id="cohort", stage="habitat", suffix="consistency", ext=cfg["save_format"]
    )
    out_path = _save_fig(fig, out_dir, filename, cfg)
    logger.info(f"[Cohort] Consistency panel saved → {out_path}  (N={n}, K={k})")
    return out_path


# ── 3.7 t-SNE 特征空间散点图（新增）──────────────────────

def plot_habitat_feature_space(
    patient_id: str,
    feature_matrix: np.ndarray,
    labels: np.ndarray,
    override_config: Optional[Dict] = None,
    method: str = "tsne",
) -> Optional[str]:
    """
    t-SNE / PCA / UMAP 特征空间散点图（根据 method 参数切换；默认 t-SNE）

    验证不同 Habitat 在特征空间中的分离度（按 habitat 数聚类着色）；
    建议在 habitat.py 的 optimize_k() 完成后直接调用。
    支持 method: "tsne" | "pca" | "umap"（umap 需额外安装 umap-learn，否则回退 PCA）

    新增 PCA 支持以满足 "增加habitat生成的统计图如主成分分析(根据habitat数聚类)" 需求。

    Parameters
    ----------
    patient_id     : 患者编号（或 "cohort" 表示全队列）
    feature_matrix : (N_voxels, N_features)，已归一化
    labels         : (N_voxels,)，聚类标签（0-indexed 或 1-indexed 均可）
    override_config: 临时覆盖参数
    method         : 降维方法，"tsne"(默认) / "pca" / "umap"
    """
    try:
        from sklearn.manifold import TSNE
    except ImportError:
        logger.error("[t-SNE] scikit-learn 未安装，跳过特征空间可视化")
        return None

    cfg = _cfg(override_config)
    n   = feature_matrix.shape[0]

    # Breakpoint resume support for manual calls too
    out_path = _get_viz_output_path(patient_id, "habitat", method, cfg)
    skipped = _skip_existing(out_path, f"Habitat {method} P{patient_id}")
    if skipped:
        return skipped

    # 随机下采样（避免大数据集内存溢出；对所有 method 生效）
    max_v = cfg["tsne_max_voxels"]
    if n > max_v:
        idx            = np.random.RandomState(cfg["tsne_random_state"]).choice(n, max_v, replace=False)
        feature_matrix = feature_matrix[idx]
        labels         = labels[idx]
        logger.info(f"[{method.upper()}] 下采样至 {max_v} 体素（原始 {n}）")

    k_unique = len(np.unique(labels))
    colors   = plt.get_cmap(cfg["colormap_habitat"])(np.linspace(0, 1, k_unique))

    # General dim reduction dispatcher (t-SNE / PCA / UMAP)
    method_l = method.lower()
    if method_l == "tsne":
        try:
            from sklearn.manifold import TSNE
        except ImportError:
            logger.error("[t-SNE] scikit-learn 未安装，跳过")
            return None
        logger.info(f"[t-SNE] 计算 t-SNE（perplexity={cfg['tsne_perplexity']}）…")
        reducer = TSNE(
            n_components=cfg["tsne_n_components"],
            perplexity=cfg["tsne_perplexity"],
            random_state=cfg["tsne_random_state"],
        )
        emb = reducer.fit_transform(feature_matrix)
        title_method = "t-SNE"
    elif method_l == "pca":
        try:
            from sklearn.decomposition import PCA
        except ImportError:
            logger.error("[PCA] scikit-learn 未安装，跳过")
            return None
        logger.info("[PCA] 计算 PCA（n_components=2）…")
        reducer = PCA(n_components=2)
        emb = reducer.fit_transform(feature_matrix)
        title_method = "PCA"
    elif method_l == "umap":
        try:
            import umap
            logger.info("[UMAP] 计算 UMAP…")
            reducer = umap.UMAP(
                n_components=2,
                random_state=cfg.get("tsne_random_state", 66),
                n_neighbors=15,
                min_dist=0.1,
            )
            emb = reducer.fit_transform(feature_matrix)
            title_method = "UMAP"
        except ImportError:
            logger.warning("[UMAP] umap-learn 未安装，回退到 PCA")
            try:
                from sklearn.decomposition import PCA
                reducer = PCA(n_components=2)
                emb = reducer.fit_transform(feature_matrix)
                title_method = "PCA (fallback)"
            except Exception:
                logger.error("[Embedding] 无可用降维库，跳过")
                return None
    else:
        logger.error(f"[Embedding] 未知 method={method}，请用 'tsne'/'pca'/'umap'")
        return None

    fig, ax = plt.subplots(figsize=(7, 6))
    for idx_c, lbl in enumerate(sorted(np.unique(labels))):
        mask = labels == lbl
        ax.scatter(
            emb[mask, 0], emb[mask, 1],
            c=[colors[idx_c]], alpha=0.4, s=4, label=f"H{lbl}",
        )

    ax.set_title(
        f"{title_method} Feature Space  |  P{patient_id}  |  K={k_unique}",
        fontsize=12,
    )
    ax.set_xlabel(f"{title_method} 1", fontsize=11)
    ax.set_ylabel(f"{title_method} 2", fontsize=11)
    ax.axis("equal")
    if cfg["show_legend"]:
        ax.legend(markerscale=3, fontsize=9, framealpha=0.8)
    plt.tight_layout()

    out_dir  = os.path.join(cfg["output_root"], "habitat_viz")
    filename = cfg["filename_template"].format(
        patient_id=patient_id, stage="habitat", suffix=method, ext=cfg["save_format"]
    )
    out_path = _save_fig(fig, out_dir, filename, cfg)
    logger.info(f"[{title_method}] Saved → {out_path}")
    return out_path


# ── 3.8 .nii 彩色导出（新增）──────────────────────────────
# 以下 6 个函数均输出全 3D 彩色 RGB Vector NIfTI（非单切片 2D 图），
# 可在 ITK-SNAP / 3D Slicer / ParaView 中直接以真彩色显示。

def export_registration_diff_nii(
    patient_id: str,
    override_config: Optional[Dict] = None,
) -> Optional[str]:
    """
    Difference (Fixed − Registered) 全 3D 彩色差异体积（RdBu_r 着色）。
    """
    cfg = _nii_cfg(override_config)
    out_dir = _get_nii_export_dir(patient_id, cfg)
    out_path = os.path.join(out_dir, f"p{patient_id}_diff_fixed_minus_reg.nii.gz")
    skipped = _nii_skip_existing(out_path, f"Diff(Fixed-Reg) P{patient_id}")
    if skipped:
        return skipped

    paths = build_patient_paths(patient_id)
    fixed_path = os.path.join(paths["preproc"], f"p{patient_id}_pre_image.nii.gz")
    reg_path   = os.path.join(paths["registration"], f"p{patient_id}_t2_registered.nii.gz")

    if not os.path.exists(fixed_path) or not os.path.exists(reg_path):
        logger.warning(f"[Diff-NII] 跳过 {patient_id}：Fixed/Registered 图像缺失")
        return None

    fixed_img = sitk.ReadImage(fixed_path)
    arr_f = sitk.GetArrayFromImage(fixed_img).astype(np.float32)
    arr_r = sitk.GetArrayFromImage(sitk.ReadImage(reg_path)).astype(np.float32)

    if arr_f.shape != arr_r.shape:
        logger.warning(
            f"[Diff-NII] 跳过 {patient_id}：Fixed/Registered 尺寸不一致 "
            f"{arr_f.shape} vs {arr_r.shape}"
        )
        return None

    diff = arr_f - arr_r
    diff_vmax = float(np.percentile(np.abs(diff), 98)) or 1e-6

    rgb = _apply_colormap_scalar(diff, cfg["diff_colormap"], -diff_vmax, diff_vmax)
    rgb_img = _rgb_array_to_sitk(rgb, fixed_img)
    sitk.WriteImage(rgb_img, out_path)
    logger.info(f"[Diff-NII] Saved → {out_path}")
    return out_path


def export_fixed_warped_mask_nii(
    patient_id: str,
    override_config: Optional[Dict] = None,
) -> Optional[str]:
    """
    Fixed 灰度图 + Warped Mask 红色半透明填充叠加（全 3D 彩色体积）。
    """
    cfg = _nii_cfg(override_config)
    out_dir = _get_nii_export_dir(patient_id, cfg)
    out_path = os.path.join(out_dir, f"p{patient_id}_fixed_warped_mask.nii.gz")
    skipped = _nii_skip_existing(out_path, f"Fixed+WarpedMask P{patient_id}")
    if skipped:
        return skipped

    paths = build_patient_paths(patient_id)
    fixed_path = os.path.join(paths["preproc"], f"p{patient_id}_pre_image.nii.gz")
    warped_mask_path = os.path.join(
        paths["registration"], "qc", f"p{patient_id}_t2_mask_warped.nii.gz"
    )

    if not os.path.exists(fixed_path):
        logger.warning(f"[Fixed+WarpedMask-NII] 跳过 {patient_id}：Fixed 图像不存在")
        return None
    if not os.path.exists(warped_mask_path):
        logger.warning(f"[Fixed+WarpedMask-NII] 跳过 {patient_id}：warped mask 不存在 → {warped_mask_path}")
        return None

    fixed_img = sitk.ReadImage(fixed_path)
    arr_f = sitk.GetArrayFromImage(fixed_img).astype(np.float32)
    mask_arr = sitk.GetArrayFromImage(sitk.ReadImage(warped_mask_path)).astype(bool)

    if mask_arr.shape != arr_f.shape:
        logger.warning(f"[Fixed+WarpedMask-NII] 跳过 {patient_id}：Fixed/Mask 尺寸不一致")
        return None

    vmin, vmax = get_robust_vmin_vmax(arr_f)
    rgb = _gray_to_rgb_uint8(arr_f, vmin, vmax)
    rgb = _blend_mask_overlay(rgb, mask_arr, cfg["mask_overlay_color"], cfg["mask_overlay_alpha"])

    rgb_img = _rgb_array_to_sitk(rgb, fixed_img)
    sitk.WriteImage(rgb_img, out_path)
    logger.info(f"[Fixed+WarpedMask-NII] Saved → {out_path}")
    return out_path


def export_registered_warped_mask_nii(
    patient_id: str,
    override_config: Optional[Dict] = None,
) -> Optional[str]:
    """
    Registered 灰度图 + Warped Mask 红色半透明填充叠加（全 3D 彩色体积）。
    """
    cfg = _nii_cfg(override_config)
    out_dir = _get_nii_export_dir(patient_id, cfg)
    out_path = os.path.join(out_dir, f"p{patient_id}_registered_warped_mask.nii.gz")
    skipped = _nii_skip_existing(out_path, f"Registered+WarpedMask P{patient_id}")
    if skipped:
        return skipped

    paths = build_patient_paths(patient_id)
    reg_path = os.path.join(paths["registration"], f"p{patient_id}_t2_registered.nii.gz")
    warped_mask_path = os.path.join(
        paths["registration"], "qc", f"p{patient_id}_t2_mask_warped.nii.gz"
    )

    if not os.path.exists(reg_path):
        logger.warning(f"[Registered+WarpedMask-NII] 跳过 {patient_id}：配准图像不存在")
        return None
    if not os.path.exists(warped_mask_path):
        logger.warning(f"[Registered+WarpedMask-NII] 跳过 {patient_id}：warped mask 不存在 → {warped_mask_path}")
        return None

    reg_img = sitk.ReadImage(reg_path)
    arr_r = sitk.GetArrayFromImage(reg_img).astype(np.float32)
    mask_arr = sitk.GetArrayFromImage(sitk.ReadImage(warped_mask_path)).astype(bool)

    if mask_arr.shape != arr_r.shape:
        logger.warning(f"[Registered+WarpedMask-NII] 跳过 {patient_id}：Registered/Mask 尺寸不一致")
        return None

    vmin, vmax = get_robust_vmin_vmax(arr_r)
    rgb = _gray_to_rgb_uint8(arr_r, vmin, vmax)
    rgb = _blend_mask_overlay(rgb, mask_arr, cfg["mask_overlay_color"], cfg["mask_overlay_alpha"])

    rgb_img = _rgb_array_to_sitk(rgb, reg_img)
    sitk.WriteImage(rgb_img, out_path)
    logger.info(f"[Registered+WarpedMask-NII] Saved → {out_path}")
    return out_path


def export_fixed_warped_mask_enlarged_nii(
    patient_id: str,
    override_config: Optional[Dict] = None,
) -> Optional[str]:
    """
    Fixed + Warped Mask 叠加图，裁剪至 mask 包围盒（按 enlarge_margin_ratio 外扩）
    并按 enlarge_zoom_factor 上采样放大（全 3D 彩色体积）。
    """
    cfg = _nii_cfg(override_config)
    out_dir = _get_nii_export_dir(patient_id, cfg)
    out_path = os.path.join(out_dir, f"p{patient_id}_fixed_warped_mask_enlarged.nii.gz")
    skipped = _nii_skip_existing(out_path, f"Fixed+WarpedMask(enlarged) P{patient_id}")
    if skipped:
        return skipped

    paths = build_patient_paths(patient_id)
    fixed_path = os.path.join(paths["preproc"], f"p{patient_id}_pre_image.nii.gz")
    warped_mask_path = os.path.join(
        paths["registration"], "qc", f"p{patient_id}_t2_mask_warped.nii.gz"
    )

    if not os.path.exists(fixed_path) or not os.path.exists(warped_mask_path):
        logger.warning(f"[Fixed+WarpedMask-Enlarged-NII] 跳过 {patient_id}：图像/掩膜缺失")
        return None

    fixed_img = sitk.ReadImage(fixed_path)
    arr_f = sitk.GetArrayFromImage(fixed_img).astype(np.float32)
    mask_arr = sitk.GetArrayFromImage(sitk.ReadImage(warped_mask_path)).astype(bool)

    if mask_arr.shape != arr_f.shape:
        logger.warning(f"[Fixed+WarpedMask-Enlarged-NII] 跳过 {patient_id}：尺寸不一致")
        return None
    if not mask_arr.any():
        logger.warning(f"[Fixed+WarpedMask-Enlarged-NII] 跳过 {patient_id}：mask 为空")
        return None

    vmin, vmax = get_robust_vmin_vmax(arr_f)
    rgb = _gray_to_rgb_uint8(arr_f, vmin, vmax)
    rgb = _blend_mask_overlay(rgb, mask_arr, cfg["mask_overlay_color"], cfg["mask_overlay_alpha"])
    rgb_img = _rgb_array_to_sitk(rgb, fixed_img)

    z0, z1, y0, y1, x0, x1 = _compute_bbox_with_margin(mask_arr, cfg["enlarge_margin_ratio"])
    roi = sitk.RegionOfInterestImageFilter()
    roi.SetSize([int(x1 - x0), int(y1 - y0), int(z1 - z0)])
    roi.SetIndex([int(x0), int(y0), int(z0)])
    cropped = roi.Execute(rgb_img)
    enlarged = _resample_zoom(cropped, cfg["enlarge_zoom_factor"])

    sitk.WriteImage(enlarged, out_path)
    logger.info(
        f"[Fixed+WarpedMask-Enlarged-NII] Saved → {out_path}  "
        f"(bbox z:{z0}-{z1} y:{y0}-{y1} x:{x0}-{x1}, zoom={cfg['enlarge_zoom_factor']})"
    )
    return out_path


def export_habitat_t1_baseline_combined_nii(
    patient_id: str,
    override_config: Optional[Dict] = None,
) -> Optional[str]:
    """
    T1 Baseline Habitat 合并 Mask：将 03_habitat_masks 下该患者全部独立 habitat mask
    （p{id}_habitat{h}_t1_baseline.nii.gz）合并为单一 multi-label（1..K）NIfTI，
    仅输出 mask 本身，不含任何图像灰度信息。
    """
    cfg = _nii_cfg(override_config)
    out_dir = _get_nii_export_dir(patient_id, cfg)
    out_path = os.path.join(out_dir, f"p{patient_id}_habitat_t1_baseline_combined.nii.gz")
    skipped = _nii_skip_existing(out_path, f"HabitatT1BaselineCombined P{patient_id}")
    if skipped:
        return skipped

    paths = build_patient_paths(patient_id)
    baseline_path = os.path.join(paths["preproc"], f"p{patient_id}_pre_image.nii.gz")
    if not os.path.exists(baseline_path):
        logger.warning(f"[HabitatT1BaselineCombined-NII] 跳过 {patient_id}：基线图像不存在")
        return None

    habitat_files = sorted([
        f for f in os.listdir(paths["habitat"])
        if f"p{patient_id}_habitat" in f and "_t1_baseline" in f
    ])
    if not habitat_files:
        logger.warning(f"[HabitatT1BaselineCombined-NII] 未找到 {patient_id} 的 Habitat masks")
        return None

    baseline_img = sitk.ReadImage(baseline_path)
    ref_shape = sitk.GetArrayFromImage(baseline_img).shape

    mask_arrs = []
    for f in habitat_files:
        m_arr = sitk.GetArrayFromImage(
            sitk.ReadImage(os.path.join(paths["habitat"], f))
        ).astype(bool)
        if m_arr.shape == ref_shape:
            mask_arrs.append(m_arr)
        else:
            logger.warning(f"[HabitatT1BaselineCombined-NII] {f} 尺寸与基线图像不一致，跳过该 habitat")

    if not mask_arrs:
        logger.warning(f"[HabitatT1BaselineCombined-NII] 跳过 {patient_id}：无有效 habitat mask")
        return None

    combined_img = _build_combined_habitat_mask(mask_arrs, baseline_img)
    sitk.WriteImage(combined_img, out_path)
    logger.info(f"[HabitatT1BaselineCombined-NII] Saved → {out_path}  (K={len(mask_arrs)})")
    return out_path


def export_habitat_warped_direct_nii(
    patient_id: str,
    override_config: Optional[Dict] = None,
) -> Optional[str]:
    """
    Warped Habitat mask（直接复用版）：t1 baseline Habitat masks 合并为
    multi-label mask（1..K），空间信息复制自 Registered（配准后 t2）图像。
    对应 pipeline.py 中"方案A"（habitat mask 与配准后图像天然同处 t1 空间，
    无需二次传播）。仅输出 mask 本身，不含图像内容。
    """
    cfg = _nii_cfg(override_config)
    out_dir = _get_nii_export_dir(patient_id, cfg)
    out_path = os.path.join(out_dir, f"p{patient_id}_habitat_warped_direct.nii.gz")
    skipped = _nii_skip_existing(out_path, f"HabitatWarped(direct) P{patient_id}")
    if skipped:
        return skipped

    paths = build_patient_paths(patient_id)
    reg_path = os.path.join(paths["registration"], f"p{patient_id}_t2_registered.nii.gz")
    if not os.path.exists(reg_path):
        logger.warning(f"[HabitatWarped-Direct-NII] 跳过 {patient_id}：配准图像不存在")
        return None

    habitat_files = sorted([
        f for f in os.listdir(paths["habitat"])
        if f"p{patient_id}_habitat" in f and "_t1_baseline" in f
    ])
    if not habitat_files:
        logger.warning(f"[HabitatWarped-Direct-NII] 未找到 {patient_id} 的 Habitat masks")
        return None

    reg_img = sitk.ReadImage(reg_path)
    arr_r = sitk.GetArrayFromImage(reg_img).astype(np.float32)

    mask_arrs = []
    for f in habitat_files:
        m_arr = sitk.GetArrayFromImage(
            sitk.ReadImage(os.path.join(paths["habitat"], f))
        ).astype(bool)
        if m_arr.shape == arr_r.shape:
            mask_arrs.append(m_arr)
        else:
            logger.warning(f"[HabitatWarped-Direct-NII] {f} 尺寸与配准图像不一致，跳过该 habitat")

    if not mask_arrs:
        logger.warning(f"[HabitatWarped-Direct-NII] 跳过 {patient_id}：无有效 habitat mask")
        return None

    combined_img = _build_combined_habitat_mask(mask_arrs, reg_img)
    sitk.WriteImage(combined_img, out_path)
    logger.info(f"[HabitatWarped-Direct-NII] Saved → {out_path}  (K={len(mask_arrs)})")
    return out_path



def export_habitat_warped_propagated_nii(
    patient_id: str,
    override_config: Optional[Dict] = None,
) -> Optional[str]:
    """
    Warped Habitat（真实形变传播版）：调用 delta_habitat_pipeline/registration.py
    中的 propagate_masks()，复用已保存的 ANTs SyN 正向变换文件，对 t1 baseline
    Habitat masks 执行真正的形变传播，再叠加到 Registered 图像上做彩色可视化。

    若 ants 未安装或变换文件缺失，记录警告并跳过（不中断其他导出项）。
    传播产生的中间 mask 文件写入本函数的 .nii 导出目录（不污染
    pipeline 生产环境的 02_registration 目录）。
    """
    cfg = _nii_cfg(override_config)
    out_dir = _get_nii_export_dir(patient_id, cfg)
    out_path = os.path.join(out_dir, f"p{patient_id}_habitat_warped_propagated.nii.gz")
    skipped = _nii_skip_existing(out_path, f"HabitatWarped(propagated) P{patient_id}")
    if skipped:
        return skipped

    paths = build_patient_paths(patient_id)
    reg_path = os.path.join(paths["registration"], f"p{patient_id}_t2_registered.nii.gz")
    t1_path  = os.path.join(paths["preproc"], f"p{patient_id}_pre_image.nii.gz")

    if not os.path.exists(reg_path) or not os.path.exists(t1_path):
        logger.warning(f"[HabitatWarped-Propagated-NII] 跳过 {patient_id}：图像缺失")
        return None

    habitat_files = sorted([
        f for f in os.listdir(paths["habitat"])
        if f"p{patient_id}_habitat" in f and "_t1_baseline" in f
    ])
    if not habitat_files:
        logger.warning(f"[HabitatWarped-Propagated-NII] 未找到 {patient_id} 的 Habitat masks")
        return None

    transform_files = _find_ants_transform_files(paths["registration"], patient_id)
    if not transform_files:
        logger.warning(f"[HabitatWarped-Propagated-NII] 跳过 {patient_id}：未找到配准变换文件")
        return None

    try:
        import registration as _reg_mod   # delta_habitat_pipeline/registration.py（懒加载，需 ants）
    except ImportError as e:
        logger.warning(
            f"[HabitatWarped-Propagated-NII] 跳过 {patient_id}：无法导入 registration 模块"
            f"（ants 可能未安装）：{e}"
        )
        return None

    t1_img = sitk.ReadImage(t1_path)
    habitat_masks_t1 = [
        sitk.ReadImage(os.path.join(paths["habitat"], f)) for f in habitat_files
    ]
    transform = {'fwdtransforms': transform_files, 'invtransforms': []}

    try:
        propagated_masks = _reg_mod.propagate_masks(
            habitat_masks = habitat_masks_t1,
            transform      = transform,
            fixed_image    = t1_img,
            output_dir     = out_dir,
            patient_id     = f"{patient_id}_nii_export",   # 避免与 pipeline 生产文件命名冲突
        )
    except Exception as e:
        logger.warning(f"[HabitatWarped-Propagated-NII] 跳过 {patient_id}：形变传播失败（{e}）")
        return None

    reg_img = sitk.ReadImage(reg_path)
    arr_r = sitk.GetArrayFromImage(reg_img).astype(np.float32)

    mask_arrs = []
    for m in propagated_masks:
        m_arr = sitk.GetArrayFromImage(m).astype(bool)
        if m_arr.shape == arr_r.shape:
            mask_arrs.append(m_arr)

    if not mask_arrs:
        logger.warning(f"[HabitatWarped-Propagated-NII] 跳过 {patient_id}：传播后无有效 habitat mask")
        return None

    combined_img = _build_combined_habitat_mask(mask_arrs, reg_img)
    sitk.WriteImage(combined_img, out_path)
    logger.info(f"[HabitatWarped-Propagated-NII] Saved → {out_path}  (K={len(mask_arrs)})")
    return out_path



def run_nii_export(
    patient_ids: Optional[List[str]] = None,
    override_config: Optional[Dict] = None,
) -> None:
    """
    .nii 彩色导出批量 / 单样本入口。

    受 NII_EXPORT_CONFIG["enabled"] 总开关控制；处理范围与 run_visualization()
    完全一致，直接复用传入的 patient_ids（其唯一来源为顶层 PATIENT_IDS）：
    - patient_ids 为具体列表 → 仅处理该列表（可含单个或多个患者）
    - patient_ids 为 None    → 自动发现 preprocessed 目录下全部患者
    每个子导出项可通过各自的 export_* 开关独立启用 / 禁用，
    单个患者的单个导出项失败不影响其余患者 / 其余导出项。
    """
    cfg = _nii_cfg(override_config)
    if not cfg["enabled"]:
        logger.info("[NII-Export] NII_EXPORT_CONFIG['enabled']=False，跳过 .nii 彩色导出")
        return

    if patient_ids is not None:
        target_ids = patient_ids
    else:
        preproc_root = DIRS["preprocessed"]
        target_ids = sorted([
            d.replace("patient_", "")
            for d in os.listdir(preproc_root)
            if d.startswith("patient_") and os.path.isdir(os.path.join(preproc_root, d))
        ])

    logger.info(f"[NII-Export] 开始 .nii 彩色导出 | 患者数: {len(target_ids)}")


    exporters = [
        ("export_diff_nii",                          export_registration_diff_nii),
        ("export_fixed_warped_mask_nii",              export_fixed_warped_mask_nii),
        ("export_registered_warped_mask_nii",         export_registered_warped_mask_nii),
        ("export_fixed_warped_mask_enlarged_nii",     export_fixed_warped_mask_enlarged_nii),
        ("export_habitat_t1_baseline_combined_nii",   export_habitat_t1_baseline_combined_nii),
        ("export_habitat_warped_direct_nii",          export_habitat_warped_direct_nii),
        ("export_habitat_warped_propagated_nii",      export_habitat_warped_propagated_nii),
    ]


    for pid in target_ids:
        for flag_key, fn in exporters:
            if cfg.get(flag_key, True):
                try:
                    fn(pid, override_config)
                except Exception as e:
                    logger.error(f"[NII-Export] 患者 {pid} 执行 {fn.__name__} 失败：{e}")

    logger.info("[NII-Export] .nii 彩色导出全部完成！")


# ============================================================
# 4. 批量运行入口
# ============================================================


def run_visualization(
    patient_ids: Optional[List[str]] = None,
    stages: Optional[List[str]] = None,
    override_config: Optional[Dict] = None,
    run_habitat_intensity: bool = True,
    run_habitat_delta: bool = True,
    run_cohort_panel: bool = False,
    cohort_k: int = 3,
) -> None:
    """
    批量可视化入口（可重复调用）

    Parameters
    ----------
    patient_ids            : 患者 ID 列表；None = 自动发现
    stages                 : 运行阶段列表；None = 使用全局 STAGES
    override_config        : 临时覆盖 VIZ_CONFIG 任意参数
    run_habitat_intensity  : 是否同时生成强度分布图（默认 True）
    run_habitat_delta      : 是否同时生成 Delta pre/post 对比图（默认 True）
    run_cohort_panel       : 是否生成全队列一致性面板（需所有患者处理完毕后调用）
    cohort_k               : 全队列面板使用的 Habitat 数量
    """
    cfg = _cfg(override_config)
    ensure_dir(cfg["output_root"])

    # 自动发现患者（sorted 保证顺序可复现）
    if patient_ids is None:
        preproc_root = DIRS["preprocessed"]
        patient_ids  = sorted([
            d.replace("patient_", "")
            for d in os.listdir(preproc_root)
            if d.startswith("patient_") and os.path.isdir(os.path.join(preproc_root, d))
        ])

    if stages is None:
        stages = STAGES

    logger.info(f"开始可视化 | 患者数: {len(patient_ids)} | 阶段: {stages}")

    for pid in patient_ids:
        if "preproc" in stages:
            plot_preproc_qc(pid, "pre",  override_config)
            plot_preproc_qc(pid, "post", override_config)

        if "registration" in stages:
            plot_registration_qc(pid, override_config)

        if "habitat" in stages:
            plot_habitat_overlay(pid, override_config)
            if run_habitat_intensity:
                plot_habitat_intensity_distribution(pid, override_config)
            if run_habitat_delta:
                plot_habitat_delta(pid, override_config)

    # 全队列面板（需要所有患者都处理完毕）
    if "habitat" in stages and run_cohort_panel and len(patient_ids) > 1:
        plot_cohort_habitat_consistency(patient_ids, cohort_k, override_config)

    logger.info("可视化全部完成！")

    # .nii 彩色导出（受 NII_EXPORT_CONFIG["enabled"] 总开关控制，默认关闭）
    run_nii_export(patient_ids, override_config)


# ============================================================
# 5. 命令行入口
# ============================================================

if __name__ == "__main__":
    # 执行可视化：显式传入 PATIENT_IDS（None 时才会自动发现全部患者）
    run_visualization(patient_ids=PATIENT_IDS)


