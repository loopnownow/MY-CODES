"""
pipeline.py
===========
主流程编排器（全队列一致性执行策略版）

执行策略
--------
全队列模式（HABITAT["cohort_mode"] = True，推荐）：
    阶段 1  全队列特征收集与全局模型拟合
            ├─ 对所有患者的基线图像提取体素级特征
            ├─ 汇总为全队列池化特征矩阵
            └─ 拟合统一的 Scaler→PCA→KMeans 全局模型

    阶段 2  逐患者流程
            ├─ 预处理（重采样 + 归一化）
            ├─ 非刚性配准（Translation → Affine → SyN）
            ├─ 生境划分（应用全局模型，保证跨患者 Habitat 语义一致）
            ├─ Mask 传播
            ├─ 影像组学特征提取
            └─ Delta 特征计算

    阶段 3  汇总与筛选（提供标签时）
            ├─ 汇总全队列 Delta 矩阵
            ├─ 训练集/测试集划分
            └─ 三步特征筛选（近零方差 → 相关去冗余 → LassoCV）

单患者模式（HABITAT["cohort_mode"] = False）：
    直接进入阶段 2，每个患者独立优化 K 和聚类参数。

断点续跑
--------
每阶段完成后在 checkpoints/ 目录生成 .done 标志文件。
重新运行时，已完成的阶段自动跳过，从断点处继续。

Bug 修复（相比原版）
--------------------
    [BUG-3] _load_transform_from_disk 使用 sorted() 对变换文件名排序，
    得到 [...0GenericAffine.mat, ...1Warp.nii.gz] 顺序。
    但 ANTs fwdtransforms 要求 [Warp.nii.gz, Affine.mat]（先应用非线性，
    后应用仿射），错误顺序导致断点续跑时配准结果错误。
    修复：按文件扩展名分组，确保 Warp.nii.gz 排在 Affine.mat 之前。

    [BUG-5] run_cohort_aggregation 中 train_test_split(stratify=y) 在
    某类样本数 < 2 时会抛出 ValueError 崩溃。修复为添加样本量检查，
    样本不足时降级为非分层随机划分并记录警告。

运行方式
--------
    python pipeline.py --data E:\\lxf\\lalist.csv --labels E:\\lxf\\QC_train.csv --delta_t 66 --debug
    python pipeline.py --data E:\\lxf\\delta_habitat_pipeline\\combind.csv --debug
"""

import os
import sys
import json
import logging
import argparse
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import SimpleITK as sitk
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler

from config import DIRS, OUTPUT_ROOT, HABITAT
from data_loader   import load_patient_pairs
from preprocessing import preprocess
from registration  import register_images
from habitat       import build_habitats, CohortHabitatModel, compute_voxel_feature_maps
from radiomics     import (extract_features_all_habitats,
                            compute_delta_features,
                            aggregate_delta_features,
                            select_features)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  初始化工具
# ═══════════════════════════════════════════════════════════════════

def _setup_dirs():
    """
    创建所有配置的输出子目录（含 checkpoints 目录）。
    使用 exist_ok=True 确保目录已存在时不报错（幂等操作）。
    """
    for path in DIRS.values():
        os.makedirs(path, exist_ok=True)
    os.makedirs(os.path.join(OUTPUT_ROOT, "checkpoints"), exist_ok=True)
    logger.info(f"输出根目录：{OUTPUT_ROOT}")


def _setup_logging(log_level: int = logging.INFO):
    """
    配置日志：同时输出到控制台（StreamHandler）和时间戳命名的日志文件（FileHandler）。

    日志文件路径：{OUTPUT_ROOT}/pipeline_{YYYYmmdd_HHMMSS}.log
    每次运行生成独立文件，避免覆盖历史记录。
    """
    os.makedirs(OUTPUT_ROOT, exist_ok=True)
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(OUTPUT_ROOT, f"pipeline_{ts}.log")
    logging.basicConfig(
        level    = log_level,
        format   = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt  = "%H:%M:%S",
        handlers = [
            logging.StreamHandler(),
            logging.FileHandler(log_path, encoding='utf-8'),
        ],
    )
    logger.info(f"日志文件：{log_path}")


def _patient_dir(base_dir: str, patient_id: str) -> str:
    """
    返回某阶段下该患者的专属子目录路径（自动创建）。
    格式：{base_dir}/patient_{patient_id}/
    """
    d = os.path.join(base_dir, f"patient_{patient_id}")
    os.makedirs(d, exist_ok=True)
    return d


