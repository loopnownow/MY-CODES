"""
registration.py
===============
非刚性配准模块（ANTsPy 实现）

配准策略：三阶段级联
--------------------
    阶段 1  Translation   — 平移对齐，解决两次扫描床位偏差
    阶段 2  Affine        — 仿射配准，校正全局旋转/缩放/剪切
    阶段 3  SyN           — 非刚性配准，捕获肿瘤形变和呼吸运动等局部位移
    QC      —              Dice 系数 + Hausdorff 距离评估配准质量

设计原则
--------
- 无任何回退方案：任何阶段失败均直接抛出异常，
  由 pipeline.py 捕获并决定是否继续处理下一患者。
- 变换文件持久化：所有变换文件保存到 output_dir，支持断点续跑重新加载。
- Mask 引导配准：将 fixed_mask 传入 mask_image 参数，使 ANTs 的相似度
  优化集中于肿瘤区域，而非平均整张图像，显著提升肿瘤区配准精度。

Bug 修复（相比原版）
--------------------
    [BUG-4] propagate_masks 中 ants_to_sitk 原来使用 reference=mask_sitk，
    即将变形后的 mask 的空间元数据设置为变形前 mask 的元数据（pre 空间），
    导致空间信息错误。修复为 reference=fixed_image（t1 图像），
    使传播后的 mask 位于正确的 t1 物理空间。

单独运行示例
------------
    python registration.py
    （对第一个患者执行配准，结果保存到 DIRS["registration"]）
"""

import os
import logging
import numpy as np
import SimpleITK as sitk
import ants

from config import REGISTRATION, DIRS

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  格式转换工具
# ═══════════════════════════════════════════════════════════════════

