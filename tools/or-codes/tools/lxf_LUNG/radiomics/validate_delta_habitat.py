"""
validate_delta_habitat.py
=========================
Delta 生境影像组学模型 — 外部验证脚本

功能
----
对新的验证队列数据执行与训练流程完全对称的处理，并利用训练阶段已保存的
全局模型（Scaler/PCA/KMeans）、特征筛选列表和特征标准化参数，生成验证集
Delta 特征矩阵，最终输出预测结果与评估报告。

验证流程与训练流程的关键区别
------------------------------
| 步骤               | 训练流程                          | 本验证脚本                          |
|--------------------|-----------------------------------|-------------------------------------|
| 生境划分全局模型   | 从头拟合，保存 .joblib            | 直接加载，不重新拟合                |
| 特征标准化 Scaler  | 在训练集上 fit_transform          | 只做 transform（载入训练集参数）    |
| 特征列集合         | 由 LassoCV 筛选后保存列表         | 只保留训练集选定列（严格对齐）      |
| 分类模型           | 在训练集上训练，保存 .joblib      | 直接加载，调用 predict/predict_proba|

使用方法
--------
方式一：命令行
    python validate_delta_habitat.py \
        --data      E:\\lxf\\val\\list.csv \
        --model_dir E:\\lxf\\results \
        --out       E:\\lxf\\val_results \
        --clf       E:\\lxf\\results\\classifier.joblib \
        --delta_t   42 \
        --debug

方式二：Python 调用
    from validate_delta_habitat import run_validation
    result = run_validation(
        data_table_path = r"E:\\lxf\\val\\list.csv",
        model_dir       = r"E:\\lxf\\results",
        output_root     = r"E:\\lxf\\val_results",
        clf_path        = r"E:\\lxf\\results\\classifier.joblib",
        delta_t_days    = 42,
    )

必需的训练阶段产物（model_dir 下）
------------------------------------
    03_habitat_masks/global_habitat_model.joblib   — 全队列生境模型
    06_selected/selected_feature_names.csv          — 训练集筛选后特征列表
    06_selected/feature_scaler.joblib               — 训练集 Z-score Scaler
                                                      （若不存在则跳过标准化，
                                                       见 --no_scaler 选项）
可选：
    {clf_path}                                      — 已训练分类器（sklearn 兼容）

输出文件（output_root 下）
--------------------------
    val_delta_features_raw.csv      — 验证集展平 Delta 矩阵（未标准化）
    val_delta_features_aligned.csv  — 对齐到训练集特征列后的矩阵（已标准化）
    val_predictions.csv             — 预测结果（patient_id, pred_label, pred_prob_1）
    val_qc_report.csv               — 配准 QC 汇总（Dice / Hausdorff）
    val_pipeline_summary.csv        — 运行摘要（成功/失败患者数）
    validate_YYYYMMDD_HHMMSS.log    — 时间戳日志文件
    checkpoints/p{id}_{stage}.done  — 断点续跑标志文件（与训练流程独立）
"""

import os
import sys
import json
import logging
import argparse
import traceback
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import SimpleITK as sitk
import joblib
from sklearn.preprocessing import StandardScaler

# ── 从原始代码库导入复用模块 ────────────────────────────────────────
# 注意：validate 脚本与训练代码放在同一目录下；若不在同一目录，请调整 sys.path
try:
    from config import DIRS, OUTPUT_ROOT, HABITAT, REGISTRATION
    from data_loader import load_patient_pairs
    from preprocessing import preprocess
    from registration import register_images, propagate_masks
    from habitat import (CohortHabitatModel, build_habitat_masks,
                         compute_voxel_feature_maps)
    from radiomics import (extract_features_all_habitats,
                           compute_delta_features,
                           aggregate_delta_features)
except ImportError as e:
    print(
        f"[错误] 无法导入原始模块：{e}\n"
        "请确保 validate_delta_habitat.py 与 config.py / pipeline.py 等文件在同一目录，\n"
        "或在 sys.path 中添加正确路径。"
    )
    sys.exit(1)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  路径与目录管理
# ═══════════════════════════════════════════════════════════════════

# 验证流程各阶段输出子目录（与训练 DIRS 完全平行，但根目录不同）
def _build_val_dirs(output_root: str) -> dict:
    """
    在 output_root 下创建与训练流程平行的子目录结构。

    Parameters
    ----------
    output_root : str — 验证结果根目录

    Returns
    -------
    dict — 与 config.DIRS 结构一致的目录字典（键名相同）
    """
    val_dirs = {
        "preprocessed"  : os.path.join(output_root, "01_preprocessed"),
        "registration"  : os.path.join(output_root, "02_registration"),
        "habitat_masks" : os.path.join(output_root, "03_habitat_masks"),
        "features"      : os.path.join(output_root, "04_features"),
        "delta"         : os.path.join(output_root, "05_delta"),
        "qc_reports"    : os.path.join(output_root, "06_qc"),
    }
    for path in val_dirs.values():
        os.makedirs(path, exist_ok=True)
    os.makedirs(os.path.join(output_root, "checkpoints"), exist_ok=True)
    return val_dirs


def _patient_dir(base_dir: str, patient_id: str) -> str:
    """返回某阶段下该患者的专属子目录（自动创建）。"""
    d = os.path.join(base_dir, f"patient_{patient_id}")
    os.makedirs(d, exist_ok=True)
    return d