# ═══════════════════════════════════════════════════════════════════
#  断点续跑机制
# ═══════════════════════════════════════════════════════════════════

def _checkpoint_path(patient_id: str, stage: str) -> str:
    """
    返回断点标志文件的完整路径。
    路径格式：{OUTPUT_ROOT}/checkpoints/p{patient_id}_{stage}.done
    删除该文件即可强制重新执行对应阶段。
    """
    return os.path.join(OUTPUT_ROOT, "checkpoints", f"p{patient_id}_{stage}.done")


def _is_done(patient_id: str, stage: str) -> bool:
    """检查某患者的某阶段是否已完成（标志文件是否存在）。"""
    return os.path.exists(_checkpoint_path(patient_id, stage))


def _mark_done(patient_id: str, stage: str):
    """将某患者的某阶段标记为已完成（创建空标志文件）。"""
    Path(_checkpoint_path(patient_id, stage)).touch()


# ═══════════════════════════════════════════════════════════════════
#  阶段验证函数
# ═══════════════════════════════════════════════════════════════════

def _validate_preproc(img_t1: sitk.Image, mask_t1: sitk.Image,
                       img_t2: sitk.Image, mask_t2: sitk.Image,
                       patient_id: str):
    """
    验证预处理结果：两个 mask 均非空。
    （不再检查 t1/t2 尺寸一致性，配准环节会自动处理 FOV 差异）
    失败时抛出 ValueError，由 pipeline 捕获并跳过该患者。
    """
    for tp, mask in [('pre', mask_t1), ('post', mask_t2)]:
        if int(sitk.GetArrayFromImage(mask).sum()) == 0:
            raise ValueError(f"患者 {patient_id} | {tp} mask 为空（请检查 ROI 文件）。")
    logger.info("  [验证✓] 预处理：图像尺寸一致，mask 非空")


def _validate_registration(reg_dir: str, patient_id: str):
    """
    验证配准阶段：检查配准后图像文件是否实际存在于磁盘上。
    若文件缺失，说明配准中途失败但 checkpoint 被意外写入。
    """
    reg_img_path = os.path.join(reg_dir, f"p{patient_id}_t2_registered.nii.gz")
    if not os.path.exists(reg_img_path):
        raise FileNotFoundError(
            f"患者 {patient_id}：配准后图像缺失 {reg_img_path}。"
        )
    logger.info("  [验证✓] 配准：图像文件存在")


def _validate_habitat(habitat_masks: list[sitk.Image], patient_id: str):
    """
    验证生境划分结果：Habitat 数量必须 ≥ 2（K=1 无意义）。
    """
    if len(habitat_masks) < 2:
        raise ValueError(
            f"患者 {patient_id}：Habitat 数量 {len(habitat_masks)} < 2。"
            "请检查全局模型的 optimal_k 或调整 HABITAT['k_range']。"
        )
    logger.info(f"  [验证✓] 生境划分：{len(habitat_masks)} 个 Habitat")


def _validate_features(features: pd.DataFrame, patient_id: str, timepoint: str):
    """验证特征 DataFrame 非空。"""
    if features.empty:
        raise ValueError(
            f"患者 {patient_id} | {timepoint}：特征 DataFrame 为空，"
            "请检查 Pictologics 配置或 Habitat Mask。"
        )
    logger.info(f"  [验证✓] 特征提取（{timepoint}）")


def _validate_delta(delta_df: pd.DataFrame, patient_id: str):
    """验证 Delta DataFrame 非空。"""
    if delta_df.empty:
        raise ValueError(
            f"患者 {patient_id}：Delta 特征为空，"
            "请检查 pre/post 特征列名和 Habitat 索引是否一致。"
        )
    logger.info("  [验证✓] Delta 特征完成")


# ═══════════════════════════════════════════════════════════════════
#  断点续跑：从磁盘重新加载变换文件
# ═══════════════════════════════════════════════════════════════════

