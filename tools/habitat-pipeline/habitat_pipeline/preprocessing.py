"""
preprocessing.py
================
图像预处理模块（SimpleITK 实现）—— 多序列 / 多模态版

流程
----
    读取（DICOM 目录 / NIfTI 文件 / NRRD 文件，三种格式自动识别）
    → 空间重采样（统一体素间距，B-Spline 插值）
    → N4 偏置场校正（按模态从 config 决定是否执行）
    → 灰度归一化（按模态 / 序列从 config 决定方法：zscore / minmax / none）
    → （仅参考序列）ROI mask 读取并对齐至图像空间（最近邻插值）
    → 中间结果验证
    → 保存 NIfTI 文件

与旧版的区别
------------
旧版按 pre/post 两个固定时间点组织；本版本按"序列名"组织，可处理
任意数量、任意命名的序列（CT 多期增强 / MRI 多参数），且预处理策略
（N4、归一化、CT 窗宽窗位）按模态（CT/MRI）在 config.PREPROC 中集中配置，
必要时可按具体序列名覆盖（SEQUENCE_OVERRIDES）。

单独运行示例
------------
    python preprocessing.py \
        --image E:\\data\\P001\\arterial \
        --out   E:\\results\\01_preprocessed\\P001 \
        --pid   P001 --sequence CT_arterial --modality CT
"""

import os
import argparse
import logging
import numpy as np
import SimpleITK as sitk

from config import PREPROC

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  配置解析：按模态 + 序列覆盖，取得该序列最终生效的预处理参数
# ═══════════════════════════════════════════════════════════════════

def resolve_preproc_settings(sequence_name: str, modality: str) -> dict:
    """
    合并"模态级默认设置"与"序列级覆盖设置"，返回该序列最终生效的
    预处理参数字典。

    合并优先级
    ----------
    SEQUENCE_OVERRIDES[sequence_name]（若存在） > modality_settings[modality]

    Parameters
    ----------
    sequence_name : str — 序列名（如 "CT_arterial", "ADC"）
    modality      : str — "CT" 或 "MRI"

    Returns
    -------
    dict — 含 apply_n4 / n4_iterations / normalization / ct_window 的完整配置

    Raises
    ------
    KeyError — modality 不在 config.PREPROC["modality_settings"] 中
    """
    if modality not in PREPROC["modality_settings"]:
        raise KeyError(
            f"未知模态 '{modality}'，请在 config.PREPROC['modality_settings'] "
            f"中添加对应配置。当前支持：{list(PREPROC['modality_settings'].keys())}"
        )
    settings = dict(PREPROC["modality_settings"][modality])   # 浅拷贝，避免污染全局配置
    overrides = PREPROC.get("SEQUENCE_OVERRIDES", {}).get(sequence_name, {})
    settings.update(overrides)
    return settings


# ═══════════════════════════════════════════════════════════════════
#  图像读取（DICOM 目录 / NIfTI / NRRD 自动识别）
# ═══════════════════════════════════════════════════════════════════