# ═══════════════════════════════════════════════════════════════════
#  断点续跑（独立于训练流程的 checkpoints 目录）
# ═══════════════════════════════════════════════════════════════════

def _ckpt_path(output_root: str, patient_id: str, stage: str) -> str:
    """返回验证集断点标志文件路径。"""
    return os.path.join(output_root, "checkpoints", f"p{patient_id}_{stage}.done")


def _is_done(output_root: str, patient_id: str, stage: str) -> bool:
    return os.path.exists(_ckpt_path(output_root, patient_id, stage))


def _mark_done(output_root: str, patient_id: str, stage: str):
    Path(_ckpt_path(output_root, patient_id, stage)).touch()


# ═══════════════════════════════════════════════════════════════════
#  训练产物加载
# ═══════════════════════════════════════════════════════════════════

def load_train_artifacts(model_dir: str) -> dict:
    """
    从 model_dir（训练结果根目录）加载所有必需的训练阶段产物。

    必需文件
    --------
    - 03_habitat_masks/global_habitat_model.joblib  — 生境全局模型
    - 06_selected/selected_feature_names.csv         — 筛选后特征列表

    可选文件
    --------
    - 06_selected/feature_scaler.joblib              — Z-score Scaler
      （pipeline.py 默认不保存 Scaler；若有，自动加载；若无，记录警告后跳过标准化）

    Parameters
    ----------
    model_dir : str — 训练流程的 OUTPUT_ROOT

    Returns
    -------
    dict — {
        'global_model'     : CohortHabitatModel,
        'selected_features': list[str],
        'scaler'           : StandardScaler 或 None,
    }

    Raises
    ------
    FileNotFoundError — 必需文件不存在
    """
    artifacts = {}

    # ── 1. 全局生境模型 ───────────────────────────────────────────
    model_path = os.path.join(model_dir, "03_habitat_masks",
                              "global_habitat_model.joblib")
    if not os.path.exists(model_path):
        raise FileNotFoundError(
            f"全局生境模型不存在：{model_path}\n"
            "请确认 model_dir 指向训练流程的 OUTPUT_ROOT，且训练已完成阶段 1。"
        )
    artifacts['global_model'] = CohortHabitatModel.load(model_path)
    logger.info(
        f"全局生境模型已加载：K={artifacts['global_model'].optimal_k}，"
        f"路径：{model_path}"
    )

    # ── 2. 训练集筛选特征列表 ─────────────────────────────────────
    feat_names_path = os.path.join(model_dir, "06_selected",
                                   "selected_feature_names.csv")
    if not os.path.exists(feat_names_path):
        raise FileNotFoundError(
            f"特征名列表不存在：{feat_names_path}\n"
            "请确认训练流程已完成特征筛选（阶段 3）且提供了标签文件。"
        )
    selected = pd.read_csv(feat_names_path, header=None).iloc[:, 0].tolist()
    artifacts['selected_features'] = selected
    logger.info(f"训练集筛选特征：{len(selected)} 个，路径：{feat_names_path}")

    # ── 3. Z-score Scaler（可选）─────────────────────────────────
    scaler_path = os.path.join(model_dir, "06_selected", "feature_scaler.joblib")
    if os.path.exists(scaler_path):
        artifacts['scaler'] = joblib.load(scaler_path)
        logger.info(f"特征 Scaler 已加载：{scaler_path}")
    else:
        artifacts['scaler'] = None
        logger.warning(
            f"未找到特征 Scaler：{scaler_path}\n"
            "  → 跳过 Z-score 标准化（验证集特征将以原始尺度与分类器对接）。\n"
            "  → 若分类器训练时使用了标准化，预测结果可能不可靠。\n"
            "  建议：在训练流程中保存 Scaler（见本脚本末尾的 FAQ）。"
        )

    return artifacts


# ═══════════════════════════════════════════════════════════════════
#  变换文件重新加载（断点续跑时）
# ═══════════════════════════════════════════════════════════════════

def _load_transform_from_disk(reg_dir: str, patient_id: str) -> dict:
    """
    从磁盘重建 ANTs 变换字典（与 pipeline.py 中 _load_transform_from_disk 相同逻辑）。

    变换文件应用顺序（ANTs fwdtransforms）：
        [Warp.nii.gz, GenericAffine.mat]  — 先非线性，后仿射

    Parameters
    ----------
    reg_dir    : str — 配准结果目录
    patient_id : str

    Returns
    -------
    dict — {'fwdtransforms': [Warp, Affine], 'invtransforms': [InvWarp, Affine]}

    Raises
    ------
    FileNotFoundError — 目录中未找到变换文件
    """
    prefix    = f"p{patient_id}_"
    all_files = [
        os.path.join(reg_dir, f)
        for f in os.listdir(reg_dir)
        if f.startswith(prefix)
    ]

    # 正向变换：[Warp.nii.gz, Affine.mat]（排除 InverseWarp）
    warp_files   = [f for f in all_files
                    if f.endswith('Warp.nii.gz') and 'Inverse' not in f]
    affine_files = [f for f in all_files if f.endswith('.mat')]
    inv_warp_files = [f for f in all_files
                      if f.endswith('Warp.nii.gz') and 'Inverse' in f]

    fwd = sorted(warp_files) + sorted(affine_files)
    inv = sorted(inv_warp_files) + sorted(affine_files)

    if not fwd:
        raise FileNotFoundError(
            f"患者 {patient_id}：在 {reg_dir} 中未找到变换文件。\n"
            "请删除配准阶段的 .done 标志文件后重新运行。"
        )

    logger.debug(f"从磁盘加载变换：fwd={fwd}")
    return {'fwdtransforms': fwd, 'invtransforms': inv}