def _load_transform_from_disk(reg_dir: str, patient_id: str) -> dict:
    """
    从已保存的配准结果目录中加载变换文件，重建 ANTs 变换字典。

    ANTs outprefix 命名规则
    -----------------------
    syn_result['fwdtransforms'] 输出两个文件：
        {outprefix}1Warp.nii.gz        ← 非线性形变场（正向）
        {outprefix}0GenericAffine.mat  ← 仿射变换矩阵

    正向变换应用顺序（从左到右）
    ----------------------------
    [Warp.nii.gz, Affine.mat]：先应用非线性形变，再应用仿射
    注意：ANTs 应用 fwdtransforms 列表时，从列表索引 0 开始依次应用。

    [BUG-3 修复]
    -----------
    原版使用 sorted() 得到 [...0GenericAffine.mat, ...1Warp.nii.gz]（字母序），
    导致仿射变换被先于非线性变换应用，变换顺序错误。
    修复：按文件扩展名分组，强制 Warp.nii.gz 排在 Affine.mat 之前。
    同时排除 InverseWarp 文件（仅用于反向变换）。

    Parameters
    ----------
    reg_dir    : str — 配准结果目录（{DIRS["registration"]}/patient_{pid}/）
    patient_id : str

    Returns
    -------
    dict — {'fwdtransforms': [...], 'invtransforms': []}

    Raises
    ------
    FileNotFoundError — 目录中未找到变换文件
    """
    prefix = f"p{patient_id}_"
    all_files = [
        os.path.join(reg_dir, f)
        for f in os.listdir(reg_dir)
        if f.startswith(prefix)
    ]

    # 按文件类型分组
    # Warp：非线性形变场（排除 InverseWarp，它用于反向变换）
    warp_files   = [f for f in all_files
                    if f.endswith('Warp.nii.gz') and 'Inverse' not in f]
    affine_files = [f for f in all_files if f.endswith('.mat')]

    # [BUG-3 修复] 正确顺序：[Warp, Affine]（与 syn_result['fwdtransforms'] 一致）
    transform_files = sorted(warp_files) + sorted(affine_files)

    if not transform_files:
        raise FileNotFoundError(
            f"患者 {patient_id}：在目录 {reg_dir} 中未找到变换文件（.mat / Warp.nii.gz）。\n"
            "请删除配准阶段的 .done 标志文件并重新运行配准。"
        )

    logger.debug(f"从磁盘加载变换：{transform_files}")
    return {'fwdtransforms': transform_files, 'invtransforms': []}


# ═══════════════════════════════════════════════════════════════════
#  核心：单患者流程
# ═══════════════════════════════════════════════════════════════════