def sitk_to_ants(sitk_image: sitk.Image) -> ants.ANTsImage:
    """
    SimpleITK.Image → ANTsImage（轴顺序转换 + 空间信息传递）。

    轴顺序差异
    ----------
    SimpleITK 内部数组：(Z, Y, X)（GetArrayFromImage 返回）
    ANTsPy 内部数组：  (X, Y, Z)（from_numpy 期望输入）
    转换：arr.transpose(2, 1, 0)

    空间信息
    --------
    Origin、Spacing、Direction 直接从 sitk_image 读取并传递。
    Direction 从扁平列表 reshape 为 3×3 矩阵（ANTs 要求）。

    Parameters
    ----------
    sitk_image : sitk.Image

    Returns
    -------
    ants.ANTsImage
    """
    arr = sitk.GetArrayFromImage(sitk_image)           # (Z, Y, X)
    arr_ants = arr.transpose(2, 1, 0).astype(np.float32)   # → (X, Y, Z)
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

    轴顺序：(X, Y, Z) → (Z, Y, X)（与 sitk_to_ants 互逆）

    为什么需要 reference
    --------------------
    ANTs 在内部计算时可能对方向余弦进行近似（浮点截断），
    传入 reference 可从原始 sitk 图像精确复制方向余弦，
    避免累积误差导致后续 mask 对齐偏差。

    Parameters
    ----------
    ants_image : ants.ANTsImage
    reference  : sitk.Image, optional — 用于精确复制方向余弦的参考图像

    Returns
    -------
    sitk.Image（Float32）
    """
    arr_sitk = ants_image.numpy().transpose(2, 1, 0)   # (X,Y,Z) → (Z,Y,X)
    sitk_img = sitk.GetImageFromArray(arr_sitk.astype(np.float32))
    sitk_img.SetSpacing(ants_image.spacing)
    sitk_img.SetOrigin(ants_image.origin)
    if reference is not None:
        # 从原始图像精确复制方向余弦（避免 ANTs 内部近似误差）
        sitk_img.SetDirection(reference.GetDirection())
    else:
        sitk_img.SetDirection(ants_image.direction.flatten().tolist())
    return sitk_img


def mask_sitk_to_ants(mask_sitk: sitk.Image,
                      reference_ants: ants.ANTsImage) -> ants.ANTsImage:
    """
    将 SimpleITK mask 转换为 ANTsImage，空间信息来自 reference_ants。

    用途
    ----
    将 sitk 格式的 ROI mask 传入 ANTs 配准函数的 mask_image 参数，
    使配准在肿瘤区域内优化相似度指标。

    Parameters
    ----------
    mask_sitk      : sitk.Image     — 二值 mask（UInt8 或其他整型）
    reference_ants : ants.ANTsImage — 空间信息来源（通常为 fixed_ants）

    Returns
    -------
    ants.ANTsImage（Float32）
    """
    # (Z, Y, X) → (X, Y, Z)，与 sitk_to_ants 相同
    arr_ants = sitk.GetArrayFromImage(mask_sitk).transpose(2, 1, 0).astype(np.float32)
    return ants.from_numpy(
        arr_ants,
        origin    = reference_ants.origin,
        spacing   = reference_ants.spacing,
        direction = reference_ants.direction,
    )


# ═══════════════════════════════════════════════════════════════════
#  配准质量控制
# ═══════════════════════════════════════════════════════════════════

def compute_registration_qc(fixed_mask:  sitk.Image,
                             moving_mask: sitk.Image,
                             transform:   dict,
                             output_dir:  str,
                             patient_id:  str) -> dict:
    """
    配准 QC：将 moving_mask 经变换传播到 fixed 空间，
    计算与 fixed_mask 的 Dice 系数和 Hausdorff 距离。

    评估逻辑
    --------
    Dice ≥ REGISTRATION["qc_dice_threshold"]（默认 0.85）
    AND
    Hausdorff ≤ REGISTRATION["qc_hausdorff_threshold"]（默认 5.0 mm）
    → valid = True，流程继续
    → valid = False，抛出 RuntimeError，跳过该患者

    中间结果
    --------
    {output_dir}/p{patient_id}_t2_mask_warped.nii.gz
    （配准后的 moving mask，可在 ITK-SNAP 中叠加 fixed_mask 目视检查）

    Parameters
    ----------
    fixed_mask  : sitk.Image — t1 基线肿瘤 mask（参考）
    moving_mask : sitk.Image — t2 随访肿瘤 mask（将被传播）
    transform   : dict       — ANTs 变换字典（含 fwdtransforms 文件列表）
    output_dir  : str        — QC 文件保存目录
    patient_id  : str

    Returns
    -------
    dict — {'patient_id', 'dice', 'hausdorff_mm', 'valid'}

    Raises
    ------
    RuntimeError — QC 未通过（Dice 或 Hausdorff 不满足阈值）
    """
    os.makedirs(output_dir, exist_ok=True)

    # 将 moving_mask 和 fixed_mask 转换为 ANTs 格式以应用变换
    moving_mask_ants = sitk_to_ants(moving_mask)
    fixed_mask_ants  = sitk_to_ants(fixed_mask)

    # 若配准时使用了 force_spatial_sync，QC 也需同步空间信息保持一致性
    if REGISTRATION.get("force_spatial_sync", False):
        moving_mask_ants.set_origin(fixed_mask_ants.origin)
        moving_mask_ants.set_direction(fixed_mask_ants.direction)

    # 将 t2 mask 经正向变换传播到 t1 空间（最近邻插值保持二值性）
    warped_mask_ants = ants.apply_transforms(
        fixed         = fixed_mask_ants,
        moving        = moving_mask_ants,
        transformlist = transform['fwdtransforms'],
        interpolator  = 'nearestNeighbor',
    )
    warped_mask_sitk = sitk.Cast(
        ants_to_sitk(warped_mask_ants, reference=fixed_mask),
        sitk.sitkUInt8
    )

    # 保存传播后的 mask（用于人工目视 QC，可在 ITK-SNAP 中叠加对比）
    sitk.WriteImage(
        warped_mask_sitk,
        os.path.join(output_dir, f"p{patient_id}_t2_mask_warped.nii.gz")
    )

    # ── Dice 系数（重叠度）────────────────────────────────────────
    # Dice = 2|A∩B| / (|A| + |B|)，范围 [0, 1]，越高越好
    overlap = sitk.LabelOverlapMeasuresImageFilter()
    overlap.Execute(sitk.Cast(fixed_mask, sitk.sitkUInt8), warped_mask_sitk)
    dice = float(overlap.GetDiceCoefficient())

    # ── Hausdorff 距离（边界偏差，mm）────────────────────────────
    # 定义：两个 mask 边界点集之间的最大最小距离，越小越好
    hd_filter = sitk.HausdorffDistanceImageFilter()
    hd_filter.Execute(sitk.Cast(fixed_mask, sitk.sitkUInt8), warped_mask_sitk)
    hausdorff = float(hd_filter.GetHausdorffDistance())

    valid = (
        dice      >= REGISTRATION["qc_dice_threshold"] and
        hausdorff <= REGISTRATION["qc_hausdorff_threshold"]
    )

    qc = {
        'patient_id'   : patient_id,
        'dice'         : round(dice, 4),
        'hausdorff_mm' : round(hausdorff, 2),
        'valid'        : valid,
    }

    if not valid:
        raise RuntimeError(
            f"患者 {patient_id} 配准 QC 不合格：\n"
            f"  Dice={dice:.4f}（阈值 ≥ {REGISTRATION['qc_dice_threshold']}）\n"
            f"  Hausdorff={hausdorff:.2f} mm（阈值 ≤ {REGISTRATION['qc_hausdorff_threshold']} mm）\n"
            "请检查预处理结果，或在 config.REGISTRATION 中调整 QC 阈值后重试。"
        )

    logger.info(
        f"  配准 QC 通过：Dice={dice:.4f}，Hausdorff={hausdorff:.2f} mm"
    )
    return qc


# ═══════════════════════════════════════════════════════════════════
#  三阶段配准主函数
# ═══════════════════════════════════════════════════════════════════

def register_images(fixed_image:  sitk.Image,
                    moving_image: sitk.Image,
                    fixed_mask:   sitk.Image,
                    moving_mask:  sitk.Image,
                    output_dir:   str,
                    patient_id:   str) -> dict:
    """
    执行三阶段 ANTs 配准（Translation → Affine → SyN）。

    为什么分三阶段
    --------------
    Translation：首先消除两次扫描的床位偏差（粗对齐），
                 为后续精配准提供良好的初始位置。
    Affine：     校正扫描体位差异导致的全局旋转/缩放/剪切（线性变形）。
    SyN：        捕获肿瘤收缩、周围组织形变、呼吸运动等非线性局部形变，
                 使 t2 图像的每个体素精确对应 t1 的同一解剖位置。

    Mask 引导配准
    -------------
    将 fixed_mask 传入 ANTs 的 mask_image 参数，引导相似度指标优化
    集中于肿瘤区域，而非平均全图。这对肿瘤小、周围组织对比度高的情况尤为重要。

    强度平移策略（intensity_shift=True）
    ------------------------------------
    ANTs 部分相似度指标（如 MeanSquares）要求图像为正值。
    若图像经 z-score 归一化含有负值，会触发 "Total Mass is zero" 错误。
    解决方案：配准时将图像整体平移至正值区间，配准完成后对原始量程图像
    应用变换（保留 HU 值或信号值的物理意义）。

    Parameters
    ----------
    fixed_image  : sitk.Image — t1 基线图像（配准固定参考）
    moving_image : sitk.Image — t2 随访图像（将被变形配准至 t1 空间）
    fixed_mask   : sitk.Image — t1 基线肿瘤 mask（引导配准优化方向）
    moving_mask  : sitk.Image — t2 随访肿瘤 mask（仅用于 QC 评估）
    output_dir   : str        — 变换文件和 QC 结果保存目录
    patient_id   : str

    Returns
    -------
    dict — {
        'registered_image' : sitk.Image — 配准后的 t2 图像（t1 空间）
        'transform'        : dict       — ANTs 变换字典（含 fwdtransforms 文件列表）
        'qc'               : dict       — QC 指标 {'dice', 'hausdorff_mm', 'valid'}
    }

    Raises
    ------
    ValueError   — fixed_mask 体素数 ≤ 100（过小，无法引导配准）
    RuntimeError — 配准阶段失败，或 QC 不合格
    """
    logger.info(f"[配准] 患者 {patient_id}")
    os.makedirs(output_dir, exist_ok=True)

    # ── 前置检查：mask 必须足够大才能有效引导配准 ─────────────────
    n_mask_voxels = int(sitk.GetArrayFromImage(fixed_mask).sum())
    if n_mask_voxels <= 100:
        raise ValueError(
            f"患者 {patient_id}：基线 mask 体素数过少（{n_mask_voxels} ≤ 100），"
            "无法可靠地引导配准。请检查 mask 文件或预处理结果。"
        )

    # ── 转换为 ANTs 格式 ─────────────────────────────────────────
    fixed_ants  = sitk_to_ants(fixed_image)
    moving_ants = sitk_to_ants(moving_image)
    mask_ants   = mask_sitk_to_ants(fixed_mask, fixed_ants)

    # ── 增强策略 1：强制同步物理空间（解决 Origin 差异过大的问题）──
    if REGISTRATION.get("force_spatial_sync", False):
        logger.info("  [增强] 强制同步 Moving 图像的 Origin/Direction 至 Fixed")
        moving_ants.set_origin(fixed_ants.origin)
        moving_ants.set_direction(fixed_ants.direction)

    # ── 增强策略 2：强度平移（解决 ANTs "Total Mass is zero" 错误）──
    # 将图像平移至正值区间用于配准计算，避免负值导致的相似度计算异常
    if REGISTRATION.get("intensity_shift", False):
        logger.info("  [增强] 执行强度平移至正值区间（仅用于配准计算）")
        f_min, m_min = fixed_ants.min(), moving_ants.min()
        fixed_reg_input  = fixed_ants  - f_min + 1.0
        moving_reg_input = moving_ants - m_min + 1.0
    else:
        fixed_reg_input  = fixed_ants
        moving_reg_input = moving_ants

    # ── 阶段 1：Translation 平移对齐 ─────────────────────────────
    # 目的：快速消除两次扫描的粗略床位偏差，为后续配准提供良好初始位置
    # 注意：此阶段不使用 mask 引导（平移变换参数少，全局优化即可）
    logger.info("  阶段 1/3：Translation 平移对齐...")
    init_tx = ants.registration(
        fixed             = fixed_reg_input,
        moving            = moving_reg_input,
        type_of_transform = 'Translation',
        verbose           = False,
    )

    # 将平移后的图像作为阶段 2 的输入（避免 Affine 承担过多平移任务）
    moving_translated = ants.apply_transforms(
        fixed         = fixed_reg_input,
        moving        = moving_reg_input,
        transformlist = init_tx['fwdtransforms'],
    )

    # ── 阶段 2：Affine 仿射配准（带 mask 引导）───────────────────
    # 目的：校正扫描体位差异导致的全局旋转、缩放、剪切（12 个自由度）
    logger.info("  阶段 2/3：Affine 仿射配准（mask 引导）...")
    affine_result = ants.registration(
        fixed             = fixed_reg_input,
        moving            = moving_translated,
        type_of_transform = 'Affine',
        mask_image        = mask_ants,   # 引导优化集中于肿瘤区域
        verbose           = False,
    )

    # 组合变换：先应用平移，再应用仿射（作为 SyN 的初始变换）
    affine_combined = affine_result['fwdtransforms'] + init_tx['fwdtransforms']

    # ── 阶段 3：SyN 非刚性配准（带 mask 引导）───────────────────
    # 目的：捕获肿瘤形变、呼吸运动等非线性局部位移
    # initial_transform：继承前两阶段的结果作为初始值，加快收敛
    logger.info("  阶段 3/3：SyN 非刚性配准（mask 引导）...")
    syn_result = ants.registration(
        fixed             = fixed_reg_input,
        moving            = moving_reg_input,
        type_of_transform = 'SyN',
        initial_transform = affine_combined,       # 利用前两阶段结果初始化
        mask_image        = mask_ants,
        syn_iterations    = tuple(REGISTRATION["syn_iterations"]),
        smoothing_sigmas  = tuple(REGISTRATION["syn_smoothing"]),
        shrink_factors    = tuple(REGISTRATION["syn_shrink"]),
        verbose           = False,
        outprefix         = os.path.join(output_dir, f"p{patient_id}_"),
    )
    # syn_result['fwdtransforms'] 文件命名：
    #   p{pid}_1Warp.nii.gz          ← 非线性形变场（正向）
    #   p{pid}_0GenericAffine.mat    ← 仿射变换矩阵
    # ANTs 正向变换的应用顺序：先 Warp，后 Affine（列表中 [0]=Warp, [1]=Affine）

    # ── 增强策略 3：对原始量程图像应用变换 ──────────────────────
    # 配准用平移后的图像计算变换，但最终应用到原始图像，
    # 保证输出图像的 HU 值（或信号值）不因强度平移而失真
    logger.info("  将变换应用于原始量程图像...")
    warped_moving_ants = ants.apply_transforms(
        fixed         = fixed_ants,          # 原始 fixed（不含强度偏移）
        moving        = moving_ants,         # 原始 moving（不含强度偏移）
        transformlist = syn_result['fwdtransforms'],
        interpolator  = 'linear',            # 连续灰度值用线性插值
    )

    # 转换回 sitk 格式并保存
    reg_sitk = ants_to_sitk(warped_moving_ants, reference=fixed_image)
    sitk.WriteImage(
        reg_sitk,
        os.path.join(output_dir, f"p{patient_id}_t2_registered.nii.gz")
    )
    logger.info(f"  配准后图像已保存：{output_dir}")

    # ── QC 评估 ──────────────────────────────────────────────────
    # 不合格时直接抛出 RuntimeError，由 pipeline.py 捕获并记录该患者失败
    qc = compute_registration_qc(
        fixed_mask  = fixed_mask,
        moving_mask = moving_mask,
        transform   = syn_result,
        output_dir  = os.path.join(output_dir, "qc"),
        patient_id  = patient_id,
    )

    return {
        'registered_image' : reg_sitk,
        'transform'        : syn_result,
        'qc'               : qc,
    }


# ═══════════════════════════════════════════════════════════════════
#  Mask 传播
# ═══════════════════════════════════════════════════════════════════

def propagate_masks(habitat_masks: list[sitk.Image],
                    transform:     dict,
                    fixed_image:   sitk.Image,
                    output_dir:    str,
                    patient_id:    str) -> list[sitk.Image]:
    """
    将 Baseline（t1）的 K 个 Habitat Mask 经配准变换传播至 t2 空间。

    为什么传播而不是独立聚类
    ------------------------
    若对 t2 独立执行聚类，同一编号的 Habitat 在 t1 和 t2 之间可能代表
    不同的解剖区域（聚类结果具有置换不变性）。
    传播机制确保：Habitat H 在 t1 和 t2 中始终对应相同的组织区域，
    从而使 Delta 特征（f_post - f_pre）具有逐体素的解剖对应意义。

    插值方式：nearestNeighbor（保持二值标签离散性，不产生中间值）

    [BUG-4 修复]
    -----------
    原版 ants_to_sitk(warped_ants, reference=mask_sitk) 将变形后 mask 的
    空间元数据设置为变形前（t1 空间）mask 的元数据，导致输出 mask 空间信息
    错误（指向 pre 空间而非 fixed 空间）。
    修复：reference=fixed_image，使传播后的 mask 正确位于 t1 物理空间。

    Parameters
    ----------
    habitat_masks : list[sitk.Image] — K 个 t1 Habitat Mask（UInt8）
    transform     : dict             — register_images 返回的变换字典
    fixed_image   : sitk.Image       — t1 基线图像（提供目标空间信息）
    output_dir    : str
    patient_id    : str

    Returns
    -------
    list[sitk.Image] — K 个传播后的 Habitat Mask（UInt8，t1 空间）

    中间结果文件
    ------------
    {output_dir}/p{patient_id}_habitat{h}_t2_propagated.nii.gz
    """
    os.makedirs(output_dir, exist_ok=True)

    # fixed_image 提供目标物理空间的参考（Spacing/Origin/Direction）
    fixed_ants = sitk_to_ants(fixed_image)
    propagated = []

    for h, mask_sitk in enumerate(habitat_masks):
        # 将每个 Habitat Mask 转换为 ANTs 格式
        mask_ants = mask_sitk_to_ants(mask_sitk, fixed_ants)

        # 应用正向变换（t1 mask → t2 空间对应位置）
        warped_ants = ants.apply_transforms(
            fixed         = fixed_ants,
            moving        = mask_ants,
            transformlist = transform['fwdtransforms'],
            interpolator  = 'nearestNeighbor',   # 保持二值，不产生中间值
        )

        # [BUG-4 修复] reference=fixed_image（而非 mask_sitk），
        # 确保输出 mask 具有正确的 t1 物理空间元数据
        prop_sitk = sitk.Cast(
            ants_to_sitk(warped_ants, reference=fixed_image),
            sitk.sitkUInt8
        )

        save_path = os.path.join(
            output_dir, f"p{patient_id}_habitat{h}_t2_propagated.nii.gz"
        )
        sitk.WriteImage(prop_sitk, save_path)
        propagated.append(prop_sitk)
        logger.debug(f"  Habitat {h} mask 传播完成 → {save_path}")

    logger.info(f"  {len(propagated)} 个 Habitat Mask 传播完成")
    return propagated


# ═══════════════════════════════════════════════════════════════════
#  单独运行入口（调试用）
# ═══════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import logging
    logging.basicConfig(level=logging.INFO, format='%(levelname)s  %(message)s')

    from config import DATA_TABLE_PATH, DIRS
    from data_loader import load_patient_pairs

    pairs = load_patient_pairs(DATA_TABLE_PATH, validate_paths=True)
    if not pairs:
        print("没有可用患者，请检查数据表。")
        exit(1)

    pair = pairs[0]
    pid  = pair['patient_id']

    # 从预处理结果目录加载图像（假设预处理已完成）
    preproc_dir = os.path.join(DIRS["preprocessed"], f"patient_{pid}")
    img_t1  = sitk.ReadImage(os.path.join(preproc_dir, f"p{pid}_pre_image.nii.gz"))
    mask_t1 = sitk.ReadImage(os.path.join(preproc_dir, f"p{pid}_pre_mask.nii.gz"))
    img_t2  = sitk.ReadImage(os.path.join(preproc_dir, f"p{pid}_post_image.nii.gz"))
    mask_t2 = sitk.ReadImage(os.path.join(preproc_dir, f"p{pid}_post_mask.nii.gz"))

    reg_dir = os.path.join(DIRS["registration"], f"patient_{pid}")
    result  = register_images(img_t1, img_t2, mask_t1, mask_t2, reg_dir, pid)

    print(f"\n配准 QC：{result['qc']}")
    print(f"配准后图像尺寸：{result['registered_image'].GetSize()}")
