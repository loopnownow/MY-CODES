"""
pipeline.py
===========
主流程编排器 —— 多参数融合生境影像组学流程（Multi-parametric Habitat Radiomics）

执行策略
--------
全队列模式（HABITAT["cohort_mode"] = True，推荐）：

    阶段 1  逐患者对齐（预处理 + 序列间配准）
            ├─ 对参考序列预处理（重采样 + 归一化 + ROI mask 对齐）
            ├─ 对其余序列预处理，再配准至参考序列空间
            └─ 得到该患者全部序列的"对齐图像字典"

    阶段 2  全队列特征收集与全局生境模型拟合
            ├─ 对每位患者提取多序列融合体素特征（Intensity + Parametric
            │  map + Local texture + Gradient），可选 Superpixel 预分割
            ├─ 汇总为全队列池化特征矩阵
            └─ 拟合统一的 Scaler→PCA→KMeans 全局模型

    阶段 3  逐患者生境划分 + 影像组学特征提取
            ├─ 应用全局模型生成 Habitat Mask（跨患者语义一致）
            └─ 对每个 Habitat × 每个序列提取 Pictologics 特征

    阶段 4  队列汇总与筛选（提供标签时）
            ├─ 汇总全队列特征矩阵（患者 × 特征）
            ├─ 训练集/测试集划分
            └─ 三步特征筛选（近零方差 → 相关去冗余 → LassoCV）

单患者模式（HABITAT["cohort_mode"] = False）：
    跳过阶段 2 的全局建模，每个患者在阶段 3 独立优化 K 和聚类参数。

与旧版（Delta 双时间点）的区别
--------------------------------
    - 不再有 pre/post 概念，改为"参考序列 + N 个待配准序列"
    - 不再计算 Delta（变化量）特征，直接对齐后的多序列做融合聚类 + 逐序列放射组学
    - 配准 QC 从 Dice/Hausdorff（需要两个 mask）改为 ROI 内互信息（仅需参考 mask）

断点续跑
--------
每患者每阶段完成后在 checkpoints/ 目录生成 .done 标志文件。
重新运行时，已完成的阶段自动跳过，从断点处继续。

内存策略
--------
默认 MEMORY['lazy_load_aligned']=True：
    - 阶段 1 的 aligned_store 只存磁盘路径句柄，已完成患者不 ReadImage
    - 阶段 2/3 对当前患者 load_aligned → 处理 → release_aligned + gc
    - 全局模型 / fusion_features.npy 已存在时，阶段 2 零影像 I/O
避免千例队列把全部 sitk.Image 常驻 RAM 导致 OOM。

运行方式
--------

    python pipeline.py --data E:\\mp_habitat\\patient_list.csv \
                        --labels E:\\mp_habitat\\labels.csv --debug
    python pipeline.py --data E:\\mp_habitat\\patient_list.csv
"""

import os
import gc
import json
import logging
import argparse
import traceback
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import SimpleITK as sitk
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler

from config import DIRS, OUTPUT_ROOT, HABITAT, PREPROC, MEMORY
from data_loader   import load_patient_data
from preprocessing import preprocess_sequence
from registration  import register_sequence
from habitat       import build_habitats, CohortHabitatModel, compute_voxel_feature_maps
from radiomics     import (extract_features_all_habitats,
                            aggregate_cohort_features,
                            select_features)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  初始化工具
# ═══════════════════════════════════════════════════════════════════

def _setup_dirs():
    """创建所有配置的输出子目录（含 checkpoints 目录），幂等操作。"""
    for path in DIRS.values():
        os.makedirs(path, exist_ok=True)
    os.makedirs(os.path.join(OUTPUT_ROOT, "checkpoints"), exist_ok=True)
    logger.info(f"输出根目录：{OUTPUT_ROOT}")