# ═══════════════════════════════════════════════════════════════════
#  生境划分（应用全局模型）
# ═══════════════════════════════════════════════════════════════════

def apply_global_habitat(image:        sitk.Image,
                          mask:         sitk.Image,
                          global_model: CohortHabitatModel,
                          output_dir:   str,
                          patient_id:   str
                          ) -> tuple[list[sitk.Image], int]:
    """
    将训练阶段拟合好的全局模型直接应用于验证患者，生成 Habitat Mask。

    与训练流程的区别：不重新拟合 Scaler / PCA / KMeans，
    仅调用 global_model.predict() 获取体素标签。

    Parameters
    ----------
    image        : sitk.Image            — 基线（t1）预处理图像
    mask         : sitk.Image            — 基线肿瘤 mask
    global_model : CohortHabitatModel    — 训练阶段保存的全局模型
    output_dir   : str
    patient_id   : str

    Returns
    -------
    (habitat_masks, optimal_k)
    """
    # 提取体素特征（与训练流程完全相同的特征提取函数）
    voxel_feats, mask_arr = compute_voxel_feature_maps(image, mask)

    # 直接使用全局模型预测（不重新拟合）
    labels = global_model.predict(voxel_feats)
    k      = global_model.optimal_k
    logger.info(f"  [生境划分] 患者 {patient_id}：应用全局模型（K={k}）")

    # 每个 Habitat 的最小体素数检查
    for h in range(k):
        n_h = int((labels == h).sum())
        if n_h < HABITAT["min_voxels"]:
            logger.warning(
                f"  患者 {patient_id} Habitat {h} 体素数 {n_h} < "
                f"min_voxels={HABITAT['min_voxels']}，特征可能不可靠。"
            )

    habitat_masks = build_habitat_masks(labels, mask_arr, image, patient_id, output_dir)
    return habitat_masks, k


# ═══════════════════════════════════════════════════════════════════
#  Mask 传播（t1 → t2，使用 invtransforms）
# ═══════════════════════════════════════════════════════════════════

def propagate_masks_val(habitat_masks: list[sitk.Image],
                         transform:     dict,
                         fixed_image:   sitk.Image,
                         moving_image:  sitk.Image,
                         output_dir:    str,
                         patient_id:    str) -> list[sitk.Image]:
    """
    将 t1 Habitat Mask 经反向变换（invtransforms）传播到 t2 原始空间。

    与训练流程 propagate_masks 的区别
    ----------------------------------
    训练流程中 Mask 传播的目标是与 img_t2_reg（已配准到 t1 空间）对齐，
    因此直接用 habitat_masks_t1 即可（方案 A：masks_t2 = masks_t1）。
    本脚本沿用方案 A 以保持与训练特征提取的一致性：
        img_t2_reg 在 t1 空间 + habitat_masks_t1 在 t1 空间 → 逐体素对齐。

    注意：本函数实际返回 habitat_masks_t1（t1 空间的 mask），
    与 img_t2_reg 配对使用。这与配准后图像的空间完全一致。

    Parameters
    ----------
    habitat_masks : list[sitk.Image] — t1 Habitat Mask 列表
    transform     : dict             — ANTs 变换字典（含 fwdtransforms）
    fixed_image   : sitk.Image       — t1 基线图像（空间参考）
    moving_image  : sitk.Image       — t2 随访图像（原始空间，仅作备用）
    output_dir    : str
    patient_id    : str

    Returns
    -------
    list[sitk.Image] — 与 img_t2_reg 对齐的 Habitat Mask（即 habitat_masks_t1）
    """
    # 方案 A：img_t2_reg 已在 t1 空间，直接复用 t1 的 Habitat Mask
    # 保存一份副本到 output_dir，文件名标注 t2_propagated 以保持目录结构一致
    propagated = []
    for h, mask_sitk in enumerate(habitat_masks):
        save_path = os.path.join(
            output_dir, f"p{patient_id}_habitat{h}_t2_propagated.nii.gz"
        )
        sitk.WriteImage(mask_sitk, save_path)
        propagated.append(mask_sitk)
        logger.debug(f"  Habitat {h} mask 已复制（方案 A）→ {save_path}")

    logger.info(
        f"  Mask 传播完成（方案 A：t1 mask 直接复用，共 {len(propagated)} 个）"
    )
    return propagated


# ═══════════════════════════════════════════════════════════════════
#  单患者验证流程
# ═══════════════════════════════════════════════════════════════════