def load_image(path: str) -> sitk.Image:
    """
    自动识别路径类型并读取图像，兼容 DICOM 目录、NIfTI 单文件、NRRD 单文件。

    策略
    ----
    - 目录 → DICOM 序列：使用 ImageSeriesReader，自动按 InstanceNumber 排序
    - 文件 → NIfTI（.nii/.nii.gz）或 NRRD（.nrrd）：由 SimpleITK 按扩展名
             自动识别读取，无需额外分支处理

    Parameters
    ----------
    path : str — 图像路径（DICOM 目录 / .nii(.gz) 文件 / .nrrd 文件）

    Returns
    -------
    sitk.Image

    Raises
    ------
    FileNotFoundError — 路径不存在，或目录内无 DICOM 文件
    """
    path = str(path).strip()

    if os.path.isdir(path):
        reader = sitk.ImageSeriesReader()
        dicom_files = reader.GetGDCMSeriesFileNames(path)
        if not dicom_files:
            raise FileNotFoundError(f"目录 '{path}' 中未找到 DICOM 文件。")
        reader.SetFileNames(dicom_files)
        reader.MetaDataDictionaryArrayUpdateOn()
        reader.LoadPrivateTagsOn()
        image = reader.Execute()
        logger.debug(f"读取 DICOM 目录：{len(dicom_files)} 层")
    else:
        if not os.path.exists(path):
            raise FileNotFoundError(f"文件不存在：{path}")
        # SimpleITK 依据文件扩展名自动选择 NIfTI / NRRD 等格式的读取器
        image = sitk.ReadImage(path)
        logger.debug(f"读取文件：{path}")

    # 向量/多通道图像处理：取第 0 通道继续（避免后续 Cast 到 Float32 失败）
    n_comp = image.GetNumberOfComponentsPerPixel()
    if n_comp > 1:
        logger.warning(
            f"检测到向量图像（{n_comp} 通道，类型={image.GetPixelIDTypeAsString()}），"
            f"自动提取第 0 通道继续处理"
        )
        image = sitk.VectorIndexSelectionCast(image, 0)

    logger.info(
        f"图像尺寸={image.GetSize()}, "
        f"间距={tuple(round(s, 3) for s in image.GetSpacing())} mm, "
        f"像素类型={image.GetPixelIDTypeAsString()}"
    )
    return image


def load_mask(path: str, reference: sitk.Image) -> sitk.Image:
    """
    读取 ROI mask 并重采样至与参考图像完全一致的物理空间。

    插值方式：最近邻（sitkNearestNeighbor），保持二值标签离散性。

    Parameters
    ----------
    path      : str        — ROI mask 文件路径（NIfTI / NRRD）
    reference : sitk.Image — 目标物理空间的参考图像（已重采样+归一化）

    Returns
    -------
    sitk.Image — 与 reference 空间完全对齐的 UInt8 mask
    """
    mask = sitk.ReadImage(str(path).strip(), sitk.sitkUInt8)

    resampler = sitk.ResampleImageFilter()
    resampler.SetReferenceImage(reference)
    resampler.SetInterpolator(sitk.sitkNearestNeighbor)
    resampler.SetDefaultPixelValue(0)
    mask_resampled = resampler.Execute(mask)

    n_voxels = int(sitk.GetArrayFromImage(mask_resampled).sum())
    logger.info(f"Mask 重采样完成，ROI 体素数：{n_voxels}")
    return mask_resampled


# ═══════════════════════════════════════════════════════════════════
#  空间重采样
# ═══════════════════════════════════════════════════════════════════