def _setup_logging(log_level: int = logging.INFO):
    """配置日志：控制台 + 时间戳命名的日志文件（{OUTPUT_ROOT}/pipeline_{ts}.log）。"""
    os.makedirs(OUTPUT_ROOT, exist_ok=True)
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(OUTPUT_ROOT, f"pipeline_{ts}.log")
    logging.basicConfig(
        level    = log_level,
        format   = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt  = "%H:%M:%S",
        handlers = [logging.StreamHandler(), logging.FileHandler(log_path, encoding='utf-8')],
    )
    logger.info(f"日志文件：{log_path}")


def _patient_dir(base_dir: str, patient_id: str) -> str:
    """返回某阶段下该患者的专属子目录路径（自动创建）：{base_dir}/{patient_id}/"""
    d = os.path.join(base_dir, patient_id)
    os.makedirs(d, exist_ok=True)
    return d


# ═══════════════════════════════════════════════════════════════════
#  断点续跑机制
# ═══════════════════════════════════════════════════════════════════

def _checkpoint_path(patient_id: str, stage: str) -> str:
    """{OUTPUT_ROOT}/checkpoints/{patient_id}_{stage}.done —— 删除即可强制重跑该阶段。"""
    return os.path.join(OUTPUT_ROOT, "checkpoints", f"{patient_id}_{stage}.done")


def _is_done(patient_id: str, stage: str) -> bool:
    return os.path.exists(_checkpoint_path(patient_id, stage))


def _mark_done(patient_id: str, stage: str):
    Path(_checkpoint_path(patient_id, stage)).touch()


# ═══════════════════════════════════════════════════════════════════
#  内存友好：对齐结果路径句柄 / 按需加载 / 释放
# ═══════════════════════════════════════════════════════════════════

def _aligned_paths(patient: dict) -> dict:
    """
    根据约定文件名构造该患者对齐阶段的磁盘路径句柄（不读像素）。

    Returns
    -------
    dict — {
        'sequence_paths': dict[str, str],
        'mask_path'     : str,
        'qc_records'    : list,   # 续跑时为空；新算时由调用方填入
        'lazy'          : True,
    }
    """
    pid     = patient['patient_id']
    ref_seq = patient['reference_sequence']
    preproc_dir = os.path.join(DIRS["preprocessed"], pid)
    reg_dir     = os.path.join(DIRS["registration"], pid)

    sequence_paths = {}
    for seq_name in patient['sequences']:
        if seq_name == ref_seq:
            sequence_paths[seq_name] = os.path.join(
                preproc_dir, f"{pid}_{seq_name}_image.nii.gz"
            )
        else:
            sequence_paths[seq_name] = os.path.join(
                reg_dir, f"{pid}_{seq_name}_registered.nii.gz"
            )
    mask_path = os.path.join(preproc_dir, f"{pid}_{ref_seq}_mask.nii.gz")
    return {
        'sequence_paths': sequence_paths,
        'mask_path'     : mask_path,
        'qc_records'    : [],
        'lazy'          : True,
    }


def _validate_aligned_paths(handle: dict, patient_id: str):
    """校验路径句柄中的文件均存在，缺一则抛错（避免静默失败）。"""
    missing = [p for p in handle['sequence_paths'].values() if not os.path.exists(p)]
    if not os.path.exists(handle['mask_path']):
        missing.append(handle['mask_path'])
    if missing:
        raise FileNotFoundError(
            f"患者 {patient_id}：对齐产物缺失（checkpoint 存在但文件不在）：\n"
            + "\n".join(f"  {m}" for m in missing)
        )


def load_aligned(handle: dict) -> dict:
    """
    将路径句柄（或已含 Image 的旧式 dict）解析为可用的对齐结果。

    - lazy 句柄：从磁盘 ReadImage
    - 非 lazy（MEMORY['lazy_load_aligned']=False 时的旧行为）：原样返回
    """
    if not handle.get('lazy', False):
        return handle

    sequence_images = {
        seq: sitk.ReadImage(path)
        for seq, path in handle['sequence_paths'].items()
    }
    mask = sitk.ReadImage(handle['mask_path'])
    return {
        'sequence_images': sequence_images,
        'mask'           : mask,
        'qc_records'     : handle.get('qc_records', []),
    }