def validate_single_patient(pair:          dict,
                              global_model:  CohortHabitatModel,
                              val_dirs:      dict,
                              output_root:   str,
                              qc_records:    list,
                              delta_store:   dict,
                              delta_t_days:  float = None):
    """
    对单个验证患者执行完整的处理流程。

    执行顺序
    --------
    ① 预处理（与训练完全一致，复用 preprocessing.preprocess）
    ② 配准（与训练完全一致，复用 registration.register_images）
    ③ 生境划分（应用全局模型，不重新拟合）
    ④ Mask 传播（方案 A：直接复用 t1 mask）
    ⑤ 特征提取（t1 + t2，复用 radiomics.extract_features_all_habitats）
    ⑥ Delta 特征计算（复用 radiomics.compute_delta_features）

    每阶段完成后写入 .done 标志文件，支持断点续跑。

    Parameters
    ----------
    pair         : dict                 — 患者配对信息
    global_model : CohortHabitatModel   — 训练阶段的全局生境模型
    val_dirs     : dict                 — 验证集各阶段子目录字典
    output_root  : str                  — 验证结果根目录（用于断点标志）
    qc_records   : list                 — 配准 QC 记录（原地追加）
    delta_store  : dict                 — Delta 特征存储（原地追加）
    delta_t_days : float, optional      — 两次扫描间隔天数
    """
    pid = pair['patient_id']
    logger.info(f"\n{'='*60}\n  验证患者：{pid}\n{'='*60}")

    # ── ① 预处理 ─────────────────────────────────────────────────
    preproc_dir = _patient_dir(val_dirs["preprocessed"], pid)
    if _is_done(output_root, pid, "preproc"):
        logger.info("[跳过] 预处理（已完成，从磁盘加载）")
        img_t1  = sitk.ReadImage(os.path.join(preproc_dir, f"p{pid}_pre_image.nii.gz"))
        mask_t1 = sitk.ReadImage(os.path.join(preproc_dir, f"p{pid}_pre_mask.nii.gz"))
        img_t2  = sitk.ReadImage(os.path.join(preproc_dir, f"p{pid}_post_image.nii.gz"))
        mask_t2 = sitk.ReadImage(os.path.join(preproc_dir, f"p{pid}_post_mask.nii.gz"))
    else:
        img_t1, mask_t1 = preprocess(
            pair['pre']['image_path'],  pair['pre']['roi_path'],
            preproc_dir, pid, 'pre'
        )
        img_t2, mask_t2 = preprocess(
            pair['post']['image_path'], pair['post']['roi_path'],
            preproc_dir, pid, 'post'
        )
        # 快速验证
        for tp, mask in [('pre', mask_t1), ('post', mask_t2)]:
            if int(sitk.GetArrayFromImage(mask).sum()) == 0:
                raise ValueError(f"患者 {pid} | {tp} mask 为空，请检查 ROI 文件。")
        logger.info("  [验证✓] 预处理：mask 非空")
        _mark_done(output_root, pid, "preproc")

    # ── ② 配准 ───────────────────────────────────────────────────
    reg_dir = _patient_dir(val_dirs["registration"], pid)
    if _is_done(output_root, pid, "registration"):
        logger.info("[跳过] 配准（已完成，从磁盘加载）")
        img_t2_reg = sitk.ReadImage(
            os.path.join(reg_dir, f"p{pid}_t2_registered.nii.gz")
        )
        transform = _load_transform_from_disk(reg_dir, pid)
    else:
        try:
            reg_result = register_images(
                img_t1, img_t2, mask_t1, mask_t2, reg_dir, pid
            )
            img_t2_reg = reg_result['registered_image']
            transform  = reg_result['transform']
            qc_records.append(reg_result['qc'])

            # 验证配准后图像是否落盘
            reg_img_path = os.path.join(reg_dir, f"p{pid}_t2_registered.nii.gz")
            if not os.path.exists(reg_img_path):
                raise FileNotFoundError(f"配准后图像未生成：{reg_img_path}")
            logger.info("  [验证✓] 配准：图像文件存在")
            _mark_done(output_root, pid, "registration")

        except RuntimeError as e:
            # QC 不合格时记录警告并尝试继续（与 pipeline.py 保持一致）
            if "配准 QC 不合格" in str(e):
                logger.warning(f"患者 {pid} 配准 QC 未达标，继续提取特征（调试模式）：{e}")
                reg_img_path = os.path.join(reg_dir, f"p{pid}_t2_registered.nii.gz")
                if os.path.exists(reg_img_path):
                    img_t2_reg = sitk.ReadImage(reg_img_path)
                    transform  = _load_transform_from_disk(reg_dir, pid)
                    _mark_done(output_root, pid, "registration")
                else:
                    raise
            else:
                raise

    # ── ③ 生境划分（应用全局模型）───────────────────────────────
    habitat_dir = _patient_dir(val_dirs["habitat_masks"], pid)
    if _is_done(output_root, pid, "habitat"):
        logger.info("[跳过] 生境划分（已完成，从磁盘加载）")
        baseline_files = sorted([
            f for f in os.listdir(habitat_dir)
            if f"p{pid}_habitat" in f and "_t1_baseline" in f
        ])
        habitat_masks_t1 = [
            sitk.ReadImage(os.path.join(habitat_dir, f)) for f in baseline_files
        ]
        if len(habitat_masks_t1) < 2:
            raise ValueError(
                f"患者 {pid}：Habitat 数量 {len(habitat_masks_t1)} < 2，"
                "请删除 .done 标志文件后重新运行生境划分。"
            )
    else:
        habitat_masks_t1, optimal_k = apply_global_habitat(
            img_t1, mask_t1, global_model, habitat_dir, pid
        )
        if len(habitat_masks_t1) < 2:
            raise ValueError(
                f"患者 {pid}：生境划分结果 K={len(habitat_masks_t1)} < 2。"
            )
        logger.info(f"  [验证✓] 生境划分：{len(habitat_masks_t1)} 个 Habitat")
        _mark_done(output_root, pid, "habitat")

    # ── ④ Mask 传播（方案 A：直接复用 t1 mask）──────────────────
    if _is_done(output_root, pid, "propagation"):
        logger.info("[跳过] Mask 传播（已完成，从磁盘加载）")
        prop_files = sorted([
            f for f in os.listdir(habitat_dir)
            if f"p{pid}_habitat" in f and "_t2_propagated" in f
        ])
        habitat_masks_t2 = [
            sitk.ReadImage(os.path.join(habitat_dir, f)) for f in prop_files
        ]
    else:
        habitat_masks_t2 = propagate_masks_val(
            habitat_masks  = habitat_masks_t1,
            transform      = transform,
            fixed_image    = img_t1,
            moving_image   = img_t2,
            output_dir     = habitat_dir,
            patient_id     = pid,
        )
        _mark_done(output_root, pid, "propagation")

    # ── ⑤ 特征提取 ───────────────────────────────────────────────
    feat_dir       = _patient_dir(val_dirs["features"], pid)
    feat_pre_path  = os.path.join(feat_dir, f"p{pid}_pre_features.csv")
    feat_post_path = os.path.join(feat_dir, f"p{pid}_post_features.csv")

    if _is_done(output_root, pid, "features"):
        logger.info("[跳过] 特征提取（已完成，从磁盘加载）")
        features_t1 = pd.read_csv(feat_pre_path,  index_col='habitat')
        features_t2 = pd.read_csv(feat_post_path, index_col='habitat')
    else:
        # t1：img_t1 + habitat_masks_t1（均在 t1 空间）
        features_t1 = extract_features_all_habitats(
            img_t1, habitat_masks_t1, pid, 'pre', feat_dir
        )
        if features_t1.empty:
            raise ValueError(f"患者 {pid} t1 特征为空，请检查 Pictologics 配置。")

        # t2：img_t2_reg + habitat_masks_t2（均在 t1 空间，方案 A）
        features_t2 = extract_features_all_habitats(
            img_t2_reg, habitat_masks_t2, pid, 'post', feat_dir
        )
        if features_t2.empty:
            raise ValueError(f"患者 {pid} t2 特征为空，请检查 Pictologics 配置。")

        features_t1.to_csv(feat_pre_path)
        features_t2.to_csv(feat_post_path)
        logger.info(f"  [验证✓] 特征提取：t1 {features_t1.shape}，t2 {features_t2.shape}")
        _mark_done(output_root, pid, "features")

    # ── ⑥ Delta 特征计算 ─────────────────────────────────────────
    delta_dir  = _patient_dir(val_dirs["delta"], pid)
    delta_path = os.path.join(delta_dir, f"p{pid}_delta_features.csv")

    if _is_done(output_root, pid, "delta"):
        logger.info("[跳过] Delta 计算（已完成，从磁盘加载）")
        delta_df = pd.read_csv(delta_path, index_col='habitat')
    else:
        delta_df = compute_delta_features(
            features_t1, features_t2, pid, delta_dir, delta_t_days
        )
        if delta_df.empty:
            raise ValueError(f"患者 {pid} Delta 特征为空，请检查 pre/post 特征一致性。")
        logger.info(f"  [验证✓] Delta 计算完成：{delta_df.shape}")
        _mark_done(output_root, pid, "delta")

    delta_store[pid] = delta_df
    logger.info(f"  患者 {pid} 验证完成 ✓")


