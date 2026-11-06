"""
registration.py
===============
序列间配准模块（ANTsPy 实现）—— 多参数融合版

配准策略：三阶段级联（与旧版一致，但配准对象由"pre/post 两时间点"
改为"任意非参考序列 → 参考序列"）
--------------------------------------------------------------------
    阶段 1  Translation   — 平移对齐，解决不同序列扫描床位/体位偏差
    阶段 2  Affine        — 仿射配准，校正全局旋转/缩放/剪切
    阶段 3  SyN           — 非刚性配准，捕获器官形变、呼吸运动等局部位移
    QC      —              基于 ROI 区域内 Mattes 互信息的质控指标（非强制失败）

为什么用互信息（Mattes MI）而非灰度相关系数
--------------------------------------------
本流程需要处理同模态配准（如 CT 平扫→动脉期）也需要处理跨模态配准
（如 T2WI→ADC，二者灰度关系是非线性的甚至非单调的）。互信息基于
联合概率分布的统计依赖性，无需假设灰度间存在线性关系，因此对同/跨
模态配准均稳健，是多参数融合场景下的通用相似度测度。

QC 设计变更说明
----------------
旧版 QC 基于"配准前后同一个肿瘤 mask 的 Dice/Hausdorff"，前提是
moving 图像本身也有独立 mask（pre/post 配对场景）。而在多参数融合
场景下，通常只有参考序列拥有人工勾画的 ROI，其余序列没有对应 mask，
因此无法计算重叠度。改为在 ROI 区域内计算配准前后的 Mattes 互信息，
作为可解释的、无需第二个 mask 的配准质量参考指标；默认仅记录警告，
不中断流程（可通过 config.REGISTRATION["qc_hard_fail"]=True 改为硬性失败）。

设计原则
--------
- 无隐藏回退：任何配准阶段失败均直接抛出异常，由 pipeline.py 捕获。
- 变换文件持久化：保存到 output_dir，支持断点续跑重新加载。
- Mask 引导配准：将参考序列的 ROI mask 传入 mask_image 参数，使相似度
  优化集中于感兴趣区域而非全图背景。

单独运行示例
------------
    python registration.py
    （对第一个患者的首个非参考序列执行配准，结果保存到 DIRS["registration"]）
"""

import os
import logging
import numpy as np
import SimpleITK as sitk
import ants

from config import REGISTRATION, DIRS

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  格式转换工具（与旧版一致，SimpleITK ↔ ANTsPy 轴顺序转换）
# ═══════════════════════════════════════════════════════════════════

def sitk_to_ants(sitk_image: sitk.Image) -> ants.ANTsImage:
    """
    SimpleITK.Image → ANTsImage（轴顺序转换 + 空间信息传递）。

    轴顺序差异：SimpleITK 数组 (Z,Y,X) → ANTsPy 数组 (X,Y,Z)：
    arr.transpose(2, 1, 0)

    Parameters
    ----------
    sitk_image : sitk.Image

    Returns
    -------
    ants.ANTsImage
    """
    arr = sitk.GetArrayFromImage(sitk_image)
    arr_ants = arr.transpose(2, 1, 0).astype(np.float32)
    return ants.from_numpy(
        arr_ants,
        origin    = list(sitk_image.GetOrigin()),
        spacing   = list(sitk_image.GetSpacing()),
        direction = np.array(sitk_image.GetDirection()).reshape(3, 3),
    )


def ants_to_sitk(ants_image: ants.ANTsImage,
                 reference: sitk.Image = None) -> sitk.Image:
    """
    ANTsImage → SimpleITK.Image（轴顺序转换 + 空间信息恢复）。

    传入 reference 可从原始 sitk 图像精确复制方向余弦，避免 ANTs
    内部浮点截断导致的累积误差。

    Parameters
    ----------
    ants_image : ants.ANTsImage
    reference  : sitk.Image, optional

    Returns
    -------
    sitk.Image（Float32）
    """
    arr_sitk = ants_image.numpy().transpose(2, 1, 0)
    sitk_img = sitk.GetImageFromArray(arr_sitk.astype(np.float32))
    sitk_img.SetSpacing(ants_image.spacing)
    sitk_img.SetOrigin(ants_image.origin)
    if reference is not None:
        sitk_img.SetDirection(reference.GetDirection())
    else:
        sitk_img.SetDirection(ants_image.direction.flatten().tolist())
    return sitk_img