def release_aligned(aligned: dict = None):
    """释放单患者对齐影像占用的内存（SimpleITK + numpy 大块需显式 gc）。"""
    if aligned is None:
        return
    if 'sequence_images' in aligned:
        aligned['sequence_images'].clear()
    aligned.pop('mask', None)
    aligned.pop('sequence_images', None)
    gc.collect()


def _maybe_gc(counter: int) -> int:
    """按 MEMORY['gc_every_n'] 周期性强制回收；返回更新后的计数器。"""
    n = max(1, int(MEMORY.get('gc_every_n', 1)))
    counter += 1
    if counter >= n:
        gc.collect()
        return 0
    return counter


# ═══════════════════════════════════════════════════════════════════
#  阶段验证函数
# ═══════════════════════════════════════════════════════════════════

def _validate_aligned(sequence_images: dict, mask: sitk.Image, patient_id: str):
    """验证对齐阶段：mask 非空，且所有序列图像尺寸与 mask 一致。"""
    n_voxels = int(sitk.GetArrayFromImage(mask).sum())
    if n_voxels == 0:
        raise ValueError(f"患者 {patient_id}：ROI mask 为空（请检查 ROI 文件）。")
    for seq, img in sequence_images.items():
        if img.GetSize() != mask.GetSize():
            raise ValueError(
                f"患者 {patient_id} | {seq}：图像尺寸 {img.GetSize()} 与 "
                f"mask 尺寸 {mask.GetSize()} 不一致，配准/预处理可能失败。"
            )
    logger.info(f"  [验证✓] 对齐阶段：{len(sequence_images)} 个序列，mask 体素数={n_voxels}")


def _validate_habitat(habitat_masks: list, patient_id: str):
    """
    验证生境划分结果。
    - 统计非空 Habitat 数量（体素数 > 0）。
    - 硬性门槛读自 HABITAT['min_nonempty_habitats']（默认 2）；
      当 mask 总数==1 且 allow_k1=True 时门槛自动降为 1。
    - 若仍不足，仅 WARNING 并继续（不中断程序）。
    """
    n_total = len(habitat_masks)
    n_nonempty = 0
    for m in habitat_masks:
        arr = sitk.GetArrayFromImage(m)
        if int(arr.sum()) > 0:
            n_nonempty += 1

    min_req = HABITAT.get("min_nonempty_habitats", 2)
    if n_total == 1 and HABITAT.get("allow_k1", True):
        min_req = 1

    if n_nonempty < min_req:
        logger.warning(
            f"  [警告] 患者 {patient_id}：非空 Habitat 数 {n_nonempty} < {min_req} "
            f"（总 mask 数={n_total}）。已继续运行，特征中对应行将为 NaN。"
        )
    else:
        logger.info(
            f"  [验证✓] 生境划分：{n_total} 个 Habitat（非空 {n_nonempty}）"
        )


def _validate_features(features: pd.DataFrame, patient_id: str):
    """验证特征 DataFrame 非空。"""
    if features.empty:
        raise ValueError(f"患者 {patient_id}：特征 DataFrame 为空，请检查配置或 Habitat Mask。")
    logger.info("  [验证✓] 特征提取")


# ═══════════════════════════════════════════════════════════════════
#  阶段 A：单患者预处理 + 序列间配准（得到对齐后的序列图像字典）
# ═══════════════════════════════════════════════════════════════════

