# -*- coding: utf-8 -*-
"""
reslice_derived_maps.py - 利用已保存的配准矩阵, 将机载派生参数图重采样到 T1 空间
==========================================================
输入:
  derivatives/Diffusion_Coreg/sub-XXX/coreg_matrix_sub-XXX_<mod>.mat
  (G->F voxel 约定, 由 coreg_diffusion_to_t1.m 生成)
  源派生图: 原始 dingallcases 中的 *_adc / *_fa / *_tracew .nii.gz

输出:
  derivatives/Diffusion_Coreg/sub-XXX/params/coreg_<mod>_<LABEL>.nii.gz

用法:
  python reslice_derived_maps.py
  python reslice_derived_maps.py --subs sub-007 --mods dti
"""
from __future__ import annotations
import os

import argparse
import sys
from pathlib import Path

import nibabel as nib
import numpy as np
from scipy.io import loadmat
from scipy.ndimage import affine_transform

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ====================================================================
# 配置区
# ====================================================================
STUDY_ROOT = Path(os.environ.get("STUDY_ROOT", ""))
RAW_DIR    = STUDY_ROOT / "rawdata"
OUT_ROOT   = STUDY_ROOT / "derivatives" / "Diffusion_Coreg"
SRC_ROOT   = Path(os.environ.get("SRC_ROOT", ""))
MATRIX_MAP = STUDY_ROOT / "subjects_matrix.tsv"   # sub-XXX -> source_dir
FORCE      = False
SUBS_FILTER: tuple = ()
MODS_FILTER = ("dti", "dki", "ivim")

# 模态 -> 源目录中派生图关键词 -> 输出标签
DERIVED_MAP = {
    "dti":  [("ep2d_diff_mddw_25_p2_adc", "ADC"),
             ("ep2d_diff_mddw_25_p2_tracew", "TRACEW"),
             ("ep2d_diff_mddw_25_p2_fa", "FA")],
    "dki":  [("ep2d_diff_dki_adc", "ADC")],
    "ivim": [("ep2d_diff_ivim_adc", "ADC")],
}
# ====================================================================


def load_sub2src():
    """读取 subjects_matrix.tsv -> {sub-XXX: source_dir}。"""
    out = {}
    for l in MATRIX_MAP.read_text(encoding="utf-8").splitlines()[1:]:
        f = l.split("\t")
        if len(f) >= 3:
            out[f[0]] = f[2]
    return out


def find_source_dir(sub, src_map):
    src = src_map.get(sub)
    if not src:
        return None
    p1 = SRC_ROOT / src / "rawnii"
    p2 = SRC_ROOT / "rawnii" / src
    if p1.is_dir():
        return p1
    if p2.is_dir():
        return p2
    return None


def reslice_one(sub_dir: Path, sub: str, mod: str, src_dir: Path) -> int:
    """重采样单个受试者单模态的派生图。返回成功数。"""
    matf = sub_dir / f"coreg_matrix_{sub}_{mod}.mat"
    if not matf.exists():
        return 0
    aff = np.asarray(loadmat(str(matf))["aff"], dtype=float)   # G->F voxel
    # 输出网格 = T1 尺寸 (固定 256x256x176, 与配准输出一致)
    t1_path = RAW_DIR / sub / "anat" / f"{sub}_T1w.nii.gz"
    if not t1_path.exists():
        return 0
    t1 = nib.load(str(t1_path))
    out_shape = t1.shape[:3]
    t1_affine = t1.affine

    A = aff[:3, :3]
    t = aff[:3, 3]
    # scipy affine_transform: f0 = A @ g0 + (A@ones + t - 1)
    offset = A @ np.ones(3) + t - 1.0

    params_dir = sub_dir / "params"
    params_dir.mkdir(exist_ok=True)
    n = 0
    for kw, label in DERIVED_MAP.get(mod, []):
        srcf = None
        for f in src_dir.iterdir():
            if kw in f.name and f.name.endswith(".nii.gz"):
                srcf = f
                break
        if srcf is None:
            continue
        dst = params_dir / f"coreg_{mod}_{label}.nii.gz"
        if dst.exists() and not FORCE:
            n += 1
            continue
        try:
            data = nib.load(str(srcf)).get_fdata()
            if data.ndim == 4:
                data = data[..., 0]
            resampled = affine_transform(
                data.astype(np.float64), A, offset=offset, output_shape=out_shape,
                order=1, mode="constant", cval=0.0)
            nib.save(nib.Nifti1Image(resampled.astype(np.float32), t1_affine),
                     str(dst))
            n += 1
        except Exception as e:
            print(f"  [ERR] {sub} {mod} {label}: {e}")
    return n


def main() -> None:
    global FORCE, SUBS_FILTER, MODS_FILTER
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--subs", type=str, default="")
    ap.add_argument("--mods", type=str, default="")
    args = ap.parse_args()
    FORCE = FORCE or args.force
    if args.subs:
        SUBS_FILTER = tuple(x.strip() for x in args.subs.split(",") if x.strip())
    if args.mods:
        MODS_FILTER = tuple(x.strip() for x in args.mods.split(",") if x.strip())

    src_map = load_sub2src()
    subs = sorted(p.name for p in RAW_DIR.iterdir()
                  if p.is_dir() and p.name.startswith("sub-"))
    if SUBS_FILTER:
        subs = [s for s in subs if s in SUBS_FILTER]

    print(f"subjects={len(subs)} mods={MODS_FILTER}")
    total = 0
    for sub in subs:
        sub_dir = OUT_ROOT / sub
        if not sub_dir.is_dir():
            continue
        src_dir = find_source_dir(sub, src_map)
        if src_dir is None:
            continue
        for mod in MODS_FILTER:
            total += reslice_one(sub_dir, sub, mod, src_dir)
    print(f"resliced derived maps total: {total}")


if __name__ == "__main__":
    main()