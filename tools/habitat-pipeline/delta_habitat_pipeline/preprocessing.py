"""
preprocessing.py
================
图像预处理模块（SimpleITK 实现）

流程
----
    读取（DICOM 目录 or NIfTI 文件）
    → 空间重采样（统一体素间距，B-Spline 插值）
    → N4 偏置场校正（MRI 可选，CT 跳过）
    → 灰度归一化（zscore / minmax / none）
    → Mask 读取并对齐至图像空间（最近邻插值）
    → 中间结果验证（尺寸一致性 + mask 非空）
    → 保存 NIfTI 文件

Bug 修复（相比原版）
--------------------
    [BUG-2] resample_image 中 SetDefaultPixelValue 原先传入的是
    image.GetPixelIDValue()（像素类型枚举整数，如 Float32=8），
    而非实际强度值。修复为固定传入 0，与归一化后的背景值一致。

单独运行示例
------------
    python preprocessing.py \
        --image E:\\lxf\\img\\new\\1\\series\\1 \
        --roi   E:\\lxf\\roi\\mask1.nii.gz \
        --out   E:\\lxf\\results\\01_preprocessed\\patient_1 \
        --pid   1 --tp pre
"""

import os
import argparse
import logging
import numpy as np
import SimpleITK as sitk

from config import PREPROC

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  图像读取
# ═══════════════════════════════════════════════════════════════════

def load_image(path: str) -> sitk.Image:
    """
    自动识别路径类型并读取图像。

    策略
    ----
    - 目录 → DICOM 序列：使用 ImageSeriesReader，自动按 InstanceNumber 排序
    - 文件 → NIfTI / MetaImage 等格式（由 SimpleITK 自动识别）

    Parameters
    ----------
    path : str — 图像路径（DICOM 目录或 NIfTI 文件）

    Returns
    -------
    sitk.Image

    Raises
    ------
    FileNotFoundError — 路径不存在，或目录内无 DICOM 文件
    """
    path = str(path).strip()

    if os.path.isdir(path):
        # DICOM 目录：读取序列文件列表并按 InstanceNumber 排序
        reader = sitk.ImageSeriesReader()
        dicom_files = reader.GetGDCMSeriesFileNames(path)
        if not dicom_files:
            raise FileNotFoundError(
                f"目录 '{path}' 中未找到 DICOM 文件。"
            )
        reader.SetFileNames(dicom_files)
        # 加载私有标签（某些厂商序列号信息存储于私有标签中）
        reader.MetaDataDictionaryArrayUpdateOn()
        reader.LoadPrivateTagsOn()
        image = reader.Execute()
        logger.debug(f"读取 DICOM 目录：{len(dicom_files)} 层")
    else:
        if not os.path.exists(path):
            raise FileNotFoundError(f"文件不存在：{path}")
        image = sitk.ReadImage(path)
        logger.debug(f"读取文件：{path}")

    logger.info(
        f"图像尺寸={image.GetSize()}, "
        f"间距={tuple(round(s, 3) for s in image.GetSpacing())} mm, "
        f"像素类型={image.GetPixelIDTypeAsString()}"
    )
    return image


def load_mask(path: str, reference: sitk.Image) -> sitk.Image:
    """
    读取 ROI mask 并重采样至与参考图像完全一致的物理空间。

    插值方式
    --------
    使用最近邻插值（sitkNearestNeighbor）：
    保持二值标签的离散性，不产生 0 和 1 之间的中间值。

    Parameters
    ----------
    path      : str        — ROI mask 文件路径（NIfTI）
    reference : sitk.Image — 目标物理空间的参考图像（已重采样）

    Returns
    -------
    sitk.Image — 与 reference 空间完全对齐的 UInt8 mask
    """
    mask = sitk.ReadImage(str(path).strip(), sitk.sitkUInt8)

    resampler = sitk.ResampleImageFilter()
    resampler.SetReferenceImage(reference)          # 对齐到参考图像的尺寸/间距/方向
    resampler.SetInterpolator(sitk.sitkNearestNeighbor)  # 最近邻，保持二值
    resampler.SetDefaultPixelValue(0)               # 超出 mask 范围的体素填 0
    mask_resampled = resampler.Execute(mask)

    n_voxels = int(sitk.GetArrayFromImage(mask_resampled).sum())
    logger.info(f"Mask 重采样完成，肿瘤体素数：{n_voxels}")
    return mask_resampled