# ═══════════════════════════════════════════════════════════════════
#  Delta 矩阵对齐：验证集 → 训练集特征列
# ═══════════════════════════════════════════════════════════════════

def align_to_train_features(X_val:             pd.DataFrame,
                              selected_features: list[str],
                              scaler:            StandardScaler = None,
                              output_dir:        str = None
                              ) -> pd.DataFrame:
    """
    将验证集展平 Delta 矩阵对齐到训练集筛选后的特征列，并可选进行标准化。

    对齐逻辑
    --------
    1. 仅保留 selected_features 中在 X_val 里存在的列（交集）
    2. 训练集有但验证集无的列：填充 0.0（记录警告）
    3. 验证集有但训练集未选的列：直接丢弃
    4. 若提供 scaler：用训练集拟合的参数执行 transform（不重新拟合）

    Parameters
    ----------
    X_val             : pd.DataFrame   — 验证集展平 Delta 矩阵（患者 × 特征）
    selected_features : list[str]      — 训练集筛选后特征列表
    scaler            : StandardScaler — 训练集 Z-score Scaler（None 则跳过）
    output_dir        : str, optional  — 对齐后矩阵的保存目录

    Returns
    -------
    pd.DataFrame — 与训练集特征列完全对齐的矩阵（患者 × 选定特征）
    """
    # 计算列集合交集与差集
    val_cols   = set(X_val.columns)
    train_cols = set(selected_features)
    missing    = train_cols - val_cols          # 训练有、验证无
    extra      = val_cols   - train_cols        # 验证有、训练未选

    if missing:
        logger.warning(
            f"以下 {len(missing)} 个训练特征在验证集中缺失，填充 0.0：\n"
            + "\n".join(f"  {c}" for c in sorted(missing))
        )
        for col in missing:
            X_val[col] = 0.0

    if extra:
        logger.info(f"丢弃 {len(extra)} 个训练集未选特征（验证集多余列）。")

    # 严格按 selected_features 顺序排列列（与训练集对齐）
    X_aligned = X_val[selected_features].copy()
    X_aligned = X_aligned.fillna(0.0)

    # Z-score 标准化（使用训练集 Scaler 的参数，不重新拟合）
    if scaler is not None:
        arr_scaled = scaler.transform(X_aligned.values)
        X_aligned  = pd.DataFrame(
            arr_scaled,
            index   = X_aligned.index,
            columns = X_aligned.columns,
        )
        logger.info("验证集特征已使用训练集 Scaler 完成 Z-score 标准化。")
    else:
        logger.warning("未应用 Z-score 标准化（训练集 Scaler 未加载）。")

    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        path = os.path.join(output_dir, "val_delta_features_aligned.csv")
        X_aligned.to_csv(path)
        logger.info(f"对齐后特征矩阵已保存：{path}")

    return X_aligned


