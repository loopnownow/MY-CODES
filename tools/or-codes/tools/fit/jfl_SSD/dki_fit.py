#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DKI 参数图计算脚本（trace-weighted 单方向数据）

注意：FA/AD/RD/AK/RK/MKT 需要多方向张量数据，本脚本不提供。
可计算参数：S0, MD, MK, ADC_b1000, ADC_b2000, NG (非高斯度), R2 (拟合优度)

用法：
  python dki_fit.py               # 批量：遍历配置区 ROOT_DIR 下 GROUPS 各组全部患者
  python dki_fit.py <文件夹路径>   # 单文件夹模式（兼容原用法）

输出写入各患者目录内。
"""

# ── 配置区（软编码：修改此处即可，勿改下方逻辑）────────────────
ROOT_DIR = _tk.get("FIT_DKI_ROOT_DIR")
GROUPS   = ['HC', 'SSD']                 # 组名单，批量模式下按此顺序遍历各患者子目录
DRY_RUN  = False                         # True=演练模式（只校验文件与预览输出，不拟合/不保存）
MD_CLIP  = (0, 5e-3)                     # mm²/s
MK_CLIP  = (0, 3.0)
ADC_CLIP = (0, 5e-3)
NG_CLIP  = (0, 1e-5)
# ────────────────────────────────────────────────────────────

import sys
import os
import glob
import numpy as np
import nibabel as nib
from dipy.segment.mask import median_otsu

# ---- 配置来自工程 settings.ini（--ini / --project / 向上查找）----
import os as _os
import sys as _sys

_CORE = _os.environ.get("HABITAT_CORE_ROOT") or r"{or_codes}"
if _CORE not in _sys.path:
    _sys.path.insert(0, _CORE)

from habitat_core import tool_ini as _tk  # noqa: E402


# 输出文件清单（用于 dry run 预览）
OUTPUT_FILES = ['brain_mask.nii.gz', 'S0.nii.gz', 'MD.nii.gz', 'MK.nii.gz',
                'ADC_b1000.nii.gz', 'ADC_b2000.nii.gz', 'NG.nii.gz', 'R2.nii.gz']


def find_files(folder):
    """在单文件夹内查找 DKI 的 nii/bval/bvec 文件。"""
    # 主图按 tracew 关键字判定（不再用 '28_' 前缀排除 ADC：部分病例主图恰为 28_*_tracew）
    nii = sorted(
        f for f in glob.glob(os.path.join(folder, '*.nii*'))
        if 'dki' in os.path.basename(f).lower()
        and 'tracew' in os.path.basename(f).lower()
    )
    bval = glob.glob(os.path.join(folder, '*.bval'))
    bvec = glob.glob(os.path.join(folder, '*.bvec'))
    if not nii or not bval or not bvec:
        raise FileNotFoundError(f"在 {folder} 中未找到 DKI nii/bval/bvec 文件")
    return nii[0], bval[0], bvec[0]


def save(arr, affine, header, path, clip=None):
    """保存 nii.gz（dry run 模式不会调用到此函数）。"""
    if clip:
        arr = np.clip(arr, *clip)
    nib.save(nib.Nifti1Image(arr.astype(np.float32), affine, header), path)
    print(f"  已保存 {os.path.basename(path)}")


def main(folder):
    """计算单个文件夹（患者）的 DKI 参数；DRY_RUN=True 时仅演练。"""
    print(f"=== DKI 参数计算 ({folder}) ===")

    # 定位输入文件（校验：缺文件即抛出，由调用方捕获）
    nii_file, bval_file, bvec_file = find_files(folder)
    bvals = np.loadtxt(bval_file)
    print(f"  输入 nii : {os.path.basename(nii_file)}")
    print(f"  输入 bval: {os.path.basename(bval_file)}（b 值: {bvals}）")
    print(f"  输入 bvec: {os.path.basename(bvec_file)}")

    if DRY_RUN:
        # 演练模式：只预览将生成的输出文件，不拟合、不保存
        print(f"  [DRY RUN] 将生成 {len(OUTPUT_FILES)} 个输出文件:")
        for name in OUTPUT_FILES:
            print(f"    - {os.path.join(folder, name)}")
        print("  [DRY RUN] 跳过拟合与保存。")
        return

    img = nib.load(nii_file)
    data = img.get_fdata(dtype=np.float32)   # (X,Y,Z,N)
    affine, header = img.affine, img.header
    print(f"  数据形状: {data.shape}")

    # 脑掩码
    b0_idx = int(np.argmin(bvals))
    _, mask = median_otsu(data[..., b0_idx], median_radius=3, numpass=2)
    print(f"  脑组织体素: {mask.sum()}")

    # 矢量化 OLS 拟合（对 b 做 /1000 归一化，避免 b² 数值溢出）
    # 模型：ln S(b̃) = c0 - c1*b̃ + c2*b̃²
    # 其中 b̃ = b/1000，则 D = c1/1000 (mm²/s)，K = 6*c2/(c1²)（无单位）
    S = data.reshape(-1, len(bvals))       # (Nvox, N)
    S = np.where(S <= 0, 1e-6, S)
    Y = np.log(S)                          # (Nvox, N)

    b = bvals / 1000.0                     # 归一化，量级 ~0-2
    A = np.column_stack([np.ones_like(b), -b, b**2])   # (N, 3)
    # 最小二乘：C = (AᵀA)⁻¹Aᵀ Y.T  → shape (3, Nvox)
    coef, _, _, _ = np.linalg.lstsq(A, Y.T, rcond=None)   # (3, Nvox)
    c0, c1, c2 = coef                       # 每个 shape (Nvox,)

    S0 = np.exp(c0).reshape(data.shape[:3])
    MD = (c1 / 1000.0).reshape(data.shape[:3])   # 还原单位 mm²/s
    # c2 = (D_norm)²·K/6，其中 D_norm=c1 → K = 6*c2/c1²（与单位无关）
    with np.errstate(divide='ignore', invalid='ignore'):
        MK = np.where(c1 > 1e-4, 6 * c2 / (c1**2), 0).reshape(data.shape[:3])
    MK = MK.clip(*MK_CLIP)                 # 裁剪后再计算 NG

    # 单指数 ADC（用原始 bvals 查找最近 b 值的体积索引）
    S0_lin = data[..., b0_idx]
    idx1000 = int(np.argmin(np.abs(bvals - 1000)))
    idx2000 = int(np.argmin(np.abs(bvals - 2000)))
    with np.errstate(divide='ignore', invalid='ignore'):
        ADC_b1000 = np.where(data[..., idx1000] > 0,
                             np.log(np.maximum(S0_lin, 1e-6) /
                                    np.maximum(data[..., idx1000], 1e-6)) / bvals[idx1000],
                             0)
        ADC_b2000 = np.where(data[..., idx2000] > 0,
                             np.log(np.maximum(S0_lin, 1e-6) /
                                    np.maximum(data[..., idx2000], 1e-6)) / bvals[idx2000],
                             0)

    # 非高斯度 NG = MK * MD²
    NG = MK * MD**2

    # 拟合优度 R²（掩码内）
    Y_pred = (A @ coef).T.reshape(data.shape)   # (X,Y,Z,N)
    SS_res = np.sum((np.log(np.maximum(data, 1e-6)) - Y_pred)**2, axis=-1)
    Y_mean = np.mean(np.log(np.maximum(data, 1e-6)), axis=-1)
    SS_tot = np.sum((np.log(np.maximum(data, 1e-6)) - Y_mean[..., None])**2, axis=-1)
    with np.errstate(divide='ignore', invalid='ignore'):
        R2 = np.where(SS_tot > 0, 1 - SS_res / SS_tot, 0)

    # 掩码外置零
    for arr in [MD, MK, S0, ADC_b1000, ADC_b2000, NG, R2]:
        arr[~mask] = 0

    # 保存
    save(mask.astype(np.uint8),  affine, header, os.path.join(folder, 'brain_mask.nii.gz'))
    save(S0,      affine, header, os.path.join(folder, 'S0.nii.gz'))
    save(MD,      affine, header, os.path.join(folder, 'MD.nii.gz'),      MD_CLIP)
    save(MK,      affine, header, os.path.join(folder, 'MK.nii.gz'),      MK_CLIP)
    save(ADC_b1000, affine, header, os.path.join(folder, 'ADC_b1000.nii.gz'), ADC_CLIP)
    save(ADC_b2000, affine, header, os.path.join(folder, 'ADC_b2000.nii.gz'), ADC_CLIP)
    save(NG,      affine, header, os.path.join(folder, 'NG.nii.gz'),      NG_CLIP)
    save(R2,      affine, header, os.path.join(folder, 'R2.nii.gz'),      (0, 1))

    # 统计（标签使用 ASCII 字符，避免 GBK 控制台编码错误）
    print("\n参数均值（脑掩码内）:")
    for name, arr in [('S0', S0), ('MD(x1e-3)', MD*1e3), ('MK', MK),
                      ('ADC_b1000(x1e-3)', ADC_b1000*1e3),
                      ('ADC_b2000(x1e-3)', ADC_b2000*1e3), ('NG', NG), ('R2', R2)]:
        v = arr[mask]
        print(f"  {name:<22}: mean={np.nanmean(v):.4f}  [{np.nanmin(v):.4f}, {np.nanmax(v):.4f}]")
    print("=== 完成 ===")


def run_batch():
    """批量：遍历 ROOT_DIR 下 GROUPS 各组的所有患者子目录，逐个调用 main()。"""
    print(f"===== DKI 批量处理（DRY_RUN={'ON' if DRY_RUN else 'OFF'}）=====")
    ok, fail = 0, 0
    for group in GROUPS:
        group_dir = os.path.join(ROOT_DIR, group)
        if not os.path.isdir(group_dir):
            print(f"[跳过] 找不到组目录: {group_dir}")
            continue
        patient_dirs = sorted(
            d for d in glob.glob(os.path.join(group_dir, '*'))
            if os.path.isdir(d)
        )
        if not patient_dirs:
            print(f"[组{group}] 无患者子目录")
            continue
        print(f"\n--- 组 {group}: {len(patient_dirs)} 个患者 ---")
        for d in patient_dirs:
            try:
                main(d)
                ok += 1
            except Exception as e:
                fail += 1
                print(f"  [失败] {os.path.basename(d)}: {e}")
    print(f"\n===== 批量结束：成功 {ok}，失败 {fail} =====")
    return fail


if __name__ == '__main__':
    if len(sys.argv) > 1:
        # 单文件夹模式（兼容原用法）
        sys.exit(main(sys.argv[1]) or 0)
    else:
        # 批量模式
        sys.exit(run_batch())