# ═══════════════════════════════════════════════════════════════════
#  空间重采样
# ═══════════════════════════════════════════════════════════════════

def resample_image(image: sitk.Image,
                   target_spacing: tuple = None) -> sitk.Image:
    """
    将图像重采样至目标体素间距（默认取自 config.PREPROC["target_spacing"]）。

    插值方式
    --------
    B-Spline 5 阶插值：在保留图像细节的同时抑制阶梯状插值伪影，
    比线性插值更准确，比三次样条更快。

    物理一致性
    ----------
    按照"物理视野不变"原则计算新体素数：
        new_size[i] = round(orig_size[i] × orig_spacing[i] / target_spacing[i])
    输出图像保留与原始图像相同的物理原点（Origin）和方向余弦（Direction）。

    [BUG-2 修复]
    -----------
    原版 SetDefaultPixelValue(int(image.GetPixelIDValue())) 实际上传入的是
    像素类型的枚举整数（如 Float32 对应 8），而非任何有意义的强度值。
    修复为传入 0，与归一化后的背景保持一致。

    Parameters
    ----------
    image          : sitk.Image — 输入图像
    target_spacing : tuple      — 目标体素间距 (x, y, z)，单位 mm

    Returns
    -------
    sitk.Image — 重采样后的图像
    """
    if target_spacing is None:
        target_spacing = PREPROC["target_spacing"]

    orig_spacing = image.GetSpacing()
    orig_size    = image.GetSize()

    # 按物理视野不变计算新的体素尺寸
    new_size = [
        int(round(osz * osp / tsp))
        for osz, osp, tsp in zip(orig_size, orig_spacing, target_spacing)
    ]

    resampler = sitk.ResampleImageFilter()
    resampler.SetOutputSpacing(target_spacing)
    resampler.SetSize(new_size)
    resampler.SetOutputDirection(image.GetDirection())
    resampler.SetOutputOrigin(image.GetOrigin())
    resampler.SetTransform(sitk.Transform())         # 恒等变换（仅重采样，不配准）

    # [BUG-2 修复] 背景填充值为 0（原版误传像素类型枚举 ID）
    resampler.SetDefaultPixelValue(0)

    resampler.SetInterpolator(sitk.sitkBSpline)      # B-Spline 5 阶

    resampled = resampler.Execute(image)
    logger.info(
        f"重采样：{orig_size} → {new_size}，"
        f"间距：{tuple(round(s,2) for s in orig_spacing)} → {target_spacing} mm"
    )
    return resampled


# ═══════════════════════════════════════════════════════════════════
#  N4 偏置场校正（MRI 专用）
# ═══════════════════════════════════════════════════════════════════

def correct_bias_field(image: sitk.Image,
                       mask: sitk.Image = None) -> sitk.Image:
    """
    N4 偏置场校正（Tustison et al., 2010）。

    适用场景
    --------
    MRI 图像因主磁场不均匀产生低频强度漂移（同一组织在图像不同位置
    亮度不一致），N4 算法通过迭代估计并去除该漂移场。
    CT 的 HU 值由硬件校准，无此问题，应将 apply_n4 设为 False。

    Parameters
    ----------
    image : sitk.Image      — 输入图像（将被转换为 Float32 后处理）
    mask  : sitk.Image, optional — 若提供，仅在 mask 区域内估计偏置场
                                   （更稳定，推荐）

    Returns
    -------
    sitk.Image — 校正后的 Float32 图像
    """
    image_float = sitk.Cast(image, sitk.sitkFloat32)

    corrector = sitk.N4BiasFieldCorrectionImageFilter()
    # 迭代次数：每层级分别设定，从粗到细逐层收紧
    corrector.SetMaximumNumberOfIterations(PREPROC["n4_iterations"])
    corrector.SetConvergenceThreshold(0.001)  # 能量变化低于此值时提前停止

    if mask is not None:
        result = corrector.Execute(image_float, sitk.Cast(mask, sitk.sitkUInt8))
    else:
        result = corrector.Execute(image_float)

    logger.info("N4 偏置场校正完成")
    return result