# ═══════════════════════════════════════════════════════════════════
#  分类器预测
# ═══════════════════════════════════════════════════════════════════

def predict_with_classifier(X_aligned: pd.DataFrame,
                              clf_path:  str,
                              output_dir: str) -> pd.DataFrame:
    """
    加载已训练的分类器，对验证集特征矩阵执行预测。

    支持任何 sklearn 兼容分类器（LogisticRegression、SVM、RandomForest 等），
    要求实现 predict() 和（可选）predict_proba() 方法。

    Parameters
    ----------
    X_aligned  : pd.DataFrame — 已对齐和标准化的验证集特征矩阵
    clf_path   : str          — 分类器 .joblib 文件路径
    output_dir : str          — 预测结果保存目录

    Returns
    -------
    pd.DataFrame — 预测结果（列：patient_id, pred_label, pred_prob_1）

    Raises
    ------
    FileNotFoundError — clf_path 不存在
    """
    if not os.path.exists(clf_path):
        raise FileNotFoundError(
            f"分类器文件不存在：{clf_path}\n"
            "请确认已提供 --clf 参数，或通过 --no_predict 跳过预测步骤。"
        )

    clf = joblib.load(clf_path)
    logger.info(f"分类器已加载：{clf_path}（类型：{type(clf).__name__}）")

    pred_labels = clf.predict(X_aligned.values)
    result_df   = pd.DataFrame({
        'patient_id' : X_aligned.index,
        'pred_label' : pred_labels,
    })

    # 输出预测概率（若分类器支持 predict_proba）
    if hasattr(clf, 'predict_proba'):
        proba = clf.predict_proba(X_aligned.values)
        # 取类别为 1 的概率（二分类）
        if proba.shape[1] == 2:
            result_df['pred_prob_1'] = proba[:, 1]
        else:
            # 多分类：输出各类别概率
            for i in range(proba.shape[1]):
                result_df[f'pred_prob_{i}'] = proba[:, i]
    else:
        logger.info(f"分类器 {type(clf).__name__} 不支持 predict_proba，跳过概率输出。")

    os.makedirs(output_dir, exist_ok=True)
    save_path = os.path.join(output_dir, "val_predictions.csv")
    result_df.to_csv(save_path, index=False)
    logger.info(
        f"预测结果已保存：{save_path}（{len(result_df)} 例，"
        f"正例：{int((pred_labels == 1).sum())}，"
        f"负例：{int((pred_labels == 0).sum())}）"
    )
    return result_df


# ═══════════════════════════════════════════════════════════════════
#  报告保存
# ═══════════════════════════════════════════════════════════════════

def save_val_reports(qc_records:   list,
                      success_ids:  list,
                      failed_ids:   list,
                      output_root:  str):
    """
    保存配准 QC 报告和流程摘要。

    输出文件
    --------
    val_qc_report.csv     — 各患者 Dice / Hausdorff / valid
    val_pipeline_summary.csv — 本次运行摘要
    """
    qc_dir = os.path.join(output_root, "06_qc")
    os.makedirs(qc_dir, exist_ok=True)

    if qc_records:
        pd.DataFrame(qc_records).to_csv(
            os.path.join(qc_dir, "val_qc_report.csv"), index=False
        )
        logger.info(f"配准 QC 报告已保存：{qc_dir}/val_qc_report.csv")

    summary = {
        'run_time'   : datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        'total'      : len(success_ids) + len(failed_ids),
        'success'    : len(success_ids),
        'failed'     : len(failed_ids),
        'success_ids': json.dumps(success_ids, ensure_ascii=False),
        'failed_ids' : json.dumps(failed_ids,  ensure_ascii=False),
    }
    pd.DataFrame([summary]).to_csv(
        os.path.join(qc_dir, "val_pipeline_summary.csv"), index=False
    )
    logger.info(f"流程摘要已保存：{qc_dir}/val_pipeline_summary.csv")