def mask_sitk_to_ants(mask_sitk: sitk.Image,
                      reference_ants: ants.ANTsImage) -> ants.ANTsImage:
    """
    将 SimpleITK mask 转换为 ANTsImage，空间信息来自 reference_ants。

    Parameters
    ----------
    mask_sitk      : sitk.Image
    reference_ants : ants.ANTsImage

    Returns
    -------
    ants.ANTsImage（Float32）
    """
    arr_ants = sitk.GetArrayFromImage(mask_sitk).transpose(2, 1, 0).astype(np.float32)
    return ants.from_numpy(
        arr_ants,
        origin    = reference_ants.origin,
        spacing   = reference_ants.spacing,
        direction = reference_ants.direction,
    )


# ═══════════════════════════════════════════════════════════════════
#  配准质量控制（基于 ROI 内互信息，非强制失败）
# ═══════════════════════════════════════════════════════════════════

def compute_registration_qc(fixed_image:  sitk.Image,
                             registered_image: sitk.Image,
                             fixed_mask:   sitk.Image,
                             patient_id:   str,
                             sequence:     str) -> dict:
    """
    在 ROI 区域内计算 fixed 与配准后 moving 图像的 Mattes 互信息，
    作为配准质量的参考指标。

    评估逻辑
    --------
    similarity ≥ REGISTRATION["qc_min_similarity"] → valid = True
    否则：
        qc_hard_fail = True  → 抛出 RuntimeError，跳过该序列/患者
        qc_hard_fail = False → 仅记录警告，流程继续（默认行为）

    为什么不用 Dice/Hausdorff
    --------------------------
    非参考序列通常没有独立勾画的 ROI mask，无法计算两个 mask 的重叠度；
    互信息只需要 fixed 图像 + 配准后图像 + 一个 mask（来自参考序列），
    因此更适合多参数融合场景。

    Parameters
    ----------
    fixed_image      : sitk.Image — 参考序列图像
    registered_image : sitk.Image — 配准至参考空间后的图像
    fixed_mask       : sitk.Image — 参考序列 ROI mask（用于限定统计区域）
    patient_id        : str
    sequence          : str       — 被配准的序列名（用于日志）

    Returns
    -------
    dict — {'patient_id', 'sequence', 'similarity', 'valid'}

    Raises
    ------
    RuntimeError — qc_hard_fail=True 且未达标时
    """
    # 在 ROI 的外接包围盒内裁剪计算区域，减少无关背景对互信息的稀释
    mask_arr = sitk.GetArrayFromImage(fixed_mask)
    zs, ys, xs = np.where(mask_arr > 0)
    pad = 5
    z0, z1 = max(zs.min() - pad, 0), min(zs.max() + pad, mask_arr.shape[0] - 1)
    y0, y1 = max(ys.min() - pad, 0), min(ys.max() + pad, mask_arr.shape[1] - 1)
    x0, x1 = max(xs.min() - pad, 0), min(xs.max() + pad, mask_arr.shape[2] - 1)

    roi_slice = (slice(z0, z1 + 1), slice(y0, y1 + 1), slice(x0, x1 + 1))
    fixed_roi = sitk.GetArrayFromImage(fixed_image)[roi_slice].astype(np.float32)
    moved_roi = sitk.GetArrayFromImage(registered_image)[roi_slice].astype(np.float32)

    # 使用 SimpleITK 的 ImageRegistrationMethod 仅取其相似度评估功能
    # （MattesMutualInformation，负值越小表示互信息越高，故取相反数）
    f_img = sitk.GetImageFromArray(fixed_roi)
    m_img = sitk.GetImageFromArray(moved_roi)
    reg_eval = sitk.ImageRegistrationMethod()
    reg_eval.SetMetricAsMattesMutualInformation(numberOfHistogramBins=32)
    reg_eval.SetInterpolator(sitk.sitkLinear)
    reg_eval.SetInitialTransform(sitk.TranslationTransform(3), inPlace=False)
    try:
        metric_value = reg_eval.MetricEvaluate(f_img, m_img)
        similarity = float(-metric_value)   # 转为"越大越好"
    except Exception as e:
        logger.warning(f"互信息评估失败（{e}），QC 跳过数值判定。")
        similarity = float('nan')

    valid = (not np.isnan(similarity)) and (similarity >= REGISTRATION["qc_min_similarity"])

    qc = {
        'patient_id' : patient_id,
        'sequence'   : sequence,
        'similarity' : round(similarity, 4) if not np.isnan(similarity) else None,
        'valid'      : bool(valid),
    }

    if not valid:
        msg = (
            f"患者 {patient_id} | 序列 {sequence} 配准 QC 未达标："
            f"互信息={qc['similarity']}（阈值 ≥ {REGISTRATION['qc_min_similarity']}）"
        )
        if REGISTRATION.get("qc_hard_fail", False):
            raise RuntimeError(msg)
        else:
            logger.warning(msg + " —— 已记录，流程继续（qc_hard_fail=False）。")

    return qc