# ═══════════════════════════════════════════════════════════════════
#  灰度归一化
# ═══════════════════════════════════════════════════════════════════

def normalize_intensity(image: sitk.Image,
                        mask:  sitk.Image = None) -> sitk.Image:
    """
    灰度归一化（方法由 config.PREPROC["normalization"] 决定）。

    minmax（推荐 CT）
    ----------------
    1. 按 ct_window 裁剪异常值（超出窗范围的 HU 值截断）
    2. Min-Max 线性映射至 [0, 1]
    好处：保留 HU 的绝对物理意义，适合同一扫描协议的多中心研究。

    zscore（推荐 MRI）
    -----------------
    在 mask 内（若有）或全图统计均值 μ 和标准差 σ，执行：
        output = (image - μ) / σ
    好处：消除 MRI 扫描仪间的信号尺度差异，适合不同场强/线圈的数据。

    none（调试）
    -----------
    跳过归一化，直接返回 Float32 副本。

    Parameters
    ----------
    image : sitk.Image      — 输入图像
    mask  : sitk.Image, optional — ROI mask（zscore 模式下用于统计区域内统计量）

    Returns
    -------
    sitk.Image — 归一化后的 Float32 图像

    Raises
    ------
    ValueError   — 未知归一化方法
    RuntimeError — zscore 模式下 mask 内标准差接近零（图像可能无效）
    """
    method = PREPROC["normalization"]

    if method == "none":
        return sitk.Cast(image, sitk.sitkFloat32)

    arr = sitk.GetArrayFromImage(image).astype(np.float32)

    if method == "minmax":
        lo, hi = PREPROC.get("ct_window", (float(arr.min()), float(arr.max())))
        arr = np.clip(arr, lo, hi)              # 裁剪异常 HU 值
        arr = (arr - lo) / (hi - lo + 1e-8)    # 映射至 [0, 1]（+1e-8 防止除零）
        logger.info(f"灰度归一化（minmax，窗：[{lo}, {hi}]）")

    elif method == "zscore":
        # 在 mask 内统计，避免背景空气（CT）或脂肪（MRI）影响均值/标准差
        if mask is not None:
            roi_voxels = arr[sitk.GetArrayFromImage(mask).astype(bool)]
        else:
            roi_voxels = arr.ravel()

        mu    = float(roi_voxels.mean())
        sigma = float(roi_voxels.std())

        if sigma < 1e-8:
            raise RuntimeError(
                "Z-score 失败：mask 内标准差接近零，图像可能无效或 mask 仅含单一值。"
            )

        arr = (arr - mu) / sigma
        logger.info(f"灰度归一化（zscore，mu={mu:.4f}，sigma={sigma:.4f}）")

    else:
        raise ValueError(
            f"未知归一化方法：'{method}'，可选：zscore / minmax / none"
        )

    result = sitk.GetImageFromArray(arr)
    result.CopyInformation(image)   # 保留原始空间元数据（Origin/Spacing/Direction）
    return result


# ═══════════════════════════════════════════════════════════════════
#  中间结果验证
# ═══════════════════════════════════════════════════════════════════

def validate_preprocessed(image: sitk.Image,
                           mask:  sitk.Image,
                           patient_id: str,
                           timepoint:  str):
    """
    验证预处理结果的基本一致性。

    检查项
    ------
    1. 图像与 mask 的体素尺寸（Size）完全一致
       → 若不一致，后续特征提取时 mask 索引会错位，导致特征污染
    2. mask 内体素数 > 0
       → mask 为空意味着 ROI 文件有误或对齐失败

    Parameters
    ----------
    image      : sitk.Image — 预处理后的图像
    mask       : sitk.Image — 对齐后的 mask
    patient_id : str
    timepoint  : str

    Raises
    ------
    RuntimeError — 任意检查项不通过
    """
    img_size  = image.GetSize()
    mask_size = mask.GetSize()

    if img_size != mask_size:
        raise RuntimeError(
            f"预处理验证失败（患者 {patient_id} | {timepoint}）："
            f"图像尺寸 {img_size} ≠ mask 尺寸 {mask_size}。"
        )

    n_voxels = int(sitk.GetArrayFromImage(mask).sum())
    if n_voxels == 0:
        raise RuntimeError(
            f"预处理验证失败（患者 {patient_id} | {timepoint}）："
            f"mask 体素数为 0，请检查 ROI 文件。"
        )

    logger.info(
        f"预处理验证通过（患者 {patient_id} | {timepoint}）："
        f"尺寸={img_size}，mask 体素数={n_voxels}"
    )