# ═══════════════════════════════════════════════════════════════════
#  日志初始化
# ═══════════════════════════════════════════════════════════════════

def _setup_logging(output_root: str, log_level: int = logging.INFO):
    """
    同时输出到控制台和时间戳命名的日志文件。
    日志文件路径：{output_root}/validate_{YYYYmmdd_HHMMSS}.log
    """
    os.makedirs(output_root, exist_ok=True)
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(output_root, f"validate_{ts}.log")
    logging.basicConfig(
        level    = log_level,
        format   = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt  = "%H:%M:%S",
        handlers = [
            logging.StreamHandler(),
            logging.FileHandler(log_path, encoding='utf-8'),
        ],
    )
    logger.info(f"验证日志文件：{log_path}")


# ═══════════════════════════════════════════════════════════════════
#  主验证入口
# ═══════════════════════════════════════════════════════════════════

def run_validation(data_table_path: str,
                   model_dir:       str,
                   output_root:     str,
                   clf_path:        str   = None,
                   delta_t_days:    float = None,
                   log_level:       int   = logging.INFO,
                   no_predict:      bool  = False) -> dict:
    """
    验证集完整处理流程。

    Parameters
    ----------
    data_table_path : str        — 验证集数据表 CSV/TSV 路径
    model_dir       : str        — 训练流程 OUTPUT_ROOT（含全局模型和特征列表）
    output_root     : str        — 验证结果根目录（与训练结果分开存储）
    clf_path        : str, opt   — 已训练分类器 .joblib 路径（None 则跳过预测）
    delta_t_days    : float, opt — 两次扫描间隔天数（用于 delta_rate 特征）
    log_level       : int        — 日志级别（logging.INFO / logging.DEBUG）
    no_predict      : bool       — 为 True 时跳过分类器预测步骤

    Returns
    -------
    dict — {
        'success_ids'  : list[str],
        'failed_ids'   : list[str],
        'X_raw'        : pd.DataFrame,  — 验证集原始展平 Delta 矩阵
        'X_aligned'    : pd.DataFrame,  — 对齐后特征矩阵
        'predictions'  : pd.DataFrame,  — 预测结果（no_predict=True 时为 None）
    }
    """
    _setup_logging(output_root, log_level)
    logger.info(
        f"\n{'='*60}\n"
        f"  Delta 生境影像组学 — 外部验证\n"
        f"  验证数据表：{data_table_path}\n"
        f"  训练模型目录：{model_dir}\n"
        f"  验证结果目录：{output_root}\n"
        f"{'='*60}"
    )

    # ── 1. 加载训练产物 ───────────────────────────────────────────
    logger.info("\n>>> [步骤 1] 加载训练产物...")
    artifacts = load_train_artifacts(model_dir)
    global_model      = artifacts['global_model']
    selected_features = artifacts['selected_features']
    scaler            = artifacts['scaler']

    # ── 2. 建立验证集目录结构 ─────────────────────────────────────
    val_dirs = _build_val_dirs(output_root)
    logger.info(f"验证结果目录已创建：{output_root}")

    # ── 3. 解析验证集数据表 ───────────────────────────────────────
    logger.info("\n>>> [步骤 2] 解析验证集数据表...")
    patient_pairs = load_patient_pairs(data_table_path, validate_paths=True)
    logger.info(f"共加载 {len(patient_pairs)} 对有效验证患者")

    # ── 4. 逐患者处理 ─────────────────────────────────────────────
    logger.info("\n>>> [步骤 3] 逐患者处理...")
    qc_records, delta_store = [], {}
    success_ids, failed_ids = [], []

    for pair in patient_pairs:
        pid = pair['patient_id']
        try:
            validate_single_patient(
                pair         = pair,
                global_model = global_model,
                val_dirs     = val_dirs,
                output_root  = output_root,
                qc_records   = qc_records,
                delta_store  = delta_store,
                delta_t_days = delta_t_days,
            )
            success_ids.append(pid)
        except Exception as e:
            logger.error(
                f"患者 {pid} 验证失败：{e}\n{traceback.format_exc()}"
            )
            failed_ids.append(pid)

    # ── 5. 汇总展平 Delta 矩阵 ────────────────────────────────────
    logger.info("\n>>> [步骤 4] 汇总展平 Delta 特征矩阵...")
    if not delta_store:
        logger.error("所有患者均失败，无法生成 Delta 矩阵。")
        save_val_reports(qc_records, success_ids, failed_ids, output_root)
        return {
            'success_ids': success_ids,
            'failed_ids' : failed_ids,
            'X_raw'      : None,
            'X_aligned'  : None,
            'predictions': None,
        }

    X_raw = aggregate_delta_features(delta_store)
    raw_path = os.path.join(output_root, "val_delta_features_raw.csv")
    X_raw.to_csv(raw_path)
    logger.info(
        f"验证集原始 Delta 矩阵已保存：{raw_path}\n"
        f"  形状：{X_raw.shape}（{X_raw.shape[0]} 患者 × {X_raw.shape[1]} 特征）"
    )

    # ── 6. 对齐到训练集特征列 ─────────────────────────────────────
    logger.info("\n>>> [步骤 5] 对齐特征列 + 标准化...")
    X_aligned = align_to_train_features(
        X_val             = X_raw.copy(),
        selected_features = selected_features,
        scaler            = scaler,
        output_dir        = output_root,
    )

    # ── 7. 分类器预测 ─────────────────────────────────────────────
    predictions = None
    if not no_predict:
        if clf_path:
            logger.info("\n>>> [步骤 6] 分类器预测...")
            try:
                predictions = predict_with_classifier(X_aligned, clf_path, output_root)
            except FileNotFoundError as e:
                logger.warning(f"跳过预测步骤：{e}")
        else:
            logger.info(
                "\n>>> [步骤 6] 跳过预测步骤（未提供 --clf 参数）。\n"
                "  提示：验证集特征矩阵已保存，可手动加载分类器进行预测。"
            )

    # ── 8. 保存报告 ───────────────────────────────────────────────
    save_val_reports(qc_records, success_ids, failed_ids, output_root)

    logger.info(
        f"\n{'='*60}\n"
        f"  验证完成：成功 {len(success_ids)} 例，失败 {len(failed_ids)} 例\n"
        + (f"  失败 ID：{failed_ids}\n" if failed_ids else "")
        + f"{'='*60}"
    )

    return {
        'success_ids': success_ids,
        'failed_ids' : failed_ids,
        'X_raw'      : X_raw,
        'X_aligned'  : X_aligned,
        'predictions': predictions,
    }


