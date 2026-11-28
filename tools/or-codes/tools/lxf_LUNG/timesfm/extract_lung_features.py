#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extract_lung_features.py
========================
肺癌时序 CT 影像特征提取脚本

功能：
  - 读取 manifest.csv（患者 ID、时间点、NIfTI 路径、mask 路径）
  - 逐患者逐时间点计算肿瘤体积（volume_ml）和 axial 最长径（max_diam_mm）
  - 支持多靶病灶（multi-lesion）：通过 lesion_id 列区分，按 RECIST 规则聚合
  - 输出 lung_features_long.csv，供 run_lung_recist_timesfm.py 直接读入

Manifest CSV 格式（必填列 *，可选列）：
  patient_id*    | 患者唯一标识（字符串）
  timepoint_idx* | 时间点索引（0=基线，1,2,...）
  days_from_tx_start* | 距治疗开始天数（用于排序）
  image_path*    | CT NIfTI 文件绝对路径（.nii 或 .nii.gz）
  mask_path*     | 对应分割 mask 绝对路径（同维度，同 spacing）
  lesion_id      | 病灶 ID（可选；若存在则支持多靶病灶聚合）

用法：
  python extract_lung_features.py --config config.yaml
  python extract_lung_features.py --config config.yaml --manifest data/my_manifest.csv