def align_patient_sequences(patient: dict) -> dict:
    """
    对单个患者执行"预处理 → 配准"。

    默认（MEMORY['lazy_load_aligned']=True）返回**路径句柄**，不把
    sitk.Image 常驻内存；调用方在阶段 2/3 通过 load_aligned() 按需读取。

    断点续跑：以 "aligned" 的 .done 为准；已完成时只校验路径存在，
    **不再 ReadImage**（这是此前千例 OOM 的根因）。

    Returns
    -------
    dict — lazy 模式：
        {'sequence_paths', 'mask_path', 'qc_records', 'lazy': True}
           非 lazy 模式（调试回退）：
        {'sequence_images', 'mask', 'qc_records', 'lazy': False}
    """
    pid     = patient['patient_id']
    ref_seq = patient['reference_sequence']
    lazy    = bool(MEMORY.get('lazy_load_aligned', True))

    preproc_dir = _patient_dir(DIRS["preprocessed"], pid)
    reg_dir     = _patient_dir(DIRS["registration"], pid)

    # ── 已完成：只记路径，不读像素 ────────────────────────────────
    if _is_done(pid, "aligned"):
        handle = _aligned_paths(patient)
        _validate_aligned_paths(handle, pid)
        if lazy:
            logger.info(f"[跳过] 患者 {pid}：对齐阶段（已完成，仅记录路径）")
            return handle
        # 调试回退：立即读入内存
        logger.info(f"[跳过] 患者 {pid}：对齐阶段（已完成，从磁盘加载）")
        loaded = load_aligned(handle)
        loaded['lazy'] = False
        return loaded

    # ── 步骤 1：预处理参考序列（含 ROI mask 对齐）──────────────────
    ref_info = patient['sequences'][ref_seq]
    ref_image, mask = preprocess_sequence(
        image_path = ref_info['image_path'],
        output_dir = preproc_dir,
        patient_id = pid,
        sequence   = ref_seq,
        modality   = ref_info['modality'],
        roi_path   = patient['roi_path'],
    )

    sequence_images = {ref_seq: ref_image}
    qc_records = []

    # ── 步骤 2：预处理 + 配准其余序列 ────────────────────────────
    for seq_name, info in patient['sequences'].items():
        if seq_name == ref_seq:
            continue

        moving_image, _ = preprocess_sequence(
            image_path = info['image_path'],
            output_dir = preproc_dir,
            patient_id = pid,
            sequence   = seq_name,
            modality   = info['modality'],
            roi_path   = None,
        )

        reg_result = register_sequence(
            moving_image = moving_image,
            fixed_image  = ref_image,
            fixed_mask   = mask,
            output_dir   = reg_dir,
            patient_id   = pid,
            sequence     = seq_name,
        )
        sequence_images[seq_name] = reg_result['registered_image']
        qc_records.append(reg_result['qc'])
        # 配准中间 moving 不再需要
        del moving_image

    _validate_aligned(sequence_images, mask, pid)
    _mark_done(pid, "aligned")

    if lazy:
        # 影像已写盘，释放内存，只返回路径句柄
        handle = _aligned_paths(patient)
        handle['qc_records'] = qc_records
        _validate_aligned_paths(handle, pid)
        del sequence_images, ref_image, mask
        gc.collect()
        return handle

    return {
        'sequence_images': sequence_images,
        'mask'           : mask,
        'qc_records'     : qc_records,
        'lazy'           : False,
    }


# ═══════════════════════════════════════════════════════════════════
#  阶段 B/C：生境划分 + 影像组学特征提取（单患者）
# ═══════════════════════════════════════════════════════════════════