# ═══════════════════════════════════════════════════════════════════
#  完整预处理流程
# ═══════════════════════════════════════════════════════════════════

def preprocess(image_path: str,
               roi_path:   str,
               output_dir: str,
               patient_id: str,
               timepoint:  str) -> tuple[sitk.Image, sitk.Image]:
    """
    对单个时间点执行完整预处理流程。

    步骤顺序
    --------
    1. load_image()          — 读取图像（DICOM/NIfTI 自动识别）
    2. resample_image()      — 统一体素间距
    3. correct_bias_field()  — N4 校正（apply_n4=True 时）
    4. normalize_intensity() — 灰度归一化
    5. load_mask()           — 读取并对齐 mask
    6. validate_preprocessed() — 一致性检查
    7. 保存 NIfTI 文件

    Parameters
    ----------
    image_path : str — 输入图像路径（DICOM 目录或 NIfTI）
    roi_path   : str — ROI mask 路径（NIfTI）
    output_dir : str — 预处理结果保存目录
    patient_id : str — 患者 ID（用于文件命名和日志）
    timepoint  : str — 'pre' 或 'post'

    Returns
    -------
    (image, mask) : tuple[sitk.Image, sitk.Image]

    Raises
    ------
    任何步骤的异常直接向上传播，由 pipeline.py 捕获并记录。
    """
    logger.info(f"[预处理] 患者 {patient_id} | {timepoint}")
    os.makedirs(output_dir, exist_ok=True)

    # 步骤 1：读取图像
    image = load_image(image_path)

    # 步骤 2：重采样（统一 1×1×1 mm）
    image = resample_image(image)

    # 步骤 3：N4 偏置场校正（仅 MRI，CT 跳过）
    if PREPROC["apply_n4"]:
        logger.info("执行 N4 偏置场校正...")
        image = correct_bias_field(image)

    # 步骤 4：灰度归一化（在加载 mask 之前，因为 zscore 需要先有图像）
    image = normalize_intensity(image)

    # 步骤 5：读取并对齐 mask（以重采样+归一化后的图像为参考空间）
    mask = load_mask(roi_path, reference=image)

    # 步骤 6：验证（验证通过后才保存，防止保存无效文件）
    validate_preprocessed(image, mask, patient_id, timepoint)

    # 步骤 7：保存结果
    sitk.WriteImage(image, os.path.join(output_dir, f"p{patient_id}_{timepoint}_image.nii.gz"))
    sitk.WriteImage(mask,  os.path.join(output_dir, f"p{patient_id}_{timepoint}_mask.nii.gz"))
    logger.info(f"预处理结果已保存：{output_dir}")

    return image, mask


# ═══════════════════════════════════════════════════════════════════
#  单独运行入口
# ═══════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(levelname)s  %(message)s')

    parser = argparse.ArgumentParser(description="单独运行预处理模块")
    parser.add_argument('--image', required=True, help='图像路径（DICOM 目录或 NIfTI）')
    parser.add_argument('--roi',   required=True, help='ROI mask 路径（NIfTI）')
    parser.add_argument('--out',   required=True, help='输出目录')
    parser.add_argument('--pid',   required=True, help='患者 ID')
    parser.add_argument('--tp',    required=True, choices=['pre', 'post'], help='时间点')
    args = parser.parse_args()

    img, msk = preprocess(args.image, args.roi, args.out, args.pid, args.tp)
    print(
        f"完成：图像尺寸={img.GetSize()}，"
        f"mask 体素数={int(sitk.GetArrayFromImage(msk).sum())}"
    )