# ═══════════════════════════════════════════════════════════════════
#  核心配准函数：单序列 → 参考序列
# ═══════════════════════════════════════════════════════════════════

def register_sequence(moving_image: sitk.Image,
                       fixed_image:  sitk.Image,
                       fixed_mask:   sitk.Image,
                       output_dir:   str,
                       patient_id:   str,
                       sequence:     str) -> dict:
    """
    将 moving_image（非参考序列，已预处理）配准至 fixed_image（参考序列）
    的物理空间，三阶段级联（Translation → Affine → SyN），Mask 引导。

    Parameters
    ----------
    moving_image : sitk.Image — 待配准的序列图像（已重采样+归一化）
    fixed_image  : sitk.Image — 参考序列图像（已重采样+归一化）
    fixed_mask   : sitk.Image — 参考序列 ROI mask（引导配准 + QC 用）
    output_dir   : str        — 配准中间文件与结果保存目录
    patient_id   : str
    sequence     : str        — 被配准的序列名（用于文件命名与日志）

    Returns
    -------
    dict — {
        'registered_image' : sitk.Image — 配准后的图像（参考序列空间）
        'transform'        : dict       — ANTs 变换字典（fwdtransforms 文件列表）
        'qc'               : dict       — QC 指标 {'similarity', 'valid', ...}
    }

    Raises
    ------
    ValueError   — fixed_mask 体素数 ≤ 100（过小，无法引导配准）
    RuntimeError — 配准阶段失败，或 qc_hard_fail=True 且 QC 不合格
    """
    logger.info(f"[配准] 患者 {patient_id} | 序列 {sequence} → 参考序列空间")
    os.makedirs(output_dir, exist_ok=True)

    n_mask_voxels = int(sitk.GetArrayFromImage(fixed_mask).sum())
    if n_mask_voxels <= 100:
        raise ValueError(
            f"患者 {patient_id}：参考序列 mask 体素数过少（{n_mask_voxels} ≤ 100），"
            "无法可靠地引导配准。请检查 mask 文件或预处理结果。"
        )

    fixed_ants  = sitk_to_ants(fixed_image)
    moving_ants = sitk_to_ants(moving_image)
    mask_ants   = mask_sitk_to_ants(fixed_mask, fixed_ants)

    if REGISTRATION.get("force_spatial_sync", False):
        logger.info("  [增强] 强制同步 Moving 图像的 Origin/Direction 至 Fixed")
        moving_ants.set_origin(fixed_ants.origin)
        moving_ants.set_direction(fixed_ants.direction)

    if REGISTRATION.get("intensity_shift", False):
        logger.info("  [增强] 执行强度平移至正值区间（仅用于配准计算）")
        f_min, m_min = fixed_ants.min(), moving_ants.min()
        fixed_reg_input  = fixed_ants  - f_min + 1.0
        moving_reg_input = moving_ants - m_min + 1.0
    else:
        fixed_reg_input  = fixed_ants
        moving_reg_input = moving_ants

    aff_metric = REGISTRATION.get("aff_metric", "mattes")
    syn_metric = REGISTRATION.get("syn_metric", "mattes")

    # ── 阶段 1：Translation 平移对齐（不使用 mask，全局优化即可）───
    logger.info("  阶段 1/3：Translation 平移对齐...")
    init_tx = ants.registration(
        fixed             = fixed_reg_input,
        moving            = moving_reg_input,
        type_of_transform = 'Translation',
        aff_metric        = aff_metric,
        verbose           = False,
    )
    moving_translated = ants.apply_transforms(
        fixed         = fixed_reg_input,
        moving        = moving_reg_input,
        transformlist = init_tx['fwdtransforms'],
    )

    # ── 阶段 2：Affine 仿射配准（mask 引导）───────────────────────
    logger.info("  阶段 2/3：Affine 仿射配准（mask 引导）...")
    affine_result = ants.registration(
        fixed             = fixed_reg_input,
        moving            = moving_translated,
        type_of_transform = 'Affine',
        mask_image        = mask_ants,
        aff_metric        = aff_metric,
        verbose           = False,
    )
    affine_combined = affine_result['fwdtransforms'] + init_tx['fwdtransforms']

    # ── 阶段 3：SyN 非刚性配准（mask 引导）────────────────────────
    logger.info("  阶段 3/3：SyN 非刚性配准（mask 引导）...")
    syn_result = ants.registration(
        fixed             = fixed_reg_input,
        moving            = moving_reg_input,
        type_of_transform = 'SyN',
        initial_transform = affine_combined,
        mask_image        = mask_ants,
        syn_metric        = syn_metric,
        syn_iterations    = tuple(REGISTRATION["syn_iterations"]),
        smoothing_sigmas  = tuple(REGISTRATION["syn_smoothing"]),
        shrink_factors    = tuple(REGISTRATION["syn_shrink"]),
        verbose           = False,
        outprefix         = os.path.join(output_dir, f"{patient_id}_{sequence}_"),
    )

    # ── 将变换应用于原始量程图像（避免强度平移导致输出失真）────────
    logger.info("  将变换应用于原始量程图像...")
    warped_moving_ants = ants.apply_transforms(
        fixed         = fixed_ants,
        moving        = moving_ants,
        transformlist = syn_result['fwdtransforms'],
        interpolator  = 'linear',
    )

    reg_sitk = ants_to_sitk(warped_moving_ants, reference=fixed_image)
    sitk.WriteImage(
        reg_sitk,
        os.path.join(output_dir, f"{patient_id}_{sequence}_registered.nii.gz")
    )
    logger.info(f"  配准后图像已保存：{output_dir}")

    qc = compute_registration_qc(
        fixed_image       = fixed_image,
        registered_image  = reg_sitk,
        fixed_mask        = fixed_mask,
        patient_id        = patient_id,
        sequence          = sequence,
    )

    return {
        'registered_image' : reg_sitk,
        'transform'        : syn_result,
        'qc'               : qc,
    }