def process_patient_habitat_and_radiomics(patient: dict,
                                           aligned: dict,
                                           global_model: CohortHabitatModel = None
                                           ) -> pd.DataFrame:
    """
    对已对齐的患者数据执行生境划分与逐 Habitat × 逐序列影像组学特征提取。

    Parameters
    ----------
    patient      : dict — 患者原始记录（用于获取模态映射）
    aligned      : dict — load_aligned() 之后的结果（必须含 sequence_images/mask）
    global_model : CohortHabitatModel, optional — 全局生境模型（None=单患者模式）

    Returns
    -------
    pd.DataFrame — 该患者的 (Habitat × 序列) 特征矩阵
    """
    pid = patient['patient_id']

    # 特征已完成：无需任何影像，直接读 CSV（续跑最快路径）
    feat_dir  = _patient_dir(DIRS["features"], pid)
    feat_path = os.path.join(feat_dir, f"{pid}_features.csv")
    if _is_done(pid, "features") and os.path.exists(feat_path):
        logger.info(f"[跳过] 患者 {pid}：特征提取（已完成，从磁盘加载 CSV）")
        return pd.read_csv(feat_path, index_col='habitat')

    sequence_images = aligned['sequence_images']
    mask            = aligned['mask']

    # ─── 生境划分 ───────────────────────────────────────────────
    habitat_dir = _patient_dir(DIRS["habitat_masks"], pid)
    if _is_done(pid, "habitat"):
        logger.info(f"[跳过] 患者 {pid}：生境划分（已完成，从磁盘加载）")
        habitat_files = sorted([
            f for f in os.listdir(habitat_dir) if f.startswith(f"{pid}_habitat")
        ])
        habitat_masks = [sitk.ReadImage(os.path.join(habitat_dir, f)) for f in habitat_files]
    else:
        habitat_masks, optimal_k, _ = build_habitats(
            sequence_images = sequence_images,
            mask            = mask,
            output_dir      = habitat_dir,
            patient_id      = pid,
            global_model    = global_model,
        )
        _validate_habitat(habitat_masks, pid)
        _mark_done(pid, "habitat")

    # ─── 影像组学特征提取 ───────────────────────────────────────
    modality_map = {seq: info['modality'] for seq, info in patient['sequences'].items()}
    features = extract_features_all_habitats(
        sequence_images     = sequence_images,
        sequence_modalities = modality_map,
        habitat_masks       = habitat_masks,
        patient_id          = pid,
        output_dir          = feat_dir,
    )
    _validate_features(features, pid)
    _mark_done(pid, "features")

    # 生境 mask 列表用完即释
    del habitat_masks
    return features


# ═══════════════════════════════════════════════════════════════════
#  主流程
# ═══════════════════════════════════════════════════════════════════