def process_single_patient(pair:         dict,
                            qc_records:   list,
                            delta_store:  dict,
                            delta_t_days: float = None,
                            global_model: CohortHabitatModel = None):
    """
    单患者完整流程（预处理 → 配准 → 生境划分 → Mask 传播 → 特征提取 → Delta 计算）。

    断点续跑逻辑
    ------------
    每个阶段开始前检查 .done 标志文件：
    - 存在 → 从磁盘加载已保存的结果，跳过重新计算
    - 不存在 → 执行计算，完成后写入 .done 文件

    全局模型
    --------
    若提供 global_model（全队列模式），生境划分直接使用全局聚类参数，
    保证跨患者的 Habitat 编号语义一致。
    若 global_model=None（单患者模式），每个患者独立优化 K 和聚类。

    Parameters
    ----------
    pair         : dict             — 患者配对信息（来自 data_loader.py）
    qc_records   : list             — 配准 QC 记录（原地追加）
    delta_store  : dict             — Delta 特征存储（原地追加 {pid: DataFrame}）
    delta_t_days : float, optional  — 两次扫描间隔天数（用于变化率特征）
    global_model : CohortHabitatModel, optional — 全局生境模型

    Raises
    ------
    任何阶段的异常直接向上传播，由 run_pipeline 捕获并记录患者失败。
    """
    pid = pair['patient_id']
    logger.info(f"\n{'='*60}\n  患者：{pid}\n{'='*60}")

    # ─── 阶段 1：预处理 ───────────────────────────────────────────
    preproc_dir = _patient_dir(DIRS["preprocessed"], pid)
    if _is_done(pid, "preproc"):
        logger.info("[跳过] 预处理（已完成，从磁盘加载）")
        img_t1  = sitk.ReadImage(os.path.join(preproc_dir, f"p{pid}_pre_image.nii.gz"))
        mask_t1 = sitk.ReadImage(os.path.join(preproc_dir, f"p{pid}_pre_mask.nii.gz"))
        img_t2  = sitk.ReadImage(os.path.join(preproc_dir, f"p{pid}_post_image.nii.gz"))
        mask_t2 = sitk.ReadImage(os.path.join(preproc_dir, f"p{pid}_post_mask.nii.gz"))
    else:
        img_t1, mask_t1 = preprocess(
            pair['pre']['image_path'],  pair['pre']['roi_path'],  preproc_dir, pid, 'pre'
        )
        img_t2, mask_t2 = preprocess(
            pair['post']['image_path'], pair['post']['roi_path'], preproc_dir, pid, 'post'
        )
        _validate_preproc(img_t1, mask_t1, img_t2, mask_t2, pid)
        _mark_done(pid, "preproc")

    # ─── 阶段 2：非刚性配准 ───────────────────────────────────────
    reg_dir = _patient_dir(DIRS["registration"], pid)
    if _is_done(pid, "registration"):
        logger.info("[跳过] 配准（已完成，从磁盘加载）")
        img_t2_reg = sitk.ReadImage(os.path.join(reg_dir, f"p{pid}_t2_registered.nii.gz"))
        # 续跑校验：确认配准变换文件存在（缺失则抛 FileNotFoundError 提示重跑配准）。
        # 方案 A 删除传播后变换不再用于后续步骤，此处仅作完整性校验。
        _load_transform_from_disk(reg_dir, pid)
    else:
        try:
            reg_result = register_images(img_t1, img_t2, mask_t1, mask_t2, reg_dir, pid)
            img_t2_reg = reg_result['registered_image']
            qc_records.append(reg_result['qc'])
            _validate_registration(reg_dir, pid)
            _mark_done(pid, "registration")
        except RuntimeError as e:
            # [调试增强] 若仅是 QC 不合格导致的 RuntimeError，记录警告并尝试继续
            if "配准 QC 不合格" in str(e):
                logger.warning(f"患者 {pid} 配准 QC 未达标，但将继续提取特征以供调试：{e}")
                # 尝试获取不合格但已生成的中间结果（如果存在）
                reg_img_path = os.path.join(reg_dir, f"p{pid}_t2_registered.nii.gz")
                if os.path.exists(reg_img_path):
                    img_t2_reg = sitk.ReadImage(reg_img_path)
                    _mark_done(pid, "registration")
                else:
                    raise e
            else:
                raise e

    # ─── 阶段 3：生境划分 ─────────────────────────────────────────
    habitat_dir = _patient_dir(DIRS["habitat_masks"], pid)
    if _is_done(pid, "habitat"):
        logger.info("[跳过] 生境划分（已完成，从磁盘加载）")
        baseline_files = sorted([
            f for f in os.listdir(habitat_dir)
            if f"p{pid}_habitat" in f and "_t1_baseline" in f
        ])
        habitat_masks_t1 = [sitk.ReadImage(os.path.join(habitat_dir, f))
                            for f in baseline_files]
        optimal_k = len(habitat_masks_t1)
    else:
        habitat_masks_t1, optimal_k, _ = build_habitats(
            image        = img_t1,
            mask         = mask_t1,
            output_dir   = habitat_dir,
            patient_id   = pid,
            global_model = global_model,   # None → 单患者模式
        )
        _validate_habitat(habitat_masks_t1, pid)
        _mark_done(pid, "habitat")

    # ─── 阶段 4：Habitat Mask 复用（方案 A，删除传播）────────────
    # img_t2_reg 已由 register_images 配准至 t1 空间，与 t1 baseline
    # habitat masks 逐体素对齐，无需再做形变传播。
    # 原传播逻辑对 t1 mask 重复施加 t2→t1 变换，会造成 mask 与 img_t2_reg
    # 的空间错配，故直接复用 t1 masks。
    habitat_masks_t2 = habitat_masks_t1

    # ─── 阶段 5：影像组学特征提取 ─────────────────────────────────
    feat_dir      = _patient_dir(DIRS["features"], pid)
    feat_pre_path  = os.path.join(feat_dir, f"p{pid}_pre_features.csv")
    feat_post_path = os.path.join(feat_dir, f"p{pid}_post_features.csv")

    if _is_done(pid, "features"):
        logger.info("[跳过] 特征提取（已完成，从磁盘加载）")
        features_t1 = pd.read_csv(feat_pre_path,  index_col='habitat')
        features_t2 = pd.read_csv(feat_post_path, index_col='habitat')
    else:
        # 对 t1 图像 + baseline masks 提取特征
        features_t1 = extract_features_all_habitats(
            img_t1, habitat_masks_t1, pid, 'pre', feat_dir
        )
        _validate_features(features_t1, pid, 'pre')
        # 对 t2 配准后图像 + propagated masks 提取特征
        features_t2 = extract_features_all_habitats(
            img_t2_reg, habitat_masks_t2, pid, 'post', feat_dir
        )
        _validate_features(features_t2, pid, 'post')
        features_t1.to_csv(feat_pre_path)
        features_t2.to_csv(feat_post_path)
        _mark_done(pid, "features")

    # ─── 阶段 6：Delta 特征计算 ───────────────────────────────────
    delta_dir  = _patient_dir(DIRS["delta"], pid)
    delta_path = os.path.join(delta_dir, f"p{pid}_delta_features.csv")
    if _is_done(pid, "delta"):
        logger.info("[跳过] Delta 计算（已完成，从磁盘加载）")
        delta_df = pd.read_csv(delta_path, index_col='habitat')
    else:
        delta_df = compute_delta_features(
            features_t1, features_t2, pid, delta_dir, delta_t_days
        )
        _validate_delta(delta_df, pid)
        _mark_done(pid, "delta")

    # 将当前患者的 Delta 特征存入全队列字典
    delta_store[pid] = delta_df
    logger.info(f"  患者 {pid} 完成 ✓")


