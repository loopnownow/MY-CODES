#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
IVIM 参数图计算脚本（segmented 两步法，全矢量化，trace-weighted 数据）
可计算参数：S0, D (真扩散), f (灌注分数), Dstar (伪扩散), brain_mask

算法：
  Step 1 (高 b ≥ B_THRESH)：OLS 拟合对数线性模型 → D, f
  Step 2 (全 b)：信号减法 + OLS → D*
    Y(b) = ln[(S(b)/S0 - (1-f)*exp(-b*D)) / f]  vs.  b
    slope = -D*   （纯矢量化，无循环）

用法：
  python ivim_fit.py               # 批量：遍历配置区 ROOT_DIR 下 GROUPS 各组全部患者
  python ivim_fit.py <文件夹路径>   # 单文件夹模式（兼容原用法）

输出写入各患者目录内。
"""

# ── 配置区（软编码：修改此处即可，勿改下方逻辑）────────────────
ROOT_DIR = _tk.get("FIT_IVIM_ROOT_DIR")
GROUPS    = ['HC', 'SSD']                  # 组名单，批量模式下按此顺序遍历各患者子目录
DRY_RUN   = False                          # True=演练模式（只校验文件与预览输出，不拟合/不保存）
B_THRESH  = 200                            # 高 b 值阈值 (s/mm²)
D_CLIP     = (0,    3e-3)                  # 真扩散裁剪范围 (mm²/s)
F_CLIP     = (0,    1.0)                   # 灌注分数裁剪范围
DSTAR_CLIP = (5e-3, 0.3)                   # 伪扩散裁剪范围 (mm²/s)
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
OUTPUT_FILES = ['brain_mask.nii.gz', 'S0.nii.gz', 'D.nii.gz', 'f.nii.gz', 'Dstar.nii.gz']


def find_files(folder):
    """在单文件夹内查找 IVIM 的 nii/bval 文件。"""
    niis = [f for f in glob.glob(os.path.join(folder, '*.nii*'))
            if 'ivim' in os.path.basename(f).lower()
            and 'adc' not in os.path.basename(f).lower()]
    bvals = glob.glob(os.path.join(folder, '*.bval'))
    if not niis or not bvals:
        raise FileNotFoundError(f"在 {folder} 中未找到 IVIM nii/bval 文件")
    return niis[0], bvals[0]


def save(arr, affine, header, path, clip=None):
    """保存 nii.gz（dry run 模式不会调用到此函数）。"""
    if clip:
        arr = np.clip(arr, *clip)
    nib.save(nib.Nifti1Image(arr.astype(np.float32), affine, header), path)
    print(f"  已保存 {os.path.basename(path)}")


def main(folder):
    """计算单个文件夹（患者）的 IVIM 参数；DRY_RUN=True 时仅演练。"""
    print(f"=== IVIM 参数计算 ({folder}) ===")

    # 定位输入文件（校验：缺文件即抛出，由调用方捕获）
    nii_file, bval_file = find_files(folder)
    bvals = np.loadtxt(bval_file)
    print(f"  输入 nii : {os.path.basename(nii_file)}")
    print(f"  输入 bval: {os.path.basename(bval_file)}（b 值: {bvals}）")

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

    Nvox = int(np.prod(data.shape[:3]))
    S = data.reshape(Nvox, -1)              # (Nvox, N)
    S = np.where(S <= 0, 1e-6, S)

    # ── Step 1：高 b 值 OLS → D 和 f ─────────────────────────────────
    hi = bvals >= B_THRESH
    b_hi = bvals[hi] / 1000.0              # 归一化
    A_hi = np.column_stack([np.ones_like(b_hi), -b_hi])
    coef_hi, _, _, _ = np.linalg.lstsq(A_hi, np.log(S[:, hi]).T, rcond=None)
    lnS0hi, D_norm = coef_hi               # 各 (Nvox,)

    D_flat = D_norm / 1000.0               # mm²/s
    S0hi   = np.exp(lnS0hi)
    S0_b0  = S[:, b0_idx]
    with np.errstate(invalid='ignore', divide='ignore'):
        f_flat = np.where(S0_b0 > 0, 1 - S0hi / S0_b0, 0).clip(*F_CLIP)
    D_flat = D_flat.clip(*D_CLIP)

    # ── Step 2：信号减法后 OLS → D* ────────────────────────────────────
    # S_fast = S/S0 - (1-f)*exp(-b*D)
    # ln(S_fast / f) = -b*D*  → OLS 求斜率
    # 计算慢扩散成分 (Nvox, N)：exp(-b * D)，b 单位 s/mm²，D 单位 mm²/s
    slow = (1 - f_flat[:, None]) * np.exp(-bvals[None, :] * D_flat[:, None])
    with np.errstate(invalid='ignore', divide='ignore'):
        S_fast = S / S0_b0[:, None] - slow     # (Nvox, N)

    # ln(S_fast / f) = -b*D*
    # 只用低 b 值（b < B_THRESH）：高 b 时快成分已完全衰减，S_fast→0 不可用
    lo = bvals < B_THRESH                   # 布尔索引，仅选低 b 值列
    mask_flat = mask.reshape(-1)
    S_fast_lo = S_fast[mask_flat][:, lo]    # (N_brain, N_lo)
    f_brain   = f_flat[mask_flat]           # (N_brain,)
    valid = (f_brain[:, None] > 0.01) & (S_fast_lo > 1e-6)
    with np.errstate(divide='ignore', invalid='ignore'):
        Y_fast_brain = np.where(valid, np.log(S_fast_lo / f_brain[:, None]), 0.0)

    # 矢量化 OLS：A_dstar (N_lo,2) × coef (2, N_brain) = Y_fast_brain.T
    b_lo = bvals[lo]
    A_dstar = np.column_stack([-b_lo, np.ones_like(b_lo)])  # Y = -b*D* + c
    coef_ds, _, _, _ = np.linalg.lstsq(A_dstar, Y_fast_brain.T, rcond=None)
    Dstar_brain = coef_ds[0].clip(*DSTAR_CLIP)

    Dstar_flat = np.zeros(Nvox, dtype=np.float32)
    Dstar_flat[mask_flat] = Dstar_brain

    # Reshape
    shape3 = data.shape[:3]
    D_map     = D_flat.reshape(shape3)
    f_map     = f_flat.reshape(shape3)
    Dstar_map = Dstar_flat.reshape(shape3)

    # 掩码外置零
    for arr in [D_map, f_map, Dstar_map]:
        arr[~mask] = 0

    # 保存
    save(mask.astype(np.uint8), affine, header, os.path.join(folder, 'brain_mask.nii.gz'))
    save(S0_b0.reshape(shape3), affine, header, os.path.join(folder, 'S0.nii.gz'))
    save(D_map,     affine, header, os.path.join(folder, 'D.nii.gz'),     D_CLIP)
    save(f_map,     affine, header, os.path.join(folder, 'f.nii.gz'),     F_CLIP)
    save(Dstar_map, affine, header, os.path.join(folder, 'Dstar.nii.gz'), DSTAR_CLIP)

    # 统计（标签使用 ASCII 字符，避免 GBK 控制台编码错误）
    print("\n参数均值（脑掩码内）:")
    for name, arr in [('D(x1e-3 mm2/s)', D_map*1e3), ('f', f_map),
                      ('Dstar(x1e-3 mm2/s)', Dstar_map*1e3)]:
        v = arr[mask]
        v = v[v > 0]
        if len(v):
            print(f"  {name:<26}: mean={np.nanmean(v):.4f}  [{np.nanmin(v):.4f}, {np.nanmax(v):.4f}]")
    print("=== 完成 ===")


def run_batch():
    """批量：遍历 ROOT_DIR 下 GROUPS 各组的所有患者子目录，逐个调用 main()。"""
    print(f"===== IVIM 批量处理（DRY_RUN={'ON' if DRY_RUN else 'OFF'}）=====")
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