def run_pipeline(data_table_path: str,
                 labels_path:     str   = None,
                 log_level:       int   = logging.INFO) -> dict:
    """
    主流程入口：逐患者对齐 → 全队列建模 → 逐患者生境+放射组学 → 汇总筛选。

    内存策略（MEMORY['lazy_load_aligned']=True，默认）
    ------------------------------------------------
    aligned_store 只保存磁盘路径句柄，不缓存 sitk.Image。
    阶段 2/3 对当前患者 load_aligned → 处理 → release_aligned，
    避免千例队列把全部影像常驻 RAM 导致 OOM。

    Parameters
    ----------
    data_table_path : str        — 数据表 CSV/TSV 路径（长表，见 data_loader.py）
    labels_path     : str, opt   — 标签文件路径（CSV，含 patient_id / label 列）
    log_level       : int        — 日志级别

    Returns
    -------
    dict — {'success_ids': list[str], 'failed_ids': list[str]}
    """
    _setup_logging(log_level)
    _setup_dirs()
    lazy = bool(MEMORY.get('lazy_load_aligned', True))
    logger.info(
        f"多参数生境影像组学流程启动 | 模式: {'全队列' if HABITAT['cohort_mode'] else '单患者'}"
        f" | 内存: {'懒加载(路径句柄)' if lazy else '全量常驻'}"
    )

    patients = load_patient_data(
        data_table_path, validate_paths=True, default_modality=PREPROC["default_modality"]
    )
    logger.info(f"共加载 {len(patients)} 位有效患者")

    # ─── 阶段 1：逐患者预处理 + 配准（对齐至参考序列空间）─────────
    # lazy 模式下 store 只存路径句柄；已完成患者不 ReadImage
    logger.info("\n>>> [阶段 1] 逐患者预处理与序列间配准...")
    aligned_store: dict = {}
    qc_records: list = []
    failed_ids: list = []
    gc_counter = 0

    for patient in patients:
        pid = patient['patient_id']
        try:
            handle = align_patient_sequences(patient)
            aligned_store[pid] = handle
            qc_records.extend(handle.get('qc_records', []))
        except Exception as e:
            logger.error(f"患者 {pid} 对齐阶段失败：{e}\n{traceback.format_exc()}")
            failed_ids.append(pid)
        gc_counter = _maybe_gc(gc_counter)

    # ─── 阶段 2：全队列特征池化与全局生境模型拟合（cohort_mode=True）─
    global_model = None
    if HABITAT["cohort_mode"]:
        model_path = HABITAT["global_model_path"]

        if os.path.exists(model_path):
            # 模型已存在：零影像 I/O，直接加载（当前断点续跑的常见路径）
            logger.info(f"加载现有全局模型：{model_path}")
            global_model = CohortHabitatModel.load(model_path)
        else:
            logger.info("\n>>> [阶段 2] 全队列多序列融合特征收集与全局模型拟合...")
            all_units = []   # 体素级或（启用 Superpixel 时）超体素级特征
            gc_counter = 0

            for patient in patients:
                pid = patient['patient_id']
                if pid not in aligned_store:
                    continue   # 阶段 1 已失败，跳过

                aligned = None
                try:
                    feat_npy_path = os.path.join(
                        _patient_dir(DIRS["preprocessed"], pid), f"{pid}_fusion_features.npy"
                    )
                    if os.path.exists(feat_npy_path):
                        # 优先复用已缓存的融合特征，无需读影像
                        units = np.load(feat_npy_path)
                    else:
                        from habitat import (compute_superpixel_segmentation,
                                              aggregate_by_superpixel)
                        aligned = load_aligned(aligned_store[pid])
                        v_feats, mask_arr, _ = compute_voxel_feature_maps(
                            aligned['sequence_images'], aligned['mask']
                        )
                        if HABITAT.get("use_superpixel", False):
                            sp_labels = compute_superpixel_segmentation(
                                aligned['sequence_images'], aligned['mask']
                            )
                            units, _, _ = aggregate_by_superpixel(v_feats, mask_arr, sp_labels)
                        else:
                            units = v_feats
                        np.save(feat_npy_path, units)

                    all_units.append(units)
                    logger.info(
                        f"  患者 {pid} 融合特征已提取"
                        f"（{units.shape[0]} 单元 × {units.shape[1]} 通道）"
                    )
                except Exception as e:
                    logger.error(f"  [阶段 2] 患者 {pid} 特征提取失败（跳过）：{e}")
                finally:
                    release_aligned(aligned)
                    gc_counter = _maybe_gc(gc_counter)

            if not all_units:
                raise RuntimeError("全队列融合特征收集失败：没有任何患者成功提取特征。")

            logger.info(f"正在合并全队列特征（共 {len(all_units)} 例）...")
            X_pool = np.concatenate(all_units, axis=0).astype(np.float32)
            del all_units
            gc.collect()
            np.save(HABITAT["pooled_features_path"], X_pool)
            logger.info(f"全队列池化特征已保存：{X_pool.shape}")

            global_model = CohortHabitatModel()
            global_model.fit(X_pool, os.path.join(DIRS["habitat_masks"], "k_optimization"))
            global_model.save(model_path)
            del X_pool
            gc.collect()

    # ─── 阶段 3：逐患者生境划分 + 影像组学特征提取 ─────────────────
    # 每位患者：features 已完成 → 只读 CSV；否则 load → 处理 → release
    logger.info("\n>>> [阶段 3] 逐患者生境划分与影像组学特征提取...")
    feature_store: dict = {}
    success_ids: list = []
    gc_counter = 0

    for patient in patients:
        pid = patient['patient_id']
        if pid in failed_ids:
            continue

        aligned = None
        try:
            handle = aligned_store[pid]
            # 仅当 checkpoint + CSV 均在时跳过读影像；否则按需 load
            feat_csv = os.path.join(DIRS["features"], pid, f"{pid}_features.csv")
            features_ready = _is_done(pid, "features") and os.path.exists(feat_csv)
            if not features_ready:
                aligned = load_aligned(handle)
            features = process_patient_habitat_and_radiomics(
                patient,
                aligned if aligned is not None else {'sequence_images': {}, 'mask': None},
                global_model,
            )
            feature_store[pid] = features
            success_ids.append(pid)
            logger.info(f"  患者 {pid} 完成 ✓")
        except Exception as e:
            logger.error(f"患者 {pid} 生境/特征阶段失败：{e}\n{traceback.format_exc()}")
            failed_ids.append(pid)
        finally:
            release_aligned(aligned)
            gc_counter = _maybe_gc(gc_counter)


    # 阶段 1 的路径句柄不再需要
    aligned_store.clear()
    gc.collect()

    # ─── 阶段 4：队列汇总与特征筛选 ─────────────────────────────────
    if feature_store:
        run_cohort_aggregation(feature_store, labels_path)

    save_qc_report(qc_records, success_ids, failed_ids)

    logger.info(
        f"\n流程结束：成功 {len(success_ids)} 例，失败 {len(failed_ids)} 例。"
        + (f"\n失败患者 ID：{failed_ids}" if failed_ids else "")
    )
    return {'success_ids': success_ids, 'failed_ids': failed_ids}