作者：Dr. Ying Li, Jinshan Hospital, Fudan University
版本：1.0.0（2026-06）
"""

import argparse
import logging
import os
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

# ── 可选依赖（nibabel 处理 NIfTI）──────────────────────────────────────────
try:
    import nibabel as nib
except ImportError:
    sys.exit(
        "[错误] 缺少 nibabel。请执行：pip install nibabel scikit-image scipy"
    )

# ── 忽略 nibabel 的版本警告（不影响功能）──────────────────────────────────
warnings.filterwarnings("ignore", category=UserWarning, module="nibabel")


# =============================================================================
# 工具函数：日志初始化
# =============================================================================
def setup_logging(verbose: bool = False) -> logging.Logger:
    """
    初始化全局日志器。
    verbose=True 时输出 DEBUG 级别（含每个 voxel 的计算细节）；
    否则输出 INFO 级别（只显示患者级别进度）。
    """
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    return logging.getLogger(__name__)


# =============================================================================
# 工具函数：加载 YAML 配置
# =============================================================================
def load_config(config_path: str) -> dict:
    """
    从 YAML 文件加载配置，返回字典。
    config_path: YAML 文件路径（绝对或相对路径均可）
    """
    config_path = Path(config_path)
    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在：{config_path}")
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return cfg


# =============================================================================
# 核心函数：计算单个时间点的肿瘤特征
# =============================================================================
def compute_features(
    image_path: str,
    mask_path: str,
    binarize_threshold: float = 0.5,
    compute_diameter: bool = True,
    skip_empty: bool = True,
    min_volume_ml: float = 0.01,
    logger: logging.Logger = None,
) -> dict:
    """
    从一对 (image, mask) NIfTI 文件计算肿瘤体积和最长径。

    参数
    ----
    image_path          : CT 图像 NIfTI 路径（仅用于读取 header 中的 spacing）
    mask_path           : 分割 mask NIfTI 路径（与 image 同尺寸同 spacing）
    binarize_threshold  : mask 二值化阈值（概率 mask 用 0.5；整型 mask 同样适用）
    compute_diameter    : 是否计算 axial 最长径（False 时 max_diam_mm 返回 NaN）
    skip_empty          : True=跳过空切片；False=遇到全零切片报错
    min_volume_ml       : 体积小于此值时发出警告（可能是分割噪声）
    logger              : logging.Logger 对象（None 时使用 print）

    返回
    ----
    dict，包含：
      volume_ml    : 肿瘤体积（毫升，1 ml = 1000 mm³）
      max_diam_mm  : axial 切面最长径（毫米，RECIST proxy）
      n_voxels     : 肿瘤体素数
      spacing_xyz  : 体素间距 (x, y, z) mm
    """
    log = logger or logging.getLogger(__name__)

    # ── 1. 加载 mask 并二值化 ──────────────────────────────────────────────
    mask_nib = nib.load(mask_path)
    mask_data = mask_nib.get_fdata(dtype=np.float32)
    binary_mask = mask_data > binarize_threshold  # 布尔型 3D array

    # ── 2. 从 image header 获取体素间距（spacing）────────────────────────
    #    nibabel 的 get_zooms() 返回 (x, y, z) mm 间距
    #    如果 mask header 与 image header 的 spacing 不一致，优先用 image
    img_nib = nib.load(image_path)
    spacing = np.array(img_nib.header.get_zooms()[:3], dtype=float)  # (sx, sy, sz) mm

    log.debug(f"  spacing={spacing} mm，mask shape={binary_mask.shape}")

    # ── 3. 计算体积（volume_ml）──────────────────────────────────────────
    #    体素体积 = sx * sy * sz (mm³)；1 ml = 1000 mm³
    voxel_vol_mm3 = float(np.prod(spacing))
    n_voxels = int(binary_mask.sum())
    volume_ml = n_voxels * voxel_vol_mm3 / 1000.0

    # 体积过小时警告（通常提示分割有误）
    if volume_ml < min_volume_ml:
        log.warning(
            f"  肿瘤体积 {volume_ml:.4f} ml < 最小阈值 {min_volume_ml} ml，"
            f"请检查 mask：{mask_path}"
        )

    # ── 4. 计算 axial 最长径（max_diam_mm）──────────────────────────────
    #    RECIST 1.1 使用"靶灶最长径"，此处以 axial 切面逐层扫描取最大值
    #    逻辑：对每一 axial 切面（z 方向），分别计算 X/Y 方向最大跨度
    #          X 跨度 = cols.sum() * spacing[0]（cols: 沿 axis=0 投影有无肿瘤）
    #          Y 跨度 = rows.sum() * spacing[1]（rows: 沿 axis=1 投影有无肿瘤）
    #    注：这是"bounding box 对角线"的近似，非像素级精确最长径
    #         对于形状规则的肺结节足够准确；不规则肿瘤可改用 scipy.ndimage
    max_diam_mm = float("nan")
    if compute_diameter:
        slice_diameters = []
        for z in range(binary_mask.shape[2]):
            sl = binary_mask[:, :, z]
            if sl.sum() == 0:
                # 空切片跳过（肿瘤不在此层）
                if not skip_empty:
                    raise ValueError(
                        f"mask 切片 z={z} 为全零，请检查分割：{mask_path}"
                    )
                continue

            # X 方向投影（沿 Y 轴"压缩"，判断每列是否有肿瘤）
            # cols[j] = True 表示第 j 列存在肿瘤体素
            cols = np.any(sl, axis=0)   # shape: (n_cols,)；True/False
            # Y 方向投影（沿 X 轴"压缩"，判断每行是否有肿瘤）
            rows = np.any(sl, axis=1)   # shape: (n_rows,)；True/False

            # 跨度 = True 的数量 × 对应方向的体素间距
            d_x = float(cols.sum()) * spacing[0]  # mm，X 方向跨度
            d_y = float(rows.sum()) * spacing[1]  # mm，Y 方向跨度

            # 取该切面最大跨度（非对角线近似，保守估计）
            slice_diameters.append(max(d_x, d_y))

        if slice_diameters:
            max_diam_mm = max(slice_diameters)
        else:
            log.warning(f"  所有切片均为空，无法计算直径：{mask_path}")

    return {
        "volume_ml": round(volume_ml, 6),
        "max_diam_mm": round(max_diam_mm, 4) if not np.isnan(max_diam_mm) else float("nan"),
        "n_voxels": n_voxels,
        "spacing_x": spacing[0],
        "spacing_y": spacing[1],
        "spacing_z": spacing[2],
    }


# =============================================================================
# 核心函数：多靶病灶 RECIST 聚合
# =============================================================================
def aggregate_multi_lesion(group_df: pd.DataFrame, agg_method: str) -> dict:
    """
    将同一患者同一时间点的多个靶病灶聚合为单一测量值。

    RECIST 1.1 规则：
    - 最多选取 5 个靶病灶（每器官最多 2 个）
    - 基线总体积 = 各靶灶体积之和
    - 基线最长径之和（SLD, Sum of Longest Diameters）= 各靶灶最长径之和

    参数
    ----
    group_df   : 同一 (patient_id, timepoint_idx) 的多行 DataFrame
    agg_method : "sum_diameter"（SLD，RECIST 标准）或 "sum_volume"（辅助指标）

    返回
    ----
    dict：aggregated_value（用于 TimesFM 输入）、n_lesions
    """
    n_lesions = len(group_df)

    if agg_method == "sum_diameter":
        # RECIST 1.1 标准：最长径之和（SLD）
        agg_val = group_df["max_diam_mm"].sum()
        feature_col = "max_diam_mm"
    elif agg_method == "sum_volume":
        # 辅助指标：体积之和
        agg_val = group_df["volume_ml"].sum()
        feature_col = "volume_ml"
    else:
        raise ValueError(f"未知聚合方法：{agg_method}，请在 config.yaml 中设置 sum_diameter 或 sum_volume")

    return {
        "aggregated_value": float(agg_val),
        "feature_col_used": feature_col,
        "n_lesions": n_lesions,
    }


# =============================================================================
# 主函数：读取 manifest → 提取特征 → 写出 CSV
# =============================================================================
def main():
    # ── 解析命令行参数 ─────────────────────────────────────────────────────
    parser = argparse.ArgumentParser(
        description="肺癌时序 CT 影像特征提取：volume + axial diameter",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--config", type=str, default="config.yaml",
        help="YAML 配置文件路径（默认：config.yaml）",
    )
    parser.add_argument(
        "--manifest", type=str, default=None,
        help="覆盖 config.yaml 中的 manifest 路径（可选）",
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="覆盖 config.yaml 中的特征输出路径（可选）",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="输出 DEBUG 级别日志（默认 INFO）",
    )
    args = parser.parse_args()

    # ── 加载配置 ──────────────────────────────────────────────────────────
    cfg = load_config(args.config)

    # 命令行参数覆盖 config（优先级：CLI > config.yaml）
    verbose = args.verbose or cfg.get("runtime", {}).get("verbose", False)
    logger = setup_logging(verbose)

    manifest_path = args.manifest or cfg["paths"]["manifest"]
    output_path = args.output or cfg["paths"]["features_out"]
    ext_cfg = cfg.get("extraction", {})
    agg_method = cfg.get("forecast", {}).get("multi_lesion_agg", "sum_diameter")

    logger.info(f"=== 肺癌特征提取开始 ===")
    logger.info(f"配置文件：{args.config}")
    logger.info(f"Manifest：{manifest_path}")
    logger.info(f"输出路径：{output_path}")

    # ── 读取 manifest ─────────────────────────────────────────────────────
    manifest_path = Path(manifest_path)
    if not manifest_path.exists():
        logger.error(f"Manifest 文件不存在：{manifest_path}")
        sys.exit(1)

    manifest = pd.read_csv(manifest_path)
    required_cols = ["patient_id", "timepoint_idx", "days_from_tx_start", "image_path", "mask_path"]
    missing = [c for c in required_cols if c not in manifest.columns]
    if missing:
        logger.error(f"Manifest 缺少必填列：{missing}")
        sys.exit(1)

    # 检测是否存在多靶病灶（lesion_id 列）
    has_multi_lesion = "lesion_id" in manifest.columns
    if has_multi_lesion:
        logger.info(f"检测到 lesion_id 列，启用多靶病灶模式（聚合方法：{agg_method}）")
    else:
        logger.info("未检测到 lesion_id 列，以单靶病灶模式运行")

    total_rows = len(manifest)
    logger.info(f"Manifest 共 {total_rows} 行，{manifest['patient_id'].nunique()} 例患者")

    # ── 逐行提取特征 ──────────────────────────────────────────────────────
    records = []  # 收集每行提取结果

    for idx, row in manifest.iterrows():
        pid = row["patient_id"]
        tp = row["timepoint_idx"]
        days = row["days_from_tx_start"]
        img_path = row["image_path"]
        msk_path = row["mask_path"]
        lesion_id = row.get("lesion_id", "L1")  # 无 lesion_id 时默认 L1

        logger.info(
            f"[{idx+1}/{total_rows}] 患者 {pid}，时间点 {tp}（day {days}），病灶 {lesion_id}"
        )

        # 检查文件存在性（快速失败，不等到 nibabel 报错）
        for fpath, fname in [(img_path, "image"), (msk_path, "mask")]:
            if not Path(fpath).exists():
                logger.error(f"  {fname} 文件不存在：{fpath}，跳过此行")
                break
        else:
            # ── 调用特征提取核心函数 ────────────────────────────────────
            try:
                feat = compute_features(
                    image_path=img_path,
                    mask_path=msk_path,
                    binarize_threshold=ext_cfg.get("mask_binarize_threshold", 0.5),
                    compute_diameter=ext_cfg.get("compute_diameter", True),
                    skip_empty=ext_cfg.get("skip_empty_slices", True),
                    min_volume_ml=ext_cfg.get("min_tumor_volume_ml", 0.01),
                    logger=logger,
                )
                records.append({
                    "patient_id": pid,
                    "timepoint_idx": tp,
                    "days_from_tx_start": days,
                    "lesion_id": lesion_id,
                    "image_path": img_path,
                    "mask_path": msk_path,
                    **feat,  # volume_ml, max_diam_mm, n_voxels, spacing_xyz
                })
                logger.info(
                    f"  → volume={feat['volume_ml']:.3f} ml，"
                    f"max_diam={feat['max_diam_mm']:.2f} mm，"
                    f"voxels={feat['n_voxels']}"
                )
            except Exception as e:
                logger.error(f"  提取失败（患者={pid}，时间点={tp}）：{e}")
                continue

    if not records:
        logger.error("未提取到任何特征，请检查 manifest 和文件路径")
        sys.exit(1)

    # ── 构建特征 DataFrame ────────────────────────────────────────────────
    feat_df = pd.DataFrame(records)

    # ── 多靶病灶聚合（若有 lesion_id）────────────────────────────────────
    if has_multi_lesion:
        logger.info("开始多靶病灶聚合...")
        agg_records = []
        # 按 (patient_id, timepoint_idx) 分组，对每组中的多病灶聚合
        for (pid, tp), grp in feat_df.groupby(["patient_id", "timepoint_idx"]):
            agg = aggregate_multi_lesion(grp, agg_method)
            # 保留第一行的元信息（days、spacing 等），替换聚合后的测量值
            row_base = grp.iloc[0].to_dict()
            row_base.update({
                "n_lesions": agg["n_lesions"],
                "aggregated_value": agg["aggregated_value"],
                "feature_col_used": agg["feature_col_used"],
                # 为 TimesFM 统一使用 aggregated_value 列；下游脚本读取此列
                "volume_ml": grp["volume_ml"].sum(),         # 总体积（辅助）
                "max_diam_mm": grp["max_diam_mm"].sum(),     # SLD（RECIST 标准）
            })
            agg_records.append(row_base)
        feat_df = pd.DataFrame(agg_records)
        logger.info(f"聚合后：{len(feat_df)} 行（每患者每时间点一行）")

    # ── 排序（患者 → 时间点）────────────────────────────────────────────
    feat_df = feat_df.sort_values(
        ["patient_id", "days_from_tx_start"]
    ).reset_index(drop=True)

    # ── 写出 CSV ──────────────────────────────────────────────────────────
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    feat_df.to_csv(out_path, index=False)
    logger.info(f"=== 特征提取完成 ===")
    logger.info(f"共提取 {len(feat_df)} 行特征，已保存至：{out_path}")
    logger.info(
        f"患者数：{feat_df['patient_id'].nunique()}，"
        f"缺失 volume：{feat_df['volume_ml'].isna().sum()}，"
        f"缺失 diameter：{feat_df['max_diam_mm'].isna().sum()}"
    )


# =============================================================================
# 入口
# =============================================================================
if __name__ == "__main__":
    main()