# ═══════════════════════════════════════════════════════════════════
#  命令行入口
# ═══════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Delta 生境影像组学模型 — 外部验证',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        '--data',
        required=True,
        help='验证集数据表路径（CSV/TSV，与训练集格式相同）'
    )
    parser.add_argument(
        '--model_dir',
        required=True,
        help='训练流程的 OUTPUT_ROOT（含 global_habitat_model.joblib 等产物）'
    )
    parser.add_argument(
        '--out',
        required=True,
        help='验证结果根目录（建议与训练结果目录分开）'
    )
    parser.add_argument(
        '--clf',
        default=None,
        help='已训练分类器路径（.joblib，sklearn 兼容）。不提供则只输出特征矩阵'
    )
    parser.add_argument(
        '--delta_t',
        type=float,
        default=None,
        help='治疗前后扫描间隔天数（用于计算 delta_rate 特征，可选）'
    )
    parser.add_argument(
        '--no_predict',
        action='store_true',
        help='提供此标志时跳过分类器预测步骤，仅输出特征矩阵'
    )
    parser.add_argument(
        '--debug',
        action='store_true',
        help='启用 DEBUG 级别日志（输出详细中间信息）'
    )
    args = parser.parse_args()

    result = run_validation(
        data_table_path = args.data,
        model_dir       = args.model_dir,
        output_root     = args.out,
        clf_path        = args.clf,
        delta_t_days    = args.delta_t,
        log_level       = logging.DEBUG if args.debug else logging.INFO,
        no_predict      = args.no_predict,
    )

    print(f"\n验证完成：成功 {len(result['success_ids'])} 例，"
          f"失败 {len(result['failed_ids'])} 例")
    if result['X_aligned'] is not None:
        print(f"对齐后特征矩阵形状：{result['X_aligned'].shape}")
    if result['predictions'] is not None:
        print(f"\n预测结果预览：\n{result['predictions'].to_string(index=False)}")


# ═══════════════════════════════════════════════════════════════════
#  FAQ：训练脚本需要额外保存什么？
# ═══════════════════════════════════════════════════════════════════
"""
FAQ
===

Q1：pipeline.py 默认不保存 feature_scaler.joblib，如何生成？
--------------------------------------------------------------
在 pipeline.py 的 run_cohort_aggregation 函数中，StandardScaler 拟合后
仅用于转换，未序列化。需在完成 fit_transform 后添加一行保存：

    scaler = StandardScaler()
    X_tr_s = pd.DataFrame(scaler.fit_transform(X_train.fillna(0)), ...)

    # ← 在此行之后添加：
    joblib.dump(scaler, os.path.join(DIRS["selected"], "feature_scaler.joblib"))

Q2：分类器如何保存？
--------------------
在训练脚本中拟合分类器后，用 joblib.dump 序列化：

    from sklearn.linear_model import LogisticRegression
    import joblib

    clf = LogisticRegression(max_iter=1000, C=1.0)
    clf.fit(X_tr_sel, y_train)
    joblib.dump(clf, os.path.join(DIRS["selected"], "classifier.joblib"))

Q3：验证集的数据表格式与训练集相同吗？
---------------------------------------
完全相同。CSV 或 TSV，至少含 image_path 和 roi_path 两列，路径结构需
包含 'new' 或 'old' 关键字以支持患者 ID 自动解析（见 data_loader.py）。

Q4：某个验证患者失败后会中断全队列吗？
---------------------------------------
不会。单个患者失败会记录到 failed_ids 列表并继续处理下一例，
与 pipeline.py 行为一致。

Q5：如何强制重新运行某患者的某一阶段？
---------------------------------------
删除对应的 .done 文件即可：

    del {output_root}\\checkpoints\\p{patient_id}_registration.done   # Windows
    rm  {output_root}/checkpoints/p{patient_id}_registration.done    # Linux
"""