# ═══════════════════════════════════════════════════════════════════
#  汇总筛选
# ═══════════════════════════════════════════════════════════════════

def run_cohort_aggregation(feature_store: dict, labels_path: str):
    """
    全队列特征汇总、训练/测试集划分与特征筛选。

    Parameters
    ----------
    feature_store : dict[patient_id → pd.DataFrame] — 各患者 (Habitat×序列) 特征
    labels_path   : str, optional                    — 标签文件路径
    """
    X_all = aggregate_cohort_features(feature_store)
    X_all.to_csv(os.path.join(DIRS["selected"], "all_patients_features.csv"))

    if not labels_path or not os.path.exists(labels_path):
        logger.warning("未提供标签文件（或文件不存在），跳过特征筛选（已保存无标签队列特征矩阵）。")
        return

    labels_df = pd.read_csv(labels_path, index_col='patient_id', dtype={'patient_id': str})
    common    = X_all.index.intersection(labels_df.index)
    if len(common) == 0:
        logger.warning("特征矩阵与标签文件无交集患者，跳过特征筛选。")
        return
    if len(common) < len(X_all.index):
        logger.warning(f"部分患者无标签，仅对 {len(common)}/{len(X_all.index)} 例执行筛选。")

    X_use = X_all.loc[common]
    y_use = labels_df.loc[common, 'label']

    # 样本量不足以分层划分时，降级为非分层随机划分（避免 ValueError 崩溃）
    min_class_count = y_use.value_counts().min()
    if min_class_count < 2:
        logger.warning(f"最小类别样本数为 {min_class_count}（< 2），降级为非分层随机划分。")
        stratify_arg = None
    else:
        stratify_arg = y_use

    X_train, X_test, y_train, y_test = train_test_split(
        X_use, y_use, test_size=0.2, stratify=stratify_arg, random_state=42,
    )

    scaler = StandardScaler()
    X_tr_s = pd.DataFrame(scaler.fit_transform(X_train.fillna(0)),
                          index=X_train.index, columns=X_train.columns)
    X_te_s = pd.DataFrame(scaler.transform(X_test.fillna(0)),
                          index=X_test.index, columns=X_test.columns)

    X_tr_sel, selected = select_features(X_tr_s, y_train, DIRS["selected"])

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
    1. registration_qc.csv   — 所有患者×序列的配准质量指标（互信息 similarity）
    2. pipeline_summary.csv  — 本次运行的整体摘要
    """
    os.makedirs(DIRS["qc_reports"], exist_ok=True)

    if qc_records:
        pd.DataFrame(qc_records).to_csv(
            os.path.join(DIRS["qc_reports"], "registration_qc.csv"), index=False
        )

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
        description='多参数融合生境影像组学流程（Multi-parametric Habitat Radiomics）',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument('--data',   required=True,
                        help='数据表路径（CSV，长表格式，见 data_loader.py 顶部说明）')
    parser.add_argument('--labels', default=None,
                        help='标签文件路径（CSV，含 patient_id 和 label 列；不提供则跳过特征筛选）')
    parser.add_argument('--debug',  action='store_true', help='启用 DEBUG 级别日志')
    args = parser.parse_args()

    run_pipeline(
        data_table_path = args.data,
        labels_path     = args.labels,
        log_level       = logging.DEBUG if args.debug else logging.INFO,
    )