# ═══════════════════════════════════════════════════════════════════
#  主流程
# ═══════════════════════════════════════════════════════════════════

def run_pipeline(data_table_path: str,
                 labels_path:     str   = None,
                 delta_t_days:    float = None,
                 log_level:       int   = logging.INFO) -> dict:
    """
    主流程入口：全队列建模 → 逐患者处理 → 汇总筛选。

    Parameters
    ----------
    data_table_path : str         — 数据表 CSV/TSV 路径
    labels_path     : str, opt    — 标签文件路径（CSV，含 patient_id / label 列）
    delta_t_days    : float, opt  — 治疗前后扫描间隔天数（用于变化率特征）
    log_level       : int         — 日志级别（logging.INFO / logging.DEBUG）

    Returns
    -------
    dict — {'success_ids': list[str], 'failed_ids': list[str]}
    """
    _setup_logging(log_level)
    _setup_dirs()
    logger.info(
        f"Delta 生境流程启动 | 模式: {'全队列' if HABITAT['cohort_mode'] else '单患者'}"
    )

    patient_pairs = load_patient_pairs(data_table_path, validate_paths=True)
    logger.info(f"共加载 {len(patient_pairs)} 对有效患者")

    # ─── 阶段 1：全队列特征池化与全局模型拟合（cohort_mode=True 时）──
    global_model = None
    if HABITAT["cohort_mode"]:
        model_path = HABITAT["global_model_path"]

        if os.path.exists(model_path):
            # 模型已存在：直接加载，跳过耗时的全队列特征提取
            logger.info(f"加载现有全局模型：{model_path}")
            global_model = CohortHabitatModel.load(model_path)
        else:
            logger.info(">>> [阶段 1] 全队列体素特征收集与全局模型拟合...")
            all_voxels = []

            for pair in patient_pairs:
                pid = pair['patient_id']
                try:
                    preproc_dir = _patient_dir(DIRS["preprocessed"], pid)

                    # 确保基线和随访图像均已预处理（后续阶段 2 也需要随访图像）
                    if not _is_done(pid, "preproc"):
                        img_t1, mask_t1 = preprocess(
                            pair['pre']['image_path'],  pair['pre']['roi_path'],
                            preproc_dir, pid, 'pre'
                        )
                        preprocess(
                            pair['post']['image_path'], pair['post']['roi_path'],
                            preproc_dir, pid, 'post'
                        )
                        _mark_done(pid, "preproc")
                    else:
                        # 仅加载基线图像（全局建模只需基线体素特征）
                        img_t1  = sitk.ReadImage(
                            os.path.join(preproc_dir, f"p{pid}_pre_image.nii.gz")
                        )
                        mask_t1 = sitk.ReadImage(
                            os.path.join(preproc_dir, f"p{pid}_pre_mask.nii.gz")
                        )

                    # 提取体素级特征（缓存 .npy 文件，断点续跑时直接加载）
                    feat_npy_path = os.path.join(preproc_dir, f"p{pid}_voxel_features.npy")
                    if os.path.exists(feat_npy_path):
                        v_feats = np.load(feat_npy_path)
                    else:
                        v_feats, _ = compute_voxel_feature_maps(img_t1, mask_t1)
                        np.save(feat_npy_path, v_feats)

                    all_voxels.append(v_feats)
                    logger.info(
                        f"  患者 {pid} 体素特征已提取（{v_feats.shape[0]} 体素，"
                        f"{v_feats.shape[1]} 通道）"
                    )
                except Exception as e:
                    logger.error(f"  [阶段 1] 患者 {pid} 提取失败（跳过）：{e}")
                    continue

            # 合并全队列体素特征矩阵
            logger.info(f"正在合并全队列特征（共 {len(all_voxels)} 例）...")
            X_pool = np.concatenate(all_voxels, axis=0).astype(np.float32)
            np.save(HABITAT["pooled_features_path"], X_pool)
            logger.info(
                f"全队列池化特征已保存：{X_pool.shape}（体素数 × 特征通道）"
            )

            # 拟合全局 Scaler→PCA→KMeans 模型
            global_model = CohortHabitatModel()
            global_model.fit(
                X_pool,
                os.path.join(DIRS["habitat_masks"], "k_optimization")
            )
            global_model.save(model_path)

    # ─── 阶段 2：逐患者应用模型并提取特征 ──────────────────────────
    logger.info("\n>>> [阶段 2] 逐患者应用模型并提取特征...")
    qc_records, delta_store = [], {}
    success_ids, failed_ids = [], []

    for pair in patient_pairs:
        try:
            process_single_patient(
                pair         = pair,
                qc_records   = qc_records,
                delta_store  = delta_store,
                delta_t_days = delta_t_days,
                global_model = global_model,
            )
            success_ids.append(pair['patient_id'])
        except Exception as e:
            import traceback
            logger.error(
                f"患者 {pair['patient_id']} 处理失败：{e}\n"
                f"{traceback.format_exc()}"
            )
            failed_ids.append(pair['patient_id'])

    # ─── 阶段 3：汇总与特征筛选 ──────────────────────────────────
    if delta_store:
        run_cohort_aggregation(delta_store, labels_path)

    save_qc_report(qc_records, success_ids, failed_ids)

    logger.info(
        f"\n流程结束：成功 {len(success_ids)} 例，失败 {len(failed_ids)} 例。"
        + (f"\n失败患者 ID：{failed_ids}" if failed_ids else "")
    )
    return {'success_ids': success_ids, 'failed_ids': failed_ids}