def resample_image(image: sitk.Image,
                   target_spacing: tuple = None) -> sitk.Image:
    """
    将图像重采样至目标体素间距（默认取自 config.PREPROC["target_spacing"]）。

    插值方式：B-Spline 5 阶插值，兼顾细节保留与阶梯伪影抑制。

    物理一致性：保留原始物理视野、Origin 和 Direction，仅改变体素网格密度：
        new_size[i] = round(orig_size[i] × orig_spacing[i] / target_spacing[i])

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

    new_size = [
        int(round(osz * osp / tsp))
        for osz, osp, tsp in zip(orig_size, orig_spacing, target_spacing)
    ]

    resampler = sitk.ResampleImageFilter()
    resampler.SetOutputSpacing(target_spacing)
    resampler.SetSize(new_size)
    resampler.SetOutputDirection(image.GetDirection())
    resampler.SetOutputOrigin(image.GetOrigin())
    resampler.SetTransform(sitk.Transform())
    resampler.SetDefaultPixelValue(0)
    resampler.SetInterpolator(sitk.sitkBSpline)

    resampled = resampler.Execute(image)
    logger.info(
        f"重采样：{orig_size} → {new_size}，"
        f"间距：{tuple(round(s,2) for s in orig_spacing)} → {target_spacing} mm"
    )
    return resampled


# ═══════════════════════════════════════════════════════════════════
#  N4 偏置场校正（MRI 专用，按模态配置决定是否执行）
# ═══════════════════════════════════════════════════════════════════

def correct_bias_field(image: sitk.Image,
                       n4_iterations: list,
                       mask: sitk.Image = None) -> sitk.Image:
    """
    N4 偏置场校正（Tustison et al., 2010）。

    Parameters
    ----------
    image         : sitk.Image      — 输入图像（将被转换为 Float32 后处理）
    n4_iterations : list             — 各多分辨率层级的迭代次数（粗→细）
    mask          : sitk.Image, optional — 若提供，仅在 mask 区域内估计偏置场

    Returns
    -------
    sitk.Image — 校正后的 Float32 图像
    """
    image_float = sitk.Cast(image, sitk.sitkFloat32)

    corrector = sitk.N4BiasFieldCorrectionImageFilter()
    corrector.SetMaximumNumberOfIterations(n4_iterations)
    corrector.SetConvergenceThreshold(0.001)

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
                        method: str,
                        ct_window: tuple = None,
                        mask:  sitk.Image = None) -> sitk.Image:
    """
    灰度归一化（方法由调用方传入，通常来自 resolve_preproc_settings()）。

    minmax（推荐 CT）
    ----------------
    1. 按 ct_window 裁剪异常值
    2. Min-Max 线性映射至 [0, 1]

    zscore（推荐 MRI）
    -----------------
    在 mask 内（若有）或全图统计均值 μ 和标准差 σ：output = (image - μ) / σ

    none（调试 / 已是定量图如 ADC 时可用）
    ---------------------------------------
    跳过归一化，直接返回 Float32 副本，保留原始定量意义。

    Parameters
    ----------
    image     : sitk.Image      — 输入图像
    method    : str              — "zscore" / "minmax" / "none"
    ct_window : tuple, optional — minmax 模式下的裁剪窗口 (lo, hi)
    mask      : sitk.Image, optional — zscore 模式下用于统计区域内统计量

    Returns
    -------
    sitk.Image — 归一化后的 Float32 图像

    Raises
    ------
    ValueError   — 未知归一化方法
    RuntimeError — zscore 模式下标准差接近零
    """
    if method == "none":
        return sitk.Cast(image, sitk.sitkFloat32)

    arr = sitk.GetArrayFromImage(image).astype(np.float32)

    if method == "minmax":
        lo, hi = ct_window if ct_window is not None else (float(arr.min()), float(arr.max()))
        arr = np.clip(arr, lo, hi)
        arr = (arr - lo) / (hi - lo + 1e-8)
        logger.info(f"灰度归一化（minmax，窗：[{lo}, {hi}]）")

    elif method == "zscore":
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
        raise ValueError(f"未知归一化方法：'{method}'，可选：zscore / minmax / none")

    result = sitk.GetImageFromArray(arr)
    result.CopyInformation(image)
    return result


# ═══════════════════════════════════════════════════════════════════
#  中间结果验证
# ═══════════════════════════════════════════════════════════════════

def validate_preprocessed(image: sitk.Image,
                           mask:  sitk.Image,
                           patient_id: str,
                           sequence:   str):
    """
    验证预处理结果的基本一致性：图像/mask 尺寸一致 + mask 非空。

    Raises
    ------
    RuntimeError — 任意检查项不通过
    """
    img_size  = image.GetSize()
    mask_size = mask.GetSize()

    if img_size != mask_size:
        raise RuntimeError(
            f"预处理验证失败（患者 {patient_id} | {sequence}）："
            f"图像尺寸 {img_size} ≠ mask 尺寸 {mask_size}。"
        )

    n_voxels = int(sitk.GetArrayFromImage(mask).sum())
    if n_voxels == 0:
        raise RuntimeError(
            f"预处理验证失败（患者 {patient_id} | {sequence}）：mask 体素数为 0。"
        )

    logger.info(
        f"预处理验证通过（患者 {patient_id} | {sequence}）："
        f"尺寸={img_size}，mask 体素数={n_voxels}"
    )


# ═══════════════════════════════════════════════════════════════════
#  完整预处理流程（单个序列）
# ═══════════════════════════════════════════════════════════════════

def preprocess_sequence(image_path: str,
                         output_dir: str,
                         patient_id: str,
                         sequence:   str,
                         modality:   str,
                         roi_path:   str = None) -> tuple:
    """
    对单个序列执行完整预处理流程。

    步骤顺序
    --------
    1. load_image()           — 读取图像（DICOM/NIfTI/NRRD 自动识别）
    2. resample_image()       — 统一体素间距
    3. correct_bias_field()   — N4 校正（该模态 apply_n4=True 时）
    4. normalize_intensity()  — 灰度归一化（按模态/序列配置）
    5. （若提供 roi_path）load_mask() + validate_preprocessed()
    6. 保存 NIfTI 文件

    Parameters
    ----------
    image_path : str — 输入图像路径（DICOM 目录 / NIfTI / NRRD）
    output_dir : str — 预处理结果保存目录
    patient_id : str
    sequence   : str — 序列名（如 "CT_arterial", "ADC"）
    modality   : str — "CT" 或 "MRI"
    roi_path   : str, optional — ROI mask 路径（仅参考序列需要）

    Returns
    -------
    (image, mask) : tuple[sitk.Image, sitk.Image | None]
        非参考序列（roi_path=None）时 mask 为 None

    Raises
    ------
    任何步骤的异常直接向上传播，由 pipeline.py 捕获并记录。
    """
    logger.info(f"[预处理] 患者 {patient_id} | 序列 {sequence} | 模态 {modality}")
    os.makedirs(output_dir, exist_ok=True)

    settings = resolve_preproc_settings(sequence, modality)

    # 步骤 1：读取图像
    image = load_image(image_path)

    # 步骤 2：重采样
    image = resample_image(image)

    # 步骤 3：N4 偏置场校正（按模态配置决定）
    if settings["apply_n4"]:
        logger.info("执行 N4 偏置场校正...")
        image = correct_bias_field(image, settings["n4_iterations"])

    # 步骤 4：灰度归一化
    image = normalize_intensity(
        image,
        method    = settings["normalization"],
        ct_window = settings.get("ct_window"),
    )

    # 步骤 5：ROI mask（仅参考序列）
    mask = None
    if roi_path:
        mask = load_mask(roi_path, reference=image)
        validate_preprocessed(image, mask, patient_id, sequence)
        sitk.WriteImage(mask, os.path.join(output_dir, f"{patient_id}_{sequence}_mask.nii.gz"))

    # 步骤 6：保存图像
    sitk.WriteImage(image, os.path.join(output_dir, f"{patient_id}_{sequence}_image.nii.gz"))
    logger.info(f"预处理结果已保存：{output_dir}")

    return image, mask


# ═══════════════════════════════════════════════════════════════════
#  单独运行入口
# ═══════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(levelname)s  %(message)s')

    parser = argparse.ArgumentParser(description="单独运行预处理模块（单序列）")
    parser.add_argument('--image',    required=True, help='图像路径（DICOM目录/NIfTI/NRRD）')
    parser.add_argument('--roi',      default=None,  help='ROI mask 路径（仅参考序列需要）')
    parser.add_argument('--out',      required=True, help='输出目录')
    parser.add_argument('--pid',      required=True, help='患者 ID')
    parser.add_argument('--sequence', required=True, help='序列名')
    parser.add_argument('--modality', required=True, choices=['CT', 'MRI'], help='模态')
    args = parser.parse_args()

    img, msk = preprocess_sequence(
        args.image, args.out, args.pid, args.sequence, args.modality, args.roi
    )
    msg = f"完成：图像尺寸={img.GetSize()}"
    if msk is not None:
        msg += f"，mask 体素数={int(sitk.GetArrayFromImage(msk).sum())}"
    print(msg)