def register_all_sequences(reference_image: sitk.Image,
                            reference_mask:  sitk.Image,
                            moving_sequences: dict,
                            output_dir: str,
                            patient_id: str) -> dict:
    """
    将某患者的所有非参考序列批量配准至参考序列空间。

    Parameters
    ----------
    reference_image  : sitk.Image — 参考序列图像（已预处理）
    reference_mask   : sitk.Image — 参考序列 ROI mask
    moving_sequences : dict[str, sitk.Image] — {序列名: 已预处理的图像}
                       （不含参考序列本身）
    output_dir       : str
    patient_id       : str

    Returns
    -------
    dict — {
        'aligned_images' : dict[str, sitk.Image] — 含参考序列在内、
            全部对齐至参考空间的图像（键为序列名）
        'qc_reports'      : list[dict] — 各非参考序列的 QC 结果
    }
    """
    aligned = {}
    qc_reports = []

    for seq_name, moving_image in moving_sequences.items():
        result = register_sequence(
            moving_image = moving_image,
            fixed_image  = reference_image,
            fixed_mask   = reference_mask,
            output_dir   = output_dir,
            patient_id   = patient_id,
            sequence     = seq_name,
        )
        aligned[seq_name] = result['registered_image']
        qc_reports.append(result['qc'])

    return {'aligned_images': aligned, 'qc_reports': qc_reports}


# ═══════════════════════════════════════════════════════════════════
#  单独运行入口（调试用）
# ═══════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(levelname)s  %(message)s')

    from config import DATA_TABLE_PATH, PREPROC
    from data_loader import load_patient_data

    patients = load_patient_data(DATA_TABLE_PATH, validate_paths=True,
                                  default_modality=PREPROC["default_modality"])
    if not patients:
        print("没有可用患者，请检查数据表。")
        exit(1)

    patient = patients[0]
    pid     = patient['patient_id']
    ref_seq = patient['reference_sequence']

    preproc_dir = os.path.join(DIRS["preprocessed"], pid)
    ref_image = sitk.ReadImage(os.path.join(preproc_dir, f"{pid}_{ref_seq}_image.nii.gz"))
    ref_mask  = sitk.ReadImage(os.path.join(preproc_dir, f"{pid}_{ref_seq}_mask.nii.gz"))

    other_seqs = [s for s in patient['sequences'] if s != ref_seq]
    if not other_seqs:
        print(f"患者 {pid} 只有参考序列，无需配准。")
        exit(0)

    moving_seq = other_seqs[0]
    moving_image = sitk.ReadImage(os.path.join(preproc_dir, f"{pid}_{moving_seq}_image.nii.gz"))

    reg_dir = os.path.join(DIRS["registration"], pid)
    result  = register_sequence(moving_image, ref_image, ref_mask, reg_dir, pid, moving_seq)

    print(f"\n配准 QC：{result['qc']}")
    print(f"配准后图像尺寸：{result['registered_image'].GetSize()}")