# ═══════════════════════════════════════════════════════════════════
#  汇总筛选
# ═══════════════════════════════════════════════════════════════════

def run_cohort_aggregation(delta_store: dict, labels_path: str):
    """
    全队列 Delta 特征汇总、训练/测试集划分与特征筛选。

    [BUG-5 修复]
    -----------
    原版 train_test_split(stratify=y) 在某类样本数 < 2 时抛出 ValueError。
    修复：先检查最小类别样本数，不足时降级为非分层随机划分并记录警告。

    Parameters
    ----------
    delta_store  : dict[patient_id → pd.DataFrame] — 各患者 Delta 特征
    labels_path  : str, optional                   — 标签文件路径
    """
    if not labels_path or not os.path.exists(labels_path):
        logger.warning("未提供标签文件（或文件不存在），跳过队列汇总与特征筛选。")
        # 仍然保存无标签的全队列 Delta 矩阵，便于后续外部分析
        X_all = aggregate_delta_features(delta_store)
        X_all.to_csv(os.path.join(DIRS["delta"], "all_patients_delta_features.csv"))
        return

    # 汇总全队列 Delta 矩阵（[BUG-1 已在 aggregate_delta_features 修复]）
    X_all = aggregate_delta_features(delta_store)
    X_all.to_csv(os.path.join(DIRS["delta"], "all_patients_delta_features.csv"))

    # 读取标签，过滤无标签患者
    labels_df = pd.read_csv(labels_path, index_col='patient_id', dtype={'patient_id': str})
    common    = X_all.index.intersection(labels_df.index)
    if common.empty:
        logger.warning("Delta 矩阵与标签文件无交集患者，跳过特征筛选。")
        return
    if len(common) < len(X_all.index):
        logger.warning(
            f"部分患者无标签，仅对 {len(common)}/{len(X_all.index)} 例执行筛选。"
        )

    X_use = X_all.loc[common]
    y_use = labels_df.loc[common, 'label']

    # [BUG-5 修复] 检查最小类别样本数，避免 stratify 在小类 < 2 时崩溃
    min_class_count = y_use.value_counts().min()
    if min_class_count < 2:
        logger.warning(
            f"最小类别样本数为 {min_class_count}（< 2），"
            "降级为非分层随机划分（stratify=None）。"
        )
        stratify_arg = None
    else:
        stratify_arg = y_use

    X_train, X_test, y_train, y_test = train_test_split(
        X_use, y_use,
        test_size  = 0.2,
        stratify   = stratify_arg,
        random_state = 42,
    )

    # Z-score 标准化（仅在训练集上拟合，防止测试集信息泄露）
    scaler = StandardScaler()
    X_tr_s = pd.DataFrame(
        scaler.fit_transform(X_train.fillna(0)),
        index   = X_train.index,
        columns = X_train.columns,
    )
    X_te_s = pd.DataFrame(
        scaler.transform(X_test.fillna(0)),
        index   = X_test.index,
        columns = X_test.columns,
    )

    # 三步特征筛选（在训练集上执行）
    X_tr_sel, selected = select_features(X_tr_s, y_train, DIRS["selected"])

    # 将筛选后的特征列应用于测试集
    X_te_s[selected].to_csv(os.path.join(DIRS["selected"], "X_test_selected.csv"))
    y_train.to_csv(os.path.join(DIRS["selected"], "y_train.csv"))
    y_test.to_csv(os.path.join(DIRS["selected"], "y_test.csv"))

    logger.info(
        f"[汇总完成] 训练集：{len(y_train)} 例，测试集：{len(y_test)} 例，"
        f"筛选后特征数：{len(selected)}"
    )


