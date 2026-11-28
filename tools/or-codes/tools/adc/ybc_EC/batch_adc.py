#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
批量计算 ADC 图像（DWI0 b=0, DWI1 b=800）
配置参数全部集中在下方“用户配置”区域，请根据需要修改。
"""

import os
import glob
import numpy as np
import SimpleITK as sitk

# ---- 配置来自工程 settings.ini（--ini / --project / 向上查找）----
import os as _os
import sys as _sys

_CORE = _os.environ.get("HABITAT_CORE_ROOT") or r"{or_codes}"
if _CORE not in _sys.path:
    _sys.path.insert(0, _CORE)

from habitat_core import tool_ini as _tk  # noqa: E402


# ========================= 用户配置（请修改） =========================
# 1. 根目录（包含所有子文件夹，每个子文件夹内有 DWI0 和 DWI1）
ROOT_DIR = _tk.get("ADC_ROOT_DIR")
DRY_RUN = False          # --dry-run：只计算并打印目标路径，不落盘

# 2. b 值（单位：s/mm²）
B0 = 0
B1 = 800

# 3. DWI0 和 DWI1 的目录/文件名关键词（用于自动匹配）
DWI0_KEY = "DWI0"   # 可以匹配文件夹名或文件名开头
DWI1_KEY = "DWI1"

# 4. 输出 ADC 文件名（将保存在每个子文件夹下）
OUTPUT_FILENAME = "ADC.nii.gz"   # 支持 .nii, .nii.gz, .mhd 等

# 5. 是否强制覆盖已有输出文件（True 覆盖，False 跳过）
FORCE_OVERWRITE = True

# 6. 数值稳定性参数（避免除零）
EPSILON = 1e-10

# ========================= 核心函数（无需修改） =========================

def read_image(path):
    """读取 DICOM 系列文件夹或单个图像文件（NIfTI、MHD等）"""
    if os.path.isdir(path):
        reader = sitk.ImageSeriesReader()
        series_ids = reader.GetGDCMSeriesIDs(path)
        if not series_ids:
            raise ValueError(f"No DICOM series found in {path}")
        series_file_names = reader.GetGDCMSeriesFileNames(path, series_ids[0])
        reader.SetFileNames(series_file_names)
        return reader.Execute()
    else:
        return sitk.ReadImage(path)

def compute_adc(dwi0_path, dwi1_path, output_path, b0, b1, eps):
    """计算 ADC 并保存"""
    try:
        img0 = read_image(dwi0_path)
        img1 = read_image(dwi1_path)
    except Exception as e:
        raise RuntimeError(f"读取图像失败: {e}")

    # 检查几何一致性（仅警告，不中断）
    if img0.GetSize() != img1.GetSize() or img0.GetOrigin() != img1.GetOrigin():
        print("  警告: DWI0 和 DWI1 的几何信息不一致，假定已配准。")

    s0 = sitk.GetArrayFromImage(img0).astype(np.float64)
    s1 = sitk.GetArrayFromImage(img1).astype(np.float64)

    # 避免除零
    s0_safe = np.where(s0 == 0, eps, s0)
    ratio = s1 / s0_safe
    ratio = np.clip(ratio, eps, 1.0)   # 防止 log 负数
    adc = -np.log(ratio) / (b1 - b0)

    adc_img = sitk.GetImageFromArray(adc)
    adc_img.CopyInformation(img0)
    if DRY_RUN:
        print(f"  [dry-run] 将写出 {output_path}（未落盘）")
        return
    sitk.WriteImage(adc_img, output_path)
    print(f"  ADC 已保存: {output_path}")

def batch_process():
    """批量遍历子文件夹"""
    if not os.path.exists(ROOT_DIR):
        print(f"错误: 根目录不存在 {ROOT_DIR}")
        return

    subdirs = [d for d in os.listdir(ROOT_DIR) 
               if os.path.isdir(os.path.join(ROOT_DIR, d))]
    
    for sub in subdirs:
        sub_path = os.path.join(ROOT_DIR, sub)
        # 构造 DWI0 和 DWI1 的路径（支持文件夹或文件）
        dwi0_candidates = glob.glob(os.path.join(sub_path, f"{DWI0_KEY}*"))
        dwi1_candidates = glob.glob(os.path.join(sub_path, f"{DWI1_KEY}*"))

        if not dwi0_candidates or not dwi1_candidates:
            print(f"跳过 {sub}: 未找到 {DWI0_KEY} 或 {DWI1_KEY}")
            continue

        dwi0_path = dwi0_candidates[0]  # 取第一个匹配
        dwi1_path = dwi1_candidates[0]

        output_adc = os.path.join(sub_path, OUTPUT_FILENAME)
        if not FORCE_OVERWRITE and os.path.exists(output_adc):
            print(f"跳过 {sub}: ADC 已存在 ({output_adc})")
            continue

        print(f"处理 {sub} ...")
        try:
            compute_adc(dwi0_path, dwi1_path, output_adc, B0, B1, EPSILON)
        except Exception as e:
            print(f"  错误: {e}")

if __name__ == "__main__":
    import sys as _sys2
    DRY_RUN = "--dry-run" in _sys2.argv
    if DRY_RUN:
        print("*** DRY-RUN：只打印将写出的 ADC 路径，不落盘 ***")
    batch_process()