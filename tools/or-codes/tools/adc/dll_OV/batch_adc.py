#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
批量计算 ADC 图像（DWI0 b=0, DWI1 b=800）

适配真实目录结构:
    ROOT_DIR/组文件夹(CCC/ECC等)/病例文件夹/.../DWI文件夹(单个DICOM序列)

DWI 序列内按 DiffusionBValue tag 拆分:
    b=800            → S1 (b1)
    tag 缺失/≈0      → S0 (b0)

若 S1 的层数是 S0 的整数倍（表示 b800 含多个扩散方向），
会先对各扩散方向做体素均值，再计算 ADC。

说明:
    - 只处理含 DWI 文件夹的病例；无 DWI 的病例完全忽略（不计数、不报错）。
    - 断点续跑: 输出文件已存在则跳过（FORCE_OVERWRITE=False 时）。
    - dry run: 只打印将处理的病例清单，不读取像素、不计算、不写文件。

配置参数全部集中在下方"用户配置"区域，请根据需要修改。
"""

import os
import numpy as np
import SimpleITK as sitk
import pydicom

# ---- 配置来自工程 settings.ini（--ini / --project / 向上查找）----
import os as _os
import sys as _sys

_CORE = _os.environ.get("HABITAT_CORE_ROOT") or r"{or_codes}"
if _CORE not in _sys.path:
    _sys.path.insert(0, _CORE)

from habitat_core import tool_ini as _tk  # noqa: E402


# ========================= 用户配置（请修改） =========================
# 1. 根目录（包含组文件夹，组下为病例文件夹）
ROOT_DIR = _tk.get("ADC_ROOT_DIR")

# 2. b 值（单位：s/mm²）
B0 = 0
B1 = 800

# 3. 病例文件夹内 DWI 文件夹名关键词（用于自动匹配）
DWI_DIR_KEY = "DWI"

# 4. 输出 ADC 文件名（将保存在病例文件夹根目录）
OUTPUT_FILENAME = "ADC.nii.gz"   # 支持 .nii, .nii.gz, .mhd 等

# 5. 是否强制覆盖已有输出文件（True 覆盖，False 跳过 → 断点续跑）
FORCE_OVERWRITE = False

# 6. dry run: True 只打印将处理的病例清单，不实际计算
DRY_RUN = False

# 7. 数值稳定性参数（避免除零）
EPSILON = 1e-10

# 8. 是否对 b1 的多扩散方向先做体素均值（True 自动处理，False 则按层对齐）
MEAN_MULTI_DIR = True

# 9. 只在「无原生 ADC」的病例上拟合（有 ADC 目录的病例保持原生 ADC）
SKIP_IF_NATIVE_ADC = True

# 10. 组白名单（None = 全部）；由 --groups 或控制台传入
GROUP_FILTER = None

# ========================= 核心函数（无需修改） =========================

def list_dcm_files(directory):
    """列出文件夹内的 DICOM 文件并按 InstanceNumber 排序"""
    dcm_files = [os.path.join(directory, f) for f in os.listdir(directory)
                 if f.lower().endswith(('.dcm', '.dicom'))]
    if not dcm_files:
        return []
    # 按 InstanceNumber 排序，保证 b 值与层面正确对应
    def inst_num(path):
        try:
            d = pydicom.dcmread(path, stop_before_pixels=True)
            return int(d.get('InstanceNumber', 0))
        except Exception:
            return 0
    dcm_files.sort(key=inst_num)
    return dcm_files


def get_bvalue(dcm):
    """提取 DICOM 的 b 值。

    优先标准 tag DiffusionBValue (0018,9087)。
    若缺失（常见于 GE 设备），回退到 GE 私有 tag (0043,1039) 的首元素。
    返回数值；无法确定时返回 None。
    """
    b = dcm.get('DiffusionBValue')
    if b is not None:
        try:
            return float(b)
        except (TypeError, ValueError):
            pass
    # GE 私有 tag: Slop_int_6...slop_int_9, 首元素为 b 值
    slop = dcm.get((0x0043, 0x1039))
    if slop is not None:
        try:
            return float(slop[0])
        except (TypeError, ValueError, IndexError):
            pass
    return None


def split_by_bvalue(dcm_files):
    """按 b 值将 DICOM 文件拆分为 b0 和 b1 两组"""
    s0_files, s1_files = [], []
    for f in dcm_files:
        d = pydicom.dcmread(f, stop_before_pixels=True)
        b = get_bvalue(d)
        # tag 缺失或 b≈0 视为 b0
        if b is None or b <= 1:
            s0_files.append(f)
        else:
            s1_files.append(f)
    # 全无 b 值标签（去标识化）→ 退化为「双栈按亮度拆分」
    if not s1_files and len(s0_files) >= 4:
        s0_files, s1_files = split_two_stacks(dcm_files)
    return s0_files, s1_files


def split_two_stacks(dcm_files):
    """标签缺失时的双栈拆分：同一层位置内亮度高者为 b0、低者为 b800。

    适用前提：同一 series 内 b0 栈与 b800 栈层数相同且层位置重合。
    任一前置条件不满足时返回 ([], [])，由调用方报错，不做猜测。

    Returns
    -------
    (s0_files, s1_files) : tuple[list, list]
    """
    by_z = {}
    for f in dcm_files:
        d = pydicom.dcmread(f, stop_before_pixels=True, force=True)
        z = round(float(d.get("SliceLocation", 0) or 0), 2)
        by_z.setdefault(z, []).append(f)
    if not by_z or any(len(v) != 2 for v in by_z.values()):
        return [], []
    s0_files, s1_files = [], []
    for z in sorted(by_z):
        pair = by_z[z]
        means = [pydicom.dcmread(f, force=True).pixel_array.astype(float).mean() for f in pair]
        order = sorted(range(len(pair)), key=lambda i: means[i], reverse=True)
        s0_files.append(pair[order[0]])
        s1_files.append(pair[order[1]])
    return s0_files, s1_files


def check_geometry(s0_files, s1_files):
    """b0 / b1 几何一致性校验；不一致直接报错，不重采样（避免引入额外插值）。"""
    keys = ("Rows", "Columns", "PixelSpacing", "SliceThickness")
    d0 = pydicom.dcmread(s0_files[0], stop_before_pixels=True, force=True)
    d1 = pydicom.dcmread(s1_files[0], stop_before_pixels=True, force=True)
    diffs = [k for k in keys if str(d0.get(k)) != str(d1.get(k))]
    if diffs:
        raise ValueError(f"b0/b1 几何不一致: {diffs}")


def read_series_3d(dcm_files):
    """用 ImageSeriesReader 读取一串 DICOM 文件为 3D 图像（多帧沿 z 串联）"""
    if not dcm_files:
        return None
    reader = sitk.ImageSeriesReader()
    reader.SetFileNames(dcm_files)
    return reader.Execute()


def compute_adc_from_dir(dwi_dir, output_path, b0, b1, eps, mean_multi_dir):
    """从 DWI 文件夹计算 ADC 并保存"""
    dcm_files = list_dcm_files(dwi_dir)
    if not dcm_files:
        raise ValueError(f"DWI 文件夹无 DICOM 文件: {dwi_dir}")

    s0_files, s1_files = split_by_bvalue(dcm_files)
    if not s0_files or not s1_files:
        raise ValueError("DWI 序列中未同时找到 b0 与 b1 数据")
    check_geometry(s0_files, s1_files)

    s0 = read_series_3d(s0_files)
    s1 = read_series_3d(s1_files)
    if s0 is None or s1 is None:
        raise ValueError("读取 DICOM 系列失败")

    a0 = sitk.GetArrayFromImage(s0).astype(np.float64)  # (z, y, x)
    a1 = sitk.GetArrayFromImage(s1).astype(np.float64)

    # b1 层数是 b0 的整数倍 → 说明含多个扩散方向，先按方向取体素均值
    if mean_multi_dir and a1.shape[0] != a0.shape[0] and a0.shape[0] > 0:
        ratio = a1.shape[0] // a0.shape[0]
        if a1.shape[0] % a0.shape[0] == 0:
            a1 = a1.reshape(a0.shape[0], ratio, *a0.shape[1:]).mean(axis=1)
            print(f"  检测到 {ratio} 个扩散方向，已做体素均值。")

    if a0.shape != a1.shape:
        raise ValueError(
            f"b0 与 b1 尺寸不匹配: {a0.shape} vs {a1.shape}，"
            "b1 层数需为 b0 的整数倍（多方向）或与 b0 相同"
        )

    # 避免除零
    a0_safe = np.where(a0 == 0, eps, a0)
    ratio = a1 / a0_safe
    ratio = np.clip(ratio, eps, 1.0)   # 防止 log 负数
    adc = -np.log(ratio) / (b1 - b0)

    adc_img = sitk.GetImageFromArray(adc)
    adc_img.CopyInformation(s0)
    output_path = os.path.abspath(output_path)
    sitk.WriteImage(adc_img, output_path)
    print(f"  ADC 已保存: {output_path}")


def collect_cases():
    """遍历根目录，收集所有含 DWI 文件夹的病例。

    返回: [(grp, sub, dwi_dir, output_path), ...]
    无 DWI 文件夹的病例完全忽略。
    """
    cases = []
    if not os.path.exists(ROOT_DIR):
        print(f"错误: 根目录不存在 {ROOT_DIR}")
        return cases

    # 第一层：组文件夹（CCC / ECC / SMBOT）
    groups = [d for d in os.listdir(ROOT_DIR)
              if os.path.isdir(os.path.join(ROOT_DIR, d))]
    if not groups:
        print(f"错误: 根目录下无组文件夹 {ROOT_DIR}")
        return cases

    for grp in groups:
        if GROUP_FILTER and grp not in GROUP_FILTER:
            continue
        grp_path = os.path.join(ROOT_DIR, grp)
        # 第二层：病例文件夹
        subdirs = [d for d in os.listdir(grp_path)
                   if os.path.isdir(os.path.join(grp_path, d))]
        if not subdirs:
            continue

        for sub in subdirs:
            sub_path = os.path.join(grp_path, sub)

            # 定位 DWI 文件夹；无 DWI 的病例完全忽略
            dwi_candidates = [d for d in os.listdir(sub_path)
                              if DWI_DIR_KEY in d and
                              os.path.isdir(os.path.join(sub_path, d))]
            if not dwi_candidates:
                continue
            # 有原生 ADC 目录的病例保持原生 ADC，不拟合
            if SKIP_IF_NATIVE_ADC and os.path.isdir(os.path.join(sub_path, "ADC")):
                continue
            dwi_dir = os.path.join(sub_path, dwi_candidates[0])
            output_path = os.path.join(sub_path, OUTPUT_FILENAME)
            cases.append((grp, sub, dwi_dir, output_path))

    return cases


def batch_process():
    """两层遍历：根目录 → 组文件夹 → 病例文件夹"""
    cases = collect_cases()
    if not cases:
        print("未找到任何含 DWI 文件夹的病例。")
        return

    total_done = 0
    total_skip = 0
    total_err = 0

    for grp, sub, dwi_dir, output_path in cases:
        # 断点续跑: 输出已存在则跳过
        if not FORCE_OVERWRITE and os.path.exists(output_path):
            if DRY_RUN:
                print(f"[dry-run] {grp}/{sub}: ADC 已存在，将跳过 ({output_path})")
            else:
                print(f"跳过 {grp}/{sub}: ADC 已存在 ({output_path})")
            total_skip += 1
            continue

        if DRY_RUN:
            # dry run: 只打印将处理的病例，不读取像素、不计算
            dcm_files = list_dcm_files(dwi_dir)
            s0_files, s1_files = split_by_bvalue(dcm_files)
            print(f"[dry-run] 将处理 {grp}/{sub}: "
                  f"DWI={dwi_dir} b0={len(s0_files)} b1={len(s1_files)} "
                  f"→ {output_path}")
            continue

        print(f"处理 {grp}/{sub} ...")
        try:
            compute_adc_from_dir(dwi_dir, output_path, B0, B1, EPSILON, MEAN_MULTI_DIR)
            total_done += 1
        except Exception as e:
            print(f"  错误: {e}")
            total_err += 1

    if DRY_RUN:
        print(f"\n[dry-run] 共 {len(cases)} 个含 DWI 病例，"
              f"其中将跳过 {total_skip} 个（输出已存在）。")
    else:
        print(f"\n完成: 成功 {total_done}，跳过 {total_skip}，失败 {total_err}")


if __name__ == "__main__":
    import argparse as _ap

    _parser = _ap.ArgumentParser(description="DWI(b0/b800) → ADC 批量拟合")
    _parser.add_argument("--root", default=None,
                         help="覆盖 ROOT_DIR（组/病例/DWI 三层结构）")
    _parser.add_argument("--groups", default=None,
                         help="组白名单，逗号分隔（如 CCC_EA,CCC_NEA）")
    _parser.add_argument("--execute", action="store_true",
                         help="实际计算并写盘；省略则 dry-run")
    _args = _parser.parse_args()

    if _args.root:
        ROOT_DIR = _args.root
    if _args.groups:
        GROUP_FILTER = [g.strip() for g in _args.groups.split(",") if g.strip()]
    DRY_RUN = not _args.execute
    if DRY_RUN:
        print("*** DRY-RUN：只打印将处理的病例，不落盘 ***")
    batch_process()