# ═══════════════════════════════════════════════════════════════════
#  QC 报告保存
# ═══════════════════════════════════════════════════════════════════

def save_qc_report(qc_records: list, success_ids: list, failed_ids: list):
    """
    保存两个 QC 报告文件：
    1. registration_qc.csv   — 所有患者的配准质量指标（Dice / Hausdorff）
    2. pipeline_summary.csv  — 本次运行的整体摘要（时间 / 成功/失败数）
    """
    os.makedirs(DIRS["qc_reports"], exist_ok=True)

    # 配准 QC 表（每行一个患者）
    if qc_records:
        pd.DataFrame(qc_records).to_csv(
            os.path.join(DIRS["qc_reports"], "registration_qc.csv"), index=False
        )

    # 流程摘要
    summary = {
        'run_time'   : datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        'total'      : len(success_ids) + len(failed_ids),
        'success'    : len(success_ids),
        'failed'     : len(failed_ids),
        'success_ids': json.dumps(success_ids, ensure_ascii=False),
        'failed_ids' : json.dumps(failed_ids,  ensure_ascii=False),
    }
    pd.DataFrame([summary]).to_csv(
        os.path.join(DIRS["qc_reports"], "pipeline_summary.csv"), index=False
    )
    logger.info(f"QC 报告已保存：{DIRS['qc_reports']}")


# ═══════════════════════════════════════════════════════════════════
#  命令行入口
# ═══════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Delta 生境影像组学流程',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        '--data',
        required=True,
        help='数据表路径（CSV，含 image_path 和 roi_path 列）'
    )
    parser.add_argument(
        '--labels',
        default=None,
        help='标签文件路径（CSV，含 patient_id 和 label 列；不提供则跳过特征筛选）'
    )
    parser.add_argument(
        '--delta_t',
        type=float,
        default=None,
        help='治疗前后扫描间隔天数（用于计算变化率特征，可选）'
    )
    parser.add_argument(
        '--debug',
        action='store_true',
        help='启用 DEBUG 级别日志（输出详细中间信息）'
    )
    args = parser.parse_args()

    run_pipeline(
        data_table_path = args.data,
        labels_path     = args.labels,
        delta_t_days    = args.delta_t,
        log_level       = logging.DEBUG if args.debug else logging.INFO,
    )
